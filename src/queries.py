"""SQL query layer for the web UI.

All filtering happens in SQL with bound parameters (no string-formatted
user input). Keyword matching uses the same :func:`text.normalize_text`
normalisation as ``listings.search_text``.

Location filters: a listing whose location cannot be determined
(``kraj IS NULL``) is kept in the results by default
(``include_unknown_location=True``) so invalid/unknown PSČ never make a
listing disappear. See :func:`search_listings` for the precise semantics.

The same :func:`filters_from_dict` / :func:`filters_to_dict` pair is used by
the Flask route and by saved searches (Phase 5); there is no second filter
parser.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from categories import get_category
from config import load_config, resolve_db_path
from db import Database
from geo import KRAJ_UNKNOWN, all_kraje
from text import normalize_text, parse_terms

logger = logging.getLogger(__name__)

SORT_OPTIONS = (
    "price", "-price", "date", "-date", "first_seen", "-first_seen",
    "views", "relevance",
)
PRICE_TYPES = ("fixed", "negotiable", "free", "in_text", "offer")
KRAJE = all_kraje()

_LIKE_ESCAPE = "ESCAPE '\\'"

_EXTRA_COLUMNS = """
    (SELECT COUNT(*) FROM price_history ph WHERE ph.listing_id = l.id) AS price_history_count,
    (SELECT ph2.price_amount FROM price_history ph2
      WHERE ph2.listing_id = l.id ORDER BY ph2.id DESC LIMIT 1 OFFSET 1) AS previous_price_amount
"""

# URL keys of the filters that a saved search persists (Phase 5). Transient
# state (page, include_hidden, only_new, only_favorites, ...) is deliberately
# not stored; ``only_new`` is applied at open time via a link flag.
PERSISTENT_URL_KEYS = (
    "kw_all", "kw_any", "kw_desc", "kw_exclude", "price_min", "price_max",
    "price_type", "psc", "kraj", "city", "days", "added",
)


class Filters(dict):
    """A validated filter set.

    Behaves exactly like the historical ``dict`` (``.get``, ``[]``, Jinja
    attribute access) so every existing caller keeps working.
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover - only for typo safety
            raise AttributeError(name) from exc


class SearchResult(tuple):
    """``(rows, total)`` tuple with extra location-count metadata.

    Unpacks like the historical ``(rows, total)`` while also exposing
    ``rows``, ``total``, ``confirmed`` and ``unknown`` attributes, where
    ``confirmed``/``unknown`` split the current result set by whether the
    region is known (``kraj IS NOT NULL`` / ``kraj IS NULL``).
    """

    def __new__(cls, rows: list, total: int, confirmed: int, unknown: int) -> "SearchResult":
        instance = super().__new__(cls, (rows, total))
        instance.rows = rows
        instance.total = total
        instance.confirmed = confirmed
        instance.unknown = unknown
        return instance


def _like_pattern(term: str) -> str:
    escaped = normalize_text(term)
    escaped = escaped.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _open_db(db: Optional[Database]) -> tuple[Database, bool]:
    if db is not None:
        return db, False
    return Database(resolve_db_path(load_config())), True


# ---------------------------------------------------------------------------
# filter (de)serialization
# ---------------------------------------------------------------------------

def _one(data: Any, name: str, default: Any = "") -> Any:
    value = data.get(name, default) if hasattr(data, "get") else default
    return default if value is None else value


def _many(data: Any, name: str) -> list[Any]:
    getlist = getattr(data, "getlist", None)
    if callable(getlist):
        return list(getlist(name))
    value = data.get(name) if hasattr(data, "get") else None
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _text(data: Any, name: str, default: str = "") -> str:
    value = _one(data, name, default)
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        value = value[-1] if value else default
    return value if isinstance(value, str) else str(value)


def _flag(data: Any, name: str) -> bool:
    values = _many(data, name)
    if not values:
        return False
    value = values[-1]
    if isinstance(value, bool):
        return value
    return str(value) not in ("", "0", "false", "False", "none", "None")


def _int_value(data: Any, name: str) -> Optional[int]:
    raw = _text(data, name).strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _first_seen_since(added: str) -> Optional[str]:
    if added == "24h":
        delta = timedelta(hours=24)
    elif added == "7d":
        delta = timedelta(days=7)
    else:
        return None
    return (datetime.now(timezone.utc) - delta).replace(microsecond=0).isoformat()


def filters_from_dict(data: Any) -> Filters:
    """Normalize filter input (``request.args``, a plain dict or stored JSON)
    to a validated :class:`Filters` object.

    Never raises on old/partial/garbage input: unknown keys are ignored and
    invalid values fall back to defaults (a warning is logged).
    """
    if not hasattr(data, "get"):
        if data not in (None, {}, ""):
            logger.warning("filters_from_dict: expected a mapping, got %r; using defaults",
                           type(data).__name__)
        data = {}

    category_key = _text(data, "category").strip() or None
    if category_key and get_category(category_key) is None:
        logger.warning("filters_from_dict: unknown category %r ignored", category_key)
        category_key = None

    kraj = _text(data, "kraj").strip() or None
    if kraj and kraj != KRAJ_UNKNOWN and kraj not in KRAJE:
        logger.warning("filters_from_dict: unknown kraj %r ignored", kraj)
        kraj = None

    added = _text(data, "added").strip()
    if added not in ("", "24h", "7d"):
        added = ""

    raw_days = _text(data, "days").strip()
    if raw_days == "":
        max_age_days = None
    else:
        try:
            max_age_days = int(raw_days)
        except ValueError:
            max_age_days = None
        if max_age_days is not None and max_age_days < 0:
            max_age_days = None

    raw_unknown = _many(data, "include_unknown_location")
    if not raw_unknown:
        include_unknown_location = True  # default: keep unknown locations
    elif len(raw_unknown) == 1 and isinstance(raw_unknown[0], bool):
        include_unknown_location = raw_unknown[0]
    else:
        include_unknown_location = any(str(value) == "1" for value in raw_unknown)

    return Filters(
        category_key=category_key,
        keywords_all=parse_terms(_text(data, "kw_all")),
        keywords_any=parse_terms(_text(data, "kw_any")),
        keywords_desc=parse_terms(_text(data, "kw_desc")),
        keywords_exclude=parse_terms(_text(data, "kw_exclude")),
        price_min=_int_value(data, "price_min"),
        price_max=_int_value(data, "price_max"),
        include_no_price=_flag(data, "no_price"),
        price_types=[t for t in _many(data, "price_type") if t in PRICE_TYPES],
        psc_prefix=_text(data, "psc").strip() or None,
        city=_text(data, "city").strip() or None,
        kraj=kraj,
        only_new=_flag(data, "new"),
        only_favorites=_flag(data, "fav"),
        include_hidden=_flag(data, "hidden"),
        added=added,
        first_seen_since=_first_seen_since(added),
        max_age_days=max_age_days,
        include_unknown_location=include_unknown_location,
    )


def filters_to_dict(filters: Any) -> dict[str, Any]:
    """Serialize a filter set back to URL query parameters.

    Stable key order; only non-default values are emitted. In particular
    ``include_unknown_location`` is only emitted when it is ``False`` (the
    default is ``True``).
    """
    out: dict[str, Any] = {}
    if filters.get("category_key"):
        out["category"] = filters["category_key"]
    for source, target in (
        ("keywords_all", "kw_all"),
        ("keywords_any", "kw_any"),
        ("keywords_desc", "kw_desc"),
        ("keywords_exclude", "kw_exclude"),
    ):
        terms = filters.get(source) or []
        if terms:
            out[target] = ", ".join(str(t) for t in terms)
    if filters.get("price_min") is not None:
        out["price_min"] = str(filters["price_min"])
    if filters.get("price_max") is not None:
        out["price_max"] = str(filters["price_max"])
    if filters.get("include_no_price"):
        out["no_price"] = "1"
    if filters.get("price_types"):
        out["price_type"] = list(filters["price_types"])
    if filters.get("psc_prefix"):
        out["psc"] = filters["psc_prefix"]
    if filters.get("city"):
        out["city"] = filters["city"]
    if filters.get("kraj"):
        out["kraj"] = filters["kraj"]
    if filters.get("only_new"):
        out["new"] = "1"
    if filters.get("only_favorites"):
        out["fav"] = "1"
    if filters.get("include_hidden"):
        out["hidden"] = "1"
    if filters.get("added"):
        out["added"] = filters["added"]
    if filters.get("max_age_days") is not None:
        out["days"] = str(filters["max_age_days"])
    if not filters.get("include_unknown_location", True):
        out["include_unknown_location"] = "0"
    return out


def saved_search_filters(filters: Any) -> dict[str, Any]:
    """The persistent subset of a filter set, as URL-style keys (Phase 5)."""
    url = filters_to_dict(filters)
    return {key: url[key] for key in PERSISTENT_URL_KEYS if key in url}


# ---------------------------------------------------------------------------
# query building (shared by search and counts)
# ---------------------------------------------------------------------------

@dataclass
class QueryParts:
    join_sql: str
    where_sql: str
    where_params: list[Any]
    keywords_all: list[str]
    keywords_any: list[str]
    keywords_desc: list[str]


def _build_query(filters: Any) -> QueryParts:
    """Build the shared JOIN/WHERE fragments for a filter set."""
    filters = filters or {}
    include_unknown = bool(filters.get("include_unknown_location", True))

    joins: list[str] = []
    clauses: list[str] = []
    where_params: list[Any] = []

    category_key = filters.get("category_key")
    if category_key:
        joins.append("JOIN listing_categories lc ON lc.listing_id = l.id")
        clauses.append("lc.category_key = ?")
        where_params.append(category_key)

    if filters.get("only_new"):
        clauses.append("l.seen = 0")
    if filters.get("only_favorites"):
        clauses.append("l.is_favorite = 1")
    if not filters.get("include_hidden"):
        clauses.append("l.is_hidden = 0")

    price_min = filters.get("price_min")
    price_max = filters.get("price_max")
    if price_min is not None or price_max is not None:
        parts, params = [], []
        if price_min is not None:
            parts.append("l.price_amount >= ?")
            params.append(int(price_min))
        if price_max is not None:
            parts.append("l.price_amount <= ?")
            params.append(int(price_max))
        price_range = "(" + " AND ".join(parts) + ")"
        if filters.get("include_no_price"):
            clauses.append(f"(l.price_amount IS NULL OR {price_range})")
        else:
            clauses.append(f"(l.price_amount IS NOT NULL AND {price_range})")
        where_params.extend(params)

    price_types = [t for t in (filters.get("price_types") or []) if t in PRICE_TYPES]
    if price_types:
        clauses.append("l.price_type IN (" + ",".join("?" * len(price_types)) + ")")
        where_params.extend(price_types)

    keywords_all = [t for t in (normalize_text(t) for t in filters.get("keywords_all") or []) if t]
    keywords_any = [t for t in (normalize_text(t) for t in filters.get("keywords_any") or []) if t]
    keywords_desc = [t for t in (normalize_text(t) for t in filters.get("keywords_desc") or []) if t]
    keywords_exclude = [t for t in (normalize_text(t) for t in filters.get("keywords_exclude") or []) if t]

    for term in keywords_all:
        clauses.append(f"l.search_text LIKE ? {_LIKE_ESCAPE}")
        where_params.append(_like_pattern(term))
    if keywords_any:
        clauses.append("(" + " OR ".join(f"l.search_text LIKE ? {_LIKE_ESCAPE}" for _ in keywords_any) + ")")
        where_params.extend(_like_pattern(t) for t in keywords_any)
    for term in keywords_desc:
        clauses.append(f"l.search_desc LIKE ? {_LIKE_ESCAPE}")
        where_params.append(_like_pattern(term))
    for term in keywords_exclude:
        clauses.append(f"l.search_text NOT LIKE ? {_LIKE_ESCAPE}")
        where_params.append(_like_pattern(term))

    city = str(filters.get("city") or "").strip()
    if city:
        clauses.append(f"l.city_norm LIKE ? {_LIKE_ESCAPE}")
        where_params.append(_like_pattern(city))

    psc_prefix = re.sub(r"\D", "", str(filters.get("psc_prefix") or ""))
    if psc_prefix:
        if include_unknown:
            clauses.append(
                "(l.psc LIKE ? OR l.psc_status IS NULL OR l.psc_status <> 'valid')"
            )
        else:
            clauses.append("l.psc LIKE ?")
        where_params.append(f"{psc_prefix}%")

    kraj = filters.get("kraj")
    if kraj == KRAJ_UNKNOWN:
        clauses.append("l.kraj IS NULL")
    elif kraj:
        if include_unknown:
            clauses.append("(l.kraj = ? OR l.kraj IS NULL)")
        else:
            clauses.append("l.kraj = ?")
        where_params.append(kraj)

    max_age_days = filters.get("max_age_days")
    if max_age_days:
        cutoff = (date.today() - timedelta(days=int(max_age_days))).isoformat()
        clauses.append("l.posted_date IS NOT NULL AND l.posted_date >= ?")
        where_params.append(cutoff)

    date_from = filters.get("date_from")
    if date_from:
        clauses.append("l.posted_date >= ?")
        where_params.append(str(date_from))
    first_seen_since = filters.get("first_seen_since")
    if first_seen_since:
        clauses.append("l.first_seen >= ?")
        where_params.append(str(first_seen_since))

    join_sql = " ".join(joins)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return QueryParts(join_sql, where_sql, where_params, keywords_all, keywords_any,
                      keywords_desc)


def count_listings(filters: Any, *, db: Optional[Database] = None) -> int:
    """Count listings matching the filters (same logic as ``search_listings``)."""
    database, owns_db = _open_db(db)
    try:
        parts = _build_query(filters or {})
        row = database.conn.execute(
            f"SELECT COUNT(*) AS c FROM listings l {parts.join_sql}{parts.where_sql}",
            parts.where_params,
        ).fetchone()
        return row["c"]
    finally:
        if owns_db:
            database.close()


def count_listings_split(filters: Any, *, db: Optional[Database] = None) -> tuple[int, int]:
    """Return ``(total, new)`` for the filters in one count query.

    ``new`` counts rows with ``seen = 0``; both exclude hidden rows unless the
    filters ask for them.
    """
    database, owns_db = _open_db(db)
    try:
        parts = _build_query(filters or {})
        row = database.conn.execute(
            f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN l.seen = 0 THEN 1 ELSE 0 END) AS new
              FROM listings l {parts.join_sql}{parts.where_sql}
            """,
            parts.where_params,
        ).fetchone()
        return row["total"], (row["new"] or 0)
    finally:
        if owns_db:
            database.close()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def search_listings(
    filters: Any,
    sort: str = "-first_seen",
    page: int = 1,
    per_page: int = 50,
    *,
    db: Optional[Database] = None,
) -> SearchResult:
    """Return a :class:`SearchResult` for the filters, sorted and paginated.

    Location semantics:

    * ``include_unknown_location`` (default ``True``): keep rows whose region
      is unknown (``kraj IS NULL``).
    * a real ``kraj`` filter matches ``kraj = X`` and, when the flag is on,
      also ``kraj IS NULL``;
    * the special ``kraj = "unknown"`` filter returns only ``kraj IS NULL``;
    * a ``psc_prefix`` filter matches ``psc LIKE prefix%`` and, when the flag
      is on, also every row with an invalid/unknown PSČ
      (``psc_status <> 'valid'``), regardless of the prefix.
    """
    database, owns_db = _open_db(db)
    try:
        filters = filters or {}
        page = max(1, int(page or 1))
        per_page = max(1, min(int(per_page or 50), 500))
        offset = (page - 1) * per_page
        if sort not in SORT_OPTIONS:
            sort = "-first_seen"

        parts = _build_query(filters)
        keywords_all, keywords_any = parts.keywords_all, parts.keywords_any
        score_sql = "0"
        score_params: list[Any] = []

        if sort == "relevance":
            relevance_terms = keywords_all + keywords_any + parts.keywords_desc
            if relevance_terms:
                score_sql = "(0 + " + " + ".join(
                    f"CASE WHEN (l.search_text LIKE ? {_LIKE_ESCAPE} "
                    f"OR l.search_desc LIKE ? {_LIKE_ESCAPE}) THEN 1 ELSE 0 END"
                    for _ in relevance_terms
                ) + ")"
                score_params = [
                    pattern
                    for t in relevance_terms
                    for pattern in (_like_pattern(t), _like_pattern(t))
                ]
            order_by = "score DESC, (l.posted_date IS NULL), l.posted_date DESC, l.id DESC"
        elif sort == "views":
            order_by = "(l.views IS NULL), l.views DESC, l.id DESC"
        else:
            order_by = {
                "price": "(l.price_amount IS NULL), l.price_amount ASC, l.id DESC",
                "-price": "(l.price_amount IS NULL), l.price_amount DESC, l.id DESC",
                "date": "(l.posted_date IS NULL), l.posted_date ASC, l.id DESC",
                "-date": "(l.posted_date IS NULL), l.posted_date DESC, l.id DESC",
                "first_seen": "l.first_seen ASC, l.id DESC",
                "-first_seen": "l.first_seen DESC, l.id DESC",
            }[sort]

        counts = database.conn.execute(
            f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN l.kraj IS NULL THEN 1 ELSE 0 END) AS unknown
              FROM listings l {parts.join_sql}{parts.where_sql}
            """,
            parts.where_params,
        ).fetchone()
        total = counts["total"]
        unknown = counts["unknown"] or 0
        confirmed = total - unknown

        rows = database.conn.execute(
            f"""
            SELECT l.*, {score_sql} AS score, {_EXTRA_COLUMNS}
              FROM listings l {parts.join_sql}{parts.where_sql}
             ORDER BY {order_by}
             LIMIT ? OFFSET ?
            """,
            score_params + parts.where_params + [per_page, offset],
        ).fetchall()

        results = []
        for row in rows:
            item = dict(row)
            count = item.get("price_history_count") or 0
            item["price_change_count"] = max(count - 1, 0)
            results.append(item)
        return SearchResult(results, total, confirmed, unknown)
    finally:
        if owns_db:
            database.close()


def get_category_overview(db: Optional[Database] = None) -> dict[str, dict]:
    """Return ``{category_key: {total, unseen, last_run, label}}`` for scraped categories."""
    database, owns_db = _open_db(db)
    try:
        counts = database.conn.execute(
            """
            SELECT lc.category_key AS category_key,
                   COUNT(*) AS total,
                   SUM(CASE WHEN l.seen = 0 THEN 1 ELSE 0 END) AS unseen
              FROM listing_categories lc
              JOIN listings l ON l.id = lc.listing_id
             GROUP BY lc.category_key
            """
        ).fetchall()
        last_runs = {
            row["category_key"]: row["last_run"]
            for row in database.conn.execute(
                "SELECT category_key, MAX(finished_at) AS last_run FROM scrape_runs "
                "WHERE status = 'completed' GROUP BY category_key"
            )
        }
        estimates = database.all_category_estimates()
        backfill_states = database.all_backfill_states()
    finally:
        if owns_db:
            database.close()

    overview: dict[str, dict] = {}
    for row in counts:
        key = row["category_key"]
        category = get_category(key)
        state = backfill_states.get(key)
        backfill_status = state["status"] if state is not None else None
        estimate = estimates.get(key)
        if state is not None and state["total_estimate"]:
            estimate = state["total_estimate"]
        overview[key] = {
            "total": row["total"],
            "unseen": row["unseen"],
            "last_run": last_runs.get(key),
            "label": category.label if category else key,
            "estimate": estimate,
            "backfill_status": backfill_status,
            "complete": backfill_status == "complete",
        }
    return overview


def get_last_run(db: Database, category_key: str) -> Optional[str]:
    row = db.conn.execute(
        "SELECT MAX(finished_at) AS last_run FROM scrape_runs "
        "WHERE category_key = ? AND status = 'completed'",
        (category_key,),
    ).fetchone()
    return row["last_run"] if row else None
