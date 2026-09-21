"""Tests for saved searches and watched categories (no network)."""

from __future__ import annotations

from datetime import date

import pytest

import saved
from db import Database
from parser import Listing
from queries import search_listings

CATEGORY = "pc/notebook"
OTHER = "auto"


def add(db: Database, ad_id: int, category: str = CATEGORY, price: int = 100,
        description: str = "i5 ssd") -> None:
    db.upsert_listing(
        Listing(
            id=ad_id, url=f"https://pc.bazos.sk/inzerat/{ad_id}/x.php",
            category_key=category, title=f"Title {ad_id}", short_description=description,
            price_amount=price, price_type="fixed", price_text=f"{price} €",
            city="Košice", psc="04012", posted_date=date(2026, 1, 1),
            views=1, is_top=False,
        ),
        category,
    )


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "saved.db")
    add(database, 1, price=100)
    add(database, 2, price=200)
    add(database, 3, price=300)
    add(database, 4, category=OTHER, price=150, description="auto diely")
    yield database
    database.close()


def test_create_list_rename_update_delete(db):
    item = saved.create_saved_search(
        "Test", CATEGORY, {"kw_all": "i5", "price_max": "250"}, "-price", db=db
    )
    assert item["name"] == "Test"
    assert item["category_key"] == CATEGORY
    assert item["filters"] == {"kw_all": "i5", "price_max": "250"}
    assert item["sort"] == "-price"
    assert [s["name"] for s in saved.list_saved_searches(db=db)] == ["Test"]

    renamed = saved.rename_saved_search(item["id"], "Novy", db=db)
    assert renamed["name"] == "Novy"

    updated = saved.update_saved_search(
        item["id"], category_key=OTHER, filters={"price_min": "100"}, sort="price", db=db
    )
    assert updated["category_key"] == OTHER
    assert updated["filters"] == {"price_min": "100"}
    assert updated["sort"] == "price"

    assert saved.delete_saved_search(item["id"], db=db) == 1
    assert saved.list_saved_searches(db=db) == []
    assert saved.delete_saved_search(item["id"], db=db) == 0


def test_transient_filters_not_stored(db):
    item = saved.create_saved_search(
        "Transient", CATEGORY,
        {"kw_all": "i5", "new": "1", "hidden": "1", "fav": "1",
         "include_unknown_location": "0"},
        db=db,
    )
    assert item["filters"] == {"kw_all": "i5"}


def test_unique_name_and_limits(db):
    saved.create_saved_search("A", CATEGORY, {}, db=db)
    with pytest.raises(saved.SavedSearchError):
        saved.create_saved_search("A", CATEGORY, {}, db=db)

    config = {"max_saved_searches": 2}
    saved.create_saved_search("B", CATEGORY, {}, db=db, config=config)
    with pytest.raises(saved.SavedSearchError):
        saved.create_saved_search("C", CATEGORY, {}, db=db, config=config)


def test_validation_errors(db):
    with pytest.raises(saved.SavedSearchError):
        saved.create_saved_search("", CATEGORY, {}, db=db)
    with pytest.raises(saved.SavedSearchError):
        saved.create_saved_search("x" * 61, CATEGORY, {}, db=db)
    with pytest.raises(saved.SavedSearchError):
        saved.create_saved_search("Ok", "nope/nope", {}, db=db)
    with pytest.raises(saved.SavedSearchError):
        saved.watch_category("nope/nope", db=db)


def test_counts_match_search_and_new_drops_after_mark_seen(db):
    saved.create_saved_search(
        "i5 do 250", CATEGORY, {"kw_all": "i5", "price_max": "250"}, db=db
    )
    listed = saved.list_saved_searches_with_counts(db=db)
    assert listed[0]["total"] == 2
    assert listed[0]["new_count"] == 2

    expected = search_listings(
        {"category_key": CATEGORY, "keywords_all": ["i5"], "price_max": 250}, db=db
    )
    assert listed[0]["total"] == expected.total

    db.mark_seen(CATEGORY)
    listed = saved.list_saved_searches_with_counts(db=db)
    assert listed[0]["new_count"] == 0
    assert listed[0]["total"] == 2


def test_watch_unwatch_and_counts(db):
    saved.watch_category(CATEGORY, db=db)
    saved.watch_category(CATEGORY, db=db)  # idempotent
    watched = saved.list_watched(db=db)
    assert len(watched) == 1
    assert watched[0]["category_key"] == CATEGORY
    assert watched[0]["total"] == 3
    assert watched[0]["new"] == 3

    assert saved.unwatch_category(CATEGORY, db=db) == 1
    assert saved.list_watched(db=db) == []


def test_watch_limit(db):
    config = {"max_watched_categories": 1}
    saved.watch_category(CATEGORY, db=db, config=config)
    with pytest.raises(saved.SavedSearchError):
        saved.watch_category(OTHER, db=db, config=config)


def test_overlap_warnings(db):
    saved.watch_category("pc", db=db)
    saved.watch_category(CATEGORY, db=db)  # pc/notebook is a subcategory of pc
    warnings = saved.overlap_warnings(db=db)
    assert len(warnings) == 1
    assert warnings[0]["section"] == "pc"
    assert warnings[0]["subcategory"] == CATEGORY
    assert "prekrývajú" in warnings[0]["message"]
