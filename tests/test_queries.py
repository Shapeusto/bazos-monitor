"""Tests for the SQL query layer (no network)."""

from datetime import date

import pytest

from db import Database
from parser import Listing
from queries import (
    Filters,
    count_listings,
    filters_from_dict,
    filters_to_dict,
    saved_search_filters,
    search_listings,
)

CATEGORY = "pc/notebook"

SEED = [
    # id, title, description, price, price_type, city, psc, posted, views
    (1, "Acer Nitro 5", "herny notebook i5", 500, "fixed", "Košice", "04012", date(2026, 1, 10), 100),
    (2, "Dell Latitude", "kancelarsky i5", 250, "fixed", "Nitra", "95188", date(2026, 1, 5), 50),
    (3, "HP ProBook", "lacny notebook", None, "negotiable", "Žilina", "01001", date(2026, 1, 8), 10),
    (4, "Acer Predator", "gaming i7", 800, "fixed", "Košice", "04012", date(2026, 1, 12), 200),
    (5, "Skrytý inzerát", "tajny", 100, "fixed", "Košice", "04012", date(2026, 1, 11), 5),
    (6, "Obľúbený inzerát", "oblubeny", 150, "fixed", "Košice", "04012", date(2026, 1, 9), 7),
    (7, "Košická kosačka", "zahradna", 50, "fixed", "Košice", "04012", date(2026, 1, 7), 3),
]


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "q.db")
    for ad_id, title, desc, price, ptype, city, psc, posted, views in SEED:
        database.upsert_listing(
            Listing(
                id=ad_id, url=f"https://pc.bazos.sk/inzerat/{ad_id}/x.php",
                category_key=CATEGORY, title=title, short_description=desc,
                price_amount=price, price_type=ptype,
                price_text=(f"{price} €" if price is not None else "Dohodou"),
                city=city, psc=psc, posted_date=posted, views=views, is_top=False,
            ),
            CATEGORY,
        )
    database.upsert_listing(
        Listing(
            id=1, url="https://pc.bazos.sk/inzerat/1/x.php", category_key="pc",
            title="Acer Nitro 5", short_description="herny notebook i5",
            price_amount=500, price_type="fixed", price_text="500 €",
            city="Košice", psc="04012", posted_date=date(2026, 1, 10),
            views=100, is_top=False,
        ),
        "pc",
    )
    database.toggle_field(5, "is_hidden")
    database.toggle_field(6, "is_favorite")
    yield database
    database.close()


def ids(rows):
    return [row["id"] for row in rows]


def test_category_filter_and_all(db):
    rows, total = search_listings({"category_key": CATEGORY}, db=db)
    assert total == 6 and len(rows) == 6  # id 5 is hidden by default
    _, total_pc = search_listings({"category_key": "pc"}, db=db)
    assert total_pc == 1
    _, total_all = search_listings({}, db=db)
    assert total_all == 6  # distinct listings across all scraped categories


def test_only_new_and_favorites(db):
    db.set_seen(1)
    _, total_new = search_listings({"category_key": CATEGORY, "only_new": True}, db=db)
    assert total_new == 5  # 7 rows - 1 hidden - 1 seen
    rows, _ = search_listings({"category_key": CATEGORY, "only_favorites": True}, db=db)
    assert ids(rows) == [6]


def test_hidden_default_excluded(db):
    _, total = search_listings({"category_key": CATEGORY}, db=db)
    assert total == 6
    _, total_hidden = search_listings({"category_key": CATEGORY, "include_hidden": True}, db=db)
    assert total_hidden == 7


def test_price_range_and_no_price_option(db):
    rows, total = search_listings(
        {"category_key": CATEGORY, "price_max": 300}, db=db
    )
    assert total == 3 and sorted(ids(rows)) == [2, 6, 7]
    rows2, total2 = search_listings(
        {"category_key": CATEGORY, "price_max": 300, "include_no_price": True}, db=db
    )
    assert total2 == 4 and 3 in ids(rows2)


def test_price_type_filter(db):
    rows, _ = search_listings({"category_key": CATEGORY, "price_types": ["negotiable"]}, db=db)
    assert ids(rows) == [3]


def test_keywords_all_any_exclude_phrase_diacritics(db):
    rows_all, _ = search_listings({"category_key": CATEGORY, "keywords_all": ["i5"]}, db=db)
    assert sorted(ids(rows_all)) == [1, 2]

    rows_any, _ = search_listings(
        {"category_key": CATEGORY, "keywords_any": ["gaming", "herny"]}, db=db
    )
    assert sorted(ids(rows_any)) == [1, 4]

    rows_excl, total_excl = search_listings(
        {"category_key": CATEGORY, "keywords_exclude": ["dell"]}, db=db
    )
    assert total_excl == 5 and 2 not in ids(rows_excl)

    rows_phrase, _ = search_listings(
        {"category_key": CATEGORY, "keywords_all": ["acer nitro"]}, db=db
    )
    assert ids(rows_phrase) == [1]

    rows_dia, _ = search_listings(
        {"category_key": CATEGORY, "keywords_all": ["kosicka"]}, db=db
    )
    assert ids(rows_dia) == [7]


def test_keywords_desc_matches_description_only(db):
    # "notebook" is only in the descriptions of ids 1 and 3, never in a title.
    rows, total = search_listings(
        {"category_key": CATEGORY, "keywords_desc": ["notebook"]}, db=db
    )
    assert total == 2 and sorted(ids(rows)) == [1, 3]

    # "predator" is only in the title of id 4 -> not found in the description.
    _, total_title_only = search_listings(
        {"category_key": CATEGORY, "keywords_desc": ["predator"]}, db=db
    )
    assert total_title_only == 0
    _, total_all = search_listings(
        {"category_key": CATEGORY, "keywords_all": ["predator"]}, db=db
    )
    assert total_all == 1


def test_max_age_days_filter(db):
    # All SEED listings are from 2026-01, far in the past, so a small window
    # hides them all; a huge window and days=0 (no limit) include them.
    _, total_recent = search_listings({"category_key": CATEGORY, "max_age_days": 10}, db=db)
    assert total_recent == 0
    _, total_wide = search_listings({"category_key": CATEGORY, "max_age_days": 100000}, db=db)
    assert total_wide == 6
    _, total_all = search_listings({"category_key": CATEGORY, "max_age_days": 0}, db=db)
    assert total_all == 6


def test_max_age_days_roundtrips_and_is_persistent():
    filters = filters_from_dict({"category": CATEGORY, "days": "10"})
    assert filters["max_age_days"] == 10
    assert filters_to_dict(filters)["days"] == "10"
    assert saved_search_filters(filters)["days"] == "10"

    off = filters_from_dict({"category": CATEGORY, "days": "0"})
    assert off["max_age_days"] == 0
    assert filters_to_dict(off)["days"] == "0"


def test_city_filter_is_diacritics_insensitive(db):
    # City is matched as a normalised substring ("kosice" finds "Košice").
    rows, total = search_listings({"category_key": CATEGORY, "city": "kosice"}, db=db)
    assert total == 4 and sorted(ids(rows)) == [1, 4, 6, 7]  # id 5 is hidden

    rows2, total2 = search_listings({"category_key": CATEGORY, "city": "NIT"}, db=db)
    assert total2 == 1 and ids(rows2) == [2]


def test_city_filter_roundtrips_and_is_persistent():
    filters = filters_from_dict({"category": CATEGORY, "city": "Bratislava"})
    assert filters["city"] == "Bratislava"
    assert filters_to_dict(filters)["city"] == "Bratislava"
    assert saved_search_filters(filters)["city"] == "Bratislava"


def test_keywords_desc_roundtrips_and_is_persistent():
    filters = filters_from_dict({"category": CATEGORY, "kw_desc": "darujem"})
    assert filters["keywords_desc"] == ["darujem"]
    assert filters_to_dict(filters)["kw_desc"] == "darujem"
    assert saved_search_filters(filters)["kw_desc"] == "darujem"


def test_price_sort_nulls_last(db):
    rows, _ = search_listings({"category_key": CATEGORY}, sort="price", db=db)
    assert ids(rows) == [7, 6, 2, 1, 4, 3]  # 50,150,250,500,800,null
    rows_desc, _ = search_listings({"category_key": CATEGORY}, sort="-price", db=db)
    assert ids(rows_desc) == [4, 1, 2, 6, 7, 3]


def test_date_sort(db):
    rows, _ = search_listings({"category_key": CATEGORY}, sort="date", db=db)
    assert ids(rows) == [2, 7, 3, 6, 1, 4]


def test_relevance_sort(db):
    rows, _ = search_listings(
        {"category_key": CATEGORY, "keywords_any": ["acer", "gaming"]},
        sort="relevance",
        db=db,
    )
    # id 4 matches both "acer" and "gaming" (score 2); id 1 matches only "acer".
    assert [row["id"] for row in rows] == [4, 1]


def test_pagination(db):
    rows, total = search_listings({"category_key": CATEGORY}, sort="-date", page=1, per_page=2, db=db)
    assert total == 6 and len(rows) == 2
    page2, _ = search_listings({"category_key": CATEGORY}, sort="-date", page=2, per_page=2, db=db)
    assert len(page2) == 2
    assert ids(rows) != ids(page2)


def test_psc_prefix_and_kraj(db):
    rows, total = search_listings({"category_key": CATEGORY, "psc_prefix": "04"}, db=db)
    assert total == 4 and sorted(ids(rows)) == [1, 4, 6, 7]
    rows_kraj, total_kraj = search_listings(
        {"category_key": CATEGORY, "kraj": "Košický kraj"}, db=db
    )
    assert total_kraj == 4 and sorted(ids(rows_kraj)) == [1, 4, 6, 7]


# ---------------------------------------------------------------------------
# Unknown-location handling
# ---------------------------------------------------------------------------

UNKNOWN_SEED = [
    # id, city, psc
    (1, "Žilina", "01001"),            # valid -> Žilinský
    (2, "Košice", "04012"),            # valid -> Košický
    (3, "Zahraničie", "12345"),        # placeholder -> unknown
    (4, "Česká republika", "11000"),   # foreign -> unknown
    (5, "Košice", "99998"),            # unmatched but city -> Košický
    (6, "Zahraničie", ""),             # missing -> unknown
]


@pytest.fixture()
def db_unknown(tmp_path):
    database = Database(tmp_path / "unknown.db")
    for ad_id, city, psc in UNKNOWN_SEED:
        database.upsert_listing(
            Listing(
                id=ad_id, url=f"https://pc.bazos.sk/inzerat/{ad_id}/x.php",
                category_key=CATEGORY, title=f"L{ad_id}", short_description="d",
                price_amount=100, price_type="fixed", price_text="100 €",
                city=city, psc=psc, posted_date=date(2026, 1, 1),
                views=1, is_top=False,
            ),
            CATEGORY,
        )
    yield database
    database.close()


def test_counts_split_confirmed_vs_unknown(db_unknown):
    result = search_listings({"category_key": CATEGORY}, db=db_unknown)
    assert result.total == 6
    assert result.confirmed == 3   # ids 1, 2, 5
    assert result.unknown == 3     # ids 3, 4, 6


def test_kraj_filter_includes_unknown_when_flag_on(db_unknown):
    on = search_listings(
        {"category_key": CATEGORY, "kraj": "Žilinský kraj",
         "include_unknown_location": True}, db=db_unknown
    )
    # id 1 plus the three unknown rows; id 5 is Košický, not unknown.
    assert sorted(ids(on.rows)) == [1, 3, 4, 6]
    assert on.confirmed == 1 and on.unknown == 3

    off = search_listings(
        {"category_key": CATEGORY, "kraj": "Žilinský kraj",
         "include_unknown_location": False}, db=db_unknown
    )
    assert ids(off.rows) == [1]
    assert off.confirmed == 1 and off.unknown == 0


def test_kraj_filter_excludes_other_kraje(db_unknown):
    result = search_listings(
        {"category_key": CATEGORY, "kraj": "Košický kraj",
         "include_unknown_location": True}, db=db_unknown
    )
    # ids 2 and 5 (city fallback) plus unknown rows; id 1 excluded.
    assert sorted(ids(result.rows)) == [2, 3, 4, 5, 6]


def test_psc_prefix_includes_invalid_psc_when_flag_on(db_unknown):
    on = search_listings(
        {"category_key": CATEGORY, "psc_prefix": "01",
         "include_unknown_location": True}, db=db_unknown
    )
    # id 1 matches the prefix; ids 3-6 have a non-valid PSČ (id 5 included
    # even though its city gave it a kraj).
    assert sorted(ids(on.rows)) == [1, 3, 4, 5, 6]
    assert on.confirmed == 2 and on.unknown == 3

    off = search_listings(
        {"category_key": CATEGORY, "psc_prefix": "01",
         "include_unknown_location": False}, db=db_unknown
    )
    assert ids(off.rows) == [1]


def test_unknown_kraj_option_returns_only_unknown(db_unknown):
    result = search_listings(
        {"category_key": CATEGORY, "kraj": "unknown"}, db=db_unknown
    )
    assert sorted(ids(result.rows)) == [3, 4, 6]
    assert result.confirmed == 0 and result.unknown == 3


def test_filters_from_to_dict_roundtrip():
    defaults = filters_from_dict({})
    assert isinstance(defaults, Filters)
    assert defaults["include_unknown_location"] is True
    assert defaults.category_key is None
    assert "include_unknown_location" not in filters_to_dict(defaults)

    off = filters_from_dict({"include_unknown_location": "0"})
    assert off["include_unknown_location"] is False
    assert filters_to_dict(off)["include_unknown_location"] == "0"

    on = filters_from_dict({"include_unknown_location": "1"})
    assert on["include_unknown_location"] is True
    assert "include_unknown_location" not in filters_to_dict(on)


def test_saved_search_filters_drop_transient_state():
    filters = filters_from_dict({
        "category": CATEGORY, "kw_all": "i5", "price_max": "250", "psc": "04",
        "new": "1", "hidden": "1", "fav": "1", "include_unknown_location": "0",
    })
    stored = saved_search_filters(filters)
    assert stored == {"kw_all": "i5", "price_max": "250", "psc": "04"}
    for transient in ("new", "hidden", "fav", "include_unknown_location", "category"):
        assert transient not in stored


def test_filters_from_dict_tolerates_garbage():
    assert filters_from_dict("not a mapping")["category_key"] is None
    assert filters_from_dict([1, 2, 3])["price_max"] is None
    assert filters_from_dict(None)["price_min"] is None

    filters = filters_from_dict({
        "price_min": "abc", "price_max": "10", "kw_all": 5,
        "price_type": "fixed", "kraj": "Neznamy", "category": "nope/nope",
        "added": "3d",
    })
    assert filters["price_min"] is None
    assert filters["price_max"] == 10
    assert filters["price_types"] == ["fixed"]
    assert filters["kraj"] is None
    assert filters["category_key"] is None
    assert filters["added"] == ""


def test_count_listings_matches_search(db):
    filters = {"category_key": CATEGORY, "price_max": 300}
    rows, total = search_listings(filters, db=db)
    assert count_listings(filters, db=db) == total


