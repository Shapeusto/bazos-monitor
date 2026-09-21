"""Resumable full-category backfill tests (fake multi-page category, no network)."""

from __future__ import annotations

import pytest

from backfill import (
    BACKFILL_HARD_CEILING,
    STATUS_BLOCKED,
    STATUS_COMPLETE,
    STATUS_PAUSED,
    _page_cap,
    backfill_category,
    category_coverage,
)
from db import Database
from fetcher import BlockedError, NotFoundError
from scraper import scrape_category

CATEGORY = "pc/notebook"
BASE = "https://pc.bazos.sk/notebook/"


# --- fake pages ------------------------------------------------------------

def item_html(iid: int, top: bool = False, price: str = "100 €",
              date_str: str = "1.1. 2026") -> str:
    top_span = '<span title="TOP" class="ztop">TOP</span>' if top else ""
    return f'''<div class="inzeraty inzeratyflex">
<div class="inzeratynadpis"><a href="/inzerat/{iid}/x.php"><img class="obrazek" alt="x"></a>
<h2 class=nadpis><a href="/inzerat/{iid}/x.php">Title {iid}</a></h2><span class=velikost10> - {top_span} - [{date_str}]</span><br>
<div class=popis>desc {iid} ...</div><br><br></div>
<div class="inzeratycena"><b><span translate="no">{price}</span></b></div>
<div class="inzeratylok">Bratislava<br>811 01</div>
<div class="inzeratyview">5 x</div></div>'''


def page_html(items, total: int, range_start: int = 1, range_end: int | None = None,
              last: bool = False) -> str:
    if range_end is None:
        range_end = total if last else range_start + len(items) - 1
    header = (
        '<div class="listainzerat inzeratyflex"><div class="inzeratynadpis">'
        f'Zobrazených {range_start}-{range_end} inzerátov z {total}</div></div>'
    )
    body = "".join(item_html(**item) for item in items)
    return (
        '<html><head><link rel="canonical" href="https://pc.bazos.sk/notebook/"></head>'
        f"<body>{header}{body}</body></html>"
    )


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.status_code = 200
        self.content = text.encode("utf-8")


class FakeSession:
    """Serves pages by number; a missing page raises (like a 404)."""

    def __init__(self, pages: dict[int, str], not_found: bool = False) -> None:
        self.pages = pages
        self.not_found = not_found
        self.requested: list[str] = []

    def get(self, url: str) -> FakeResponse:
        self.requested.append(url)
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        page_number = int(tail) // 20 + 1 if tail.isdigit() else 1
        if page_number not in self.pages:
            if self.not_found:
                raise NotFoundError(f"HTTP 404 for {url}")
            raise AssertionError(f"unexpected page {page_number}: {url}")
        return FakeResponse(self.pages[page_number])

    def close(self) -> None:
        pass


class BlockedSession:
    def __init__(self, status: int = 429) -> None:
        self.status = status

    def get(self, url: str):
        raise BlockedError(f"HTTP {self.status} for {url}")

    def close(self) -> None:
        pass


def make_config(tmp_path, **over) -> dict:
    config = {
        "user_agent": "test-agent",
        "request_delay_seconds": 0,
        "timeout_connect": 1,
        "timeout_read": 1,
        "max_retries": 1,
        "backoff_factor": 1,
        "db_path": str(tmp_path / "t.db"),
        "first_run_max_pages": 10,
        "incremental_max_pages": 25,
        "hard_max_pages": 100,
        "backfill_max_pages_per_run": 1000,
    }
    config.update(over)
    return config


def five_pages() -> dict[int, str]:
    return {
        1: page_html([{"iid": 101}, {"iid": 102}], total=81, range_start=1, range_end=20),
        2: page_html([{"iid": 103}, {"iid": 104}], total=81, range_start=21, range_end=40),
        3: page_html([{"iid": 105}, {"iid": 106}], total=81, range_start=41, range_end=60),
        4: page_html([{"iid": 107}, {"iid": 108}], total=81, range_start=61, range_end=80),
        5: page_html([{"iid": 109}], total=81, range_start=81, range_end=81, last=True),
    }


def test_page_cap_ceiling_enforced():
    assert _page_cap({"backfill_max_pages_per_run": 5000}) == BACKFILL_HARD_CEILING
    assert _page_cap({"backfill_max_pages_per_run": 3}) == 3
    assert _page_cap({}) == 1000


def test_backfill_goes_beyond_pages_that_are_fully_known(tmp_path):
    # The exact stuck case: pages 1-2 are already fully known, so the normal
    # incremental scrape would stop with caught_up. Backfill must continue.
    config = make_config(tmp_path, first_run_max_pages=2)
    db = Database(config["db_path"])
    scrape_category(CATEGORY, db=db, session=FakeSession(five_pages()), config=config)
    assert db.conn.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"] == 4

    session = FakeSession(five_pages())
    result = backfill_category(CATEGORY, db=db, session=session, config=config)

    assert result.status == STATUS_COMPLETE
    assert result.stop_reason == "last_page"
    assert result.pages_done == 5
    assert result.listings_new == 5           # ids 105-109
    assert db.conn.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"] == 9
    # The backfill fetched each page exactly once (the trailing requests are
    # the automatic post-backfill incremental refresh of page 1-2).
    assert session.requested[:5] == [BASE] + [f"{BASE}{n}/" for n in (20, 40, 60, 80)]
    db.close()


def test_backfill_ends_on_zero_items(tmp_path):
    config = make_config(tmp_path)
    pages = {
        1: page_html([{"iid": 1}], total=21, range_start=1, range_end=1),
        2: page_html([], total=21, range_start=21, range_end=20),
    }
    db = Database(config["db_path"])
    result = backfill_category(CATEGORY, db=db, session=FakeSession(pages), config=config)
    assert result.status == STATUS_COMPLETE
    assert result.stop_reason == "last_page"
    assert result.pages_done == 2
    db.close()


def test_backfill_ends_on_404_beyond_the_end(tmp_path):
    config = make_config(tmp_path)
    pages = {
        1: page_html([{"iid": 1}], total=25, range_start=1, range_end=20),
        # page 2 missing -> the fetcher raises NotFoundError
    }
    db = Database(config["db_path"])
    session = FakeSession(pages, not_found=True)
    result = backfill_category(CATEGORY, db=db, session=session, config=config)
    assert result.status == STATUS_COMPLETE
    assert result.stop_reason == "last_page"
    assert session.requested[:2] == [BASE, f"{BASE}20/"]
    db.close()


def test_cancel_saves_next_page_and_resume_does_not_refetch(tmp_path):
    config = make_config(tmp_path)
    db = Database(config["db_path"])
    seen_pages: list[int] = []

    def on_page(progress):
        seen_pages.append(progress["pages_done"])

    def should_cancel():
        return len(seen_pages) >= 2

    first = backfill_category(
        CATEGORY, db=db, session=FakeSession(five_pages()), config=config,
        on_page=on_page, should_cancel=should_cancel,
    )
    assert first.status == STATUS_PAUSED
    assert first.stop_reason == "cancelled"
    assert first.next_page == 3
    state = db.get_backfill_state(CATEGORY)
    assert state["status"] == STATUS_PAUSED and state["next_page"] == 3

    session2 = FakeSession(five_pages())
    second = backfill_category(CATEGORY, db=db, session=session2, config=config)
    assert second.status == STATUS_COMPLETE
    assert session2.requested[:3] == [f"{BASE}40/", f"{BASE}60/", f"{BASE}80/"]
    assert BASE not in session2.requested[:3]     # pages 1-2 were not refetched
    db.close()


def test_stale_running_state_is_resumed(tmp_path):
    config = make_config(tmp_path)
    db = Database(config["db_path"])
    db.save_backfill_state(
        CATEGORY, status="running", next_page=4, pages_done=3,
        total_estimate=100,
    )
    session = FakeSession(five_pages())
    result = backfill_category(CATEGORY, db=db, session=session, config=config)
    assert result.status == STATUS_COMPLETE
    assert session.requested[0] == f"{BASE}60/"  # page 4
    db.close()


def test_cap_reached_pauses_and_continue_works(tmp_path):
    config = make_config(tmp_path, backfill_max_pages_per_run=3)
    db = Database(config["db_path"])

    first = backfill_category(CATEGORY, db=db, session=FakeSession(five_pages()), config=config)
    assert first.status == STATUS_PAUSED
    assert first.stop_reason == "cap_reached"
    assert first.next_page == 4
    assert first.pages_done == 3

    session2 = FakeSession(five_pages())
    second = backfill_category(CATEGORY, db=db, session=session2, config=config)
    assert second.status == STATUS_COMPLETE
    assert session2.requested[0] == f"{BASE}60/"  # resumed at page 4
    db.close()


def test_restart_ignores_saved_state(tmp_path):
    config = make_config(tmp_path)
    db = Database(config["db_path"])
    db.save_backfill_state(
        CATEGORY, status=STATUS_PAUSED, next_page=4, pages_done=3, total_estimate=100,
    )
    session = FakeSession(five_pages())
    result = backfill_category(CATEGORY, restart=True, db=db, session=session, config=config)
    assert result.status == STATUS_COMPLETE
    assert session.requested[0] == BASE  # started from page 1 again
    db.close()


def test_blocked_429_saves_next_page(tmp_path):
    config = make_config(tmp_path)
    db = Database(config["db_path"])
    result = backfill_category(
        CATEGORY, db=db, session=BlockedSession(429), config=config
    )
    assert result.status == STATUS_BLOCKED
    assert "429" in result.stop_reason
    state = db.get_backfill_state(CATEGORY)
    assert state["status"] == STATUS_BLOCKED
    assert state["next_page"] == 1
    run = db.get_run(result.run_id)
    assert run["status"] == "blocked" and run["mode"] == "backfill"
    db.close()


def test_duplicates_within_a_run_counted_once(tmp_path):
    config = make_config(tmp_path)
    db = Database(config["db_path"])
    pages = {
        1: page_html([{"iid": 1}, {"iid": 1}, {"iid": 2}], total=3,
                     range_start=1, range_end=3, last=True),
    }
    result = backfill_category(CATEGORY, db=db, session=FakeSession(pages), config=config)
    assert result.status == STATUS_COMPLETE
    assert result.listings_seen == 2
    assert result.listings_new == 2
    db.close()


def test_post_backfill_refresh_runs_once(tmp_path):
    config = make_config(tmp_path)
    db = Database(config["db_path"])
    result = backfill_category(CATEGORY, db=db, session=FakeSession(five_pages()), config=config)
    assert result.status == STATUS_COMPLETE
    assert result.refresh is not None
    modes = [
        row["mode"]
        for row in db.conn.execute(
            "SELECT mode FROM scrape_runs WHERE category_key = ? ORDER BY id", (CATEGORY,)
        )
    ]
    assert modes == ["backfill", "incremental"]
    db.close()


def test_backfill_respects_age_limit(tmp_path):
    from datetime import date, timedelta

    config = make_config(tmp_path, max_listing_age_days=10)
    db = Database(config["db_path"])
    today = date.today()
    old = today - timedelta(days=30)

    def ds(value):
        return f"{value.day}. {value.month}. {value.year}"

    pages = {
        1: page_html([{"iid": 1, "date_str": ds(today)}], total=100,
                     range_start=1, range_end=20),
        2: page_html([{"iid": 2, "date_str": ds(old)}], total=100,
                     range_start=21, range_end=40),
    }
    result = backfill_category(CATEGORY, db=db, session=FakeSession(pages), config=config)
    assert result.status == STATUS_COMPLETE
    assert result.stop_reason == "too_old"
    assert db.all_listing_ids() == {1}
    db.close()


def test_coverage_numbers(tmp_path):
    config = make_config(tmp_path)
    db = Database(config["db_path"])
    backfill_category(CATEGORY, db=db, session=FakeSession(five_pages()), config=config)

    coverage = category_coverage(CATEGORY, db=db)
    assert coverage["stored"] == 9
    assert coverage["estimate"] == 81
    assert coverage["backfill_status"] == STATUS_COMPLETE
    assert coverage["complete"] is True
    db.close()
