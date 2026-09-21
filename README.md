# Bazoš monitor

A personal, polite scraper and local web UI for
[bazos.sk](https://www.bazos.sk), the Slovak classifieds site. It keeps
listings from the categories you choose in a local SQLite database and lets
you search, filter and sort them offline.

* **Local only.** The web server binds to `127.0.0.1` and talks only to the
  local database. Nothing is uploaded anywhere.
* **Private by design.** Seller names, phone numbers and e-mail addresses are
  never read or stored.
* **Polite.** Honest User-Agent, a delay between requests, retries with
  backoff, an immediate stop on HTTP 403/429, and no query-string URLs.

## Features

* Incremental scraping of any category from the catalog (20 sections +
  449 subcategories); a run stops as soon as it reaches listings it already
  knows.
* **Download whole category** — a resumable mode that walks every page of a
  category, with progress, ETA and coverage (`500 z ~6 386`), so you can see
  *all* listings, not just the first few hundred.
* **Age window** — only listings posted in the last N days (default **10**)
  are scanned and shown; configurable, and can be turned off.
* Local search: keywords (all / any / exclude), a description-only keyword
  box, price range, price type, city, PSČ prefix, region, new / favourite /
  hidden, "added within", relevance sort and pagination. Keyword, city and
  description matching are diacritics-insensitive (`kosice` finds `Košice`).
* Saved searches and watched categories.
* One-click batch update of all watched categories, plus an optional automatic
  scheduler.
* Slovak UI with a light/dark theme, no CDNs and no front-end build step at
  runtime.

## Requirements

* Python **3.11+** (tested on 3.14).
* Node.js only if you want to rebuild the stylesheet — not needed to run.

## Install

Run everything from the project root (`bazos_scraper`).

```bash
python -m pip install -r requirements.txt
```

## Run the web UI

```bash
python -m src.web
```

Then open <http://127.0.0.1:5000> (the port is `web_port` in `config.yaml`).

## Run the CLI

```bash
python -m src.cli stats
python -m src.cli scrape --category pc/notebook
```

**Windows tip.** Enable UTF-8 mode so Slovak diacritics and `€` render in the
console:

```
$env:PYTHONUTF8=1      # PowerShell
set PYTHONUTF8=1       # cmd.exe
```

Stored data is always correct UTF-8 either way.

## Configuration (`config.yaml`)

| key | default | meaning |
| --- | --- | --- |
| `user_agent` | `bazos-personal-monitor/0.1 (personal use)` | HTTP User-Agent |
| `request_delay_seconds` | `1.5` | minimum delay between any two requests |
| `timeout_connect` / `timeout_read` | `10` / `30` | HTTP timeouts in seconds |
| `max_retries` / `backoff_factor` | `3` / `2.0` | retries with exponential backoff (connection errors / 5xx) |
| `db_path` | `data/bazos.db` | SQLite path, relative to the project root |
| `first_run_max_pages` | `10` | page budget for a category with no stored listings yet |
| `incremental_max_pages` | `25` | page budget for a category already in the DB |
| `hard_max_pages` | `100` | absolute cap (also caps `--max-pages`) |
| `max_listing_age_days` | `10` | scan/show only listings posted in the last N days; `0` disables |
| `backfill_max_pages_per_run` | `1000` | pages per whole-category download (hard ceiling 2000) |
| `placeholder_psc` | `["12345", "00000", …]` | PSČ values treated as "no real address" |
| `max_saved_searches` | `50` | limit on saved searches |
| `max_watched_categories` | `40` | limit on watched categories |
| `min_batch_interval_minutes` | `15` | skip watched categories scraped more recently than this |
| `web_port` | `5000` | local web port (bound to `127.0.0.1`) |
| `samples` | – | URLs used by `src/fetch_samples.py` |

## Web UI

* **Category picker** (top) — searchable, with per-category `total / new`
  counts and coverage (`X z ~Y`, badge **kompletné**).
* **Aktualizovať** — incremental scrape of the selected category in the
  background.
* **Stiahnuť celú kategóriu** — resumable download of every page. Shows an
  estimate first, then a progress panel with a spinner, percentage, ETA and a
  *Zrušiť* button. Large categories continue over several runs.
* **Filters** — keywords, description-only, price, city, PSČ, region, "added
  within", and a checkbox to turn the age window off. Filters live in the URL,
  so any view is bookmarkable.
* **Results** — sortable table (cards on mobile), row actions (seen / hide /
  favourite) and bulk actions, without page reloads.
* **Left sidebar** — saved searches and watched categories. Clicking a watched
  category opens it like a search.

### Age window

`max_listing_age_days` (default **10**, `0` disables) limits both scraping and
display to listings posted within the last N days:

* the scraper **skips** listings older than `today − N` and stops when a page's
  non-TOP listings are all older than the window (`stop_reason=too_old`). This
  applies to the whole-category download too;
* the UI shows only listings inside the window by default. The checkbox
  **„Len inzeráty z posledných 10 dní“** (in *Zobrazenie*) turns it off
  (`?days=0` shows everything stored).

The window is **sliding**: it is always computed from today's date and compared
with the listing's own posting date (`posted_date`).

### Whole-category download

The incremental scraper stops on `caught_up` (the right behaviour for
monitoring new ads), so a category stuck at ~500 stored listings would never go
deeper. **Stiahnuť celú kategóriu** is a separate, **manual** mode that ignores
`caught_up` and walks every page:

* resumable — the state (`next_page`, counters) is saved after every page; a
  cancelled or crashed run continues where it stopped, and **Začať odznova**
  restarts from page 1;
* capped per run by `backfill_max_pages_per_run`; a bigger category is
  downloaded over several runs (`paused` / `cap_reached`);
* after it finishes, one normal incremental pass over pages 1–2 runs
  automatically so the newest ads are not missed;
* each run is recorded in `scrape_runs` with `mode = backfill`;
* it uses the same polite session and the same global scrape lock. The
  scheduler and the batch update never start it.

### Saved searches and watched categories

* **Saved searches** store the current filters under a name (1–60 chars,
  unique): category, keywords, prices, price type, PSČ, region, city, age
  window and "added within"; `sort` is stored separately. Transient filters
  (`only_new`, `include_hidden`, `only_favorites`, `include_unknown_location`,
  page) are not stored. Each item shows how many new listings match.
* **Watched categories** are scraped together with **Aktualizovať všetky
  sledované** (a batch). Optionally *vynútiť* (ignore the interval) and *hlbšie
  prehľadanie*. The batch runs sequentially and uses the single global lock, so
  no other scrape can run at the same time (409 otherwise). Categories scraped
  less than `min_batch_interval_minutes` ago are skipped unless forced. An
  HTTP 403/429 aborts the whole batch.

### Automatic check (scheduler)

The **Automatická kontrola** panel runs a watched-category batch on a timer.
The interval is chosen from presets (30 min, 1 h, 2 h, 6 h, 12 h, 24 h) or a
custom value; the **minimum is 30 minutes** (hard-coded). Enabling does not run
immediately — the first run is `now + interval`; use **Spustiť teraz** to run
at once.

* Runs only while the app is running (`python -m src.web`).
* If a manual scrape or batch is in progress, the check is postponed by 5
  minutes.
* After a restart, if the scheduled time has passed, exactly one catch-up run
  happens after 60 s.
* On HTTP 403/429 the check is disabled and must be re-enabled manually; after
  three consecutive failures it is paused.
* `data/app.lock` (OS-level lock) prevents a second instance from starting.

## CLI reference

Run from the project root.

```bash
# find categories (search ignores case and diacritics)
python -m src.cli categories --search notebook
python -m src.cli categories --parent pc

# scrape one or more categories
python -m src.cli scrape --category sluzby/ustajnenie
python -m src.cli scrape --category pc/notebook --max-pages 3
python -m src.cli scrape --category pc --category auto --full

# review stored listings
python -m src.cli show --category pc/notebook --new --sort price --limit 10
python -m src.cli show --category pc/notebook --max-price 500 --psc-prefix 04
python -m src.cli mark-seen --category pc/notebook
python -m src.cli mark-seen --all
python -m src.cli refresh-kraj
python -m src.cli stats
```

| command | purpose |
| --- | --- |
| `categories` | list/search the catalog (`--search`, `--parent`) |
| `scrape` | scrape categories (`--category` repeatable, `--max-pages`, `--full`) |
| `show` | show stored listings (`--category`, `--new`, `--sort`, `--min-price`, `--max-price`, `--psc-prefix`, `--limit`) |
| `mark-seen` | mark listings reviewed (`--category` or `--all`) |
| `refresh-kraj` | recompute region for all stored listings |
| `stats` | per-category totals, new count and last run |

`show --sort` accepts `price`, `-price`, `date`, `-date`, `first_seen`,
`-first_seen`; listings without a numeric price always sort last.

## HTTP API

All `POST`/`PUT` bodies must be JSON (otherwise `415`). Starting any scrape
while another holds the lock returns `409` with a Slovak message.

| route | method | purpose |
| --- | --- | --- |
| `/` | GET | main page (filters + results) |
| `/api/scrape` | POST | start a single-category scrape `{category_key, full}` |
| `/api/scrape/status` | GET | single-scrape state |
| `/api/listings/<id>/<seen\|hide\|favorite>` | POST | toggle a row action |
| `/api/mark-seen` | POST | bulk `{ids: [...]}` or `{category_key}` |
| `/api/saved-searches` | GET / POST | list / create `{name, category_key, filters, sort}` |
| `/api/saved-searches/<id>` | PUT / DELETE | rename or update filters / delete |
| `/api/watched` | GET / POST / DELETE | list / watch `{category_key}` / unwatch |
| `/api/batch/start` | POST | start `{force, deep}` |
| `/api/batch/cancel` | POST | request cancellation |
| `/api/batch/status` | GET | batch progress |
| `/api/backfill/start` | POST | start/resume a whole-category download `{category_key, restart?}` |
| `/api/backfill/status` | GET | backfill progress |
| `/api/backfill/cancel` | POST | cancel (resumable) |
| `/api/coverage` | GET | `?category_key=` → `{stored, estimate, backfill_status, complete, …}` |
| `/api/scheduler` | GET | scheduler state (incl. `seconds_until_next_run`) |
| `/api/scheduler/enable` | POST | enable (does not run immediately) |
| `/api/scheduler/disable` | POST | disable |
| `/api/scheduler/interval` | POST | set `{minutes}` (30–1440) |
| `/api/scheduler/run-now` | POST | start a batch now (`trigger=manual`) |
| `/api/new-count` | GET | unseen, non-hidden listings in watched categories |
| `/favicon.ico` | GET | app icon |

## Project layout

```
bazos_scraper/
├── config.yaml            # politeness, DB path, page limits, age window, web port
├── categories.yaml        # generated category catalog
├── icon.png               # app icon (favicon)
├── requirements.txt
├── README.md
├── changelog.md           # change history
├── THIRD_PARTY.md         # third-party assets and licences
├── data/
│   └── bazos.db           # SQLite database (created on first run, gitignored)
├── docs/
│   └── site_structure.md  # bazos.sk selectors, URL patterns, robots.txt
├── src/
│   ├── config.py           # config loader + defaults
│   ├── fetcher.py          # polite HTTP layer (query-string guard)
│   ├── parser.py           # listing parser
│   ├── categories.py       # catalog loader (cached)
│   ├── text.py             # normalize_text / parse_terms / highlight
│   ├── geo.py              # PSČ/city -> kraj lookup + classify_location
│   ├── db.py               # SQLite storage + migrations
│   ├── queries.py          # query layer + shared filter parser
│   ├── saved.py            # saved searches + watched categories
│   ├── batch.py            # watched-category batch update
│   ├── backfill.py         # resumable whole-category download + coverage
│   ├── jobs.py             # shared scrape lock / coordinator
│   ├── scheduler.py        # automatic check + single-instance lock
│   ├── scraper.py          # incremental scraper
│   ├── cli.py              # command line
│   ├── web/                # Flask app (app.py, templates/, static/)
│   ├── data/               # generated PSČ/obec -> kraj data
│   ├── build_psc_kraje.py  # regenerates the location data
│   ├── build_categories.py # regenerates categories.yaml
│   └── fetch_samples.py    # downloads sample pages for the tests
├── tests/
│   ├── conftest.py
│   ├── test_*.py           # offline tests (no network)
│   └── fixtures/           # downloaded raw HTML + robots.txt
└── tools/                  # stylesheet build only (Tailwind CSS + Basecoat)
```

## Location data

`src/data/psc_kraje.csv` is generated by `src/build_psc_kraje.py` from the
public dataset <https://github.com/gunsoft/obce-okresy-kraje-slovenska>
(4208 municipalities with PSČ and region): 1352 exact PSČ rows plus 137
three-digit prefix fallbacks; rows that span more than one region are marked
`uncertain`.

That dataset omits the Bratislava city PSČ (80xxx–85xxx), so
`src/data/psc_kraje_overrides.csv` is generated from the
[GeoNames](https://download.geonames.org/export/zip/SK.zip) postal-code export
(CC BY 4.0) and loaded *after* the main file. `src/data/obce_kraje.csv` maps a
normalized municipality name to a region and is used as a fallback when the PSČ
does not yield one; names that occur in more than one region are never used.

Regenerate everything and backfill stored rows with:

```bash
python src/build_psc_kraje.py
python -m src.cli refresh-kraj
```

Each listing stores `psc_status` (`valid`, `placeholder`, `foreign`,
`unmatched`, `missing`) and `kraj_source` (`psc`, `city`, `NULL`). When a
region or PSČ filter is active, the UI keeps rows with an unknown location by
default (the *Zahrnúť inzeráty s neznámym alebo neplatným PSČ* checkbox);
turn it off to see only confirmed locations.

## Politeness and robots.txt

Enforced by `src/fetcher.py` from `config.yaml`:

* honest, descriptive `User-Agent`;
* at least `request_delay_seconds` (default 1.5 s) between requests;
* connect/read timeouts (10 s / 30 s);
* retry with exponential backoff on connection errors and HTTP 5xx;
* abort the whole run immediately on HTTP 403 / 429;
* refuse to fetch any URL containing a query string (`?`).

bazos.sk disallows the query parameters used for server-side sorting
(`order=`), price filtering (`cenaod=`, `cenado=`) and search (`hledat=`).
Those URLs are therefore **not fetched** — sorting and filtering are done
locally in SQLite. See [`docs/site_structure.md`](docs/site_structure.md) §5–6.

## Privacy

Never store seller names, phone numbers or e-mail addresses.

## Tests

All tests are offline (no network); they use the saved HTML fixtures and mock
the HTTP layer.

```bash
python -m pytest tests -q
```

## Development

The stylesheet (`src/web/static/app.css`) is generated and committed; the app
does not need Node at runtime. After changing `src/web/static/theme.css`,
templates or `app.js` class names, rebuild it:

```bash
cd tools
npm install       # once
npm run build
```

See [`tools/README.md`](tools/README.md) and [`THIRD_PARTY.md`](THIRD_PARTY.md).
