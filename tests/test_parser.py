"""Offline tests for src/parser.py.

Every test uses only the saved fixtures in tests/fixtures/ - no network.
Expected values were read by hand from the raw HTML.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from parser import parse_listing_page

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def by_id(page):
    return {item.id: item for item in page.items}


# ---------------------------------------------------------------------------
# notebook page 1
# ---------------------------------------------------------------------------

def test_p1_basic_counts():
    page = parse_listing_page(load("list_notebook_p1.html"), "pc/notebook")
    assert len(page.items) == 20
    assert page.total_count == 6386
    assert page.range_end == 20
    assert page.is_last_page is False


def test_p1_items_have_core_fields():
    page = parse_listing_page(load("list_notebook_p1.html"), "pc/notebook")
    assert any(item.is_top for item in page.items), "expected some TOP items"
    for item in page.items:
        assert isinstance(item.id, int) and item.id > 0
        assert item.title
        assert item.url.startswith("https://pc.bazos.sk/inzerat/")
        assert item.category_key == "pc/notebook"


def test_short_description_truncation_marker_stripped():
    page = parse_listing_page(load("list_notebook_p1.html"), "pc/notebook")
    # The site truncates the list description and appends " ..."; the parser
    # must strip that marker.
    for item in page.items:
        assert not item.short_description.endswith("...")
    assert by_id(page)[195709118].short_description.startswith("Predám 15.6")


# ---------------------------------------------------------------------------
# pagination / last page / overflow
# ---------------------------------------------------------------------------

def test_p2():
    page = parse_listing_page(load("list_notebook_p2.html"), "pc/notebook")
    assert len(page.items) == 20
    assert page.range_end == 40
    assert page.total_count == 6386
    assert page.is_last_page is False


def test_p11_has_no_top_items():
    page = parse_listing_page(load("list_notebook_p11.html"), "pc/notebook")
    assert len(page.items) == 20
    assert page.range_end == 220
    assert not any(item.is_top for item in page.items)


def test_last_page():
    """The header claims a 6381-6386 range (6 ads) but only 3 are rendered.

    The range is computed by the site as ``offset + 20`` capped at the total
    (6380 + 20 -> 6386), not from the number of ads actually present. The real
    rendered count is 3, so the parser must report 3 items while keeping
    range_end = 6386 and is_last_page = True.
    """
    page = parse_listing_page(load("list_notebook_lastpage.html"), "pc/notebook")
    assert len(page.items) == 3
    assert page.total_count == 6386
    assert page.range_end == 6386
    assert page.is_last_page is True
    assert [item.id for item in page.items] == [193900513, 193900512, 193900510]


def test_overflow_404():
    page = parse_listing_page(load("list_notebook_overflow404.html"), "pc/notebook")
    assert page.items == []
    assert page.is_last_page is True
    assert page.total_count == 6386


# ---------------------------------------------------------------------------
# non-numeric price types
# ---------------------------------------------------------------------------

def test_zvierata_price_types():
    page = parse_listing_page(load("list_zvierata_p1.html"), "zvierata")
    items = by_id(page)
    expected = {
        195628860: "negotiable",  # Dohodou
        195670972: "free",        # Zadarmo
        195749331: "negotiable",  # Dohodou
        195637360: "in_text",     # V texte
        195534092: "in_text",     # V texte
        195705238: "negotiable",  # Dohodou
    }
    for ad_id, price_type in expected.items():
        assert items[ad_id].price_type == price_type, ad_id
        assert items[ad_id].price_amount is None
    assert {items[i].price_type for i in expected} == {
        "negotiable", "free", "in_text",
    }
    # every item still gets a price_type and raw price text
    for item in page.items:
        assert item.price_type in {
            "fixed", "negotiable", "free", "in_text", "offer", "unknown",
        }
        assert item.price_text


# ---------------------------------------------------------------------------
# hand-verified listings (field by field against the raw HTML)
# ---------------------------------------------------------------------------

HAND_VERIFIED = [
    # fixture, id, title, price_amount, price_text, city, psc, date, views, top
    ("list_notebook_p1.html", 195709118,
     'Predám hliníkový15.6" notebook FullHD IPS 8GB 256GB SSD',
     389, "389 €", "Košice", "04012", date(2026, 9, 21), 126, True),
    ("list_notebook_p1.html", 195703097,
     'MacBook Pro 14" 2021, M1 Pro, 16GB/1TB, SK klávesnica',
     750, "750 €", "Prievidza", "97243", date(2026, 9, 21), 227, True),
    ("list_notebook_p1.html", 194386727,
     "Lenovo ThinkPad T15 G1 - veľmi dorý stav",
     350, "350 €", "Senec", "92526", date(2026, 9, 21), 880, True),
    ("list_notebook_lastpage.html", 193900513,
     "notebook HP 820 G3",
     120, "120 €", "Žilina", "01001", date(2026, 7, 23), 361, False),
    ("list_notebook_lastpage.html", 193900512,
     "notebook Asus M509DA",
     150, "150 €", "Žilina", "01001", date(2026, 7, 23), 333, False),
]


@pytest.mark.parametrize(
    "fixture,ad_id,title,amount,price_text,city,psc,posted,views,is_top",
    HAND_VERIFIED,
)
def test_hand_verified_listings(
    fixture, ad_id, title, amount, price_text, city, psc, posted, views, is_top
):
    page = parse_listing_page(load(fixture), "pc/notebook")
    item = by_id(page)[ad_id]
    assert item.title == title
    assert item.price_amount == amount
    assert item.price_type == "fixed"
    assert item.price_text == price_text
    assert item.city == city
    assert item.psc == psc
    assert item.posted_date == posted
    assert item.views == views
    assert item.is_top is is_top
