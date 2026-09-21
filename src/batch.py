"""Batch update of watched categories (Phase 5).

Runs :func:`scraper.scrape_category` sequentially for every watched category.
The caller (``jobs.ScrapeCoordinator``) holds the single global scrape lock, so
a batch and a single-category scrape can never run at the same time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from config import load_config, resolve_db_path
from db import Database, utcnow_iso
from saved import list_watched
from scraper import scrape_category

logger = logging.getLogger(__name__)

# Per-category states used by the UI.
PENDING = "pending"
RUNNING = "running"
DONE = "done"
SKIPPED_RECENT = "skipped_recent"
BLOCKED = "blocked"
FAILED = "failed"
NOT_RUN = "not_run"
CANCELLED = "cancelled"


@dataclass
class BatchCategory:
    category_key: str
    label: str = ""
    status: str = PENDING
    pages_fetched: int = 0
    listings_seen: int = 0
    listings_new: int = 0
    stop_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_key": self.category_key,
            "label": self.label,
            "status": self.status,
            "pages_fetched": self.pages_fetched,
            "listings_seen": self.listings_seen,
            "listings_new": self.listings_new,
            "stop_reason": self.stop_reason,
        }


@dataclass
class BatchResult:
    state: str
    categories: list[BatchCategory] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    message: Optional[str] = None

    @property
    def pages_fetched(self) -> int:
        return sum(category.pages_fetched for category in self.categories)

    @property
    def listings_new(self) -> int:
        return sum(category.listings_new for category in self.categories)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "categories": [category.to_dict() for category in self.categories],
            "pages_fetched": self.pages_fetched,
            "listings_new": self.listings_new,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "message": self.message,
        }


def _recent(last_run: Optional[str], interval_minutes: int) -> bool:
    if not last_run:
        return False
    try:
        finished = datetime.fromisoformat(last_run)
    except ValueError:
        return False
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - finished).total_seconds()
    return age < interval_minutes * 60


def run_watched_update(
    force: bool = False,
    deep: bool = False,
    *,
    config: Optional[dict[str, Any]] = None,
    db: Optional[Database] = None,
    scrape_func: Optional[Callable[..., Any]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    on_update: Optional[Callable[[dict[str, Any]], None]] = None,
    trigger: str = "manual",
) -> BatchResult:
    """Scrape every watched category sequentially.

    * ``force=True`` ignores the ``min_batch_interval_minutes`` skip.
    * ``deep=True`` maps to the scraper's ``full`` mode.
    * a ``blocked`` category aborts the batch; the rest are marked ``not_run``.
    * ``should_cancel`` is checked between categories and between pages.
    * ``trigger`` (``manual`` / ``scheduled`` / ``catch_up``) is recorded in
      ``batch_runs``.
    """
    config = config or load_config()
    owns_db = db is None
    db = db or Database(resolve_db_path(config))
    scrape_func = scrape_func or scrape_category
    cancel = should_cancel or (lambda: False)
    interval = int(config.get("min_batch_interval_minutes", 15))

    watched = list_watched(db=db)
    categories = [BatchCategory(category_key=w["category_key"], label=w["label"]) for w in watched]
    result = BatchResult(state="running", categories=categories, started_at=utcnow_iso())
    run_id: Optional[int] = None

    def notify() -> None:
        if on_update is not None:
            on_update(result.to_dict())

    def mark_remaining_not_run(start: int) -> None:
        for index in range(start, len(categories)):
            categories[index].status = NOT_RUN

    try:
        run_id = db.start_batch_run(trigger, len(categories), now=result.started_at)
        notify()

        for index, watched_item in enumerate(watched):
            category = categories[index]

            if cancel():
                category.status = NOT_RUN
                result.state = "cancelled"
                result.message = "Dávka bola zrušená."
                mark_remaining_not_run(index + 1)
                break

            if not force and _recent(watched_item["last_run"], interval):
                category.status = SKIPPED_RECENT
                category.stop_reason = "skipped_recent"
                notify()
                continue

            category.status = RUNNING
            notify()

            def on_page(pages: int, seen: int, new: int, _index: int = index) -> None:
                target = categories[_index]
                target.pages_fetched = pages
                target.listings_seen = seen
                target.listings_new = new
                notify()

            try:
                run = scrape_func(
                    watched_item["category_key"],
                    full=deep,
                    config=config,
                    db=db,
                    on_page=on_page,
                    should_cancel=cancel,
                )
            except Exception as exc:  # noqa: BLE001 - keep the batch going
                logger.exception("batch: category %s failed", watched_item["category_key"])
                category.status = FAILED
                category.stop_reason = f"{type(exc).__name__}: {exc}"
                result.state = "failed"
                notify()
                continue

            category.pages_fetched = run.pages_fetched
            category.listings_seen = run.listings_seen
            category.listings_new = run.listings_new
            category.stop_reason = run.stop_reason

            if run.status == "completed":
                category.status = DONE
            elif run.status == "blocked":
                category.status = BLOCKED
                result.state = "blocked"
                result.message = (
                    f"Kategória {watched_item['category_key']} bola zablokovaná "
                    "(HTTP 403/429). Dávka sa okamžite prerušila, zvyšné kategórie "
                    "sa nespustili. Skúste to neskôr."
                )
                mark_remaining_not_run(index + 1)
                notify()
                break
            elif run.status == "cancelled":
                category.status = CANCELLED
                result.state = "cancelled"
                result.message = "Dávka bola zrušená."
                mark_remaining_not_run(index + 1)
                notify()
                break
            else:
                category.status = FAILED
                result.state = "failed"
            notify()
    finally:
        if result.state == "running":
            result.state = "finished"
        result.finished_at = utcnow_iso()
        if run_id is not None:
            done = sum(1 for category in categories if category.status == DONE)
            skipped = sum(
                1 for category in categories
                if category.status in (SKIPPED_RECENT, NOT_RUN)
            )
            db.finish_batch_run(
                run_id,
                status=result.state,
                categories_done=done,
                categories_skipped=skipped,
                new_listings=result.listings_new,
                error=result.message,
                now=result.finished_at,
            )
        notify()
        if owns_db:
            db.close()

    return result
