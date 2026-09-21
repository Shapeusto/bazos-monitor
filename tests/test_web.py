"""Flask test-client tests (no network; scrape_category is mocked)."""

from __future__ import annotations

import threading
import time
from datetime import date
from types import SimpleNamespace

from db import Database
from parser import Listing
from web.app import create_app

CATEGORY = "pc/notebook"


def make_config(tmp_path) -> dict:
    return {
        "db_path": str(tmp_path / "web.db"),
        "user_agent": "test",
        "request_delay_seconds": 0,
        "web_port": 5000,
        # Disabled by default in tests so old fixtures stay visible; the age
        # window itself is covered by test_default_age_filter.
        "max_listing_age_days": 0,
    }


def add_listing(config, ad_id: int, title: str = "Test", price=100, **over) -> None:
    db = Database(config["db_path"])
    values = dict(
        url=f"https://pc.bazos.sk/inzerat/{ad_id}/x.php", category_key=CATEGORY,
        title=title, short_description="popis", price_amount=price,
        price_type="fixed", price_text=f"{price} €" if price is not None else "",
        city="Košice", psc="04012", posted_date=date(2026, 1, 1), views=1, is_top=False,
    )
    values.update(over)
    db.upsert_listing(Listing(id=ad_id, **values), CATEGORY)
    db.close()


class BlockingScrape:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Event()

    def __call__(self, category_key, full=False, config=None, on_page=None):
        self.started.set()
        if on_page:
            on_page(1, 1, 1)
        self.release.wait(timeout=5)
        return SimpleNamespace(
            status="completed", pages_fetched=1, listings_seen=1,
            listings_new=1, stop_reason="caught_up",
        )


class BatchMock:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, category_key, full=False, config=None, db=None, on_page=None,
                 should_cancel=None):
        self.calls.append(category_key)
        if on_page:
            on_page(1, 1, 1)
        return SimpleNamespace(
            status="completed", pages_fetched=1, listings_seen=1,
            listings_new=1, stop_reason="caught_up",
        )


class BlockingBatchMock:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Event()

    def __call__(self, category_key, full=False, config=None, db=None, on_page=None,
                 should_cancel=None):
        self.started.set()
        if on_page:
            on_page(1, 1, 1)
        self.release.wait(timeout=5)
        return SimpleNamespace(
            status="completed", pages_fetched=1, listings_seen=1,
            listings_new=1, stop_reason="caught_up",
        )


def wait_for_state(client, url, timeout=5.0):
    state = {}
    for _ in range(int(timeout / 0.05)):
        state = client.get(url).get_json()
        if state.get("state") != "running":
            return state
        time.sleep(0.05)
    return state



def test_index_renders(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1, "Acer Nitro 5")
    client = create_app(config).test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert "Acer Nitro 5" in response.get_data(as_text=True)


def test_favicon(tmp_path):
    client = create_app(make_config(tmp_path)).test_client()
    assert "static/icon.png" in client.get("/").get_data(as_text=True)
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/png"


def test_scrape_lifecycle_and_409(tmp_path):
    config = make_config(tmp_path)
    mock = BlockingScrape()
    client = create_app(config, scrape_func=mock).test_client()

    first = client.post("/api/scrape", json={"category_key": CATEGORY})
    assert first.status_code == 202
    assert mock.started.wait(2)

    assert client.get("/api/scrape/status").get_json()["state"] == "running"

    second = client.post("/api/scrape", json={"category_key": CATEGORY})
    assert second.status_code == 409

    mock.release.set()
    state = {}
    for _ in range(100):
        state = client.get("/api/scrape/status").get_json()
        if state["state"] != "running":
            break
        time.sleep(0.05)
    assert state["state"] == "finished"
    assert state["listings_new"] == 1


def test_scrape_invalid_category_and_non_json(tmp_path):
    client = create_app(make_config(tmp_path)).test_client()
    assert client.post("/api/scrape", json={"category_key": "nope"}).status_code == 400
    assert client.post("/api/scrape", data="x").status_code == 415


def test_listing_actions(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 42)
    client = create_app(config).test_client()

    assert client.post("/api/listings/42/seen", json={}).get_json()["seen"] == 1
    assert client.post("/api/listings/42/favorite", json={}).get_json()["is_favorite"] == 1
    assert client.post("/api/listings/42/hide", json={}).get_json()["is_hidden"] == 1
    assert client.post("/api/listings/999/seen", json={}).status_code == 404


def test_mark_seen_bulk(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1)
    add_listing(config, 2)
    client = create_app(config).test_client()

    assert client.post("/api/mark-seen", json={"ids": [1, 2]}).get_json()["updated"] == 2
    assert client.post("/api/mark-seen", json={"category_key": CATEGORY}).get_json()["updated"] == 2
    assert client.post("/api/mark-seen", json={}).status_code == 400


def test_escaping_of_listing_title(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 7, "<script>alert(1)</script>")
    client = create_app(config).test_client()
    body = client.get("/").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_unknown_location_flag_and_badges(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1, "Znamy", city="Košice", psc="04012")
    add_listing(config, 2, "Neznamy", city="Zahraničie", psc="12345")
    add_listing(config, 3, "PodlaMesta", city="Košice", psc="99998")
    client = create_app(config).test_client()

    # Default (flag on): all three shown, invalid PSČ badged, city estimate marked.
    body = client.get("/", query_string={"category": CATEGORY}).get_data(as_text=True)
    assert "Znamy" in body and "Neznamy" in body and "PodlaMesta" in body
    assert "PSČ neplatné" in body
    assert "s neznámym miestom" in body
    assert "kraj odhadnutý podľa mesta" in body

    # With a PSČ filter, flag on keeps the invalid-PSČ rows; flag off drops them.
    on = client.get(
        "/", query_string={"category": CATEGORY, "psc": "04"}
    ).get_data(as_text=True)
    assert "Znamy" in on and "Neznamy" in on and "PodlaMesta" in on

    off = client.get(
        "/",
        query_string={"category": CATEGORY, "psc": "04", "include_unknown_location": "0"},
    ).get_data(as_text=True)
    assert "Znamy" in off
    assert "Neznamy" not in off and "PodlaMesta" not in off

    # "Kraj neznámy" returns only unknown-location rows.
    unknown = client.get(
        "/", query_string={"category": CATEGORY, "kraj": "unknown"}
    ).get_data(as_text=True)
    assert "Neznamy" in unknown
    assert "Znamy" not in unknown and "PodlaMesta" not in unknown


def test_unknown_flag_serialized_only_when_off(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1, "Znamy", city="Košice", psc="04012")
    client = create_app(config).test_client()

    on = client.get("/", query_string={"category": CATEGORY}).get_data(as_text=True)
    # Checkbox is checked by default; pagination links omit the flag.
    assert 'id="inc-unknown" checked' in on
    off = client.get(
        "/", query_string={"category": CATEGORY, "include_unknown_location": "0"}
    ).get_data(as_text=True)
    assert 'id="inc-unknown" checked' not in off


def test_default_age_filter(tmp_path):
    config = make_config(tmp_path)
    config["max_listing_age_days"] = 10
    add_listing(config, 1, "Novy", posted_date=date.today())
    add_listing(config, 2, "Stary", posted_date=date(2020, 1, 1))
    client = create_app(config).test_client()

    body = client.get("/", query_string={"category": CATEGORY}).get_data(as_text=True)
    assert "Novy" in body and "Stary" not in body
    assert 'id="age-limit"' in body

    everything = client.get(
        "/", query_string={"category": CATEGORY, "days": "0"}
    ).get_data(as_text=True)
    assert "Novy" in everything and "Stary" in everything


def test_city_filter(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1, "Kosicka vec", city="Košice")
    add_listing(config, 2, "Nitra vec", city="Nitra")
    client = create_app(config).test_client()

    body = client.get(
        "/", query_string={"category": CATEGORY, "city": "kosice"}
    ).get_data(as_text=True)
    assert "Kosicka vec" in body and "Nitra vec" not in body
    assert 'id="city"' in body


def test_city_field_is_escaped(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 8, "X", city="<script>alert(2)</script>", psc="04012")
    client = create_app(config).test_client()
    body = client.get("/").get_data(as_text=True)
    assert "<script>alert(2)</script>" not in body
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in body


# ---------------------------------------------------------------------------
# Saved searches, watched categories, batch
# ---------------------------------------------------------------------------

def test_saved_searches_api(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1, price=100, short_description="i5 ssd")
    add_listing(config, 2, price=200, short_description="i5 ssd")
    client = create_app(config).test_client()

    assert client.get("/api/saved-searches").get_json()["saved_searches"] == []

    created = client.post("/api/saved-searches", json={
        "name": "i5 do 250", "category_key": CATEGORY,
        "filters": {"kw_all": "i5", "price_max": "250"}, "sort": "price",
    })
    assert created.status_code == 201
    item = created.get_json()["saved_search"]
    assert item["name"] == "i5 do 250"
    sid = item["id"]

    listed = client.get("/api/saved-searches").get_json()["saved_searches"]
    assert listed[0]["total"] == 2 and listed[0]["new_count"] == 2

    assert client.post("/api/saved-searches", json={
        "name": "i5 do 250", "category_key": CATEGORY, "filters": {}}).status_code == 400
    assert client.post("/api/saved-searches", json={
        "name": "x", "category_key": "nope/nope", "filters": {}}).status_code == 400
    assert client.post("/api/saved-searches", data="x").status_code == 415

    renamed = client.put(f"/api/saved-searches/{sid}", json={"name": "Novy"})
    assert renamed.status_code == 200
    assert renamed.get_json()["saved_search"]["name"] == "Novy"

    updated = client.put(f"/api/saved-searches/{sid}", json={
        "filters": {"price_min": "150"}, "sort": "price", "category_key": CATEGORY})
    assert updated.status_code == 200
    assert updated.get_json()["saved_search"]["filters"] == {"price_min": "150"}

    assert client.put("/api/saved-searches/9999", json={"name": "x"}).status_code == 404
    assert client.delete(f"/api/saved-searches/{sid}").status_code == 200
    assert client.delete(f"/api/saved-searches/{sid}").status_code == 404


def test_watched_category_is_a_link(tmp_path):
    config = make_config(tmp_path)
    client = create_app(config).test_client()
    client.post("/api/watched", json={"category_key": CATEGORY})
    body = client.get("/").get_data(as_text=True)
    assert 'href="/?category=pc/notebook"' in body


def test_watched_api(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1)
    client = create_app(config).test_client()

    assert client.get("/api/watched").get_json()["watched"] == []
    assert client.post("/api/watched", json={"category_key": "nope"}).status_code == 400
    assert client.post("/api/watched", data="x").status_code == 415

    assert client.post("/api/watched", json={"category_key": CATEGORY}).status_code == 201
    watched = client.get("/api/watched").get_json()["watched"]
    assert watched[0]["category_key"] == CATEGORY and watched[0]["total"] == 1

    assert client.delete("/api/watched", json={"category_key": CATEGORY}).status_code == 200
    assert client.delete("/api/watched", json={"category_key": CATEGORY}).status_code == 404
    assert client.delete("/api/watched").status_code == 400


def test_batch_endpoints(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1)
    mock = BatchMock()
    client = create_app(config, scrape_func=mock).test_client()

    assert client.get("/api/batch/status").get_json()["state"] == "idle"
    assert client.post("/api/batch/start", json={}).status_code == 400  # nothing watched
    assert client.post("/api/batch/start", data="x").status_code == 415
    assert client.post("/api/batch/cancel", data="x").status_code == 415
    assert client.post("/api/batch/cancel", json={}).get_json()["cancelled"] is False

    client.post("/api/watched", json={"category_key": CATEGORY})
    assert client.post("/api/batch/start", json={}).status_code == 202
    state = wait_for_state(client, "/api/batch/status")
    assert state["state"] == "finished"
    assert state["listings_new"] == 1
    assert mock.calls == [CATEGORY]


def test_shared_lock_single_blocks_batch(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1)
    mock = BlockingScrape()
    client = create_app(config, scrape_func=mock).test_client()
    client.post("/api/watched", json={"category_key": CATEGORY})

    assert client.post("/api/scrape", json={"category_key": CATEGORY}).status_code == 202
    assert mock.started.wait(2)
    response = client.post("/api/batch/start", json={})
    assert response.status_code == 409
    assert "beží" in response.get_json()["error"]
    mock.release.set()
    wait_for_state(client, "/api/scrape/status")


def test_shared_lock_batch_blocks_single(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1)
    mock = BlockingBatchMock()
    client = create_app(config, scrape_func=mock).test_client()
    client.post("/api/watched", json={"category_key": CATEGORY})

    assert client.post("/api/batch/start", json={}).status_code == 202
    assert mock.started.wait(2)
    response = client.post("/api/scrape", json={"category_key": CATEGORY})
    assert response.status_code == 409
    assert "beží" in response.get_json()["error"]
    mock.release.set()
    wait_for_state(client, "/api/batch/status")


def test_saved_search_name_is_escaped(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1)
    client = create_app(config).test_client()
    client.post("/api/saved-searches", json={
        "name": "<script>alert(9)</script>", "category_key": CATEGORY, "filters": {}})
    body = client.get("/").get_data(as_text=True)
    assert "<script>alert(9)</script>" not in body
    assert "&lt;script&gt;alert(9)&lt;/script&gt;" in body


# ---------------------------------------------------------------------------
# Scheduler + new-count
# ---------------------------------------------------------------------------

def test_foreign_badge(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1, "Zahranicny", city="Zahraničie", psc="12345")
    add_listing(config, 2, "Neznamy", city="Košice", psc="99998")
    client = create_app(config).test_client()
    body = client.get("/").get_data(as_text=True)
    assert 'title="inzerát mimo Slovenska">Zahraničie</span>' in body
    assert "PSČ neplatné" in body  # the unmatched row


def test_scheduler_api(tmp_path):
    config = make_config(tmp_path)
    client = create_app(config).test_client()

    state = client.get("/api/scheduler").get_json()
    assert state["state"] == "off"
    assert state["enabled"] is False
    assert state["min_interval_minutes"] == 30
    assert state["max_interval_minutes"] == 1440
    assert state["interval_minutes"] == 60

    assert client.post("/api/scheduler/enable", data="x").status_code == 415
    assert client.post("/api/scheduler/disable", data="x").status_code == 415
    assert client.post("/api/scheduler/interval", data="x").status_code == 415

    enabled = client.post("/api/scheduler/enable", json={})
    assert enabled.status_code == 200 and enabled.get_json()["enabled"] is True

    ok = client.post("/api/scheduler/interval", json={"minutes": 45})
    assert ok.status_code == 200 and ok.get_json()["interval_minutes"] == 45

    low = client.post("/api/scheduler/interval", json={"minutes": 29})
    assert low.status_code == 400
    assert "30" in low.get_json()["error"]

    assert client.post("/api/scheduler/interval", json={"minutes": 1441}).status_code == 400
    assert client.post("/api/scheduler/interval", json={"minutes": "abc"}).status_code == 400

    disabled = client.post("/api/scheduler/disable", json={})
    assert disabled.status_code == 200 and disabled.get_json()["enabled"] is False


def test_scheduler_run_now_writes_batch_run(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1)
    mock = BatchMock()
    client = create_app(config, scrape_func=mock).test_client()
    client.post("/api/watched", json={"category_key": CATEGORY})

    assert client.post("/api/scheduler/run-now", data="x").status_code == 415
    response = client.post("/api/scheduler/run-now", json={})
    assert response.status_code == 202
    state = wait_for_state(client, "/api/batch/status")
    assert state["state"] == "finished"

    db = Database(config["db_path"])
    row = db.last_batch_run()
    db.close()
    assert row is not None
    assert row["trigger"] == "manual"
    assert row["status"] == "finished"
    assert row["categories_total"] == 1


def test_scheduler_run_now_409_when_busy(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1)
    mock = BlockingScrape()
    client = create_app(config, scrape_func=mock).test_client()
    client.post("/api/watched", json={"category_key": CATEGORY})

    assert client.post("/api/scrape", json={"category_key": CATEGORY}).status_code == 202
    assert mock.started.wait(2)
    response = client.post("/api/scheduler/run-now", json={})
    assert response.status_code == 409
    assert "beží" in response.get_json()["error"]
    mock.release.set()
    wait_for_state(client, "/api/scrape/status")


def test_new_count(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1)
    add_listing(config, 2)
    client = create_app(config).test_client()

    assert client.get("/api/new-count").get_json()["total_new"] == 0  # nothing watched
    client.post("/api/watched", json={"category_key": CATEGORY})
    assert client.get("/api/new-count").get_json()["total_new"] == 2

    db = Database(config["db_path"])
    db.toggle_field(2, "is_hidden")
    db.close()
    assert client.get("/api/new-count").get_json()["total_new"] == 1


# ---------------------------------------------------------------------------
# Backfill (resumable full-category download)
# ---------------------------------------------------------------------------

def backfill_result(**over):
    values = dict(
        status="complete", stop_reason="last_page", pages_done=3,
        pages_estimated=3, listings_seen=3, listings_new=3,
        total_estimate=60, next_page=1, eta_seconds=0.0, refresh=None,
    )
    values.update(over)
    return SimpleNamespace(**values)


class BackfillMock:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, category_key, restart=False, config=None, on_page=None,
                 should_cancel=None):
        self.calls.append(category_key)
        if on_page:
            on_page({"pages_done": 1, "pages_estimated": 3, "listings_seen": 1,
                     "listings_new": 1, "eta_seconds": 0.0, "next_page": 2,
                     "total_estimate": 60})
        return backfill_result()


class BlockingBackfill:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Event()

    def __call__(self, category_key, restart=False, config=None, on_page=None,
                 should_cancel=None):
        self.started.set()
        if on_page:
            on_page({"pages_done": 1, "pages_estimated": 3, "listings_seen": 1,
                     "listings_new": 1, "eta_seconds": 0.0, "next_page": 2,
                     "total_estimate": 60})
        self.release.wait(timeout=5)
        return backfill_result()


def test_backfill_endpoints(tmp_path):
    config = make_config(tmp_path)
    mock = BackfillMock()
    client = create_app(config, backfill_func=mock).test_client()

    assert client.get("/api/backfill/status").get_json()["state"] == "idle"
    assert client.post("/api/backfill/start", data="x").status_code == 415
    assert client.post("/api/backfill/start", json={"category_key": "nope"}).status_code == 400
    assert client.post("/api/backfill/cancel", data="x").status_code == 415
    assert client.post("/api/backfill/cancel", json={}).get_json()["cancelled"] is False

    started = client.post("/api/backfill/start", json={"category_key": CATEGORY})
    assert started.status_code == 202
    state = wait_for_state(client, "/api/backfill/status")
    assert state["state"] == "complete"
    assert state["listings_new"] == 3
    assert mock.calls == [CATEGORY]


def test_backfill_invalid_category_and_lock(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1)
    blocking = BlockingBackfill()
    client = create_app(config, backfill_func=blocking).test_client()
    client.post("/api/watched", json={"category_key": CATEGORY})

    assert client.post("/api/backfill/start", json={"category_key": CATEGORY}).status_code == 202
    assert blocking.started.wait(2)

    # Any other scrape is refused while the backfill holds the lock.
    assert client.post("/api/scrape", json={"category_key": CATEGORY}).status_code == 409
    assert client.post("/api/batch/start", json={}).status_code == 409
    assert client.post("/api/backfill/start", json={"category_key": CATEGORY}).status_code == 409

    cancelled = client.post("/api/backfill/cancel", json={})
    assert cancelled.status_code == 200
    blocking.release.set()
    wait_for_state(client, "/api/backfill/status")


def test_backfill_blocked_by_single_scrape(tmp_path):
    config = make_config(tmp_path)
    mock = BlockingScrape()
    client = create_app(config, scrape_func=mock).test_client()

    assert client.post("/api/scrape", json={"category_key": CATEGORY}).status_code == 202
    assert mock.started.wait(2)
    response = client.post("/api/backfill/start", json={"category_key": CATEGORY})
    assert response.status_code == 409
    assert "beží" in response.get_json()["error"]
    mock.release.set()
    wait_for_state(client, "/api/scrape/status")


def test_coverage_endpoint(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1)
    client = create_app(config).test_client()

    assert client.get("/api/coverage").status_code == 400
    assert client.get("/api/coverage?category_key=nope").status_code == 400
    data = client.get("/api/coverage", query_string={"category_key": CATEGORY}).get_json()
    assert data["coverage"]["stored"] == 1
    assert data["coverage"]["complete"] is False


def test_scheduler_run_now_never_calls_backfill(tmp_path):
    config = make_config(tmp_path)
    add_listing(config, 1)
    batch = BatchMock()
    backfill = BackfillMock()
    client = create_app(config, scrape_func=batch, backfill_func=backfill).test_client()
    client.post("/api/watched", json={"category_key": CATEGORY})

    client.post("/api/scheduler/enable", json={})
    response = client.post("/api/scheduler/run-now", json={})
    assert response.status_code == 202
    wait_for_state(client, "/api/batch/status")
    assert batch.calls == [CATEGORY]
    assert backfill.calls == []  # automatic paths never start a backfill



