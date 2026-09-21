"""Saved searches and watched categories (Phase 5).

Repository layer over the ``saved_searches`` and ``watched_categories`` tables.
Counts are computed with the shared :func:`queries.count_listings_split`
(one count query per saved search) - no SQL is duplicated here.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from categories import get_category
from config import load_config, resolve_db_path
from db import Database, utcnow_iso
from queries import (
    SORT_OPTIONS,
    count_listings_split,
    filters_from_dict,
    saved_search_filters,
)

logger = logging.getLogger(__name__)

NAME_MIN, NAME_MAX = 1, 60
ALL_CATEGORIES_LABEL = "Všetky kategórie"


class SavedSearchError(ValueError):
    """Validation error surfaced to the UI as HTTP 400."""


def _open(db: Optional[Database]) -> tuple[Database, bool]:
    if db is not None:
        return db, False
    return Database(resolve_db_path(load_config())), True


def _validate_name(name: Any, database: Database, exclude_id: Optional[int] = None) -> str:
    if not isinstance(name, str):
        raise SavedSearchError("názov musí byť text")
    name = name.strip()
    if not (NAME_MIN <= len(name) <= NAME_MAX):
        raise SavedSearchError(f"názov musí mať {NAME_MIN} až {NAME_MAX} znakov")
    row = database.conn.execute(
        "SELECT id FROM saved_searches WHERE name = ?", (name,)
    ).fetchone()
    if row is not None and row["id"] != exclude_id:
        raise SavedSearchError("hľadanie s týmto názvom už existuje")
    return name


def _validate_category(category_key: Any) -> Optional[str]:
    if category_key in (None, ""):
        return None
    if not isinstance(category_key, str) or get_category(category_key) is None:
        raise SavedSearchError(f"neznáma kategória: {category_key!r}")
    return category_key


def _validate_sort(sort: Any) -> str:
    return sort if sort in SORT_OPTIONS else "-first_seen"


def _row_to_dict(row: Any) -> dict[str, Any]:
    try:
        filters = json.loads(row["filters_json"] or "{}")
    except (ValueError, TypeError):
        logger.warning("saved search %s: invalid filters_json, using empty filters", row["id"])
        filters = {}
    if not isinstance(filters, dict):
        filters = {}
    category_key = row["category_key"]
    return {
        "id": row["id"],
        "name": row["name"],
        "category_key": category_key,
        "category_label": (
            get_category(category_key).label if category_key else ALL_CATEGORIES_LABEL
        ),
        "filters": filters,
        "sort": row["sort"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ---------------------------------------------------------------------------
# saved searches
# ---------------------------------------------------------------------------

def get_saved_search(search_id: int, *, db: Optional[Database] = None) -> Optional[dict]:
    database, owns_db = _open(db)
    try:
        row = database.conn.execute(
            "SELECT * FROM saved_searches WHERE id = ?", (int(search_id),)
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        if owns_db:
            database.close()


def list_saved_searches(*, db: Optional[Database] = None) -> list[dict]:
    database, owns_db = _open(db)
    try:
        rows = database.conn.execute(
            "SELECT * FROM saved_searches ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        if owns_db:
            database.close()


def list_saved_searches_with_counts(*, db: Optional[Database] = None) -> list[dict]:
    """Each saved search plus ``total`` and ``new_count`` matching listings."""
    database, owns_db = _open(db)
    try:
        items = list_saved_searches(db=database)
        for item in items:
            filters = filters_from_dict(item["filters"])
            if item["category_key"]:
                filters["category_key"] = item["category_key"]
            filters["include_hidden"] = False
            total, new_count = count_listings_split(filters, db=database)
            item["total"] = total
            item["new_count"] = new_count
        return items
    finally:
        if owns_db:
            database.close()


def create_saved_search(
    name: Any,
    category_key: Any,
    filters: Any,
    sort: Any = "-first_seen",
    *,
    db: Optional[Database] = None,
    config: Optional[dict] = None,
) -> dict:
    database, owns_db = _open(db)
    try:
        config = config or load_config()
        limit = int(config.get("max_saved_searches", 50))
        count = database.conn.execute("SELECT COUNT(*) AS c FROM saved_searches").fetchone()["c"]
        if count >= limit:
            raise SavedSearchError(f"limit uložených hľadaní ({limit}) dosiahnutý")
        name = _validate_name(name, database)
        category_key = _validate_category(category_key)
        sort = _validate_sort(sort)
        payload = saved_search_filters(filters_from_dict(filters or {}))
        now = utcnow_iso()
        cur = database.conn.execute(
            """
            INSERT INTO saved_searches
                (name, category_key, filters_json, sort, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, category_key, json.dumps(payload, ensure_ascii=False), sort, now, now),
        )
        database.conn.commit()
        return get_saved_search(int(cur.lastrowid), db=database)
    finally:
        if owns_db:
            database.close()


def update_saved_search(
    search_id: int,
    *,
    category_key: Any,
    filters: Any,
    sort: Any = "-first_seen",
    db: Optional[Database] = None,
) -> Optional[dict]:
    """Overwrite a saved search's filters/category/sort (name unchanged)."""
    database, owns_db = _open(db)
    try:
        if get_saved_search(search_id, db=database) is None:
            return None
        category_key = _validate_category(category_key)
        sort = _validate_sort(sort)
        payload = saved_search_filters(filters_from_dict(filters or {}))
        database.conn.execute(
            "UPDATE saved_searches SET category_key = ?, filters_json = ?, sort = ?, updated_at = ? WHERE id = ?",
            (category_key, json.dumps(payload, ensure_ascii=False), sort, utcnow_iso(), int(search_id)),
        )
        database.conn.commit()
        return get_saved_search(search_id, db=database)
    finally:
        if owns_db:
            database.close()


def rename_saved_search(
    search_id: int, name: Any, *, db: Optional[Database] = None
) -> Optional[dict]:
    database, owns_db = _open(db)
    try:
        if get_saved_search(search_id, db=database) is None:
            return None
        name = _validate_name(name, database, exclude_id=int(search_id))
        database.conn.execute(
            "UPDATE saved_searches SET name = ?, updated_at = ? WHERE id = ?",
            (name, utcnow_iso(), int(search_id)),
        )
        database.conn.commit()
        return get_saved_search(search_id, db=database)
    finally:
        if owns_db:
            database.close()


def delete_saved_search(search_id: int, *, db: Optional[Database] = None) -> int:
    database, owns_db = _open(db)
    try:
        cur = database.conn.execute(
            "DELETE FROM saved_searches WHERE id = ?", (int(search_id),)
        )
        database.conn.commit()
        return cur.rowcount
    finally:
        if owns_db:
            database.close()


# ---------------------------------------------------------------------------
# watched categories
# ---------------------------------------------------------------------------

def watch_category(
    category_key: Any,
    *,
    db: Optional[Database] = None,
    config: Optional[dict] = None,
) -> dict:
    database, owns_db = _open(db)
    try:
        category = get_category(category_key) if isinstance(category_key, str) else None
        if category is None:
            raise SavedSearchError(f"neznáma kategória: {category_key!r}")
        config = config or load_config()
        limit = int(config.get("max_watched_categories", 40))
        existing = database.conn.execute(
            "SELECT 1 FROM watched_categories WHERE category_key = ?", (category.key,)
        ).fetchone()
        if existing is None:
            count = database.conn.execute(
                "SELECT COUNT(*) AS c FROM watched_categories"
            ).fetchone()["c"]
            if count >= limit:
                raise SavedSearchError(f"limit sledovaných kategórií ({limit}) dosiahnutý")
            database.conn.execute(
                "INSERT INTO watched_categories (category_key, added_at) VALUES (?, ?)",
                (category.key, utcnow_iso()),
            )
            database.conn.commit()
        return {"category_key": category.key, "label": category.label}
    finally:
        if owns_db:
            database.close()


def unwatch_category(category_key: str, *, db: Optional[Database] = None) -> int:
    database, owns_db = _open(db)
    try:
        cur = database.conn.execute(
            "DELETE FROM watched_categories WHERE category_key = ?", (str(category_key),)
        )
        database.conn.commit()
        return cur.rowcount
    finally:
        if owns_db:
            database.close()


def list_watched(*, db: Optional[Database] = None) -> list[dict]:
    """Watched categories with counts and the last run's time/stop_reason."""
    database, owns_db = _open(db)
    try:
        counts = database.conn.execute(
            """
            SELECT w.category_key AS category_key, w.added_at AS added_at,
                   COUNT(l.id) AS total,
                   SUM(CASE WHEN l.seen = 0 THEN 1 ELSE 0 END) AS new
              FROM watched_categories w
              LEFT JOIN listing_categories lc ON lc.category_key = w.category_key
              LEFT JOIN listings l ON l.id = lc.listing_id AND l.is_hidden = 0
             GROUP BY w.category_key, w.added_at
             ORDER BY w.added_at, w.category_key
            """
        ).fetchall()
        runs = {
            row["category_key"]: row
            for row in database.conn.execute(
                """
                SELECT r.category_key AS category_key,
                       MAX(CASE WHEN r.status = 'completed' THEN r.finished_at END) AS last_run,
                       (SELECT r2.stop_reason FROM scrape_runs r2
                         WHERE r2.category_key = r.category_key
                         ORDER BY r2.id DESC LIMIT 1) AS last_stop_reason
                  FROM scrape_runs r
                 GROUP BY r.category_key
                """
            )
        }
        result: list[dict] = []
        for row in counts:
            key = row["category_key"]
            category = get_category(key)
            run = runs.get(key)
            result.append({
                "category_key": key,
                "label": category.label if category else key,
                "total": row["total"],
                "new": row["new"] or 0,
                "added_at": row["added_at"],
                "last_run": run["last_run"] if run else None,
                "last_stop_reason": run["last_stop_reason"] if run else None,
            })
        return result
    finally:
        if owns_db:
            database.close()


def overlap_warnings(
    *, db: Optional[Database] = None, watched_items: Optional[list[dict]] = None
) -> list[dict]:
    """Pairs where a watched section and one of its watched subcategories overlap.

    ``watched_items`` lets the caller pass an already-computed
    :func:`list_watched` result to avoid running the same query twice.
    """
    if watched_items is None:
        watched_items = list_watched(db=db)
    watched = [item["category_key"] for item in watched_items]
    warnings: list[dict] = []
    for key in watched:
        category = get_category(key)
        if category is None or category.parent_key is not None:
            continue
        for other in watched:
            if other == key:
                continue
            sub = get_category(other)
            if sub is not None and sub.parent_key == key:
                warnings.append({
                    "section": key,
                    "subcategory": other,
                    "message": f"{key} aj {other} sa prekrývajú - zbytočné sťahovanie",
                })
    return warnings
