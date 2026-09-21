# bazos_scraper

A small, polite scraper for [bazos.sk](https://www.bazos.sk) (Slovak
classifieds). Long-term goal: show **new** listings from chosen categories,
sortable by price and filterable by keywords in the listing description.

This repository is being built in phases. **Phases 1 (recon), 2 (listing
parser + category catalog), 3 (SQLite storage + incremental scraping CLI),
4 (local web UI), 4b (location classification), 5 (saved searches + watched
categories), 6 (automatic check / scheduler) and 7 (resumable full-category
download) are done.** Notifications and detail-page fetching are not part of
any phase yet.

See [`changelog.md`](changelog.md) for the changes in each phase.

## How to run the app

Run everything from the project root (`bazos_scraper`).

```
cd bazos_scraper
python -m pip install -r requirements.txt
python -m src.web
```

Then open <http://127.0.0.1:5000> (the port is `web_port` in `config.yaml`).
The server binds only to `127.0.0.1` and talks only to the local SQLite
database.

The CLI works the same way, e.g.:

```
python -m src.cli stats
python -m src.cli scrape --category pc/notebook
```

**Windows tip.** Set UTF-8 mode first so Slovak diacritics and `€` render
correctly in the console:

```
$env:PYTHONUTF8=1      # PowerShell
set PYTHONUTF8=1       # cmd.exe
```

## Phase 1 – recon

* Downloads real sample pages from bazos.sk and stores them as raw HTML under
  `tests/fixtures/` so that selectors/URL patterns come from real pages.
* Documents the site structure in [`docs/site_structure.md`](docs/site_structure.md).

## Phase 2 – listing parser + category catalog

* `src/parser.py` – `parse_listing_page(html, category_key)` returns a
  `ParsedPage` with `Listing` dataclasses (id, url, title, short description,
  price amount/type/text, city, PSČ, date, views, TOP flag).
* `src/categories.py` + `categories.yaml` – the selectable catalog (20
  top-level sections + 449 subcategories) with `load_categories()` and
  `get_category(key)`.
* `src/build_categories.py` – regenerates `categories.yaml` from the homepage
  and the category map; `--verify` fetches 3 random categories and parses them.
* `tests/test_parser.py` – offline parser tests (fixtures only).

## Phase 3 – SQLite storage + incremental scraping CLI

* `src/db.py` – SQLite schema (`listings`, `listing_categories`,
  `price_history`, `scrape_runs`), versioned migrations, upsert and queries.
* `src/scraper.py` – `scrape_category(...)` fetches listing pages only and
  stops when it catches up (TOP-only pages are never a stop signal) or when
  the page is older than the age window (`stop_reason=too_old`).
* `src/cli.py` – command line (`categories`, `scrape`, `show`, `mark-seen`,
  `stats`).
* `src/fetcher.py` – shared polite HTTP layer: honest UA, delay, timeouts,
  backoff, abort on 403/429, and a hard guard that **refuses any URL with a
  query string** (`?`), since robots.txt disallows them.
* `tests/test_db.py`, `tests/test_scraper.py`, `tests/test_cli.py` – offline.

## Phase 4 – local web UI

* `src/web/` – Flask app (`python -m src.web`), Jinja templates and vanilla JS.
  Binds only to `127.0.0.1`; no CDNs, no frameworks, no build step.
* `src/queries.py` – parameterised SQL search (keywords all/any/exclude, a
  description-only keyword filter, price, price type, city, PSČ/kraj,
  new/favorite/hidden, date ranges, relevance sort, pagination). City and
  description matching are diacritics-insensitive.
* `src/text.py` – `normalize_text` (lowercase + diacritics removed),
  `parse_terms` and safe diacritics-insensitive keyword highlighting.
* `src/geo.py` + `src/data/psc_kraje.csv` – PSČ -> kraj lookup.
* DB migrated to schema v2: `search_text`, `is_hidden`, `is_favorite`, `kraj`.
* `tests/test_text.py`, `test_geo.py`, `test_migration.py`, `test_queries.py`,
  `test_web.py` – offline.

## Phase 4b – location classification + unknown-location filter

* `src/geo.py` – `classify_location(psc, city)` returns
  `(kraj, psc_status, kraj_source)`. `psc_status` is one of `valid`,
  `placeholder`, `foreign`, `unmatched`, `missing`; `kraj_source` is `psc`,
  `city` or `None`. The city fallback uses `src/data/obce_kraje.csv`
  (generated from the same gunsoft dataset) and only resolves names that map
  to exactly one kraj.
* DB migrated to schema v3: `psc_status`, `kraj_source` (+ index on
  `psc_status`); all existing rows are reclassified.
* `src/queries.py` – `include_unknown_location` (default on) keeps listings
  with an unknown/invalid PSČ visible when a kraj or PSČ filter is active;
  results carry a confirmed/unknown split.
* `src/web/` – checkbox "Zahrnúť inzeráty s neznámym alebo neplatným PSČ",
  a "PSČ neplatné" badge, a "~" marker for city-estimated kraj, the
  "Kraj neznámy" option and the confirmed/unknown summary.
* `python -m src.cli refresh-kraj` – recompute `kraj`, `psc_status` and
  `kraj_source` for all stored listings.
* `tests/test_geo.py`, `test_queries.py`, `test_db.py`, `test_migration.py`,
  `test_web.py`, `test_cli.py` – extended.

## Phase 5 – saved searches and watched categories

* DB migrated to schema v4: `saved_searches` and `watched_categories` tables.
* `src/queries.py` – the single filter parser is now `filters_from_dict` /
  `filters_to_dict` (a validated `Filters` object), shared by the Flask route
  and saved searches. `count_listings` / `count_listings_split` reuse the same
  WHERE builder as `search_listings` (no duplicated SQL).
* `src/saved.py` – CRUD for saved searches (`create`, `update`, `rename`,
  `delete`, `list_saved_searches_with_counts`) and watched categories
  (`watch_category`, `unwatch_category`, `list_watched`,
  `overlap_warnings`). Name/category/limit validation lives here.
* `src/batch.py` – `run_watched_update(force, deep)` scrapes every watched
  category sequentially; skips recent ones, aborts on HTTP 403/429, supports
  cancellation and reports per-category progress.
* `src/jobs.py` – `ScrapeCoordinator` owns the single global scrape lock used
  by both the single-category job and the batch (no two scrapes at once).
* `src/web/` – sidebar with "Uložené hľadania" and "Sledované kategórie",
  batch progress panel and the JSON API below.
* `tests/test_saved.py`, `test_batch.py` – new; the rest extended.

### API (Phase 5)

| route | method | purpose |
| --- | --- | --- |
| `/api/saved-searches` | GET | list saved searches with `total`/`new_count` |
| `/api/saved-searches` | POST | create `{name, category_key, filters, sort}` |
| `/api/saved-searches/<id>` | PUT | rename (`{name}`) and/or update filters |
| `/api/saved-searches/<id>` | DELETE | delete |
| `/api/watched` | GET | watched categories + overlap warnings |
| `/api/watched` | POST | watch `{category_key}` |
| `/api/watched` | DELETE | unwatch `{category_key}` |
| `/api/batch/start` | POST | start `{force, deep}` |
| `/api/batch/cancel` | POST | request cancellation |
| `/api/batch/status` | GET | batch progress state |

All POST/PUT bodies must be JSON (otherwise 415). Starting a batch or a single
scrape while another scrape runs returns 409 with a Slovak message.

## Phase 6 – automatic check (scheduler)

* DB migrated to schema v5: `settings` (key/value) and `batch_runs` (history
  of watched-category batches, with `trigger` `manual`/`scheduled`/`catch_up`).
* `src/scheduler.py` – `Scheduler` with an injectable clock and wait mechanism.
  Hard-coded limits: min interval **30 min**, max **1440 min**, default **60**;
  busy lock postpones by 5 min; 3 consecutive failures pause; HTTP 403/429
  pauses immediately (never auto re-enabled).
* `src/jobs.py` – `ScrapeCoordinator.run_batch_locked()` lets the scheduler run
  a batch through the *same* global lock as the manual paths.
* Single-instance guard: `SingleInstanceLock` (`data/app.lock`, OS-level lock).
* `src/web/` – "Automatická kontrola" panel, warning banners, live countdown,
  tab title with the number of new listings.
* `tests/test_scheduler.py` – new (fake clock, mocked batch, no network).

### API (Phase 6)

| route | method | purpose |
| --- | --- | --- |
| `/api/scheduler` | GET | scheduler state (incl. `seconds_until_next_run`) |
| `/api/scheduler/enable` | POST | enable (does not run immediately) |
| `/api/scheduler/disable` | POST | disable |
| `/api/scheduler/interval` | POST | set `{minutes}` (30–1440) |
| `/api/scheduler/run-now` | POST | start a normal batch (`trigger=manual`); 409 if busy |
| `/api/new-count` | GET | unseen, non-hidden listings in watched categories |

### API (Phase 7 – full-category download)

| route | method | purpose |
| --- | --- | --- |
| `/api/backfill/start` | POST | start/resume a full-category download `{category_key, restart?}`; 202 or 409 if busy |
| `/api/backfill/status` | GET | current backfill progress state |
| `/api/backfill/cancel` | POST | request cancellation (state saved, resumable) |
| `/api/coverage` | GET | `{stored, estimate, backfill_status, complete, ...}` for `?category_key=` |

## Automatická kontrola

Panel **Automatická kontrola** v ľavom stĺpci umožňuje zapnúť pravidelnú
kontrolu sledovaných kategórií. Interval sa nastavuje z presetov (30 min, 1 h,
2 h, 6 h, 12 h, 24 h) alebo vlastným počtom minút; **minimum je 30 minút**
(pevné, nedá sa znížiť konfigom ani API). Po zapnutí sa kontrola **nespustí
hneď** – prvý beh je naplánovaný na `teraz + interval`; okamžité spustenie je
tlačidlo **Spustiť teraz**.

* Beží len kým je aplikácia spustená (`python -m src.web`). Zelený stav
  „Čaká" ukazuje odpočet „Ďalšia kontrola o N min".
* Ak je práve spustené ručné sťahovanie alebo dávka, kontrola sa **odloží
  o 5 minút** (nezmešká sa).
* Po reštarte, ak bol naplánovaný čas už preč, prebehne **jeden** náhradný beh
  (`catch_up`) po 60 s; zmeškané intervaly sa neopakujú jednotlivo.
* Ak nie sú žiadne sledované kategórie, stav je „Nie je čo sledovať" a čas sa
  posunie.
* Pri HTTP 403/429 (blokovanie) sa kontrola **vypne** a zobrazí sa červený
  banner; zapnúť ju treba ručne. Po troch neúspešných behoch sa pozastaví
  (žltý banner). Úspešný beh vynuluje počítadlo.
* Panel zobrazuje posledný výsledok („Naposledy: pred 12 min, 5 nových").
* V záhlaví karty je počet nových inzerátov vo sledovaných kategóriách
  (`(12) Bazoš monitor`), obnovovaný každú minútu.
* `data/app.lock` (OS-level zámok) bráni spusteniu druhej inštancie.

## Časové okno (posledných N dní)

`max_listing_age_days` (predvolene **10**, `0` vypne) obmedzuje skenovanie aj
zobrazovanie na inzeráty zverejnené za posledných N dní:

* scraper inzeráty staršie ako `dnes − N` **preskočí** (neuloží) a keď na
  stránke narazí na samé staré (nie TOP) inzeráty, zastaví sa
  (`stop_reason=too_old`). Platí to aj pre backfill;
* UI predvolene zobrazuje len inzeráty z tohto okna. Vo filtri „Zobrazenie“
  je pole **„Len inzeráty z posledných 10 dní“** – po odškrtnutí sa zobrazí
  všetko uložené (`?days=0`).

Okno je **posuvné**: počíta sa vždy od dnešného dátumu, takže sa každý deň
posunie. Porovnáva sa s dátumom zverejnenia inzerátu (`posted_date`, ten
`[d.m.rrrr]` pri inzeráte). Filter `days` sa ukladá v uložených hľadaniach.

## Stiahnutie celej kategórie (Phase 7)

Inkrementálne sťahovanie sa zámerne zastaví hneď, ako narazí na už známu
stránku (`caught_up`) – to je správne pre sledovanie noviniek, ale znamená, že
kategória „zaseknutá“ na ~500 inzerátoch sa už nikdy nedostane hlbšie. Preto
existuje samostatný, **ručný** režim, ktorý prejde všetky stránky kategórie.

* DB migrovaná na schému v7: `backfill_state`, `category_stats` a stĺpec
  `scrape_runs.mode`.
* Tlačidlo **Stiahnuť celú kategóriu** (vpravo hore, len keď je vybraná
  konkrétna kategória) otvorí potvrdenie s odhadom: „Stiahne sa približne
  P stránok (~N inzerátov), potrvá aspoň M minút.“ Čísla pochádzajú z posledného
  známeho počtu zo stránky (`total_count`) a z `request_delay_seconds`.
* Ak je kategória väčšia než `backfill_max_pages_per_run` (predvolene 1000
  strán, pevný strop 2000 v kóde), upozorní, že sa stiahne na viac behov.
* Priebeh (progress bar `pages_done / pages_estimated`, nové inzeráty, ETA,
  „Zrušiť“) je v paneli **Stiahnutie celej kategórie**. Pri 403/429 sa beh
  preruší a zobrazí sa rovnaké červené upozornenie ako inde.
* **Obnoviteľnosť:** stav sa po každej stránke ukladá do tabuľky
  `backfill_state` (`status`, `next_page`, `total_estimate`, `pages_done`).
  Zrušený alebo spadnutý beh (aj „running“ po páde procesu) pokračuje od
  `next_page`; tlačidlo **Začať odznova** spustí od strany 1.
* Po dokončení sa automaticky spustí jeden bežný inkrementálny sken strán 1–2,
  aby nechýbali najnovšie inzeráty.
* Každý beh je v `scrape_runs` s `mode` = `backfill` (bežné behy majú
  `incremental`), so štandardnými počítadlami a `stop_reason`
  (`last_page`, `cap_reached`, `cancelled`, `blocked`, `failed`).
* Používa **rovnaký** `PoliteSession`, rovnaký globálny zámok
  (`ScrapeCoordinator`) a rovnaké pravidlá (žiadne query stringy, žiadne
  zníženie oneskorenia, okamžitý stop pri 403/429). **Scheduler ani dávka
  nikdy nespúšťajú backfill** – je výhradne manuálny.
* `category_coverage(category_key)` vracia `stored` (uložené inzeráty),
  `estimate` (posledný známy `total_count`), `backfill_status` a `complete`.
  V prehľade kategórií sa zobrazuje „500 z ~6 386“ a odznak **kompletné**.

Nastavenie v `config.yaml`: `backfill_max_pages_per_run` (predvolene 1000).

## Layout

```
bazos_scraper/
├── config.yaml            # politeness, DB path, page limits, web port
├── categories.yaml        # generated category catalog
├── data/bazos.db          # SQLite database (created on first run)
├── requirements.txt
├── README.md
├── changelog.md           # changes per phase
├── docs/
│   └── site_structure.md  # Phase 1 findings (selectors, URL patterns, robots)
├── src/
│   ├── config.py           # config loader + defaults
│   ├── fetcher.py          # polite HTTP layer (query-string guard)
│   ├── parser.py           # listing parser
│   ├── categories.py       # catalog loader
│   ├── text.py             # normalize_text / parse_terms / highlight
│   ├── geo.py              # PSČ/city -> kraj lookup + classify_location
│   ├── db.py               # SQLite storage
│   ├── queries.py          # web query layer + shared filter parser
│   ├── saved.py            # saved searches + watched categories
│   ├── batch.py            # watched-category batch update
│   ├── backfill.py         # resumable full-category download + coverage
│   ├── jobs.py             # shared scrape lock / coordinator
│   ├── scheduler.py        # automatic check (scheduler + instance lock)
│   ├── scraper.py          # incremental scraper
│   ├── cli.py              # command line
│   ├── web/                # Flask app (app.py, templates/, static/)
│   ├── data/psc_kraje.csv  # generated PSČ -> kraj data
│   ├── data/psc_kraje_overrides.csv  # Bratislava PSČ, loaded after the above
│   ├── data/obce_kraje.csv # generated municipality -> kraj (city fallback)
│   ├── build_psc_kraje.py  # PSČ / obec data generator
│   ├── fetch_samples.py    # Phase 1 downloader
│   └── build_categories.py # catalog generator
└── tests/
    ├── conftest.py
    ├── test_*.py
    └── fixtures/          # downloaded raw HTML + robots.txt
```

## Setup

Python 3.11+ (tested on 3.14).

```
python -m pip install -r requirements.txt
```

**Windows console tip.** The console codepage can mangle Slovak diacritics and
`€` in CLI output. Enable Python's UTF-8 mode for the session first:

```
set PYTHONUTF8=1
```

(or `chcp 65001` for the raw console codepage; in PowerShell use
`$env:PYTHONUTF8=1`). Stored data is always correct UTF-8 either way.

## Run the recon downloader

```
python src/fetch_samples.py
```

It will (re)download `robots.txt`, category page 1 + page 2, a category page
with non-numeric prices, and two detail pages into `tests/fixtures/`.

## Tests and catalog

```
python -m pytest tests -q                          # all offline tests
python src/build_categories.py                     # regenerate categories.yaml
python src/build_categories.py --verify            # + parse 3 random categories
python src/build_psc_kraje.py                      # regenerate PSČ -> kraj data
```

## Usage (Phase 3 CLI)

Run from the project root. Scraping is incremental: a run stops as soon as it
reaches listings that are already in the database (TOP-only pages do not stop
it).

```
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

`show --sort` accepts `price`, `-price`, `date`, `-date`, `first_seen`,
`-first_seen`; listings without a numeric price always sort last.

## Usage (Phase 4 web UI)

```
python -m src.web        # prints http://127.0.0.1:5000
```

Open the printed URL. The page lets you pick a category (searchable, with
`total / new` counts), hit **Aktualizovať** to run an incremental scrape in the
background, then filter/sort the stored listings. Filters are stored in the
page's URL query string, so views are bookmarkable. Row actions (mark seen,
hide, favourite) and bulk actions use the JSON API without reloading.

Endpoints:

| route | method | purpose |
| --- | --- | --- |
| `/` | GET | main page (filters, results) |
| `/api/scrape` | POST | start a scrape (JSON `{category_key, full}`); 409 if one is running |
| `/api/scrape/status` | GET | current scrape state |
| `/api/listings/<id>/<seen\|hide\|favorite>` | POST | row action |
| `/api/mark-seen` | POST | bulk `{ids:[...]}` or `{category_key}` |

The server binds only to `127.0.0.1` (port from `config.yaml`, default 5000)
and talks only to the local SQLite database.

## Uložené hľadania a sledovanie

Ľavý panel v rozhraní má dve sekcie.

**Uložené hľadania.** Tlačidlom *Uložiť aktuálne hľadanie* uložíte práve
nastavené filtre pod názvom (1–60 znakov, jedinečný). Ukladá sa kategória,
kľúčové slová, ceny, typ ceny, PSČ prefix, kraj a okno „pridané za“; `sort`
zvlášť. **Neukladá sa** `only_new`, `include_hidden`, `only_favorites`,
`include_unknown_location` ani číslo stránky – sú to prechodné filtre.
Pri položke sú akcie *nové* (otvorí hľadanie s `new=1`), *Premenovať*,
*Aktualizovať filtre* (prepíše uložené filtre aktuálnymi) a *Zmazať*.
Odznak pri názve ukazuje počet nových (`seen = 0`, bez skrytých) inzerátov.

**Sledované kategórie.** Tlačidlom *Sledovať túto kategóriu* pridáte aktuálnu
kategóriu; zoznam ukazuje `celkom / nových` a čas posledného úspešného behu.
Ak sledujete sekciu aj jej podkategóriu, zobrazí sa upozornenie na zbytočné
sťahovanie (napr. `pc aj pc/notebook sa prekrývajú - zbytočné sťahovanie`).

**Aktualizovať všetky sledované** spustí dávku (`POST /api/batch/start`).
Voliteľne *vynútiť* (ignorovať interval) a *hlbšie prehľadanie*. Dávka beží
sekvenčne, medzi kategóriami dodržiava bežné oneskorenie a používa **jeden
globálny zámok** – počas dávky (ani počas jednej kategórie) nemôže bežať iné
sťahovanie a naopak (409). Kategória, ktorej posledný úspešný beh skončil pred
menej ako `min_batch_interval_minutes` (predvolene 15), sa preskočí
(`skipped_recent`), ak nepoužijete *vynútiť*. Pri HTTP 403/429 sa dávka hneď
preruší, zvyšné kategórie sú `not_run` a zobrazí sa výrazné upozornenie.
Priebeh sa obnovuje každú sekundu, tlačidlom *Zrušiť* možno dávku zastaviť
medzi stránkami aj medzi kategóriami.

Limity (v `config.yaml`): `max_saved_searches` (50),
`max_watched_categories` (40), `min_batch_interval_minutes` (15).

## PSČ -> kraj data

`src/data/psc_kraje.csv` is generated by `src/build_psc_kraje.py` from the real
public dataset <https://github.com/gunsoft/obce-okresy-kraje-slovenska>
(4208 municipalities with PSČ and region). 1352 exact PSČ rows plus 137
three-digit prefix fallbacks; rows that span more than one region are marked
`uncertain`.

That dataset omits the Bratislava city PSČ (80xxx-85xxx, e.g. `81101`), so
`src/data/psc_kraje_overrides.csv` is generated from a second real source, the
[GeoNames](https://download.geonames.org/export/zip/SK.zip) postal-code export
(CC BY 4.0), and is loaded *after* the main file. Every 8xx prefix present in
GeoNames maps to `Bratislavský kraj` only, so 41 three-digit prefixes
(810-845, 850-854) are added with `confidence=prefix`; the whole-city code
`800 00` is added from Wikipedia. Shared prefixes near the border (`906`,
`908`, `925`) stay `uncertain`. The UI labels the region as *približné*
(approximate).

`src/data/obce_kraje.csv` is generated from the same gunsoft dataset: a
normalized municipality name -> kraj map used as a fallback when the PSČ does
not yield a region. Names that occur in more than one kraj are stored as
`ambiguous` and deliberately never used. City names are normalized with
`text.normalize_text` plus the abbreviations found in the scraped data
(`n.` / `n/` -> `nad`, e.g. `Nové Mesto n.Váhom` -> `Nové Mesto nad Váhom`).

Re-run `python src/build_psc_kraje.py` to regenerate all three files, then
backfill stored listings with `python -m src.cli refresh-kraj`.

### Location status

Each listing stores `psc_status` (`valid`, `placeholder`, `foreign`,
`unmatched`, `missing`) and `kraj_source` (`psc`, `city`, `NULL`):

* `placeholder` – PSČ in the configurable `placeholder_psc` list in
  `config.yaml` (default `12345`, `00000`, `11111`, `99999`, `01234`, `54321`);
* `foreign` – well-formed 5-digit code outside the Slovak `0xx`/`8xx`/`9xx`
  ranges (e.g. `11000`, a Czech PSČ);
* `unmatched` – well-formed Slovak PSČ missing from our dataset;
* `missing` – empty PSČ.

When a kraj or PSČ filter is active, the web UI keeps rows with an unknown
location in the results by default (`include_unknown_location`, the
"Zahrnúť inzeráty s neznámym alebo neplatným PSČ" checkbox). Turn it off to
see only confirmed locations; choose the "Kraj neznámy" option to see only
the unknown ones. Results report the confirmed/unknown split.

## Politeness

Configured in `config.yaml` and enforced by `src/fetcher.py`:

* honest, descriptive `User-Agent`
* at least `request_delay_seconds` (default 1.5 s) between requests
* connect/read timeouts (10 s / 30 s)
* retry with exponential backoff on connection errors and HTTP 5xx
* abort the whole run immediately on HTTP 403 / 429
* refuse to fetch any URL containing a query string (`?`)

## robots.txt

bazos.sk disallows the query parameters used for server-side sorting
(`order=`), price filtering (`cenaod=`, `cenado=`) and search (`hledat=`).
Those URLs are documented but **not fetched**. Sorting and keyword/price
filtering should be implemented locally in later phases. See
`docs/site_structure.md` §5–6.

## Privacy

Never store seller names, phone numbers or e-mail addresses.
