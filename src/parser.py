"""Parser for bazos.sk category listing pages (Phase 2).

All selectors and formats are derived from the real pages saved in
``tests/fixtures/`` (see ``docs/site_structure.md``). This module performs no
network access.

Key facts used here:

* one listing = ``div.inzeraty.inzeratyflex`` (the column-header row is
  ``div.listainzerat.inzeratyflex`` and is deliberately ignored);
* header count: ``Zobrazených 1-20 inzerátov z 6 386``;
* price cell: ``div.inzeratycena span[translate="no"]``;
* the list description in ``div.popis`` is truncated and ends with `` ...``.

Seller names, phones and e-mails are never read or stored.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

ITEM_SELECTOR = "div.inzeraty.inzeratyflex"
HEADER_SELECTOR = "div.listainzerat div.inzeratynadpis"

_HEADER_RE = re.compile(
    r"Zobrazených\s+([\d\s]+?)\s*-\s*([\d\s]+?)\s+inzerátov\s+z\s+([\d\s]+)"
)
_AD_ID_RE = re.compile(r"/inzerat/(\d+)/")
_DATE_RE = re.compile(r"\[(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\]")

# non-numeric price labels -> price_type
_PRICE_LABELS = {
    "dohodou": "negotiable",
    "zadarmo": "free",
    "v texte": "in_text",
    "ponúknite": "offer",
    "ponuknite": "offer",  # ASCII fallback
}

DEFAULT_BASE_URL = "https://www.bazos.sk/"


@dataclass
class Listing:
    """A single classified ad as shown on a category listing page."""

    id: int
    url: str
    category_key: str
    title: str
    short_description: str
    price_amount: Optional[int]
    price_type: str
    price_text: str
    city: str
    psc: str
    posted_date: Optional[date]
    views: Optional[int]
    is_top: bool


@dataclass
class ParsedPage:
    """Result of parsing one category listing page."""

    items: list[Listing] = field(default_factory=list)
    total_count: Optional[int] = None
    range_end: Optional[int] = None
    is_last_page: bool = False


def _clean(text: str) -> str:
    """Collapse all whitespace runs (incl. newlines) to single spaces."""
    return " ".join(text.split())


def _to_int(text: str) -> Optional[int]:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def _warn(ad_id: object, field_name: str) -> None:
    logger.warning("listing %s: missing/unparseable %s", ad_id, field_name)


def _detect_base_url(soup: BeautifulSoup) -> str:
    """Absolute base URL, taken from <link rel="canonical"> if present."""
    for link in soup.find_all("link"):
        rel = link.get("rel") or []
        if "canonical" in rel and link.get("href"):
            parsed = urlparse(link["href"])
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}/"
    return DEFAULT_BASE_URL


def _parse_header(soup: BeautifulSoup) -> tuple[Optional[int], Optional[int]]:
    """Return (total_count, range_end) from the header row, or (None, None)."""
    header = soup.select_one(HEADER_SELECTOR)
    if header is None:
        return None, None
    match = _HEADER_RE.search(header.get_text(" "))
    if not match:
        return None, None
    _, end_raw, total_raw = match.groups()
    return _to_int(total_raw), _to_int(end_raw)


def _parse_price(text: str, ad_id: object) -> tuple[Optional[int], str, str]:
    cleaned = _clean(text)
    if not cleaned:
        return None, "unknown", ""
    if any(ch.isdigit() for ch in cleaned):
        return int(re.sub(r"\D", "", cleaned)), "fixed", cleaned
    price_type = _PRICE_LABELS.get(cleaned.lower().rstrip("."))
    if price_type is None:
        logger.warning("listing %s: unknown price format %r", ad_id, cleaned)
        return None, "unknown", cleaned
    return None, price_type, cleaned


def _parse_location(container: Tag) -> tuple[str, str]:
    loc = container.select_one("div.inzeratylok")
    if loc is None:
        return "", ""
    parts = [p.strip() for p in loc.get_text("\n").split("\n") if p.strip()]
    city = parts[0] if parts else ""
    psc = re.sub(r"\D", "", parts[1]) if len(parts) > 1 else ""
    return city, psc


def _parse_date(container: Tag) -> Optional[date]:
    span = container.select_one("span.velikost10")
    text = span.get_text(" ") if span is not None else container.get_text(" ")
    match = _DATE_RE.search(text)
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_item(container: Tag, category_key: str, base_url: str) -> Optional[Listing]:
    title_anchor = container.select_one("div.inzeratynadpis h2.nadpis a[href]")
    if title_anchor is None:
        title_anchor = container.select_one('a[href*="/inzerat/"]')
    if title_anchor is None:
        logger.warning("skipping listing: no detail link found")
        return None

    href = title_anchor.get("href", "")
    id_match = _AD_ID_RE.search(href)
    if not id_match:
        logger.warning("skipping listing: cannot parse id from %r", href)
        return None
    ad_id = int(id_match.group(1))

    title = _clean(title_anchor.get_text())
    if not title:
        _warn(ad_id, "title")

    popis = container.select_one("div.popis")
    short_description = _clean(popis.get_text()) if popis is not None else ""
    if popis is None:
        _warn(ad_id, "short_description")
    short_description = re.sub(r"\s*\.\.\.$", "", short_description).rstrip()

    price_span = container.select_one('div.inzeratycena span[translate="no"]')
    if price_span is None:
        _warn(ad_id, "price")
        price_amount, price_type, price_text = None, "unknown", ""
    else:
        price_amount, price_type, price_text = _parse_price(
            price_span.get_text(), ad_id
        )

    city, psc = _parse_location(container)
    if not city:
        _warn(ad_id, "location")

    posted_date = _parse_date(container)
    if posted_date is None:
        _warn(ad_id, "posted_date")

    view = container.select_one("div.inzeratyview")
    views = _to_int(view.get_text()) if view is not None else None
    if view is None:
        _warn(ad_id, "views")

    return Listing(
        id=ad_id,
        url=urljoin(base_url, href),
        category_key=category_key,
        title=title,
        short_description=short_description,
        price_amount=price_amount,
        price_type=price_type,
        price_text=price_text,
        city=city,
        psc=psc,
        posted_date=posted_date,
        views=views,
        is_top=container.select_one("span.ztop") is not None,
    )


def parse_listing_page(html: str, category_key: str) -> ParsedPage:
    """Parse a bazos.sk category listing page.

    Only ``div.inzeraty.inzeratyflex`` blocks are treated as listings; the
    header row and any related/sidebar blocks are ignored.
    """
    soup = BeautifulSoup(html, "html.parser")
    total_count, range_end = _parse_header(soup)
    base_url = _detect_base_url(soup)

    items: list[Listing] = []
    for container in soup.select(ITEM_SELECTOR):
        item = _parse_item(container, category_key, base_url)
        if item is not None:
            items.append(item)

    is_last_page = len(items) == 0 or (
        range_end is not None
        and total_count is not None
        and range_end == total_count
    )

    return ParsedPage(
        items=items,
        total_count=total_count,
        range_end=range_end,
        is_last_page=is_last_page,
    )
