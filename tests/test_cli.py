"""CLI tests (no network)."""

from __future__ import annotations

from datetime import date

import cli
from db import Database
from parser import Listing

CATEGORY = "pc/notebook"


def make_listing(ad_id: int, price, price_type="fixed", price_text=None) -> Listing:
    return Listing(
        id=ad_id,
        url=f"https://pc.bazos.sk/inzerat/{ad_id}/x.php",
        category_key=CATEGORY,
        title=f"Listing number {ad_id}",
        short_description="desc",
        price_amount=price,
        price_type=price_type,
        price_text=price_text if price_text is not None else (
            f"{price} €" if price is not None else ""
        ),
        city="Bratislava",
        psc="81101",
        posted_date=date(2026, 1, 1),
        views=1,
        is_top=False,
    )


def seed(tmp_path) -> dict:
    config = {"db_path": str(tmp_path / "cli.db"), "user_agent": "test",
              "request_delay_seconds": 0}
    db = Database(config["db_path"])
    db.upsert_listing(make_listing(1, 300), CATEGORY)
    db.upsert_listing(make_listing(2, None, "negotiable", "Dohodou"), CATEGORY)
    db.upsert_listing(make_listing(3, 100), CATEGORY)
    db.close()
    return config


def data_ids(output: str) -> list[str]:
    return [
        line[:10].strip()
        for line in output.splitlines()
        if line[:10].strip().isdigit()
    ]


def test_show_new_sort_price_puts_null_prices_last(tmp_path, capsys):
    config = seed(tmp_path)
    rc = cli.main(["show", "--category", CATEGORY, "--new", "--sort", "price",
                   "--limit", "10"], config=config)
    assert rc == 0
    assert data_ids(capsys.readouterr().out) == ["3", "1", "2"]  # 100, 300, Dohodou


def test_mark_seen_clears_new(tmp_path, capsys):
    config = seed(tmp_path)
    cli.main(["mark-seen", "--category", CATEGORY], config=config)
    capsys.readouterr()

    db = Database(config["db_path"])
    assert db.query_listings(CATEGORY, new_only=True, limit=10) == []
    assert len(db.query_listings(CATEGORY, new_only=False, limit=10)) == 3
    db.close()


def test_categories_search_is_diacritics_insensitive(capsys):
    cli.main(["categories", "--search", "zvierata"], config={})
    assert "zvierata/pes" in capsys.readouterr().out


def test_unknown_category_suggests_close_match(capsys):
    rc = cli.main(["show", "--category", "pc/notebok"], config={})
    assert rc == 2
    error = capsys.readouterr().err
    assert "unknown category" in error
    assert "pc/notebook" in error


def test_stats_lists_categories(tmp_path, capsys):
    config = seed(tmp_path)
    cli.main(["stats"], config=config)
    out = capsys.readouterr().out
    assert CATEGORY in out


def test_refresh_kraj_backfills_listings(tmp_path, capsys):
    config = seed(tmp_path)
    db = Database(config["db_path"])
    db.conn.execute("UPDATE listings SET kraj = NULL, psc_status = NULL, kraj_source = NULL")
    db.conn.commit()
    db.close()

    rc = cli.main(["refresh-kraj"], config=config)
    assert rc == 0
    assert "refreshed kraj for 3 listing(s)" in capsys.readouterr().out

    db = Database(config["db_path"])
    rows = list(db.conn.execute("SELECT kraj, psc_status, kraj_source FROM listings"))
    db.close()
    assert {row["kraj"] for row in rows} == {"Bratislavský kraj"}
    assert {row["psc_status"] for row in rows} == {"valid"}
    assert {row["kraj_source"] for row in rows} == {"psc"}

    # idempotent: nothing changes on a second run
    assert cli.main(["refresh-kraj"], config=config) == 0
    assert "refreshed kraj for 0 listing(s)" in capsys.readouterr().out


