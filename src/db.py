"""SQLite storage for the bazos.sk scraper.

Schema version history:
  * v1 (Phase 3): listings, listing_categories, price_history, scrape_runs.
  * v2 (Phase 4): listings gains ``search_text`` (normalised title +
    description), ``is_hidden``, ``is_favorite`` and ``kraj``; existing rows
    are backfilled.
  * v3 (Phase 4b): listings gains ``psc_status`` (valid/placeholder/foreign/
    unmatched/missing) and ``kraj_source`` (psc/city/NULL); existing rows are
    reclassified via ``geo.classify_location``.
  * v4 (Phase 5): ``saved_searches`` and ``watched_categories`` tables.
    Existing data is untouched.
  * v5 (Phase 6): ``settings`` (key/value) and ``batch_runs`` (history of
    watched-category batches). Existing data is untouched.
  * v6: listings gains ``search_desc`` (normalised short description only) so
    the UI can search the description independently of the title; existing
    rows are backfilled.
  * v7: ``scrape_runs`` gains ``mode`` (``incremental`` / ``backfill``) and the
    ``backfill_state`` (resumable full-category download) and
    ``category_stats`` (last known site total per category) tables are added.
    Existing data is untouched.
  * v8: index on ``price_history(listing_id)`` so the per-row price-history
    lookups in the results view do not scan the whole table.
  * v9: listings gains ``city_norm`` (normalised city) so the UI can filter by
    city diacritics-insensitively; existing rows are backfilled.

Privacy: only listing data is stored - never seller names, phones or e-mails.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from geo import classify_location
from text import normalize_text

SCHEMA_VERSION = 9

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS listings (
    id                INTEGER PRIMARY KEY,          -- bazos listing id
    url               TEXT    NOT NULL,
    title             TEXT    NOT NULL DEFAULT '',
    short_description TEXT    NOT NULL DEFAULT '',
    price_amount      INTEGER,
    price_type        TEXT    NOT NULL DEFAULT 'unknown',
    price_text        TEXT    NOT NULL DEFAULT '',
    city              TEXT    NOT NULL DEFAULT '',
    psc               TEXT    NOT NULL DEFAULT '',
    posted_date       TEXT,                          -- ISO 'YYYY-MM-DD'
    views             INTEGER,
    is_top            INTEGER NOT NULL DEFAULT 0,
    first_seen        TEXT    NOT NULL,
    last_seen         TEXT    NOT NULL,
    seen              INTEGER NOT NULL DEFAULT 0     -- 0 = new/unreviewed
);

CREATE TABLE IF NOT EXISTS listing_categories (
    listing_id   INTEGER NOT NULL,
    category_key TEXT    NOT NULL,
    PRIMARY KEY (listing_id, category_key),
    FOREIGN KEY (listing_id) REFERENCES listings(id)
);

CREATE TABLE IF NOT EXISTS price_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id   INTEGER NOT NULL,
    price_amount INTEGER,
    price_type   TEXT    NOT NULL,
    price_text   TEXT    NOT NULL,
    recorded_at  TEXT    NOT NULL,
    FOREIGN KEY (listing_id) REFERENCES listings(id)
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    category_key           TEXT    NOT NULL,
    started_at             TEXT    NOT NULL,
    finished_at            TEXT,
    pages_fetched          INTEGER NOT NULL DEFAULT 0,
    listings_seen          INTEGER NOT NULL DEFAULT 0,
    listings_new           INTEGER NOT NULL DEFAULT 0,
    listings_price_changed INTEGER NOT NULL DEFAULT 0,
    stop_reason            TEXT,
    status                 TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_listings_posted_date  ON listings(posted_date);
CREATE INDEX IF NOT EXISTS idx_listings_price_amount ON listings(price_amount);
CREATE INDEX IF NOT EXISTS idx_listings_psc          ON listings(psc);
CREATE INDEX IF NOT EXISTS idx_listings_first_seen   ON listings(first_seen);
CREATE INDEX IF NOT EXISTS idx_listings_seen         ON listings(seen);
CREATE INDEX IF NOT EXISTS idx_lc_category           ON listing_categories(category_key);
CREATE INDEX IF NOT EXISTS idx_runs_category         ON scrape_runs(category_key);
"""

_SCHEMA_V2 = """
ALTER TABLE listings ADD COLUMN search_text   TEXT    NOT NULL DEFAULT '';
ALTER TABLE listings ADD COLUMN is_hidden     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE listings ADD COLUMN is_favorite   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE listings ADD COLUMN kraj          TEXT;

CREATE INDEX IF NOT EXISTS idx_listings_kraj        ON listings(kraj);
CREATE INDEX IF NOT EXISTS idx_listings_is_hidden   ON listings(is_hidden);
CREATE INDEX IF NOT EXISTS idx_listings_is_favorite ON listings(is_favorite);
"""

_SCHEMA_V3 = """
ALTER TABLE listings ADD COLUMN psc_status   TEXT;
ALTER TABLE listings ADD COLUMN kraj_source  TEXT;

CREATE INDEX IF NOT EXISTS idx_listings_psc_status ON listings(psc_status);
"""

_SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS saved_searches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE,
    category_key TEXT,                              -- NULL = all scraped categories
    filters_json TEXT    NOT NULL,
    sort         TEXT    NOT NULL DEFAULT '-first_seen',
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS watched_categories (
    category_key TEXT PRIMARY KEY,
    added_at     TEXT NOT NULL
);
"""

_SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batch_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at         TEXT    NOT NULL,
    finished_at        TEXT,
    trigger            TEXT    NOT NULL DEFAULT 'manual',
    status             TEXT    NOT NULL,
    categories_total   INTEGER NOT NULL DEFAULT 0,
    categories_done    INTEGER NOT NULL DEFAULT 0,
    categories_skipped INTEGER NOT NULL DEFAULT 0,
    new_listings       INTEGER NOT NULL DEFAULT 0,
    error              TEXT
);
"""

_SCHEMA_V6 = """
ALTER TABLE listings ADD COLUMN search_desc TEXT NOT NULL DEFAULT '';
"""

_SCHEMA_V7_COLUMN = """
ALTER TABLE scrape_runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'incremental';
"""

_SCHEMA_V7_TABLES = """
CREATE TABLE IF NOT EXISTS backfill_state (
    category_key   TEXT PRIMARY KEY,
    status         TEXT    NOT NULL,               -- running|paused|complete|blocked
    next_page      INTEGER NOT NULL DEFAULT 1,
    total_estimate INTEGER,
    pages_done     INTEGER NOT NULL DEFAULT 0,
    started_at     TEXT,
    updated_at     TEXT    NOT NULL,
    completed_at   TEXT
);

CREATE TABLE IF NOT EXISTS category_stats (
    category_key   TEXT PRIMARY KEY,
    total_estimate INTEGER,
    updated_at     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_mode ON scrape_runs(mode);
"""

_SCHEMA_V7 = _SCHEMA_V7_COLUMN + _SCHEMA_V7_TABLES

_SCHEMA_V8 = """
CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history(listing_id);
"""

_SCHEMA_V9 = """
ALTER TABLE listings ADD COLUMN city_norm TEXT NOT NULL DEFAULT '';
"""

_MIGRATIONS = {
    1: _SCHEMA_V1, 2: _SCHEMA_V2, 3: _SCHEMA_V3, 4: _SCHEMA_V4, 5: _SCHEMA_V5,
    6: _SCHEMA_V6, 7: _SCHEMA_V7, 8: _SCHEMA_V8, 9: _SCHEMA_V9,
}

_SORTS = {
    "price": "(l.price_amount IS NULL), l.price_amount ASC, l.id DESC",
    "-price": "(l.price_amount IS NULL), l.price_amount DESC, l.id DESC",
    "date": "(l.posted_date IS NULL), l.posted_date ASC, l.id DESC",
    "-date": "(l.posted_date IS NULL), l.posted_date DESC, l.id DESC",
    "first_seen": "l.first_seen ASC, l.id DESC",
    "-first_seen": "l.first_seen DESC, l.id DESC",
}

_TOGGLE_FIELDS = {"is_hidden", "is_favorite"}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _search_text(listing: Any) -> str:
    return normalize_text(f"{listing.title} {listing.short_description}")


def _search_desc(listing: Any) -> str:
    return normalize_text(listing.short_description or "")


def _city_norm(listing: Any) -> str:
    return normalize_text(listing.city or "")


def _location(psc: str | None, city: str | None) -> tuple[str | None, str, str | None]:
    return classify_location(psc, city)


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _migrate(self) -> None:
        cur = self.conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = cur.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        current = row["version"] if row else 0
        for version in sorted(_MIGRATIONS):
            if version > current:
                # ADD COLUMN is not idempotent; skip it if a partially
                # migrated DB already has the column (e.g. version rolled back).
                if version == 6 and self._column_exists("listings", "search_desc"):
                    pass
                elif version == 7 and self._column_exists("scrape_runs", "mode"):
                    cur.executescript(_SCHEMA_V7_TABLES)
                elif version == 9 and self._column_exists("listings", "city_norm"):
                    pass
                else:
                    cur.executescript(_MIGRATIONS[version])
                if version == 2:
                    self._backfill_v2()
                elif version == 3:
                    self._backfill_v3()
                elif version == 6:
                    self._backfill_v6()
                elif version == 9:
                    self._backfill_v9()
                current = version
        if row is None:
            cur.execute("INSERT INTO schema_version (version) VALUES (?)", (current,))
        else:
            cur.execute("UPDATE schema_version SET version = ?", (current,))
        self.conn.commit()

    def _column_exists(self, table: str, column: str) -> bool:
        return any(
            row["name"] == column
            for row in self.conn.execute(f"PRAGMA table_info({table})")
        )

    def _backfill_v2(self) -> None:
        """Fill search_text and kraj for rows that predate schema v2."""
        rows = self.conn.execute(
            "SELECT id, title, short_description, psc, city FROM listings"
        ).fetchall()
        for row in rows:
            kraj = _location(row["psc"], row["city"])[0]
            self.conn.execute(
                "UPDATE listings SET search_text = ?, kraj = ? WHERE id = ?",
                (
                    normalize_text(f"{row['title']} {row['short_description']}"),
                    kraj,
                    row["id"],
                ),
            )

    def _backfill_v3(self) -> None:
        """Reclassify psc_status/kraj/kraj_source for all existing rows."""
        rows = self.conn.execute(
            "SELECT id, psc, city FROM listings"
        ).fetchall()
        for row in rows:
            kraj, psc_status, kraj_source = _location(row["psc"], row["city"])
            self.conn.execute(
                "UPDATE listings SET kraj = ?, psc_status = ?, kraj_source = ? WHERE id = ?",
                (kraj, psc_status, kraj_source, row["id"]),
            )

    def _backfill_v6(self) -> None:
        """Fill search_desc for rows that predate schema v6."""
        rows = self.conn.execute(
            "SELECT id, short_description FROM listings"
        ).fetchall()
        for row in rows:
            self.conn.execute(
                "UPDATE listings SET search_desc = ? WHERE id = ?",
                (normalize_text(row["short_description"] or ""), row["id"]),
            )

    def _backfill_v9(self) -> None:
        """Fill city_norm for rows that predate schema v9."""
        rows = self.conn.execute("SELECT id, city FROM listings").fetchall()
        for row in rows:
            self.conn.execute(
                "UPDATE listings SET city_norm = ? WHERE id = ?",
                (normalize_text(row["city"] or ""), row["id"]),
            )

    def schema_version(self) -> int:
        row = self.conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        return row["version"] if row else 0

    # -- writes ------------------------------------------------------------

    def upsert_listing(self, listing: Any, category_key: str, now: Optional[str] = None) -> tuple[bool, bool]:
        """Insert or refresh a listing. Returns ``(is_new, price_changed)``.

        ``first_seen``, ``seen``, ``is_hidden`` and ``is_favorite`` are never
        modified on re-sight.
        """
        now = now or utcnow_iso()
        cur = self.conn.cursor()
        existing = cur.execute(
            "SELECT price_amount, price_type FROM listings WHERE id = ?", (listing.id,)
        ).fetchone()

        is_new = existing is None
        price_changed = False
        search_text = _search_text(listing)
        search_desc = _search_desc(listing)
        city_norm = _city_norm(listing)
        kraj, psc_status, kraj_source = _location(listing.psc, listing.city)

        if is_new:
            cur.execute(
                """
                INSERT INTO listings
                    (id, url, title, short_description, price_amount, price_type,
                     price_text, city, psc, posted_date, views, is_top,
                     first_seen, last_seen, seen, search_text, search_desc,
                     city_norm, kraj, psc_status, kraj_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                """,
                (*self._listing_values(listing), now, now, search_text, search_desc,
                 city_norm, kraj, psc_status, kraj_source),
            )
            self._insert_price_history(listing, now)
        else:
            price_changed = (
                existing["price_amount"], existing["price_type"]
            ) != (listing.price_amount, listing.price_type)
            cur.execute(
                """
                UPDATE listings
                   SET last_seen = ?, views = ?, title = ?, short_description = ?,
                       price_amount = ?, price_type = ?, price_text = ?, is_top = ?,
                       search_text = ?, search_desc = ?, city_norm = ?, kraj = ?,
                       psc_status = ?, kraj_source = ?
                 WHERE id = ?
                """,
                (
                    now,
                    listing.views,
                    listing.title,
                    listing.short_description,
                    listing.price_amount,
                    listing.price_type,
                    listing.price_text,
                    int(bool(listing.is_top)),
                    search_text,
                    search_desc,
                    city_norm,
                    kraj,
                    psc_status,
                    kraj_source,
                    listing.id,
                ),
            )
            if price_changed:
                self._insert_price_history(listing, now)

        cur.execute(
            "INSERT OR IGNORE INTO listing_categories (listing_id, category_key) VALUES (?, ?)",
            (listing.id, category_key),
        )
        self.conn.commit()
        return is_new, price_changed

    @staticmethod
    def _listing_values(listing: Any) -> tuple:
        return (
            listing.id,
            listing.url,
            listing.title,
            listing.short_description,
            listing.price_amount,
            listing.price_type,
            listing.price_text,
            listing.city,
            listing.psc,
            listing.posted_date.isoformat() if listing.posted_date else None,
            listing.views,
            int(bool(listing.is_top)),
        )

    def _insert_price_history(self, listing: Any, now: str) -> None:
        self.conn.execute(
            """
            INSERT INTO price_history
                (listing_id, price_amount, price_type, price_text, recorded_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (listing.id, listing.price_amount, listing.price_type, listing.price_text, now),
        )

    def set_seen(self, listing_id: int, value: int = 1) -> int:
        cur = self.conn.execute(
            "UPDATE listings SET seen = ? WHERE id = ?", (int(bool(value)), listing_id)
        )
        self.conn.commit()
        return cur.rowcount

    def set_seen_many(self, ids: Iterable[int]) -> int:
        ids = list(ids)
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        cur = self.conn.execute(
            f"UPDATE listings SET seen = 1 WHERE id IN ({placeholders})", ids
        )
        self.conn.commit()
        return cur.rowcount

    def toggle_field(self, listing_id: int, field: str) -> Optional[int]:
        """Toggle ``is_hidden`` / ``is_favorite``; returns the new value or None."""
        if field not in _TOGGLE_FIELDS:
            raise ValueError(f"cannot toggle field {field!r}")
        row = self.conn.execute(
            f"SELECT {field} AS value FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        if row is None:
            return None
        new_value = 0 if row["value"] else 1
        self.conn.execute(
            f"UPDATE listings SET {field} = ? WHERE id = ?", (new_value, listing_id)
        )
        self.conn.commit()
        return new_value

    # -- scrape runs -------------------------------------------------------

    def start_run(
        self, category_key: str, now: Optional[str] = None, mode: str = "incremental"
    ) -> int:
        now = now or utcnow_iso()
        cur = self.conn.execute(
            "INSERT INTO scrape_runs (category_key, started_at, status, mode) "
            "VALUES (?, ?, 'running', ?)",
            (category_key, now, mode),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        stop_reason: str,
        pages_fetched: int,
        listings_seen: int,
        listings_new: int,
        listings_price_changed: int,
        now: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE scrape_runs
               SET finished_at = ?, pages_fetched = ?, listings_seen = ?,
                   listings_new = ?, listings_price_changed = ?,
                   stop_reason = ?, status = ?
             WHERE id = ?
            """,
            (
                now or utcnow_iso(),
                pages_fetched,
                listings_seen,
                listings_new,
                listings_price_changed,
                stop_reason,
                status,
                run_id,
            ),
        )
        self.conn.commit()

    def get_run(self, run_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM scrape_runs WHERE id = ?", (run_id,)).fetchone()

    # -- reads -------------------------------------------------------------

    def all_listing_ids(self) -> set[int]:
        return {row["id"] for row in self.conn.execute("SELECT id FROM listings")}

    def existing_ids(self, ids: Iterable[int]) -> set[int]:
        ids = list(ids)
        if not ids:
            return set()
        found: set[int] = set()
        chunk = 500
        for start in range(0, len(ids), chunk):
            part = ids[start:start + chunk]
            placeholders = ",".join("?" * len(part))
            found.update(
                row["id"]
                for row in self.conn.execute(
                    f"SELECT id FROM listings WHERE id IN ({placeholders})", part
                )
            )
        return found

    def has_category_listings(self, category_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM listing_categories WHERE category_key = ? LIMIT 1", (category_key,)
        ).fetchone()
        return row is not None

    def get_listing(self, listing_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()

    def query_listings(
        self,
        category_key: str,
        *,
        new_only: bool = False,
        sort: str = "-first_seen",
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        psc_prefix: Optional[str] = None,
        limit: int = 50,
    ) -> list[sqlite3.Row]:
        order_by = _SORTS.get(sort, _SORTS["-first_seen"])
        clauses = ["lc.category_key = ?"]
        params: list[Any] = [category_key]
        if new_only:
            clauses.append("l.seen = 0")
        if min_price is not None:
            clauses.append("l.price_amount >= ?")
            params.append(min_price)
        if max_price is not None:
            clauses.append("l.price_amount <= ?")
            params.append(max_price)
        if psc_prefix:
            clauses.append("l.psc LIKE ?")
            params.append(f"{psc_prefix}%")
        params.append(limit)
        sql = f"""
            SELECT l.id, l.url, l.title, l.price_amount, l.price_type, l.price_text,
                   l.city, l.psc, l.posted_date, l.views, l.is_top, l.first_seen, l.seen
              FROM listings l
              JOIN listing_categories lc ON lc.listing_id = l.id
             WHERE {' AND '.join(clauses)}
             ORDER BY {order_by}
             LIMIT ?
        """
        return list(self.conn.execute(sql, params))

    def mark_seen(self, category_key: Optional[str] = None) -> int:
        if category_key is None:
            cur = self.conn.execute("UPDATE listings SET seen = 1")
        else:
            cur = self.conn.execute(
                """
                UPDATE listings SET seen = 1
                 WHERE id IN (SELECT listing_id FROM listing_categories WHERE category_key = ?)
                """,
                (category_key,),
            )
        self.conn.commit()
        return cur.rowcount

    def refresh_kraj(self) -> int:
        """Recompute ``kraj``/``psc_status``/``kraj_source`` for every listing.

        Returns the number of rows that actually changed.
        """
        changed = 0
        rows = self.conn.execute(
            "SELECT id, psc, city, kraj, psc_status, kraj_source FROM listings"
        ).fetchall()
        for row in rows:
            new_kraj, new_status, new_source = _location(row["psc"], row["city"])
            if (row["kraj"], row["psc_status"], row["kraj_source"]) != (
                new_kraj,
                new_status,
                new_source,
            ):
                self.conn.execute(
                    "UPDATE listings SET kraj = ?, psc_status = ?, kraj_source = ? WHERE id = ?",
                    (new_kraj, new_status, new_source, row["id"]),
                )
                changed += 1
        self.conn.commit()
        return changed

    # -- backfill state ----------------------------------------------------

    def get_backfill_state(self, category_key: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM backfill_state WHERE category_key = ?", (category_key,)
        ).fetchone()

    def all_backfill_states(self) -> dict[str, sqlite3.Row]:
        return {
            row["category_key"]: row
            for row in self.conn.execute("SELECT * FROM backfill_state")
        }

    def save_backfill_state(
        self,
        category_key: str,
        *,
        status: str,
        next_page: int,
        pages_done: int,
        total_estimate: Optional[int] = None,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        now: Optional[str] = None,
    ) -> None:
        """Insert or update the resumable state for a category backfill."""
        now = now or utcnow_iso()
        existing = self.get_backfill_state(category_key)
        if existing is None:
            self.conn.execute(
                """
                INSERT INTO backfill_state
                    (category_key, status, next_page, total_estimate, pages_done,
                     started_at, updated_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    category_key, status, int(next_page), total_estimate,
                    int(pages_done), started_at or now, now, completed_at,
                ),
            )
        else:
            self.conn.execute(
                """
                UPDATE backfill_state
                   SET status = ?, next_page = ?, total_estimate = ?,
                       pages_done = ?, started_at = COALESCE(?, started_at),
                       updated_at = ?, completed_at = ?
                 WHERE category_key = ?
                """,
                (
                    status, int(next_page), total_estimate, int(pages_done),
                    started_at, now, completed_at, category_key,
                ),
            )
        self.conn.commit()

    # -- category stats ----------------------------------------------------

    def set_category_estimate(
        self, category_key: str, total: Optional[int], now: Optional[str] = None
    ) -> None:
        """Remember the site's last known total count for a category."""
        if total is None:
            return
        self.conn.execute(
            """
            INSERT INTO category_stats (category_key, total_estimate, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(category_key) DO UPDATE SET
                total_estimate = excluded.total_estimate,
                updated_at = excluded.updated_at
            """,
            (category_key, int(total), now or utcnow_iso()),
        )
        self.conn.commit()

    def get_category_estimate(self, category_key: str) -> Optional[int]:
        row = self.conn.execute(
            "SELECT total_estimate FROM category_stats WHERE category_key = ?",
            (category_key,),
        ).fetchone()
        return row["total_estimate"] if row else None

    def all_category_estimates(self) -> dict[str, Optional[int]]:
        return {
            row["category_key"]: row["total_estimate"]
            for row in self.conn.execute(
                "SELECT category_key, total_estimate FROM category_stats"
            )
        }

    # -- settings ----------------------------------------------------------

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, "" if value is None else str(value)),
        )
        self.conn.commit()

    def get_settings(self) -> dict[str, str]:
        return {
            row["key"]: row["value"]
            for row in self.conn.execute("SELECT key, value FROM settings")
        }

    # -- batch runs --------------------------------------------------------

    def start_batch_run(
        self, trigger: str, categories_total: int, now: Optional[str] = None
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO batch_runs (started_at, trigger, status, categories_total) "
            "VALUES (?, ?, 'running', ?)",
            (now or utcnow_iso(), trigger, categories_total),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_batch_run(
        self,
        run_id: int,
        *,
        status: str,
        categories_done: int,
        categories_skipped: int,
        new_listings: int,
        error: Optional[str] = None,
        now: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE batch_runs
               SET finished_at = ?, status = ?, categories_done = ?,
                   categories_skipped = ?, new_listings = ?, error = ?
             WHERE id = ?
            """,
            (
                now or utcnow_iso(),
                status,
                categories_done,
                categories_skipped,
                new_listings,
                error,
                run_id,
            ),
        )
        self.conn.commit()

    def last_batch_run(self) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM batch_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def stats(self) -> list[dict]:
        counts = {
            row["category_key"]: row
            for row in self.conn.execute(
                """
                SELECT lc.category_key AS category_key,
                       COUNT(*) AS total,
                       SUM(CASE WHEN l.seen = 0 THEN 1 ELSE 0 END) AS unseen
                  FROM listing_categories lc
                  JOIN listings l ON l.id = lc.listing_id
                 GROUP BY lc.category_key
                """
            )
        }
        last_runs = {
            row["category_key"]: row["last_run"]
            for row in self.conn.execute(
                "SELECT category_key, MAX(finished_at) AS last_run FROM scrape_runs GROUP BY category_key"
            )
        }
        keys = sorted(set(counts) | set(last_runs))
        return [
            {
                "category_key": key,
                "total": counts[key]["total"] if key in counts else 0,
                "unseen": counts[key]["unseen"] if key in counts else 0,
                "last_run": last_runs.get(key),
            }
            for key in keys
        ]
