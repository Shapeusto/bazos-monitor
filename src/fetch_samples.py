#!/usr/bin/env python3
"""Phase 1 (recon): download real sample pages from bazos.sk.

This script only *downloads and stores* raw HTML so that selectors and URL
patterns can be derived from real pages instead of guesses. It contains no
parsing logic for the actual project.

Politeness rules implemented (see config.yaml -> politeness):
  * honest User-Agent
  * >= delay_seconds between any two requests
  * request timeout
  * retry with exponential backoff for transient failures (connection / 5xx)
  * abort the whole run on HTTP 403 or 429

robots.txt is downloaded and its rules are respected: the query parameters
used for sorting (order=) and price filtering (cenaod=/cenado=) are
Disallowed, so those URLs are documented but NOT requested here.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup

from config import load_config, politeness_from_config

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config.yaml"
FIXTURES = ROOT / "tests" / "fixtures"

# robots.txt rules that are relevant to this project (verified against the
# downloaded robots.txt). Any URL containing one of these is not requested.
ROBOTS_DISALLOWED_PARAMS = (
    "hledat=", "hlokalita=", "order=", "cenaod=", "cenado=",
    "rubriky=", "type=", "category=",
)


class BlockedError(RuntimeError):
    """Server answered 403/429; the polite thing is to stop immediately."""


class Fetcher:
    """Tiny requests wrapper enforcing the politeness rules."""

    def __init__(self, politeness: dict) -> None:
        self.delay = float(politeness.get("delay_seconds", 1.0))
        self.timeout = float(politeness.get("timeout_seconds", 20))
        self.max_retries = int(politeness.get("max_retries", 3))
        self.backoff = float(politeness.get("backoff_factor", 2.0))
        self.abort_on = set(politeness.get("abort_on_status", [403, 429]))
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": politeness["user_agent"],
            "Accept-Language": "sk,cs;q=0.9,en;q=0.5",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self._last_request = 0.0
        self.requests_made = 0

    def _respect_delay(self) -> None:
        remaining = self.delay - (time.monotonic() - self._last_request)
        if remaining > 0:
            time.sleep(remaining)

    def get(self, url: str) -> requests.Response:
        if any(p in url for p in ROBOTS_DISALLOWED_PARAMS):
            raise ValueError(f"refusing to fetch robots.txt-disallowed URL: {url}")

        for attempt in range(1, self.max_retries + 1):
            self._respect_delay()
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                self._last_request = time.monotonic()
                if attempt == self.max_retries:
                    raise
                wait = self.backoff ** attempt
                print(f"  ! network error ({exc.__class__.__name__}); retry in {wait:.0f}s")
                time.sleep(wait)
                continue

            self._last_request = time.monotonic()
            self.requests_made += 1

            if resp.status_code in self.abort_on:
                raise BlockedError(
                    f"HTTP {resp.status_code} for {url} - stopping (403/429)"
                )
            if resp.status_code >= 500 and attempt < self.max_retries:
                wait = self.backoff ** attempt
                print(f"  ! HTTP {resp.status_code}; retry in {wait:.0f}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp

        raise RuntimeError(f"giving up on {url}")


def save(name: str, resp: requests.Response) -> pathlib.Path:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / name
    path.write_bytes(resp.content)  # raw bytes = exactly what the server sent
    print(f"  saved {name} ({len(resp.content):,} bytes) <- {resp.url}")
    return path


def find_detail_links(html: str, base_url: str, limit: int = 2) -> list[str]:
    """Return up to `limit` listing-detail URLs from a category page."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for a in soup.select("h2.nadpis a[href]"):
        href = a.get("href") or ""
        if "/inzerat/" in href:
            url = urljoin(base_url, href)
            if url not in out:
                out.append(url)
    return out[:limit]


def find_next_page(html: str, base_url: str) -> str | None:
    """Return the URL of page 2 (offset pagination) discovered from page 1."""
    soup = BeautifulSoup(html, "html.parser")
    box = soup.select_one("div.strankovani")
    if box is None:
        return None
    numeric: list[tuple[int, str]] = []
    for a in box.select("a[href]"):
        href = a.get("href") or ""
        tail = href.rstrip("/").rsplit("/", 1)[-1]
        if tail.isdigit():
            numeric.append((int(tail), urljoin(base_url, href)))
    return min(numeric)[1] if numeric else None


def describe_sort_form(html: str) -> list[tuple[str, str]]:
    """Return (name, type) for every named input of the listing filter form."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one("form#formt")
    if form is None:
        return []
    fields = []
    for inp in form.select("input[name], select[name]"):
        fields.append((inp.get("name", ""), inp.get("type") or inp.name))
    return fields


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(args.config)
    politeness = politeness_from_config(cfg)
    samples = cfg["samples"]

    fetcher = Fetcher(politeness)
    base = samples["category_url"]

    print(f"User-Agent: {politeness['user_agent']}")
    print(f"Delay: {politeness['delay_seconds']}s | Timeout: {politeness['timeout_seconds']}s\n")

    try:
        print("[1/5] robots.txt")
        save("robots.txt", fetcher.get(samples["robots_url"]))

        print("[2/5] category listing, page 1")
        p1 = fetcher.get(base)
        save("list_notebook_p1.html", p1)

        print("[3/5] category listing, page 2 (offset pagination)")
        next_url = find_next_page(p1.text, base)
        if next_url is None:
            print("  ! no pagination link found on page 1")
            return 2
        print(f"  discovered page-2 URL: {next_url}")
        save("list_notebook_p2.html", fetcher.get(next_url))

        print("[4/5] category listing with non-numeric prices (edge cases)")
        save("list_zvierata_p1.html", fetcher.get(samples["edge_case_url"]))

        print("[5/5] two listing detail pages")
        details = find_detail_links(p1.text, base, limit=2)
        if not details:
            print("  ! no detail links found on page 1")
            return 2
        for url in details:
            ad_id = url.split("/inzerat/", 1)[1].split("/", 1)[0]
            save(f"detail_{ad_id}.html", fetcher.get(url))

    except BlockedError as exc:
        print(f"\nBLOCKED: {exc}", file=sys.stderr)
        return 3
    except requests.HTTPError as exc:
        print(f"\nHTTP error: {exc}", file=sys.stderr)
        return 4

    print("\nSorting / price-filter parameters found in form#formt on page 1:")
    for name, ftype in describe_sort_form(p1.text):
        print(f"  - {name} ({ftype})")
    print(
        "\nThese are submitted as GET query parameters, e.g. order=1 for\n"
        "price ascending and cenaod=/cenado= for the price range, but the\n"
        "matching URLs are Disallowed by robots.txt and were NOT fetched."
    )
    print(f"\nDone. {fetcher.requests_made} requests made, fixtures in {FIXTURES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
