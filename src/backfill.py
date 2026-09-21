"""Resumable full-category backfill.

Walks *every* listing page of a category and ignores the incremental
``caught_up`` rule (which is correct for monitoring new ads but stops as soon
as page 1 is fully known). This lets the user see ALL listings of a category,
not just the first 200-500.

Design:

* the parser/upsert internals of :mod:`scraper` are reused - no parsing or
  storage logic is duplicated here;
* progress is persisted after every page in ``backfill_state`` so a cancelled
  or crashed run resumes from ``next_page``;
* the per-run page cap (``backfill_max_pages_per_run``, absolute ceiling
  :data:`BACKFILL_HARD_CEILING`) pauses a large category and it is downloaded
  over several runs;
* the site's header total is only an estimate for the progress bar, never a
  stop condition;
* each run is recorded in ``scrape_runs`` with ``mode = 'backfill'``.

Manual only: neither the scheduler nor the watched-category batch ever call
this module.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Optional

from categories import get_category
from config import load_config, resolve_db_path
from db import Database, utcnow_iso
from fetcher import BlockedError, NotFoundError, PoliteSession
from parser import parse_listing_page
from scraper import PAGE_SIZE, age_cutoff, page_url, scrape_category, too_old

logger = logging.getLogger(__name__)

BACKFILL_HARD_CEILING = 2000
DEFAULT_BACKFILL_MAX_PAGES = 1000

STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_COMPLETE = "complete"
STATUS_BLOCKED = "blocked"

# Post-backfill refresh: page 1-2 of the normal incremental scrape.
_REFRESH_PAGES = 2


@dataclass
class BackfillResult:
    category_key: str
    run_id: int
    status: str
    stop_reason: str
    pages_done: int
    pages_estimated: Optional[int]
    listings_seen: int
    listings_new: int
    total_estimate: Optional[int]
    next_page: int
    eta_seconds: Optional[float]
    refresh: Optional[Any] = None


def _open(db: Optional[Database]) -> tuple[Database, bool]:
    if db is not None:
        return db, False
    return Database(resolve_db_path(load_config())), True


def pages_estimated(total: Optional[int]) -> Optional[int]:
    """Approximate number of listing pages for a header total."""
    if not total or total <= 0:
        return None
    return math.ceil(total / PAGE_SIZE)


def _page_cap(config: dict[str, Any]) -> int:
    try:
        requested = int(config.get("backfill_max_pages_per_run", DEFAULT_BACKFILL_MAX_PAGES))
    except (TypeError, ValueError):
        requested = DEFAULT_BACKFILL_MAX_PAGES
    return max(1, min(requested, BACKFILL_HARD_CEILING))


def _estimate_for(config: dict[str, Any], total: Optional[int]) -> Optional[float]:
    """ETA in seconds for ``total`` listings (pages x delay)."""
    pages = pages_estimated(total)
    if pages is None:
        return None
    return round(pages * float(config.get("request_delay_seconds", 1.5)), 1)


def category_coverage(
    category_key: str, *, db: Optional[Database] = None
) -> dict[str, Any]:
    """Stored vs estimated coverage of a category plus its backfill status."""
    database, owns_db = _open(db)
    try:
        stored = database.conn.execute(
            """
            SELECT COUNT(DISTINCT l.id) AS c
              FROM listings l
              JOIN listing_categories lc ON lc.listing_id = l.id
             WHERE lc.category_key = ?
            """,
            (category_key,),
        ).fetchone()["c"]
        state = database.get_backfill_state(category_key)
        estimate = database.get_category_estimate(category_key)
        if state is not None and state["total_estimate"]:
            estimate = state["total_estimate"]
        status = state["status"] if state is not None else None
        return {
            "category_key": category_key,
            "stored": stored,
            "estimate": estimate,
            "backfill_status": status,
            "complete": status == STATUS_COMPLETE,
            "pages_done": state["pages_done"] if state is not None else 0,
            "next_page": state["next_page"] if state is not None else 1,
            "total_estimate": estimate,
            "eta_seconds": _estimate_for(load_config(), estimate),
        }
    finally:
        if owns_db:
            database.close()


def coverage_overview(*, db: Optional[Database] = None) -> dict[str, dict]:
    """``{category_key: coverage}`` for every category with stored listings."""
    database, owns_db = _open(db)
    try:
        keys = {
            row["category_key"]
            for row in database.conn.execute(
                "SELECT DISTINCT category_key FROM listing_categories"
            )
        }
        keys |= set(database.all_backfill_states())
        return {key: category_coverage(key, db=database) for key in keys}
    finally:
        if owns_db:
            database.close()


def backfill_category(
    category_key: str,
    *,
    restart: bool = False,
    db: Optional[Database] = None,
    session: Optional[PoliteSession] = None,
    config: Optional[dict[str, Any]] = None,
    on_page: Optional[Callable[[dict[str, Any]], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> BackfillResult:
    """Download a whole category page by page, resuming where it stopped.

    ``restart=True`` ignores any saved state and starts from page 1 again.
    Returns a :class:`BackfillResult`; ``status`` is ``complete``, ``paused``
    (cap reached / cancelled / failed), or ``blocked``.
    """
    config = config or load_config()
    category = get_category(category_key)
    if category is None:
        raise ValueError(f"unknown category: {category_key!r}")

    owns_db = db is None
    owns_session = session is None
    db = db or Database(resolve_db_path(config))
    session = session or PoliteSession(config)
    cancel = should_cancel or (lambda: False)
    delay = float(config.get("request_delay_seconds", 1.5))
    cap = _page_cap(config)
    cutoff = age_cutoff(config)

    state = db.get_backfill_state(category_key)
    resuming = (
        not restart
        and state is not None
        and state["status"] in (STATUS_PAUSED, STATUS_BLOCKED, STATUS_RUNNING)
    )
    start_page = max(1, int(state["next_page"])) if resuming else 1
    started_at = (
        state["started_at"] if (resuming and state["started_at"]) else utcnow_iso()
    )
    estimate: Optional[int] = db.get_category_estimate(category_key)
    if resuming and state["total_estimate"]:
        estimate = state["total_estimate"]

    db.save_backfill_state(
        category_key,
        status=STATUS_RUNNING,
        next_page=start_page,
        pages_done=start_page - 1,
        total_estimate=estimate,
        started_at=started_at,
        completed_at=None,
    )

    run_id = db.start_run(category_key, mode="backfill")
    pages_this_run = 0
    seen_this_run: set[int] = set()
    new_count = 0
    price_changed_count = 0
    stop_reason = "cap_reached"
    status = STATUS_PAUSED
    page_number = start_page

    def emit(pages_done: int, next_page: int) -> None:
        if on_page is None:
            return
        estimated = pages_estimated(estimate)
        eta = None
        if estimated is not None:
            eta = round(max(0, estimated - pages_done) * delay, 1)
        on_page({
            "pages_done": pages_done,
            "pages_estimated": estimated,
            "listings_seen": len(seen_this_run),
            "listings_new": new_count,
            "eta_seconds": eta,
            "next_page": next_page,
            "total_estimate": estimate,
        })

    try:
        while pages_this_run < cap:
            if cancel():
                status = STATUS_PAUSED
                stop_reason = "cancelled"
                break

            # Persist next_page *before* fetching so a crash or a block
            # resumes on this exact page.
            db.save_backfill_state(
                category_key,
                status=STATUS_RUNNING,
                next_page=page_number,
                pages_done=page_number - 1,
                total_estimate=estimate,
                started_at=started_at,
            )

            try:
                page = parse_listing_page(
                    session.get(page_url(category, page_number)).text, category_key
                )
            except NotFoundError:
                status = STATUS_COMPLETE
                stop_reason = "last_page"
                page_number += 1
                break

            if page.total_count:
                estimate = page.total_count
                db.set_category_estimate(category_key, page.total_count)

            if not page.items:
                status = STATUS_COMPLETE
                stop_reason = "last_page"
                page_number += 1
                break

            pages_this_run += 1
            for item in page.items:
                if item.id in seen_this_run:
                    continue  # the list may shift while scanning
                if too_old(item, cutoff):
                    continue  # outside the age window: never stored
                seen_this_run.add(item.id)
                is_new, price_changed = db.upsert_listing(item, category_key)
                new_count += int(is_new)
                price_changed_count += int(price_changed)

            page_number += 1
            db.save_backfill_state(
                category_key,
                status=STATUS_RUNNING,
                next_page=page_number,
                pages_done=page_number - 1,
                total_estimate=estimate,
                started_at=started_at,
            )
            emit(page_number - 1, page_number)

            if page.is_last_page:
                status = STATUS_COMPLETE
                stop_reason = "last_page"
                break

            non_top = [item for item in page.items if not item.is_top]
            if non_top and all(too_old(item, cutoff) for item in non_top):
                status = STATUS_COMPLETE
                stop_reason = "too_old"
                break
        else:
            status = STATUS_PAUSED
            stop_reason = "cap_reached"

    except BlockedError as exc:
        status = STATUS_BLOCKED
        stop_reason = str(exc)
    except Exception as exc:  # noqa: BLE001 - record and pause, do not lose state
        logger.exception("backfill failed for %s", category_key)
        status = STATUS_PAUSED
        stop_reason = "failed"

    finally:
        next_page = 1 if status == STATUS_COMPLETE else max(1, page_number)
        db.save_backfill_state(
            category_key,
            status=status,
            next_page=next_page,
            pages_done=max(0, page_number - 1),
            total_estimate=estimate,
            started_at=started_at,
            completed_at=utcnow_iso() if status == STATUS_COMPLETE else None,
        )
        run_status = {
            STATUS_COMPLETE: "completed",
            STATUS_BLOCKED: "blocked",
        }.get(status, "cancelled" if stop_reason == "cancelled" else "completed")
        if stop_reason == "failed":
            run_status = "failed"
        db.finish_run(
            run_id,
            status=run_status,
            stop_reason=stop_reason,
            pages_fetched=pages_this_run,
            listings_seen=len(seen_this_run),
            listings_new=new_count,
            listings_price_changed=price_changed_count,
            now=utcnow_iso(),
        )

    refresh = None
    if status == STATUS_COMPLETE:
        try:
            refresh = scrape_category(
                category_key,
                max_pages=_REFRESH_PAGES,
                db=db,
                session=session,
                config=config,
                should_cancel=cancel,
            )
        except Exception:  # noqa: BLE001 - the backfill itself already succeeded
            logger.exception("post-backfill refresh failed for %s", category_key)

    if owns_session:
        session.close()
    if owns_db:
        db.close()

    return BackfillResult(
        category_key=category_key,
        run_id=run_id,
        status=status,
        stop_reason=stop_reason,
        pages_done=max(0, page_number - 1),
        pages_estimated=pages_estimated(estimate),
        listings_seen=len(seen_this_run),
        listings_new=new_count,
        total_estimate=estimate,
        next_page=1 if status == STATUS_COMPLETE else max(1, page_number),
        eta_seconds=_estimate_for(config, estimate),
        refresh=refresh,
    )
