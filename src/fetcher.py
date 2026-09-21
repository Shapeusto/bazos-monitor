"""Shared HTTP layer for the bazos.sk scraper.

Enforces the project's politeness rules:

* honest ``User-Agent`` (from config);
* a minimum delay between any two requests (from config);
* connect/read timeouts;
* retry with exponential backoff on connection errors and HTTP 5xx;
* the whole run stops immediately on HTTP 403 / 429 (:class:`BlockedError`);
* URLs containing a query string are refused (robots.txt disallows them).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)


class BlockedError(RuntimeError):
    """Server answered 403/429 - stop the whole run immediately."""


class NotFoundError(RuntimeError):
    """Server answered HTTP 404 (e.g. a page offset beyond the end)."""


class QueryStringError(ValueError):
    """A URL with a query string was requested - robots.txt disallows it."""


def ensure_no_query_string(url: str) -> str:
    if "?" in url:
        raise QueryStringError(f"refusing to fetch URL with a query string: {url}")
    return url


class PoliteSession:
    """Thin ``requests.Session`` wrapper enforcing the politeness rules."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.delay = float(config.get("request_delay_seconds", 1.5))
        self.timeout = (
            float(config.get("timeout_connect", 10)),
            float(config.get("timeout_read", 30)),
        )
        self.max_retries = int(config.get("max_retries", 3))
        self.backoff = float(config.get("backoff_factor", 2.0))

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config["user_agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "sk,cs;q=0.9,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
        })
        self._last_request = 0.0
        self.requests_made = 0

    def _respect_delay(self) -> None:
        remaining = self.delay - (time.monotonic() - self._last_request)
        if remaining > 0:
            time.sleep(remaining)

    def get(self, url: str) -> requests.Response:
        ensure_no_query_string(url)

        for attempt in range(1, self.max_retries + 1):
            self._respect_delay()
            try:
                response = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                self._last_request = time.monotonic()
                if attempt == self.max_retries:
                    raise
                wait = self.backoff ** attempt
                logger.warning("network error (%s); retry in %.0fs", exc.__class__.__name__, wait)
                time.sleep(wait)
                continue

            self._last_request = time.monotonic()
            self.requests_made += 1

            if response.status_code in (403, 429):
                raise BlockedError(f"HTTP {response.status_code} for {url}")
            if response.status_code == 404:
                raise NotFoundError(f"HTTP 404 for {url}")
            if response.status_code >= 500 and attempt < self.max_retries:
                wait = self.backoff ** attempt
                logger.warning("HTTP %s for %s; retry in %.0fs", response.status_code, url, wait)
                time.sleep(wait)
                continue

            response.raise_for_status()
            return response

        raise RuntimeError(f"giving up on {url}")

    def close(self) -> None:
        self.session.close()
