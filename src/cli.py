"""Command-line interface for the bazos.sk scraper.

Run as a module from the project root::

    python -m src.cli categories --search notebook
    python -m src.cli scrape --category pc/notebook --max-pages 3
    python -m src.cli show --category pc/notebook --new --sort price --limit 10
    python -m src.cli mark-seen --category pc/notebook
    python -m src.cli refresh-kraj
    python -m src.cli stats
"""

from __future__ import annotations

import argparse
import difflib
import sys
import unicodedata
from pathlib import Path
from typing import Any, Optional

# Allow both `python -m src.cli` and `python src/cli.py`.
_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from categories import load_categories, get_category  # noqa: E402
from config import load_config, resolve_db_path  # noqa: E402
from db import Database  # noqa: E402
from fetcher import PoliteSession  # noqa: E402
from scraper import scrape_category  # noqa: E402


def _fold(text: str) -> str:
    """Case- and diacritics-insensitive folding."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _unknown_category_message(key: str, categories: list) -> str:
    matches = difflib.get_close_matches(key, [c.key for c in categories], n=5, cutoff=0.4)
    message = f"unknown category: {key!r}"
    if matches:
        message += "\nDid you mean: " + ", ".join(matches)
    return message


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_categories(args: argparse.Namespace, config: dict[str, Any]) -> int:
    categories = load_categories()
    if args.parent:
        categories = [c for c in categories if c.parent_key == args.parent]
    if args.search:
        needle = _fold(args.search)
        categories = [
            c for c in categories
            if needle in _fold(c.label) or needle in _fold(c.key)
        ]
    for category in categories:
        print(f"{category.key}\t{category.label}")
    print(f"\n{len(categories)} category(ies)")
    return 0


def cmd_scrape(args: argparse.Namespace, config: dict[str, Any]) -> int:
    categories = load_categories()
    for key in args.category:
        if get_category(key) is None:
            print(_unknown_category_message(key, categories), file=sys.stderr)
            return 2

    db = Database(resolve_db_path(config))
    session = PoliteSession(config)
    try:
        for key in args.category:
            result = scrape_category(
                key, max_pages=args.max_pages, full=args.full,
                db=db, session=session, config=config,
            )
            print(
                f"{key}: pages={result.pages_fetched} seen={result.listings_seen} "
                f"new={result.listings_new} price_changes={result.listings_price_changed} "
                f"stop={result.stop_reason} status={result.status}"
            )
            if result.status == "blocked":
                print(
                    f"STOPPED: {key} was blocked ({result.stop_reason}); "
                    f"aborting remaining categories.",
                    file=sys.stderr,
                )
                break
    finally:
        session.close()
        db.close()
    return 0


def _format_row(row: Any) -> str:
    price = row["price_text"] or (
        str(row["price_amount"]) if row["price_amount"] is not None else "-"
    )
    date = row["posted_date"] or "-"
    title = row["title"]
    if len(title) > 50:
        title = title[:47] + "..."
    return (
        f"{row['id']:<10} {price:>9}  {row['city'][:16]:<16} {row['psc']:<6} "
        f"{date:<11} {title:<50} {row['url']}"
    )


def cmd_show(args: argparse.Namespace, config: dict[str, Any]) -> int:
    if get_category(args.category) is None:
        print(_unknown_category_message(args.category, load_categories()), file=sys.stderr)
        return 2

    db = Database(resolve_db_path(config))
    try:
        rows = db.query_listings(
            args.category,
            new_only=args.new,
            sort=args.sort,
            min_price=args.min_price,
            max_price=args.max_price,
            psc_prefix=args.psc_prefix,
            limit=args.limit,
        )
    finally:
        db.close()

    if not rows:
        print("(no listings)")
        return 0

    print(
        f"{'id':<10} {'price':>9}  {'city':<16} {'psc':<6} "
        f"{'date':<11} {'title':<50} url"
    )
    for row in rows:
        print(_format_row(row))
    print(f"\n{len(rows)} listing(s)")
    return 0


def cmd_mark_seen(args: argparse.Namespace, config: dict[str, Any]) -> int:
    if not args.all and get_category(args.category) is None:
        print(_unknown_category_message(args.category, load_categories()), file=sys.stderr)
        return 2

    db = Database(resolve_db_path(config))
    try:
        updated = db.mark_seen(None if args.all else args.category)
    finally:
        db.close()

    scope = "all listings" if args.all else args.category
    print(f"marked {updated} listing(s) as seen ({scope})")
    return 0


def cmd_refresh_kraj(args: argparse.Namespace, config: dict[str, Any]) -> int:
    db = Database(resolve_db_path(config))
    try:
        updated = db.refresh_kraj()
    finally:
        db.close()

    print(f"refreshed kraj for {updated} listing(s)")
    return 0


def cmd_stats(args: argparse.Namespace, config: dict[str, Any]) -> int:
    db = Database(resolve_db_path(config))
    try:
        rows = db.stats()
    finally:
        db.close()

    if not rows:
        print("(no data yet)")
        return 0

    print(f"{'category':<28} {'total':>6} {'new':>6}  last run")
    for row in rows:
        print(
            f"{row['category_key']:<28} {row['total']:>6} {row['unseen']:>6}  "
            f"{row['last_run'] or '-'}"
        )
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.cli", description="bazos.sk scraper")
    sub = parser.add_subparsers(dest="command", required=True)

    p_categories = sub.add_parser("categories", help="list catalog categories")
    p_categories.add_argument("--search", help="case/diacritics-insensitive filter")
    p_categories.add_argument("--parent", help="only subcategories of this section key")
    p_categories.set_defaults(func=cmd_categories)

    p_scrape = sub.add_parser("scrape", help="incrementally scrape categories")
    p_scrape.add_argument("--category", action="append", required=True,
                          help="category key (repeatable)")
    p_scrape.add_argument("--max-pages", type=int, default=None)
    p_scrape.add_argument("--full", action="store_true",
                          help="ignore the caught-up early stop")
    p_scrape.set_defaults(func=cmd_scrape)

    p_show = sub.add_parser("show", help="show stored listings for a category")
    p_show.add_argument("--category", required=True)
    p_show.add_argument("--new", action="store_true", help="only unseen listings")
    p_show.add_argument(
        "--sort",
        choices=["price", "-price", "date", "-date", "first_seen", "-first_seen"],
        default="-first_seen",
    )
    p_show.add_argument("--max-price", type=int, default=None)
    p_show.add_argument("--min-price", type=int, default=None)
    p_show.add_argument("--psc-prefix", default=None)
    p_show.add_argument("--limit", type=int, default=50)
    p_show.set_defaults(func=cmd_show)

    p_mark = sub.add_parser("mark-seen", help="mark listings as reviewed")
    group = p_mark.add_mutually_exclusive_group(required=True)
    group.add_argument("--category")
    group.add_argument("--all", action="store_true")
    p_mark.set_defaults(func=cmd_mark_seen)

    p_stats = sub.add_parser("stats", help="per-category totals and last run")
    p_stats.set_defaults(func=cmd_stats)

    p_refresh = sub.add_parser(
        "refresh-kraj", help="recompute kraj for all listings from their PSČ"
    )
    p_refresh.set_defaults(func=cmd_refresh_kraj)

    return parser


def main(argv: Optional[list[str]] = None, config: Optional[dict[str, Any]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass
    config = config or load_config()
    args = build_parser().parse_args(argv)
    return args.func(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
