"""Build ``categories.yaml`` from the bazos.sk homepage and category map.

Sources (both allowed by robots.txt):
  * ``tests/fixtures/homepage.html``      - the 20 top-level sections plus the
    subcategory links shown on the homepage;
  * ``tests/fixtures/mapa-kategorie.html`` - the full subcategory list.

Both fixtures are downloaded once, politely, if they are missing. Re-running
regenerates ``categories.yaml`` deterministically.

Usage:
    python src/build_categories.py              # build categories.yaml
    python src/build_categories.py --verify     # build, then fetch 3 random
                                                # category pages and parse them
"""

from __future__ import annotations

import argparse
import random
import re
from fnmatch import fnmatch
from pathlib import Path
from urllib.parse import urlparse

import yaml
from bs4 import BeautifulSoup

from categories import load_categories
from config import load_config, politeness_from_config
from fetch_samples import Fetcher
from parser import parse_listing_page

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
CATEGORIES_YAML = ROOT / "categories.yaml"

CONFIG = load_config()
POLITENESS = politeness_from_config(CONFIG)

ROBOTS_URL = "https://www.bazos.sk/robots.txt"
HOMEPAGE_URL = "https://www.bazos.sk/"
MAPA_URL = "https://www.bazos.sk/mapa-kategorie.php"
MAPA_PATH = "/mapa-kategorie.php"


def _clean(text: str) -> str:
    return " ".join(text.split())


def _section_key(host: str) -> str:
    return host.split(".")[0]


def _normalize_path(path: str) -> str:
    path = re.sub(r"/+", "/", path or "")
    if not path.startswith("/"):
        path = "/" + path
    if not path.endswith("/"):
        path += "/"
    return path


def _robots_allows(path: str, robots_text: str) -> bool:
    """Minimal robots.txt check for the ``User-agent: *`` group."""
    in_star = False
    for raw in robots_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lowered = line.lower()
        if lowered.startswith("user-agent:"):
            in_star = line.split(":", 1)[1].strip() == "*"
            continue
        if in_star and lowered.startswith("disallow:"):
            rule = line.split(":", 1)[1].strip()
            if rule and fnmatch(path, rule):
                return False
    return True


def _ensure_fixture(name: str, url: str, fetcher: Fetcher, force: bool) -> Path:
    path = FIXTURES / name
    if path.exists() and not force:
        return path
    print(f"  downloading {url}")
    path.write_bytes(fetcher.get(url).content)
    return path


def _iter_section_cells(html: str, cell_selector: str, title_selector: str):
    """Yield (host, label, [other anchors]) for each category cell."""
    soup = BeautifulSoup(html, "html.parser")
    for cell in soup.select(cell_selector):
        title_a = cell.select_one(title_selector)
        if title_a is None:
            continue
        href = title_a.get("href", "")
        host = urlparse(href).netloc
        if not host:
            continue
        others = [a for a in cell.select("a[href]") if a is not title_a]
        yield host, _clean(title_a.get_text()), others


def _build_entries(homepage_html: str, mapa_html: str | None) -> list[dict]:
    sections: dict[str, dict] = {}
    subcats: dict[str, dict] = {}

    sources = [
        (homepage_html, "div.icontblcell", "span.nadpisnahlavni a[href]"),
    ]
    if mapa_html is not None:
        sources.append((mapa_html, "div.mapaflexcell", "span.maparubriky a[href]"))

    for html, cell_sel, title_sel in sources:
        for host, label, anchors in _iter_section_cells(html, cell_sel, title_sel):
            section_key = _section_key(host)
            sections.setdefault(section_key, {"label": label, "host": host})
            section_label = sections[section_key]["label"]
            for a in anchors:
                href = a.get("href", "")
                parsed = urlparse(href)
                if parsed.netloc != host:
                    continue
                path = _normalize_path(parsed.path)
                if path == "/":  # image link / group label pointing at the root
                    continue
                sub_key = f"{section_key}/{path.strip('/')}"
                subcats.setdefault(sub_key, {
                    "key": sub_key,
                    "label": f"{section_label} > {_clean(a.get_text())}",
                    "host": host,
                    "path": path,
                    "parent_key": section_key,
                    "url": f"https://{host}{path}",
                })

    entries: list[dict] = []
    for key in sorted(sections):
        section = sections[key]
        entries.append({
            "key": key,
            "label": section["label"],
            "host": section["host"],
            "path": "/",
            "parent_key": None,
            "url": f"https://{section['host']}/",
        })
    for key in sorted(subcats):
        entries.append(subcats[key])
    return entries


def build(force: bool = False) -> list[dict]:
    fetcher = Fetcher(POLITENESS)

    robots = _ensure_fixture("robots.txt", ROBOTS_URL, fetcher, force).read_text("utf-8")
    homepage = _ensure_fixture("homepage.html", HOMEPAGE_URL, fetcher, force).read_text("utf-8")

    mapa_html = None
    if _robots_allows(MAPA_PATH, robots):
        mapa_html = _ensure_fixture("mapa-kategorie.html", MAPA_URL, fetcher, force).read_text("utf-8")
    else:
        print(f"  {MAPA_PATH} is Disallowed by robots.txt - using homepage only")

    entries = _build_entries(homepage, mapa_html)
    CATEGORIES_YAML.write_text(
        yaml.safe_dump({"categories": entries}, allow_unicode=True, sort_keys=False, width=200),
        encoding="utf-8",
    )
    return entries


def verify(count: int = 3, seed: int | None = None) -> bool:
    categories = load_categories()
    picks = random.Random(seed).sample(categories, count)
    fetcher = Fetcher(POLITENESS)
    print(f"Verifying {count} random categories with parse_listing_page:")
    all_ok = True
    for category in picks:
        page = parse_listing_page(fetcher.get(category.url).text, category.key)
        ok = bool(page.items)
        all_ok = all_ok and ok
        print(
            f"  [{'OK' if ok else 'EMPTY'}] {category.key:<28} "
            f"{category.url} -> {len(page.items)} items (total={page.total_count})"
        )
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true",
                        help="fetch 3 random category pages and parse them")
    parser.add_argument("--seed", type=int, default=None,
                        help="random seed for --verify (for reproducibility)")
    parser.add_argument("--force", action="store_true",
                        help="re-download the source fixtures")
    args = parser.parse_args()

    entries = build(force=args.force)
    sections = [e for e in entries if e["parent_key"] is None]
    subcats = [e for e in entries if e["parent_key"] is not None]
    print(f"Wrote {CATEGORIES_YAML.name}: {len(sections)} sections, {len(subcats)} subcategories")

    if args.verify:
        return 0 if verify(seed=args.seed) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
