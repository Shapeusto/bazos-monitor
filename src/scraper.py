"""Incremental category scraper (Phase 3).

Fetches category *listing* pages only (never detail pages, never query-string
URLs) and stores results in SQLite. The incremental stop rule accounts for the
fact that TOP/promoted ads are shown first and break strict date ordering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Optional

from categories import Category, get_category
from config import load_config, resolve_db_path
from db import Database, utcnow_iso
from fetcher import BlockedError, PoliteSession
from parser import parse_listing_page

logger = logging.getLogger(__name__)

PAGE_SIZE = 20  # 20 ads per listing page (see docs/site_structure.md)


@dataclass
class RunResult:
    category_key: str
    run_id: int
    pages_fetched: int
    listings_seen: int
    listings_new: int
    listings_price_changed: int
    stop_reason: str
    status: str


def page_url(category: Category, page_number: int) -> str:
    """Page 1 = category URL; page n = URL + offset + '/' (e.g. /notebook/20/)."""
    if page_number <= 1:
        return category.url
    return f"{category.url}{(page_number - 1) * PAGE_SIZE}/"


def _page_budget(
    config: dict[str, Any], has_listings: bool, max_pages: Optional[int]
) -> int:
    if max_pages is not None:
        requested = int(max_pages)
    elif has_listings:
        requested = int(config["incremental_max_pages"])
    else:
        requested = int(config["first_run_max_pages"])
    return min(requested, int(config["hard_max_pages"]))


def age_cutoff(config: dict[str, Any]) -> Optional[date]:
    """Earliest ``posted_date`` to keep, or ``None`` when the limit is off.

    ``max_listing_age_days`` is a sliding window: the cutoff is always
    ``today - N``, so it moves forward every day. ``0`` disables the limit.
    """
    try:
        days = int(config.get("max_listing_age_days", 0) or 0)
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return None
    return date.today() - timedelta(days=days)


def too_old(item: Any, cutoff: Optional[date]) -> bool:
    """True when a listing's posted date is before ``cutoff``.

    A missing date never counts as too old (we cannot tell).
    """
    return cutoff is not None and item.posted_date is not None and item.posted_date < cutoff


def scrape_category(
    category_key: str,
    max_pages: Optional[int] = None,
    full: bool = False,
    *,
    db: Optional[Database] = None,
    session: Optional[PoliteSession] = None,
    config: Optional[dict[str, Any]] = None,
    on_page: Optional[Callable[[int, int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> RunResult:
    """Scrape one category incrementally.

    ``full=True`` disables the "caught_up" early stop; ``max_pages`` overrides
    the configured per-run page budget. Both are still capped by
    ``hard_max_pages``. ``on_page`` is an optional progress callback invoked
    after each page with ``(pages_fetched, listings_seen, listings_new)``.
    ``should_cancel`` is checked between pages; when it returns ``True`` the
    run stops with status ``cancelled``.
    """
    config = config or load_config()
    category = get_category(category_key)
    if category is None:
        raise ValueError(f"unknown category: {category_key!r}")

    owns_db = db is None
    owns_session = session is None
    db = db or Database(resolve_db_path(config))
    session = session or PoliteSession(config)

    limit = _page_budget(config, db.has_category_listings(category_key), max_pages)
    pre_existing = db.all_listing_ids()  # snapshot from *before* this run
    cutoff = age_cutoff(config)

    run_id = db.start_run(category_key)
    pages_fetched = 0
    seen_this_run: set[int] = set()
    new_count = 0
    price_changed_count = 0
    stop_reason = "max_pages"
    status = "completed"

    try:
        for page_number in range(1, limit + 1):
            if should_cancel is not None and should_cancel():
                status = "cancelled"
                stop_reason = "cancelled"
                break

            page = parse_listing_page(session.get(page_url(category, page_number)).text, category_key)
            pages_fetched += 1
            items = page.items
            if page.total_count:
                db.set_category_estimate(category_key, page.total_count)

            for item in items:
                if item.id in seen_this_run:
                    continue  # the list may shift while scraping; never double-count
                if too_old(item, cutoff):
                    continue  # outside the age window: never stored
                seen_this_run.add(item.id)
                is_new, price_changed = db.upsert_listing(item, category_key)
                new_count += int(is_new)
                price_changed_count += int(price_changed)

            if on_page is not None:
                on_page(pages_fetched, len(seen_this_run), new_count)

            if not items or page.is_last_page:
                stop_reason = "last_page"
                break

            non_top = [item for item in items if not item.is_top]
            # Once a page's non-TOP listings are all older than the age window,
            # the list (newest first) cannot contain anything recent any more.
            if non_top and all(too_old(item, cutoff) for item in non_top):
                stop_reason = "too_old"
                break
            if non_top and not full and all(item.id in pre_existing for item in non_top):
                stop_reason = "caught_up"
                break
        else:
            stop_reason = "max_pages"

    except BlockedError as exc:
        status = "blocked"
        stop_reason = str(exc)
    except Exception as exc:  # noqa: BLE001 - record any failure in scrape_runs
        status = "failed"
        stop_reason = f"{type(exc).__name__}: {exc}"
    finally:
        db.finish_run(
            run_id,
            status=status,
            stop_reason=stop_reason,
            pages_fetched=pages_fetched,
            listings_seen=len(seen_this_run),
            listings_new=new_count,
            listings_price_changed=price_changed_count,
            now=utcnow_iso(),
        )
        if owns_session:
            session.close()
        if owns_db:
            db.close()

    return RunResult(
        category_key=category_key,
        run_id=run_id,
        pages_fetched=pages_fetched,
        listings_seen=len(seen_this_run),
        listings_new=new_count,
        listings_price_changed=price_changed_count,
        stop_reason=stop_reason,
        status=status,
    )
