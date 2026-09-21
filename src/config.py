"""Configuration loading for the bazos.sk scraper."""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"

DEFAULTS: dict[str, Any] = {
    "user_agent": "bazos-personal-monitor/0.1 (personal use)",
    "request_delay_seconds": 1.5,
    "timeout_connect": 10,
    "timeout_read": 30,
    "max_retries": 3,
    "backoff_factor": 2.0,
    "db_path": "data/bazos.db",
    "first_run_max_pages": 10,
    "incremental_max_pages": 25,
    "hard_max_pages": 100,
    "max_listing_age_days": 10,
    "backfill_max_pages_per_run": 1000,
    "web_port": 5000,
    "placeholder_psc": ["12345", "00000", "11111", "99999", "01234", "54321"],
    "max_saved_searches": 50,
    "max_watched_categories": 40,
    "min_batch_interval_minutes": 15,
}


def load_config(path: str | pathlib.Path | None = None) -> dict[str, Any]:
    """Return the configuration merged over the built-in defaults."""
    config = dict(DEFAULTS)
    source = pathlib.Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if source.exists():
        data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        config.update({key: value for key, value in data.items() if value is not None})
    return config


def resolve_db_path(config: dict[str, Any]) -> pathlib.Path:
    """Resolve ``db_path`` to an absolute path (relative to the project root)."""
    path = pathlib.Path(config["db_path"])
    return path if path.is_absolute() else ROOT / path


def politeness_from_config(config: dict[str, Any]) -> dict[str, Any]:
    """Legacy dict shape used by the sample/catalog downloaders."""
    return {
        "user_agent": config["user_agent"],
        "delay_seconds": config["request_delay_seconds"],
        "timeout_seconds": config["timeout_read"],
        "max_retries": config["max_retries"],
        "backoff_factor": config["backoff_factor"],
        "abort_on_status": [403, 429],
    }
