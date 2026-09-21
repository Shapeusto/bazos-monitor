"""Background scheduler for watched-category batches.

Runs at most one batch at a time through the shared
:class:`jobs.ScrapeCoordinator` lock. The clock and the sleep/wake mechanism
are injectable so tests never sleep.

Hard rules (constants in code, *not* configurable):

* minimum interval 30 minutes, maximum 24 h, default 1 h;
* when the lock is busy the run is postponed by 5 minutes (never skipped);
* three consecutive failures disable the scheduler;
* an HTTP 403/429 batch disables it immediately and never auto-re-enables.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from config import load_config, resolve_db_path
from db import Database
from saved import list_watched

logger = logging.getLogger(__name__)

MIN_INTERVAL_MINUTES = 30
MAX_INTERVAL_MINUTES = 1440
DEFAULT_INTERVAL_MINUTES = 60
POSTPONE_WHEN_BUSY_MINUTES = 5
MAX_CONSECUTIVE_FAILURES = 3
STARTUP_DELAY_SECONDS = 60
WAKE_INTERVAL_SECONDS = 30
JITTER_FRACTION = 0.10

KEY_ENABLED = "scheduler_enabled"
KEY_INTERVAL = "scheduler_interval_minutes"
KEY_NEXT_RUN = "scheduler_next_run"
KEY_LAST_FINISHED = "scheduler_last_run_finished"
KEY_LAST_RESULT = "scheduler_last_result_json"
KEY_PAUSE_REASON = "scheduler_pause_reason"
KEY_FAILURES = "scheduler_consecutive_failures"


class SchedulerError(ValueError):
    """Validation error surfaced to the UI as HTTP 400."""


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def compute_next_run(
    last_finish: datetime,
    interval_minutes: int,
    *,
    rng: Optional[Callable[[float, float], float]] = None,
) -> datetime:
    """``last_finish + interval`` with at most +/-10% jitter, never < 30 min."""
    rng = rng or random.uniform
    factor = 1.0 + rng(-JITTER_FRACTION, JITTER_FRACTION)
    minutes = max(float(MIN_INTERVAL_MINUTES), interval_minutes * factor)
    return last_finish + timedelta(minutes=minutes)


class SingleInstanceLock:
    """OS-level exclusive lock on ``data/app.lock`` (PID written for info)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle: Optional[Any] = None

    @property
    def locked(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")
        try:
            handle.seek(0)
            if not handle.read(1):
                handle.write("0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
        except OSError:  # pragma: no cover - informational only
            pass
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:  # pragma: no cover
            pass
        finally:
            self._handle.close()
            self._handle = None


class Scheduler:
    """Persisted, single-threaded scheduler for watched-category batches."""

    def __init__(
        self,
        config: dict[str, Any],
        coordinator: Any,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        wait: Optional[Callable[[float], bool]] = None,
        rng: Optional[Callable[[float, float], float]] = None,
        db_factory: Optional[Callable[[], Database]] = None,
    ) -> None:
        self.config = config
        self.coordinator = coordinator
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._wait = wait or self._default_wait
        self._rng = rng or random.uniform
        self._db_factory = db_factory or (lambda: Database(resolve_db_path(config)))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._running = False
        self._catch_up = False
        self._postponed = False

    # -- settings ----------------------------------------------------------

    def _get(self, key: str, default: str = "") -> str:
        db = self._db_factory()
        try:
            value = db.get_setting(key, default)
            return default if value is None else value
        finally:
            db.close()

    def _set(self, key: str, value: Any) -> None:
        db = self._db_factory()
        try:
            db.set_setting(key, value)
        finally:
            db.close()

    def enabled(self) -> bool:
        return self._get(KEY_ENABLED, "0") == "1"

    def interval(self) -> int:
        try:
            value = int(self._get(KEY_INTERVAL, str(DEFAULT_INTERVAL_MINUTES)))
        except ValueError:
            value = DEFAULT_INTERVAL_MINUTES
        return min(max(value, MIN_INTERVAL_MINUTES), MAX_INTERVAL_MINUTES)

    def next_run(self) -> Optional[datetime]:
        return _parse_dt(self._get(KEY_NEXT_RUN, ""))

    def last_run_finished(self) -> Optional[datetime]:
        return _parse_dt(self._get(KEY_LAST_FINISHED, ""))

    def pause_reason(self) -> str:
        return self._get(KEY_PAUSE_REASON, "")

    def consecutive_failures(self) -> int:
        try:
            return int(self._get(KEY_FAILURES, "0"))
        except ValueError:
            return 0

    def last_result(self) -> Optional[dict]:
        raw = self._get(KEY_LAST_RESULT, "")
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def watched_count(self) -> int:
        db = self._db_factory()
        try:
            return len(list_watched(db=db))
        finally:
            db.close()

    # -- controls ----------------------------------------------------------

    def enable(self) -> None:
        now = self._clock()
        with self._lock:
            self._set(KEY_ENABLED, "1")
            self._set(KEY_PAUSE_REASON, "")
            self._set(KEY_FAILURES, "0")
            # Do NOT run immediately; the user can use "Spustiť teraz".
            self._set(KEY_NEXT_RUN, _iso(now + timedelta(minutes=self.interval())))
            self._catch_up = False
            self._postponed = False

    def disable(self, pause_reason: str = "manual") -> None:
        with self._lock:
            self._set(KEY_ENABLED, "0")
            self._set(KEY_PAUSE_REASON, pause_reason)

    def set_interval(self, minutes: Any) -> int:
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            raise SchedulerError("Interval musí byť celé číslo v minútach.")
        if minutes < MIN_INTERVAL_MINUTES:
            raise SchedulerError(
                f"Interval musí byť aspoň {MIN_INTERVAL_MINUTES} minút."
            )
        if minutes > MAX_INTERVAL_MINUTES:
            raise SchedulerError(
                f"Interval môže byť najviac {MAX_INTERVAL_MINUTES} minút."
            )
        with self._lock:
            self._set(KEY_INTERVAL, str(minutes))
            if self.enabled():
                self._set(KEY_NEXT_RUN, _iso(self._clock() + timedelta(minutes=minutes)))
        return minutes

    def state(self) -> dict[str, Any]:
        now = self._clock()
        enabled = self.enabled()
        interval = self.interval()
        next_run = self.next_run()
        seconds = int((next_run - now).total_seconds()) if next_run else None
        if seconds is not None:
            seconds = max(0, seconds)
        watched = self.watched_count()

        if self._running:
            state = "running"
        elif not enabled:
            reason = self.pause_reason()
            if reason == "blocked":
                state = "paused_blocked"
            elif reason == "failures":
                state = "paused_failures"
            else:
                state = "off"
        elif watched == 0:
            state = "nothing_to_watch"
        elif self._postponed:
            state = "postponed"
        else:
            state = "waiting"

        return {
            "enabled": enabled,
            "interval_minutes": interval,
            "min_interval_minutes": MIN_INTERVAL_MINUTES,
            "max_interval_minutes": MAX_INTERVAL_MINUTES,
            "next_run": _iso(next_run) if next_run else None,
            "seconds_until_next_run": seconds,
            "last_run_finished": _iso(self.last_run_finished()) or None,
            "last_result": self.last_result(),
            "pause_reason": self.pause_reason(),
            "consecutive_failures": self.consecutive_failures(),
            "state": state,
            "watched_count": watched,
        }

    # -- execution ---------------------------------------------------------

    def _postpone(self, now: datetime) -> None:
        self._postponed = True
        self._set(KEY_NEXT_RUN, _iso(now + timedelta(minutes=POSTPONE_WHEN_BUSY_MINUTES)))

    def tick(self, now: Optional[datetime] = None) -> None:
        """One scheduling decision (also called directly by tests)."""
        now = now or self._clock()
        if not self.enabled():
            return

        next_run = self.next_run()
        if next_run is None:
            base = self.last_run_finished() or now
            self._set(KEY_NEXT_RUN, _iso(compute_next_run(base, self.interval(), rng=self._rng)))
            return
        if now < next_run:
            return

        if self.watched_count() == 0:
            self._postponed = False
            self._set(KEY_NEXT_RUN, _iso(now + timedelta(minutes=self.interval())))
            return

        if self.coordinator.current_activity() is not None:
            self._postpone(now)
            return

        trigger = "catch_up" if self._catch_up else "scheduled"
        self._running = True
        try:
            result = self.coordinator.run_batch_locked(force=False, deep=False, trigger=trigger)
        finally:
            self._running = False

        if result is None:  # lock became busy in the meantime
            self._postpone(now)
            return

        self._catch_up = False
        self._handle_outcome(result, now)

    def _handle_outcome(self, result: Any, now: datetime) -> None:
        state = getattr(result, "state", "failed")
        categories = getattr(result, "categories", []) or []
        summary = {
            "status": state,
            "new_listings": getattr(result, "listings_new", 0),
            "categories_done": sum(1 for c in categories if getattr(c, "status", "") == "done"),
            "categories_skipped": sum(
                1 for c in categories
                if getattr(c, "status", "") in ("skipped_recent", "not_run")
            ),
            "finished_at": _iso(now),
        }
        self._set(KEY_LAST_FINISHED, _iso(now))
        self._set(KEY_LAST_RESULT, json.dumps(summary, ensure_ascii=False))
        self._postponed = False

        if state == "blocked":
            self._set(KEY_ENABLED, "0")
            self._set(KEY_PAUSE_REASON, "blocked")
            self._set(KEY_NEXT_RUN, "")
        elif state == "failed":
            failures = self.consecutive_failures() + 1
            self._set(KEY_FAILURES, str(failures))
            if failures >= MAX_CONSECUTIVE_FAILURES:
                self._set(KEY_ENABLED, "0")
                self._set(KEY_PAUSE_REASON, "failures")
                self._set(KEY_NEXT_RUN, "")
            else:
                self._set(
                    KEY_NEXT_RUN,
                    _iso(compute_next_run(now, self.interval(), rng=self._rng)),
                )
        else:  # finished or cancelled
            self._set(KEY_FAILURES, "0")
            self._set(KEY_NEXT_RUN, _iso(compute_next_run(now, self.interval(), rng=self._rng)))

    # -- thread ------------------------------------------------------------

    def startup(self) -> None:
        """Apply the startup catch-up rule (no thread started)."""
        if not self.enabled():
            return
        now = self._clock()
        next_run = self.next_run()
        if next_run is None or next_run <= now:
            # Exactly one catch-up after a 60 s startup delay; missed runs
            # are never replayed individually.
            self._catch_up = True
            self._set(KEY_NEXT_RUN, _iso(now + timedelta(seconds=STARTUP_DELAY_SECONDS)))

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.startup()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _default_wait(self, seconds: float) -> bool:
        return not self._stop.wait(timeout=seconds)

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self._wait(WAKE_INTERVAL_SECONDS):
                break
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - never kill the scheduler thread
                logger.exception("scheduler tick failed")

    def stop(self) -> None:
        self._stop.set()
