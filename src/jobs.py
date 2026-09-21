"""Shared scrape coordinator.

A single global lock guarantees that at most one scrape runs at a time,
whether it is a single-category scrape or a batch update of watched
categories. The single-category job and the batch job share the same lock and
the same injected ``scrape_func``.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from batch import run_watched_update

_ACTIVITY_LABELS = {
    "completed": "finished",
    "blocked": "blocked",
    "failed": "failed",
    "cancelled": "cancelled",
}


def _idle_single() -> dict[str, Any]:
    return {
        "state": "idle", "category": None, "pages_fetched": 0,
        "listings_seen": 0, "listings_new": 0, "stop_reason": None, "error": None,
    }


def _idle_batch() -> dict[str, Any]:
    return {
        "state": "idle", "categories": [], "pages_fetched": 0,
        "listings_new": 0, "started_at": None, "finished_at": None, "message": None,
    }


def _idle_backfill() -> dict[str, Any]:
    return {
        "state": "idle", "category": None, "pages_done": 0,
        "pages_estimated": None, "listings_seen": 0, "listings_new": 0,
        "eta_seconds": None, "stop_reason": None, "error": None,
        "total_estimate": None, "next_page": 1,
    }


class ScrapeCoordinator:
    """Owns the single global scrape lock and the progress state objects."""

    def __init__(
        self,
        config: dict[str, Any],
        scrape_func: Callable[..., Any],
        backfill_func: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.config = config
        self.scrape_func = scrape_func
        self.backfill_func = backfill_func
        self._state_lock = threading.Lock()
        self._scrape_lock = threading.Lock()  # the one global scrape lock
        self._single = _idle_single()
        self._batch = _idle_batch()
        self._backfill = _idle_backfill()
        self._batch_cancel = threading.Event()
        self._backfill_cancel = threading.Event()

    # -- status ------------------------------------------------------------

    def single_status(self) -> dict[str, Any]:
        with self._state_lock:
            return dict(self._single)

    def batch_status(self) -> dict[str, Any]:
        with self._state_lock:
            return dict(self._batch)

    def backfill_status(self) -> dict[str, Any]:
        with self._state_lock:
            return dict(self._backfill)

    def current_activity(self) -> Optional[str]:
        with self._state_lock:
            if self._batch["state"] == "running":
                return "dávka sledovaných kategórií"
            if self._single["state"] == "running":
                return f"kategória {self._single['category']}"
            if self._backfill["state"] == "running":
                return f"celá kategória {self._backfill['category']}"
        return None

    # -- single scrape -----------------------------------------------------

    def start_single(self, category_key: str, full: bool) -> bool:
        if not self._scrape_lock.acquire(blocking=False):
            return False
        with self._state_lock:
            self._single = {
                "state": "running", "category": category_key, "pages_fetched": 0,
                "listings_seen": 0, "listings_new": 0, "stop_reason": None, "error": None,
            }
        threading.Thread(
            target=self._run_single, args=(category_key, full), daemon=True
        ).start()
        return True

    def _run_single(self, category_key: str, full: bool) -> None:
        def on_page(pages: int, seen: int, new: int) -> None:
            with self._state_lock:
                self._single.update(pages_fetched=pages, listings_seen=seen, listings_new=new)

        try:
            result = self.scrape_func(
                category_key, full=full, config=self.config, on_page=on_page
            )
            state = _ACTIVITY_LABELS.get(result.status, "finished")
            with self._state_lock:
                self._single.update(
                    state=state,
                    pages_fetched=result.pages_fetched,
                    listings_seen=result.listings_seen,
                    listings_new=result.listings_new,
                    stop_reason=result.stop_reason,
                )
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            with self._state_lock:
                self._single.update(state="failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            self._scrape_lock.release()

    # -- full-category backfill --------------------------------------------

    def start_backfill(self, category_key: str, restart: bool = False) -> bool:
        """Start a resumable full-category backfill (same global lock)."""
        if self.backfill_func is None:
            return False
        if not self._scrape_lock.acquire(blocking=False):
            return False
        with self._state_lock:
            self._backfill = _idle_backfill()
            self._backfill.update(state="running", category=category_key, next_page=1)
        self._backfill_cancel.clear()
        threading.Thread(
            target=self._run_backfill, args=(category_key, restart), daemon=True
        ).start()
        return True

    def _run_backfill(self, category_key: str, restart: bool) -> None:
        def on_page(progress: dict[str, Any]) -> None:
            with self._state_lock:
                self._backfill.update(progress)

        try:
            result = self.backfill_func(
                category_key,
                restart=restart,
                config=self.config,
                on_page=on_page,
                should_cancel=self._backfill_cancel.is_set,
            )
            with self._state_lock:
                self._backfill.update(
                    state=result.status,
                    stop_reason=result.stop_reason,
                    pages_done=result.pages_done,
                    pages_estimated=result.pages_estimated,
                    listings_seen=result.listings_seen,
                    listings_new=result.listings_new,
                    eta_seconds=result.eta_seconds,
                    next_page=result.next_page,
                    total_estimate=result.total_estimate,
                )
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            with self._state_lock:
                self._backfill.update(state="failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            self._scrape_lock.release()

    def cancel_backfill(self) -> bool:
        with self._state_lock:
            running = self._backfill["state"] == "running"
        if not running:
            return False
        self._backfill_cancel.set()
        return True

    # -- batch -------------------------------------------------------------

    def start_batch(self, force: bool, deep: bool, trigger: str = "manual") -> bool:
        if not self._scrape_lock.acquire(blocking=False):
            return False

        def worker() -> None:
            try:
                self._execute_batch(force, deep, trigger)
            finally:
                self._scrape_lock.release()

        threading.Thread(target=worker, daemon=True).start()
        return True

    def run_batch_locked(self, force: bool, deep: bool, trigger: str) -> Any:
        """Run a batch synchronously (used by the scheduler).

        Uses the *same* global scrape lock as :meth:`start_batch`; returns
        ``None`` when a scrape is already running.
        """
        if not self._scrape_lock.acquire(blocking=False):
            return None
        try:
            return self._execute_batch(force, deep, trigger)
        finally:
            self._scrape_lock.release()

    def _execute_batch(self, force: bool, deep: bool, trigger: str) -> Any:
        self._batch_cancel.clear()
        with self._state_lock:
            self._batch = _idle_batch()
            self._batch["state"] = "running"

        def on_update(snapshot: dict[str, Any]) -> None:
            with self._state_lock:
                self._batch = snapshot

        try:
            return run_watched_update(
                force=force,
                deep=deep,
                config=self.config,
                scrape_func=self.scrape_func,
                should_cancel=self._batch_cancel.is_set,
                on_update=on_update,
                trigger=trigger,
            )
        except Exception as exc:  # noqa: BLE001
            with self._state_lock:
                self._batch.update(state="failed", message=f"{type(exc).__name__}: {exc}")
            return None

    def cancel_batch(self) -> bool:
        with self._state_lock:
            running = self._batch["state"] == "running"
        if not running:
            return False
        self._batch_cancel.set()
        return True
