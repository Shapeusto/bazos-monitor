"""Tests for the batch update of watched categories (no network)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import batch
import saved
from db import Database

CAT = "pc/notebook"
CAT2 = "auto"


def _watch(db: Database, key: str, added_at: str) -> None:
    db.conn.execute(
        "INSERT INTO watched_categories (category_key, added_at) VALUES (?, ?)",
        (key, added_at),
    )
    db.conn.commit()


def make_db(tmp_path) -> Database:
    db = Database(tmp_path / "batch.db")
    _watch(db, CAT, "2026-01-01T00:00:01+00:00")
    _watch(db, CAT2, "2026-01-01T00:00:02+00:00")
    return db


def fake_scrape(outcomes=None, calls=None):
    outcomes = outcomes or {}
    calls = calls if calls is not None else []

    def func(category_key, full=False, config=None, db=None, on_page=None, should_cancel=None):
        calls.append(category_key)
        if on_page:
            on_page(1, 1, 1)
        outcome = outcomes.get(category_key, "completed")
        if outcome == "raise":
            raise RuntimeError("boom")
        if outcome == "blocked":
            return SimpleNamespace(status="blocked", pages_fetched=1, listings_seen=1,
                                   listings_new=0, stop_reason="HTTP 429")
        return SimpleNamespace(status="completed", pages_fetched=1, listings_seen=1,
                               listings_new=1, stop_reason="caught_up")

    return func, calls


CONFIG = {"min_batch_interval_minutes": 15}


def statuses(result) -> dict[str, str]:
    return {category.category_key: category.status for category in result.categories}


def test_sequential_order_and_done(tmp_path):
    db = make_db(tmp_path)
    func, calls = fake_scrape()
    result = batch.run_watched_update(db=db, scrape_func=func, config=CONFIG)

    assert calls == [CAT, CAT2]  # added_at order
    assert statuses(result) == {CAT: "done", CAT2: "done"}
    assert result.state == "finished"
    assert result.pages_fetched == 2
    assert result.listings_new == 2
    db.close()


def test_skip_recent_and_force(tmp_path):
    db = make_db(tmp_path)
    run_id = db.start_run(CAT)
    db.finish_run(run_id, status="completed", stop_reason="caught_up", pages_fetched=1,
                  listings_seen=1, listings_new=0, listings_price_changed=0)

    func, calls = fake_scrape()
    result = batch.run_watched_update(db=db, scrape_func=func, config=CONFIG)
    assert statuses(result) == {CAT: "skipped_recent", CAT2: "done"}
    assert calls == [CAT2]

    func2, calls2 = fake_scrape()
    forced = batch.run_watched_update(force=True, db=db, scrape_func=func2, config=CONFIG)
    assert statuses(forced) == {CAT: "done", CAT2: "done"}
    assert calls2 == [CAT, CAT2]
    db.close()


def test_blocked_aborts_and_marks_not_run(tmp_path):
    db = make_db(tmp_path)
    func, calls = fake_scrape({CAT: "blocked"})
    result = batch.run_watched_update(db=db, scrape_func=func, config=CONFIG)

    assert result.state == "blocked"
    assert statuses(result) == {CAT: "blocked", CAT2: "not_run"}
    assert calls == [CAT]
    assert "zablokovaná" in (result.message or "")
    db.close()


def test_failed_continues_to_next(tmp_path):
    db = make_db(tmp_path)
    func, calls = fake_scrape({CAT: "raise"})
    result = batch.run_watched_update(db=db, scrape_func=func, config=CONFIG)

    assert result.state == "failed"
    assert statuses(result) == {CAT: "failed", CAT2: "done"}
    assert calls == [CAT, CAT2]
    db.close()


def test_cancel_before_start(tmp_path):
    db = make_db(tmp_path)
    func, calls = fake_scrape()
    result = batch.run_watched_update(
        db=db, scrape_func=func, config=CONFIG, should_cancel=lambda: True
    )
    assert result.state == "cancelled"
    assert statuses(result) == {CAT: "not_run", CAT2: "not_run"}
    assert calls == []
    db.close()


def test_cancel_between_categories(tmp_path):
    db = make_db(tmp_path)
    cancel = {"value": False}
    calls: list[str] = []

    def func(category_key, full=False, config=None, db=None, on_page=None, should_cancel=None):
        calls.append(category_key)
        if on_page:
            on_page(1, 1, 1)
        cancel["value"] = True  # request cancellation after the first category
        return SimpleNamespace(status="completed", pages_fetched=1, listings_seen=1,
                               listings_new=1, stop_reason="caught_up")

    result = batch.run_watched_update(
        db=db, scrape_func=func, config=CONFIG, should_cancel=lambda: cancel["value"]
    )
    assert calls == [CAT]
    assert statuses(result) == {CAT: "done", CAT2: "not_run"}
    assert result.state == "cancelled"
    db.close()


def test_progress_snapshots(tmp_path):
    db = make_db(tmp_path)
    func, _ = fake_scrape()
    snapshots: list[dict] = []
    batch.run_watched_update(
        db=db, scrape_func=func, config=CONFIG, on_update=snapshots.append
    )
    assert snapshots
    assert snapshots[-1]["state"] == "finished"
    assert {c["category_key"] for c in snapshots[-1]["categories"]} == {CAT, CAT2}
    db.close()


def test_batch_run_recorded_with_trigger(tmp_path):
    db = make_db(tmp_path)
    func, _ = fake_scrape()
    batch.run_watched_update(db=db, scrape_func=func, config=CONFIG, trigger="scheduled")
    row = db.last_batch_run()
    assert row is not None
    assert row["trigger"] == "scheduled"
    assert row["status"] == "finished"
    assert row["categories_total"] == 2
    assert row["categories_done"] == 2
    assert row["categories_skipped"] == 0
    assert row["new_listings"] == 2
    assert row["finished_at"]
    db.close()

