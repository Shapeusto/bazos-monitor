"""Tests for the schema migrations (no network)."""

import sqlite3

from db import Database, _SCHEMA_V1


def _make_v1_db(path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA_V1)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.execute(
        """
        INSERT INTO listings (id, url, title, short_description, city, psc,
                              first_seen, last_seen)
        VALUES (1, 'https://pc.bazos.sk/inzerat/1/x.php', 'Kosačka Šípka',
                'dobrá kosačka', 'Košice', '040 12',
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
        """
    )
    conn.commit()
    conn.close()


def test_migration_v1_to_latest_backfills(tmp_path):
    path = tmp_path / "v1.db"
    _make_v1_db(path)

    db = Database(path)
    assert db.schema_version() == 9
    row = db.get_listing(1)
    assert row["search_text"] == "kosacka sipka dobra kosacka"
    assert row["search_desc"] == "dobra kosacka"
    assert row["city_norm"] == "kosice"
    assert row["kraj"] == "Košický kraj"
    assert row["psc_status"] == "valid"
    assert row["kraj_source"] == "psc"
    assert row["is_hidden"] == 0
    assert row["is_favorite"] == 0
    tables = {
        r["name"]
        for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"saved_searches", "watched_categories", "settings", "batch_runs",
            "backfill_state", "category_stats"} <= tables
    indexes = {
        r["name"]
        for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert {"idx_listings_kraj", "idx_listings_is_hidden", "idx_listings_is_favorite",
            "idx_listings_psc_status", "idx_runs_mode",
            "idx_price_history_listing"} <= indexes
    columns = {
        r["name"] for r in db.conn.execute("PRAGMA table_info(scrape_runs)")
    }
    assert "mode" in columns
    db.close()


def test_migration_is_safe_to_run_twice(tmp_path):
    path = tmp_path / "v1.db"
    _make_v1_db(path)

    Database(path).close()
    db = Database(path)  # opening again must not re-apply or fail
    assert db.schema_version() == 9
    assert db.get_listing(1)["search_text"] == "kosacka sipka dobra kosacka"
    assert db.get_listing(1)["search_desc"] == "dobra kosacka"
    db.close()


def test_migration_v3_to_latest_keeps_existing_data(tmp_path):
    path = tmp_path / "v3.db"
    db = Database(path)
    # Simulate a v3 database by removing the Phase 5/6 tables and rolling back.
    for table in ("saved_searches", "watched_categories", "settings", "batch_runs"):
        db.conn.execute(f"DROP TABLE {table}")
    db.conn.execute("UPDATE schema_version SET version = 3")
    db.conn.commit()
    db.close()

    db = Database(path)
    assert db.schema_version() == 9
    tables = {
        r["name"]
        for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"saved_searches", "watched_categories", "settings", "batch_runs",
            "backfill_state", "category_stats"} <= tables
    db.close()
