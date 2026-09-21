"""Scheduler tests: fake clock, mocked batch, no network, no sleeping."""

from __future__ import annotations

import random
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import batch
import jobs
import saved
from db import Database
from scheduler import (
    MAX_INTERVAL_MINUTES,
    MIN_INTERVAL_MINUTES,
    Scheduler,
    SchedulerError,
    SingleInstanceLock,
    compute_next_run,
)

CAT = "pc/notebook"
CONFIG = {"user_agent": "t", "request_delay_seconds": 0}


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


class BlockingScrape:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, category_key, full=False, config=None, on_page=None):
        self.started.set()
        self.release.wait(timeout=5)
        return SimpleNamespace(status="completed", pages_fetched=1, listings_seen=1,
                               listings_new=1, stop_reason="caught_up")


def make_scheduler(tmp_path, *, watch=True, outcomes=None, scrape_func=None,
                   backfill_func=None):
    config = dict(CONFIG, db_path=str(tmp_path / "sched.db"))
    db = Database(config["db_path"])
    if watch:
        saved.watch_category(CAT, db=db)
    db.close()
    coordinator = jobs.ScrapeCoordinator(
        config, scrape_func or (lambda *a, **k: None), backfill_func
    )
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))
    scheduler = Scheduler(
        config, coordinator, clock=clock, rng=lambda a, b: 0.0,
        db_factory=lambda: Database(config["db_path"]),
    )
    return scheduler, coordinator, clock, config


def install_fake_batch(monkeypatch, outcomes, calls):
    def fake(force=False, deep=False, *, config=None, db=None, scrape_func=None,
             should_cancel=None, on_update=None, trigger="manual"):
        calls.append(trigger)
        state = outcomes.pop(0) if outcomes else "finished"
        category_status = {"finished": "done", "blocked": "blocked", "failed": "failed",
                           "cancelled": "cancelled"}.get(state, state)
        categories = [batch.BatchCategory(category_key=CAT, label="x", status=category_status,
                                          listings_new=1 if state == "finished" else 0)]
        result = batch.BatchResult(
            state=state, categories=categories, started_at="2026-01-01T12:00:00+00:00",
            finished_at="2026-01-01T12:00:00+00:00",
            message="blocked" if state == "blocked" else None,
        )
        if on_update:
            on_update(result.to_dict())
        return result

    monkeypatch.setattr(jobs, "run_watched_update", fake)


# --- interval validation ---------------------------------------------------

@pytest.mark.parametrize("bad", [29, 1441, 0, -5, 45.5, "60", None, True])
def test_set_interval_rejects_invalid(tmp_path, bad):
    scheduler, *_ = make_scheduler(tmp_path)
    with pytest.raises(SchedulerError):
        scheduler.set_interval(bad)


@pytest.mark.parametrize("ok", [30, 60, 1440])
def test_set_interval_accepts_bounds(tmp_path, ok):
    scheduler, *_ = make_scheduler(tmp_path)
    assert scheduler.set_interval(ok) == ok
    assert scheduler.interval() == ok


def test_min_interval_not_configurable(tmp_path):
    scheduler, *_ = make_scheduler(tmp_path)
    # A config value must not lower the hard-coded minimum.
    scheduler.config["scheduler_interval_minutes"] = 5
    assert MIN_INTERVAL_MINUTES == 30
    with pytest.raises(SchedulerError):
        scheduler.set_interval(5)
    assert scheduler.interval() == 60  # default


def test_next_run_jitter_bounds():
    last = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    for interval in (30, 60, 240, 1440):
        for _ in range(200):
            nxt = compute_next_run(last, interval, rng=random.uniform)
            minutes = (nxt - last).total_seconds() / 60
            assert minutes >= MIN_INTERVAL_MINUTES - 1e-6
            assert minutes <= interval * 1.10 + 1e-6


# --- scheduling behaviour --------------------------------------------------

def test_enable_does_not_run_immediately(tmp_path, monkeypatch):
    calls: list[str] = []
    install_fake_batch(monkeypatch, [], calls)
    scheduler, _, clock, _ = make_scheduler(tmp_path)

    scheduler.enable()
    assert scheduler.next_run() == clock.now + timedelta(minutes=60)
    scheduler.tick()
    assert calls == []


def test_runs_when_due(tmp_path, monkeypatch):
    calls: list[str] = []
    install_fake_batch(monkeypatch, [], calls)
    scheduler, _, clock, _ = make_scheduler(tmp_path)
    scheduler.enable()

    clock.advance(minutes=61)
    scheduler.tick()
    assert calls == ["scheduled"]
    assert scheduler.consecutive_failures() == 0


def test_one_catch_up_after_downtime(tmp_path, monkeypatch):
    calls: list[str] = []
    install_fake_batch(monkeypatch, [], calls)
    scheduler, _, clock, _ = make_scheduler(tmp_path)
    scheduler.enable()
    clock.advance(hours=5)  # overdue

    scheduler.startup()
    assert scheduler.next_run() == clock.now + timedelta(seconds=60)
    scheduler.tick()
    assert calls == []  # 60 s startup delay not elapsed

    clock.advance(seconds=60)
    scheduler.tick()
    assert calls == ["catch_up"]

    clock.advance(minutes=61)
    scheduler.tick()
    assert calls == ["catch_up", "scheduled"]  # only one catch-up, then normal


def test_postpone_while_lock_busy(tmp_path, monkeypatch):
    calls: list[str] = []
    install_fake_batch(monkeypatch, [], calls)
    blocking = BlockingScrape()
    scheduler, coordinator, clock, _ = make_scheduler(tmp_path, scrape_func=blocking)
    scheduler.enable()
    clock.advance(minutes=61)

    assert coordinator.start_single(CAT, full=False) is True
    assert blocking.started.wait(2)

    scheduler.tick()
    assert calls == []  # batch not started
    assert scheduler.next_run() == clock.now + timedelta(minutes=5)
    assert scheduler.state()["state"] == "postponed"

    blocking.release.set()


def test_blocked_disables_scheduler(tmp_path, monkeypatch):
    calls: list[str] = []
    install_fake_batch(monkeypatch, ["blocked", "finished"], calls)
    scheduler, _, clock, _ = make_scheduler(tmp_path)
    scheduler.enable()

    clock.advance(minutes=61)
    scheduler.tick()
    assert calls == ["scheduled"]
    assert scheduler.enabled() is False
    assert scheduler.pause_reason() == "blocked"
    assert scheduler.state()["state"] == "paused_blocked"

    clock.advance(hours=10)
    scheduler.tick()
    assert calls == ["scheduled"]  # no further runs


def test_three_failures_pause(tmp_path, monkeypatch):
    calls: list[str] = []
    install_fake_batch(monkeypatch, ["failed", "failed", "failed", "finished"], calls)
    scheduler, _, clock, _ = make_scheduler(tmp_path)
    scheduler.enable()

    for _ in range(3):
        clock.advance(minutes=61)
        scheduler.tick()
    assert scheduler.consecutive_failures() == 3
    assert scheduler.enabled() is False
    assert scheduler.pause_reason() == "failures"
    assert scheduler.state()["state"] == "paused_failures"


def test_success_resets_failures(tmp_path, monkeypatch):
    calls: list[str] = []
    install_fake_batch(monkeypatch, ["failed", "finished"], calls)
    scheduler, _, clock, _ = make_scheduler(tmp_path)
    scheduler.enable()

    clock.advance(minutes=61)
    scheduler.tick()
    assert scheduler.consecutive_failures() == 1
    assert scheduler.enabled() is True

    clock.advance(minutes=61)
    scheduler.tick()
    assert scheduler.consecutive_failures() == 0
    assert scheduler.enabled() is True


def test_nothing_to_watch(tmp_path, monkeypatch):
    calls: list[str] = []
    install_fake_batch(monkeypatch, [], calls)
    scheduler, _, clock, _ = make_scheduler(tmp_path, watch=False)
    scheduler.enable()

    clock.advance(minutes=61)
    scheduler.tick()
    assert calls == []
    assert scheduler.state()["state"] == "nothing_to_watch"
    assert scheduler.next_run() == clock.now + timedelta(minutes=60)


def test_scheduler_never_starts_a_backfill(tmp_path, monkeypatch):
    calls: list[str] = []
    install_fake_batch(monkeypatch, [], calls)
    backfill_calls: list[str] = []

    def recorder(*args, **kwargs):
        backfill_calls.append("called")

    scheduler, _, clock, _ = make_scheduler(tmp_path, backfill_func=recorder)
    scheduler.enable()
    clock.advance(minutes=61)
    scheduler.tick()

    assert calls == ["scheduled"]
    assert backfill_calls == []  # the scheduler must never start a backfill


def test_settings_persist_across_objects(tmp_path):
    scheduler, _, clock, config = make_scheduler(tmp_path)
    scheduler.enable()
    scheduler.set_interval(120)

    other = Scheduler(
        config, scheduler.coordinator, clock=clock,
        db_factory=lambda: Database(config["db_path"]),
    )
    assert other.enabled() is True
    assert other.interval() == 120


# --- single-instance lock --------------------------------------------------

def test_single_instance_lock(tmp_path):
    first = SingleInstanceLock(tmp_path / "app.lock")
    assert first.acquire() is True

    second = SingleInstanceLock(tmp_path / "app.lock")
    assert second.acquire() is False

    first.release()
    third = SingleInstanceLock(tmp_path / "app.lock")
    assert third.acquire() is True
    third.release()


def test_single_instance_lock_stale_file(tmp_path):
    # A leftover lock file from a dead process must not block a new instance.
    stale = tmp_path / "app.lock"
    stale.write_text("999999", encoding="utf-8")
    lock = SingleInstanceLock(stale)
    assert lock.acquire() is True
    lock.release()
