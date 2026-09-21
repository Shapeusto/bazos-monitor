"""Database tests (no network)."""

from __future__ import annotations

from datetime import date

from db import Database
from parser import Listing


def make_listing(ad_id: int = 1, price: int | None = 100,
                 price_type: str = "fixed", price_text: str | None = None, **over) -> Listing:
    values = dict(
        url=f"https://pc.bazos.sk/inzerat/{ad_id}/x.php",
        category_key="pc/notebook",
        title=f"Title {ad_id}",
        short_description="desc",
        price_amount=price,
        price_type=price_type,
        price_text=price_text if price_text is not None else (
            f"{price} €" if price is not None else ""
        ),
        city="Bratislava",
        psc="81101",
        posted_date=date(2026, 1, 1),
        views=10,
        is_top=False,
    )
    values.update(over)
    return Listing(id=ad_id, **values)


def test_schema_created(tmp_path):
    db = Database(tmp_path / "t.db")
    assert db.schema_version() == 9
    tables = {
        row["name"]
        for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"listings", "listing_categories", "price_history", "scrape_runs",
            "schema_version", "saved_searches", "watched_categories",
            "settings", "batch_runs", "backfill_state", "category_stats"} <= tables
    indexes = {
        row["name"]
        for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert {"idx_listings_posted_date", "idx_listings_price_amount",
            "idx_listings_psc", "idx_listings_first_seen", "idx_listings_seen",
            "idx_listings_psc_status", "idx_price_history_listing"} <= indexes
    db.close()


def test_upsert_idempotent_and_preserves_first_seen_and_seen(tmp_path):
    db = Database(tmp_path / "t.db")
    listing = make_listing(1)

    assert db.upsert_listing(listing, "pc/notebook", now="2026-01-01T00:00:00+00:00") == (True, False)
    db.mark_seen("pc/notebook")
    assert db.upsert_listing(listing, "pc/notebook", now="2026-02-02T00:00:00+00:00") == (False, False)

    rows = db.conn.execute("SELECT * FROM listings").fetchall()
    assert len(rows) == 1
    assert rows[0]["first_seen"] == "2026-01-01T00:00:00+00:00"
    assert rows[0]["last_seen"] == "2026-02-02T00:00:00+00:00"
    assert rows[0]["seen"] == 1  # never reset by a re-sight
    db.close()


def test_search_desc_holds_normalised_description_only(tmp_path):
    db = Database(tmp_path / "t.db")
    listing = make_listing(
        1, title="Kosačka Šípka", short_description="Dobrá kosačka", city="Košice"
    )
    db.upsert_listing(listing, "pc/notebook")
    row = db.get_listing(1)
    assert row["search_text"] == "kosacka sipka dobra kosacka"
    assert row["search_desc"] == "dobra kosacka"
    assert row["city_norm"] == "kosice"
    db.close()


def test_price_history_only_on_first_sight_and_change(tmp_path):
    db = Database(tmp_path / "t.db")
    db.upsert_listing(make_listing(1, price=100), "pc/notebook")
    assert db.conn.execute("SELECT COUNT(*) c FROM price_history").fetchone()["c"] == 1

    # same price -> no new history row
    assert db.upsert_listing(make_listing(1, price=100), "pc/notebook") == (False, False)
    assert db.conn.execute("SELECT COUNT(*) c FROM price_history").fetchone()["c"] == 1

    # price change -> one new row
    assert db.upsert_listing(make_listing(1, price=120), "pc/notebook") == (False, True)
    assert db.conn.execute("SELECT COUNT(*) c FROM price_history").fetchone()["c"] == 2
    amounts = [r["price_amount"] for r in db.conn.execute(
        "SELECT price_amount FROM price_history ORDER BY id")]
    assert amounts == [100, 120]
    db.close()


def test_listing_linked_to_multiple_categories(tmp_path):
    db = Database(tmp_path / "t.db")
    db.upsert_listing(make_listing(1), "pc")
    db.upsert_listing(make_listing(1), "pc/notebook")
    links = db.conn.execute("SELECT COUNT(*) c FROM listing_categories").fetchone()["c"]
    assert links == 2
    assert db.has_category_listings("pc")
    assert db.has_category_listings("pc/notebook")
    db.close()
