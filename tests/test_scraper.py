"""Incremental scraper tests using a fake multi-page category (no network)."""

from __future__ import annotations

from datetime import date

import pytest

from db import Database
from fetcher import BlockedError, QueryStringError, PoliteSession, ensure_no_query_string
from parser import Listing
from scraper import scrape_category

CATEGORY = "pc/notebook"
BASE = "https://pc.bazos.sk/notebook/"


# --- fixtures --------------------------------------------------------------

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
    def __init__(self, pages: dict[int, str]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url: str) -> FakeResponse:
        self.requested.append(url)
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        page_number = int(tail) // 20 + 1 if tail.isdigit() else 1
        if page_number not in self.pages:
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
        "first_run_max_pages": 2,
        "incremental_max_pages": 5,
        "hard_max_pages": 100,
    }
    config.update(over)
    return config


def make_listing(ad_id: int) -> Listing:
    return Listing(
        id=ad_id, url=f"{BASE}inzerat/{ad_id}/x.php", category_key=CATEGORY,
        title=f"Title {ad_id}", short_description="d", price_amount=100,
        price_type="fixed", price_text="100 €", city="Bratislava", psc="81101",
        posted_date=date(2026, 1, 1), views=1, is_top=False,
    )


def four_pages(new_page1=None):
    p1 = new_page1 or [{"iid": 101}, {"iid": 102}]
    return {
        1: page_html(p1, total=100, range_start=1, range_end=20),
        2: page_html([{"iid": 103}, {"iid": 104}], total=100, range_start=21, range_end=40),
        3: page_html([{"iid": 105}], total=100, range_start=41, range_end=41, last=True),
    }


# --- tests -----------------------------------------------------------------

def test_first_run_stops_at_first_run_max_pages(tmp_path):
    config = make_config(tmp_path, first_run_max_pages=2)
    db = Database(config["db_path"])
    result = scrape_category(CATEGORY, db=db, session=FakeSession(four_pages()), config=config)

    assert result.status == "completed"
    assert result.pages_fetched == 2
    assert result.stop_reason == "max_pages"
    assert result.listings_seen == 4
    assert result.listings_new == 4
    assert db.conn.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"] == 4
    db.close()


def test_second_run_catches_up_with_zero_new(tmp_path):
    config = make_config(tmp_path, first_run_max_pages=2)
    db = Database(config["db_path"])
    scrape_category(CATEGORY, db=db, session=FakeSession(four_pages()), config=config)

    result = scrape_category(CATEGORY, db=db, session=FakeSession(four_pages()), config=config)
    assert result.listings_new == 0
    assert result.listings_price_changed == 0
    assert result.stop_reason == "caught_up"
    assert result.pages_fetched == 1  # page 1 non-top items are all known
    db.close()


def test_top_only_pages_do_not_stop_the_run(tmp_path):
    config = make_config(tmp_path)
    db = Database(config["db_path"])
    for ad_id in (201, 202):  # page-2 items already known from a previous run
        db.upsert_listing(make_listing(ad_id), CATEGORY)

    pages = {
        1: page_html([{"iid": 1, "top": True}, {"iid": 2, "top": True}],
                     total=100, range_start=1, range_end=20),
        2: page_html([{"iid": 201}, {"iid": 202}], total=100, range_start=21, range_end=40),
    }
    result = scrape_category(CATEGORY, db=db, session=FakeSession(pages), config=config)

    assert result.pages_fetched == 2          # did NOT stop on the TOP-only page 1
    assert result.stop_reason == "caught_up"  # stopped on page 2
    assert result.listings_new == 2           # the two TOP items were new
    db.close()


def test_newly_injected_listing_on_page_one_is_detected(tmp_path):
    config = make_config(tmp_path, first_run_max_pages=2)
    db = Database(config["db_path"])
    scrape_category(CATEGORY, db=db, session=FakeSession(four_pages()), config=config)

    injected = four_pages(new_page1=[{"iid": 999}, {"iid": 101}, {"iid": 102}])
    result = scrape_category(CATEGORY, db=db, session=FakeSession(injected), config=config)

    assert result.listings_new == 1
    assert 999 in db.all_listing_ids()
    db.close()


def test_max_pages_argument_is_respected(tmp_path):
    config = make_config(tmp_path)
    db = Database(config["db_path"])
    result = scrape_category(CATEGORY, max_pages=1, db=db,
                             session=FakeSession(four_pages()), config=config)
    assert result.pages_fetched == 1
    db.close()


def test_http_429_aborts_and_records_blocked(tmp_path):
    config = make_config(tmp_path)
    db = Database(config["db_path"])
    result = scrape_category(CATEGORY, db=db, session=BlockedSession(429), config=config)

    assert result.status == "blocked"
    assert "429" in result.stop_reason
    run = db.get_run(result.run_id)
    assert run["status"] == "blocked"
    assert "429" in run["stop_reason"]
    db.close()


def _date_str(value) -> str:
    return f"{value.day}. {value.month}. {value.year}"


def test_age_limit_skips_old_listings_and_stops(tmp_path):
    from datetime import date, timedelta

    config = make_config(tmp_path, max_listing_age_days=10)
    db = Database(config["db_path"])
    today = date.today()
    old = today - timedelta(days=30)
    pages = {
        1: page_html(
            [{"iid": 1, "date_str": _date_str(today)},
             {"iid": 2, "date_str": _date_str(today)}],
            total=100, range_start=1, range_end=20,
        ),
        2: page_html(
            [{"iid": 3, "date_str": _date_str(old)},
             {"iid": 4, "date_str": _date_str(old)}],
            total=100, range_start=21, range_end=40,
        ),
    }
    result = scrape_category(CATEGORY, db=db, session=FakeSession(pages), config=config)

    assert result.status == "completed"
    assert result.stop_reason == "too_old"
    assert result.pages_fetched == 2
    assert result.listings_new == 2
    assert db.all_listing_ids() == {1, 2}  # the old page was never stored
    db.close()


def test_age_limit_disabled_stores_old_listings(tmp_path):
    from datetime import date, timedelta

    config = make_config(tmp_path)  # no max_listing_age_days -> disabled
    db = Database(config["db_path"])
    old = date.today() - timedelta(days=365)
    pages = {1: page_html([{"iid": 1, "date_str": _date_str(old)}], total=1,
                          range_start=1, range_end=1, last=True)}
    result = scrape_category(CATEGORY, db=db, session=FakeSession(pages), config=config)
    assert result.stop_reason == "last_page"
    assert db.all_listing_ids() == {1}
    db.close()


def test_url_guard_rejects_query_strings():
    with pytest.raises(QueryStringError):
        ensure_no_query_string("https://pc.bazos.sk/notebook/?order=1")

    session = PoliteSession({
        "user_agent": "t", "request_delay_seconds": 0,
        "timeout_connect": 1, "timeout_read": 1, "max_retries": 1, "backoff_factor": 1,
    })
    with pytest.raises(QueryStringError):
        session.get("https://pc.bazos.sk/notebook/?cenaod=10")
