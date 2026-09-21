"""Flask web UI for the bazos.sk scraper.

Binds only to 127.0.0.1, talks only to our SQLite DB and calls the existing
``scrape_category()``. No external CDNs, no JS frameworks, no build step.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlencode

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from flask import Flask, jsonify, render_template, request  # noqa: E402

import backfill as backfill_repo  # noqa: E402
from categories import get_category, load_categories  # noqa: E402
from config import ROOT, load_config, resolve_db_path  # noqa: E402
from db import Database  # noqa: E402
from geo import KRAJ_UNKNOWN, all_kraje  # noqa: E402
from jobs import ScrapeCoordinator  # noqa: E402
from queries import (  # noqa: E402
    PRICE_TYPES,
    SORT_OPTIONS,
    filters_from_dict,
    filters_to_dict,
    get_category_overview,
    get_last_run,
    search_listings,
)
import saved as saved_repo  # noqa: E402
from scheduler import Scheduler, SchedulerError, SingleInstanceLock  # noqa: E402
from scraper import scrape_category  # noqa: E402
from text import highlight  # noqa: E402

PER_PAGE = 50
KRAJE = all_kraje()

PRICE_TYPE_LABELS = {
    "fixed": "Pevná cena",
    "negotiable": "Dohodou",
    "free": "Zadarmo",
    "in_text": "V texte",
    "offer": "Ponúknite",
}


def _parse_filters(args: Any) -> dict[str, Any]:
    """Parse the request query string into the canonical filter set."""
    return filters_from_dict(args)


def _saved_href(item: dict[str, Any]) -> str:
    """URL that opens a saved search (never forces only_new)."""
    query = dict(item.get("filters") or {})
    if item.get("category_key"):
        query["category"] = item["category_key"]
    query["sort"] = item.get("sort") or "-first_seen"
    return "/?" + urlencode(query, doseq=True)


def create_app(
    config: Optional[dict[str, Any]] = None,
    scrape_func: Optional[Callable[..., Any]] = None,
    backfill_func: Optional[Callable[..., Any]] = None,
    *,
    start_scheduler: bool = False,
    scheduler: Optional[Scheduler] = None,
) -> Flask:
    config = config or load_config()
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["APP_CONFIG"] = config
    app.jinja_env.filters["highlight"] = highlight

    coordinator = ScrapeCoordinator(
        config, scrape_func or scrape_category, backfill_func or backfill_repo.backfill_category
    )
    app.extensions["coordinator"] = coordinator

    scheduler = scheduler or Scheduler(config, coordinator)
    app.extensions["scheduler"] = scheduler
    if start_scheduler:
        scheduler.start()

    def open_db() -> Database:
        return Database(resolve_db_path(app.config["APP_CONFIG"]))

    def json_body() -> Optional[dict]:
        if not request.is_json:
            return None
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else {}

    def conflict_response():
        activity = coordinator.current_activity() or "iná úloha"
        return jsonify(
            error=f"Aktuálne beží {activity}. Skúste to neskôr.", activity=activity
        ), 409

    def validation_error(exc: Exception):
        return jsonify(error=str(exc)), 400

    # -- favicon -----------------------------------------------------------

    @app.get("/favicon.ico")
    def favicon():
        # Browsers request /favicon.ico directly; serve the PNG icon.
        return app.send_static_file("icon.png")

    # -- main page ---------------------------------------------------------

    @app.get("/")
    def index():
        filters = _parse_filters(request.args)
        if "days" not in request.args:
            # Default age window from config; ?days=0 turns it off.
            filters["max_age_days"] = int(config.get("max_listing_age_days", 10))
        sort = request.args.get("sort") or "-first_seen"
        if sort not in SORT_OPTIONS:
            sort = "-first_seen"
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1

        database = open_db()
        try:
            result = search_listings(filters, sort, page, PER_PAGE, db=database)
            overview = get_category_overview(db=database)
            last_run = (
                get_last_run(database, filters["category_key"])
                if filters["category_key"] else None
            )
            saved_searches = saved_repo.list_saved_searches_with_counts(db=database)
            watched = saved_repo.list_watched(db=database)
            overlaps = saved_repo.overlap_warnings(db=database, watched_items=watched)
            coverage = (
                backfill_repo.category_coverage(filters["category_key"], db=database)
                if filters["category_key"] else None
            )
        finally:
            database.close()

        for item in saved_searches:
            item["href"] = _saved_href(item)

        total = result.total
        highlight_terms = (
            filters["keywords_all"] + filters["keywords_any"] + filters["keywords_desc"]
        )
        catalog = load_categories()
        catalog_json = [
            {"key": c.key, "label": c.label, "parent": c.parent_key} for c in catalog
        ]
        query = filters_to_dict(filters)
        page_query = urlencode({**query, "sort": sort}, doseq=True)
        sort_query = urlencode(query, doseq=True)
        current_category = (
            get_category(filters["category_key"]) if filters["category_key"] else None
        )
        return render_template(
            "index.html",
            catalog=catalog,
            catalog_json=catalog_json,
            current_category=current_category,
            current_label=current_category.label if current_category else "Všetky stiahnuté kategórie",
            overview=overview,
            overview_json=overview,
            rows=result.rows,
            total=result.total,
            confirmed=result.confirmed,
            unknown=result.unknown,
            page=page,
            per_page=PER_PAGE,
            pages=max(1, (total + PER_PAGE - 1) // PER_PAGE),
            sort=sort,
            filters=filters,
            kraje=KRAJE,
            kraj_unknown=KRAJ_UNKNOWN,
            price_types=PRICE_TYPES,
            price_type_labels=PRICE_TYPE_LABELS,
            highlight_terms=highlight_terms,
            last_run=last_run,
            page_query=page_query,
            sort_query=sort_query,
            saved_searches=saved_searches,
            watched=watched,
            overlaps=overlaps,
            batch=coordinator.batch_status(),
            max_saved_searches=int(config.get("max_saved_searches", 50)),
            max_watched_categories=int(config.get("max_watched_categories", 40)),
            current_filters=query,
            scheduler_state=scheduler.state(),
            coverage=coverage,
            backfill_max_pages_per_run=int(
                config.get("backfill_max_pages_per_run", 1000)
            ),
            request_delay_seconds=float(config.get("request_delay_seconds", 1.5)),
            max_listing_age_days=int(config.get("max_listing_age_days", 10)),
        )

    # -- single scrape API -------------------------------------------------

    @app.post("/api/scrape")
    def api_scrape():
        body = json_body()
        if body is None:
            return jsonify(error="JSON body required"), 415
        category_key = body.get("category_key")
        if not category_key or get_category(category_key) is None:
            return jsonify(error="invalid category_key"), 400
        if not coordinator.start_single(category_key, bool(body.get("full"))):
            return conflict_response()
        return jsonify(status=coordinator.single_status()), 202

    @app.get("/api/scrape/status")
    def api_scrape_status():
        return jsonify(coordinator.single_status())

    # -- full-category backfill API ---------------------------------------

    @app.post("/api/backfill/start")
    def api_backfill_start():
        body = json_body()
        if body is None:
            return jsonify(error="JSON body required"), 415
        category_key = body.get("category_key")
        if not category_key or get_category(category_key) is None:
            return jsonify(error="invalid category_key"), 400
        if not coordinator.start_backfill(category_key, bool(body.get("restart"))):
            return conflict_response()
        return jsonify(status=coordinator.backfill_status()), 202

    @app.get("/api/backfill/status")
    def api_backfill_status():
        return jsonify(coordinator.backfill_status())

    @app.post("/api/backfill/cancel")
    def api_backfill_cancel():
        if json_body() is None:
            return jsonify(error="JSON body required"), 415
        cancelled = coordinator.cancel_backfill()
        return jsonify(cancelled=cancelled, status=coordinator.backfill_status())

    @app.get("/api/coverage")
    def api_coverage():
        category_key = request.args.get("category_key")
        if not category_key or get_category(category_key) is None:
            return jsonify(error="invalid category_key"), 400
        database = open_db()
        try:
            coverage = backfill_repo.category_coverage(category_key, db=database)
        finally:
            database.close()
        return jsonify(coverage=coverage)

    # -- batch API ---------------------------------------------------------

    @app.post("/api/batch/start")
    def api_batch_start():
        body = json_body()
        if body is None:
            return jsonify(error="JSON body required"), 415
        database = open_db()
        try:
            if not saved_repo.list_watched(db=database):
                return jsonify(error="Nemáte žiadne sledované kategórie."), 400
        finally:
            database.close()
        if not coordinator.start_batch(bool(body.get("force")), bool(body.get("deep"))):
            return conflict_response()
        return jsonify(status=coordinator.batch_status()), 202

    @app.post("/api/batch/cancel")
    def api_batch_cancel():
        if json_body() is None:
            return jsonify(error="JSON body required"), 415
        cancelled = coordinator.cancel_batch()
        return jsonify(cancelled=cancelled, status=coordinator.batch_status())

    @app.get("/api/batch/status")
    def api_batch_status():
        return jsonify(coordinator.batch_status())

    # -- scheduler API -----------------------------------------------------

    @app.get("/api/scheduler")
    def api_scheduler_state():
        return jsonify(scheduler.state())

    @app.post("/api/scheduler/enable")
    def api_scheduler_enable():
        if json_body() is None:
            return jsonify(error="JSON body required"), 415
        scheduler.enable()
        return jsonify(scheduler.state())

    @app.post("/api/scheduler/disable")
    def api_scheduler_disable():
        if json_body() is None:
            return jsonify(error="JSON body required"), 415
        scheduler.disable("manual")
        return jsonify(scheduler.state())

    @app.post("/api/scheduler/interval")
    def api_scheduler_interval():
        body = json_body()
        if body is None:
            return jsonify(error="JSON body required"), 415
        try:
            scheduler.set_interval(body.get("minutes"))
        except SchedulerError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(scheduler.state())

    @app.post("/api/scheduler/run-now")
    def api_scheduler_run_now():
        if json_body() is None:
            return jsonify(error="JSON body required"), 415
        if not coordinator.start_batch(force=False, deep=False, trigger="manual"):
            return conflict_response()
        return jsonify(status=coordinator.batch_status()), 202

    @app.get("/api/new-count")
    def api_new_count():
        database = open_db()
        try:
            watched = saved_repo.list_watched(db=database)
            keys = [item["category_key"] for item in watched]
            if not keys:
                total_new = 0
            else:
                placeholders = ",".join("?" * len(keys))
                total_new = database.conn.execute(
                    f"""
                    SELECT COUNT(DISTINCT l.id) AS c
                      FROM listings l
                      JOIN listing_categories lc ON lc.listing_id = l.id
                     WHERE lc.category_key IN ({placeholders})
                       AND l.seen = 0 AND l.is_hidden = 0
                    """,
                    keys,
                ).fetchone()["c"]
        finally:
            database.close()
        return jsonify(total_new=total_new)

    # -- saved searches API ------------------------------------------------

    @app.get("/api/saved-searches")
    def api_saved_list():
        database = open_db()
        try:
            items = saved_repo.list_saved_searches_with_counts(db=database)
        finally:
            database.close()
        for item in items:
            item["href"] = _saved_href(item)
        return jsonify(saved_searches=items)

    @app.post("/api/saved-searches")
    def api_saved_create():
        body = json_body()
        if body is None:
            return jsonify(error="JSON body required"), 415
        database = open_db()
        try:
            item = saved_repo.create_saved_search(
                body.get("name"), body.get("category_key"), body.get("filters"),
                body.get("sort", "-first_seen"), db=database,
                config=app.config["APP_CONFIG"],
            )
        except saved_repo.SavedSearchError as exc:
            return validation_error(exc)
        finally:
            database.close()
        item["href"] = _saved_href(item)
        return jsonify(saved_search=item), 201

    @app.put("/api/saved-searches/<int:search_id>")
    def api_saved_update(search_id: int):
        body = json_body()
        if body is None:
            return jsonify(error="JSON body required"), 415
        database = open_db()
        try:
            item = saved_repo.get_saved_search(search_id, db=database)
            if item is None:
                return jsonify(error="Hľadanie sa nenašlo."), 404
            try:
                if "name" in body:
                    item = saved_repo.rename_saved_search(search_id, body.get("name"), db=database)
                if any(key in body for key in ("filters", "sort", "category_key")):
                    item = saved_repo.update_saved_search(
                        search_id,
                        category_key=body.get("category_key", item["category_key"]),
                        filters=body.get("filters", item["filters"]),
                        sort=body.get("sort", item["sort"]),
                        db=database,
                    )
            except saved_repo.SavedSearchError as exc:
                return validation_error(exc)
        finally:
            database.close()
        item["href"] = _saved_href(item)
        return jsonify(saved_search=item)

    @app.delete("/api/saved-searches/<int:search_id>")
    def api_saved_delete(search_id: int):
        database = open_db()
        try:
            deleted = saved_repo.delete_saved_search(search_id, db=database)
        finally:
            database.close()
        if not deleted:
            return jsonify(error="Hľadanie sa nenašlo."), 404
        return jsonify(deleted=deleted)

    # -- watched categories API --------------------------------------------

    @app.get("/api/watched")
    def api_watched_list():
        database = open_db()
        try:
            watched = saved_repo.list_watched(db=database)
            overlaps = saved_repo.overlap_warnings(db=database)
        finally:
            database.close()
        return jsonify(watched=watched, overlaps=overlaps)

    @app.post("/api/watched")
    def api_watched_add():
        body = json_body()
        if body is None:
            return jsonify(error="JSON body required"), 415
        database = open_db()
        try:
            item = saved_repo.watch_category(
                body.get("category_key"), db=database, config=app.config["APP_CONFIG"]
            )
        except saved_repo.SavedSearchError as exc:
            return validation_error(exc)
        finally:
            database.close()
        return jsonify(watched=item), 201

    @app.delete("/api/watched")
    def api_watched_remove():
        body = request.get_json(silent=True) if request.is_json else None
        category_key = (body or {}).get("category_key") if isinstance(body, dict) else None
        category_key = category_key or request.args.get("category_key")
        if not category_key:
            return jsonify(error="category_key required"), 400
        database = open_db()
        try:
            removed = saved_repo.unwatch_category(category_key, db=database)
        finally:
            database.close()
        if not removed:
            return jsonify(error="Kategória nie je sledovaná."), 404
        return jsonify(removed=removed)

    @app.delete("/api/watched/<path:category_key>")
    def api_watched_remove_path(category_key: str):
        database = open_db()
        try:
            removed = saved_repo.unwatch_category(category_key, db=database)
        finally:
            database.close()
        if not removed:
            return jsonify(error="Kategória nie je sledovaná."), 404
        return jsonify(removed=removed)

    # -- listing actions ---------------------------------------------------

    @app.post("/api/listings/<int:listing_id>/<action>")
    def api_listing_action(listing_id: int, action: str):
        if json_body() is None:
            return jsonify(error="JSON body required"), 415
        if action not in {"seen", "hide", "favorite"}:
            return jsonify(error="invalid action"), 400

        database = open_db()
        try:
            if action == "seen":
                database.set_seen(listing_id, 1)
            else:
                field = "is_hidden" if action == "hide" else "is_favorite"
                database.toggle_field(listing_id, field)
            row = database.get_listing(listing_id)
        finally:
            database.close()

        if row is None:
            return jsonify(error="listing not found"), 404

        return jsonify(
            id=listing_id,
            seen=row["seen"],
            is_hidden=row["is_hidden"],
            is_favorite=row["is_favorite"],
        )

    @app.post("/api/mark-seen")
    def api_mark_seen():
        body = json_body()
        if body is None:
            return jsonify(error="JSON body required"), 415
        database = open_db()
        try:
            category_key = body.get("category_key")
            if category_key:
                if get_category(category_key) is None:
                    return jsonify(error="invalid category_key"), 400
                updated = database.mark_seen(category_key)
            elif isinstance(body.get("ids"), list):
                ids = []
                for value in body["ids"]:
                    try:
                        ids.append(int(value))
                    except (TypeError, ValueError):
                        continue
                updated = database.set_seen_many(ids)
            else:
                return jsonify(error="ids or category_key required"), 400
        finally:
            database.close()
        return jsonify(updated=updated)

    return app


def main() -> int:
    config = load_config()
    port = int(config.get("web_port", 5000))

    lock = SingleInstanceLock(ROOT / "data" / "app.lock")
    if not lock.acquire():
        print(
            "Aplikácia už beží v inom procese (data/app.lock je zamknutý). "
            "Ukončujem."
        )
        return 1

    try:
        # Start the scheduler exactly once, in the serving process, and never
        # with the debug reloader (which would fork a second process).
        app = create_app(config, start_scheduler=True)
        print(f"bazos scraper UI: http://127.0.0.1:{port}")
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
