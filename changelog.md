# Changelog

Všetky významné zmeny projektu `bazos_scraper`.
Formát vychádza z [Keep a Changelog](https://keepachangelog.com/sk/1.1.0/).

## [Neuložené]

_Ďalšie zmeny (notifikácie, detailné stránky) prídu neskôr._

## [Popis a mesto, časové okno, stiahnutie celej kategórie a výkon] – 2026-09-21

Samostatné hľadanie v popise, filtrovanie podľa mesta, posuvné 10-dňové okno,
obnoviteľné stiahnutie celej kategórie, favicon, neutrálna téma a výkonnostné
opravy načítania výsledkov.

### Pridané

- **Časové okno (posledných 10 dní)**: nový kľúč `max_listing_age_days`
  (predvolene 10, `0` vypne). Scraper **nestahuje** inzeráty staršie ako okno
  (preskočí ich a zastaví sa s `stop_reason=too_old`) a UI predvolene
  **zobrazuje** len inzeráty z posledných 10 dní; vo filtri Zobrazenie je
  zaškrtávacie pole „Len inzeráty z posledných 10 dní“, ktoré sa dá vypnúť
  (`?days=0` = všetko). Okno je posuvné podľa dnešného dátumu a porovnáva sa
  s `posted_date` inzerátu. Filter `days` je perzistentný (uložené hľadania).
- **Favicon**: `icon.png` sa servíruje ako ikona aplikácie
  (`<link rel="icon">` + `/favicon.ico`), skopírovaný do
  `src/web/static/icon.png`.
- **Filtrovanie podľa mesta** (`city`): nový stĺpec `listings.city_norm`
  (normalizované mesto, schéma v9) a pole „Mesto“ v sekcii Miesto. Hľadá
  diakritiky-necitlivo ako podreťazec (napr. „kosice“ nájde „Košice“), takže
  funguje aj pre varianty typu „Bratislava - Petržalka“. Perzistuje sa
  v uložených hľadaniach.
- **Popisové hľadanie** (`kw_desc`): nový stĺpec `listings.search_desc`
  (normalizovaný popis) a filter, ktorý hľadá **len v popise**, nie v názve.
  Pole „Iba v popise“ vo filtri Kľúčové slová, chip, zvýrazňovanie zhôd
  a perzistencia v uložených hľadaniach (`PERSISTENT_URL_KEYS`).
  Poznámka: pôvodné „Musia obsahovať všetky“ už hľadalo v názve aj popise
  (stĺpec `search_text`); nové pole je striktne len popis.
- **Stiahnutie celej kategórie** (`src/backfill.py`): `backfill_category`
  prejde všetky stránky kategórie a **ignoruje** pravidlo `caught_up` (to
  ostáva nezmenené pre bežné sledovanie noviniek). Obnoviteľné: stav sa po
  každej stránke ukladá (`next_page`), zrušený/spadnutý beh pokračuje od
  `next_page`, `restart=True` začína od strany 1. Strop na beh
  `backfill_max_pages_per_run` (default 1000, pevný strop 2000 v kóde) →
  veľká kategória sa stiahne na viac behov (`paused`, `cap_reached`). Po
  dokončení sa spustí jeden bežný inkrementálny sken strán 1–2.
  `category_coverage()` vracia `stored` / `estimate` / `backfill_status` /
  `complete`; `coverage_overview()` pre celý prehľad.
- **DB schéma v7**: tabuľky `backfill_state` a `category_stats`, stĺpec
  `scrape_runs.mode` (`incremental` / `backfill`) + index `idx_runs_mode`.
- **DB schéma v6**: stĺpec `listings.search_desc` (doplnený pre existujúce
  riadky).
- `src/fetcher.py`: `NotFoundError` pre HTTP 404 (napr. offset za koncom
  zoznamu) – v backfille sa berie ako koniec.
- `src/scraper.py`: `scrape_category` ukladá `total_count` z hlavičky do
  `category_stats` (odhad pre pokrytie a potvrdzovací dialóg).
- `src/jobs.py`: `ScrapeCoordinator.start_backfill/cancel_backfill/
  backfill_status` cez **ten istý** globálny zámok; scheduler ani dávka
  backfill nikdy nespúšťajú (len manuálne).
- API: `POST /api/backfill/start`, `GET /api/backfill/status`,
  `POST /api/backfill/cancel`, `GET /api/coverage`.
- Web UI: tlačidlo **Stiahnuť celú kategóriu** (len pri vybranej kategórii)
  s potvrdzovacím dialógom („~P stránok (~N inzerátov), aspoň M minút“,
  „Pokračovať (od strany X)“ / „Začať odznova“), panel priebehu s
  **preloaderom, percentami a odhadovaným časom**, tlačidlom Zrušiť a červeným
  upozornením pri 403/429; pokrytie „X z ~Y“ a odznak **kompletné**.
- `config.yaml`: `backfill_max_pages_per_run`, `max_listing_age_days`.
- Testy: `tests/test_backfill.py` (obnovenie, cap, 404, blokovanie, zámok,
  duplicity, pokrytie, refresh), rozšírené `test_web.py`, `test_scheduler.py`,
  `test_db.py`, `test_migration.py`.
- `README.md`: sekcia „Stiahnutie celej kategórie (Phase 7)“ + tabuľka API.

### Zmenené

- DB schéma **v5 → v9** (postupne v6, v7, v8, v9); migrácie sú idempotentné
  a existujúce dáta nemenia.
- `src/categories.py`: katalóg sa parsuje raz na (súbor, `mtime`) cez
  `functools.lru_cache` + index `key → Category` (predtým sa `categories.yaml`
  čítal a parsoval pri **každom** volaní `get_category`).
- `src/saved.py`: `overlap_warnings(watched_items=...)` vie prijať už
  načítaný `list_watched`; `src/web/app.py` ho takto používa (o jeden dotaz
  menej).
- `src/web/app.py`, `index.html`, `app.js`, `_icons.html` (ikona `download`).
- `src/web/static/theme.css`: primárna akcentová farba zmenená z modrej na
  neutrálnu – **čierna** v svetlej téme, biela v tmavej (čierna by na tmavom
  pozadí zmizla). Zneutralizované aj `ring`, `accent` a `sidebar-*` odtiene;
  `app.css` pregenerovaný (`cd tools && npm run build`).
- Logo „Bazoš monitor“ odstránené z ľavého panela (ostáva len v hlavičke).
- Sledovaná kategória v ľavom paneli je odkaz – kliknutie zobrazí jej
  inzeráty (`/?category=<key>`), namiesto dovtedajšieho neaktívneho textu.

### Opravené

- **Nekonečné samoobnovovanie stránky** po dokončení backfillu: server si
  drží posledný stav (`complete`) a `pollBackfill()` pri každom načítaní
  spúšťal `form.submit()` → reload → dokola. Reload sa teraz spraví len ak
  **táto** stránka videla backfill bežať; starý `complete` sa skryje.
- **Pomalé stránkovanie / načítanie výsledkov** (každý preklik bol nový
  render): `get_category()` znovu čítal YAML a chýbal index
  `price_history(listing_id)`, takže dva korelačné poddotazy na cenu skenovali
  celú tabuľku pre každý riadok. Pridaný **index `idx_price_history_listing`
  (schéma v8)** a cache katalógu. Celý render stránky: **~16,5 s → ~0,85 s**
  (34 442 inzerátov).

### Poznámky

- Live check (10 requestov na bazos.sk): `sluzby/ustajnenie` → `complete`
  (46 z 46); `pc/graficka` s `cap=3` → `paused`/`cap_reached` a úspešné
  pokračovanie od strany 4; čísla dialógu pre `pc/notebook` bez spustenia.
- Reálna DB `data/bazos.db` migrovaná na v9 (35 254 inzerátov; 18 267 z
  posledných 10 dní).
- Testy: `170 passed`.

## [Fáza 7b] – 2026-09-21

Vizuálny redesign webového rozhrania v jazyku shadcn/ui (bez zmeny logiky,
API, databázy ani scrapovania).

### Pridané
- **Basecoat 1.0.2** (MIT, `basecoat-css`, vanilla HTML/CSS/JS implementácia
  shadcn/ui) – JS bundle vendorovaný do
  `src/web/static/vendor/basecoat/basecoat.min.js`. Žiadny CDN, žiadny React.
- Build tooling v `tools/` (`package.json`, `input.css`, `README.md`):
  Tailwind CSS **4.3.3** + `@tailwindcss/cli` cez standalone CLI (bez Node za
  behu). Výstup `src/web/static/app.css` je commitnutý.
- `src/web/static/theme.css` – jediný súbor s témou: shadcn tokeny
  (`--background`, `--foreground`, `--primary`, `--muted`, `--border`,
  `--radius`, …) pre svetlú aj tmavú tému, `@font-face`, font tokeny a
  app-specific farby (`--success`, `--warning`, `--highlight`, `--price-drop`).
  Téma = neutrálny (zinc) základ + jeden jemný modrý akcent.
- **Geist** (OFL-1.1, `@fontsource-variable/geist` 5.3.0) self-hostovaný ako
  `woff2` (latin + latin-ext kvôli slovenskej diakritike) v
  `src/web/static/fonts/`; žiadne Google Fonts.
- **Lucide** ikony (`lucide-static` 1.47.0, ISC) ako inline SVG v Jinja makre
  `src/web/templates/_icons.html`; vendorovaná je len použitá množina.
- `THIRD_PARTY.md` – presné verzie a licencie (Basecoat, Tailwind, lucide,
  Geist).
- Tmavý režim: prepínač v hlavičke (slnko/mesiac), uložený v
  `localStorage.themeMode`, default z `prefers-color-scheme`, bez bliknutia
  (inline skript v `<head>` pred CSS).
- App shell: sticky hlavička („Bazoš monitor", combobox kategórie, primárne
  tlačidlo „Aktualizovať", prepínač témy) + kolabovateľný ľavý sidebar
  (off-canvas na úzkych obrazovkách) s „Uložené hľadania", „Sledované
  kategórie" a panelom „Automatická kontrola".
- Filtre ako kolabovateľná karta (Kľúčové slová / Cena / Miesto / Zobrazenie),
  odoberateľné „chips" filtre, tlačidlo „Vymazať filtre".
- Výsledky: tabuľka na desktope (sticky hlavička, sortovateľné stĺpce s
  ikonami) a karty na mobile (< 768 px). Výrazná cena s tabulkovými číslicami,
  prečiarknutá predchádzajúca cena so šípkou pri poklese. Odznaky NEW, TOP,
  „Zahraničie", „PSČ neplatné". Akcie riadku ako ghost icon-buttons s
  tooltipom a `aria-label`.
- Progress panely (jedna aktualizácia, dávka) s progress barom a stavovými
  ikonami; upozornenia (blokované/zlyhania) ako `alert` komponenty.
- Prázdne stavy s ikonou a slovenským textom (nič nestiahnuté / nič sa
  nenašlo / žiadne inzeráty), loading skeletony a spinnery v tlačidlách.
- Potvrdenia (zmazanie uloženého hľadania, premenovanie, prepis filtrov)
  používajú `<dialog>` komponent namiesto `window.confirm`/`window.prompt`.
- Prístupnosť: viditeľné focus ringy, `aria-label` na icon-only tlačidlách,
  klávesovo ovládateľný combobox (šípky/Enter/Escape).

### Zmenené
- `src/web/templates/index.html` – prepísané do Basecoat markupu.
- `src/web/static/app.js` – prepísané (rovnaké API volania, bez zmien logiky);
  delegácia akcií riadku presunutá na `#listings-region` (tabuľka + mobilné
  karty), NEW odznak sa hľadá ako `.badge.bg-success`, stavové triedy
  `.status-line`, dávkové ikony ako inline SVG.
- `src/web/static/app.css` – generovaný Tailwind + Basecoat (commitnutý);
  `src/web/static/style.css` **odstránený** (už sa nepoužíva).
- Všetky pôvodné `id` prvkov, na ktorých závisí JS/testy, zostali zachované
  (vrátane presných podreťazcov `id="inc-unknown" checked` a
  `title="inzerát mimo Slovenska">Zahraničie</span>`). Testy sa nemenili.

### Opravené
- Horizontálny presah (a tým horizontálny scrollbar) na všetkých šírkach:
  neviditeľné Basecoat tooltipy (`::before`) na pravej hrane (prepínač témy,
  akcie riadku) roztvárali layout. Tooltipy pri pravej hrane majú
  `data-align="end"` a je pridaná poistka `html { overflow-x: clip }`.

### Poznámky
- Fáza 7 (detailné stránky, plný popis, príznak zmazania, hromadné sťahovanie
  detailov) v repozitári neexistuje, preto jej UI prvky neboli súčasťou
  redesignu.
- Žiadne zmeny v `src/web/app.py`, v logike, API, schéme DB ani v scrapovaní.
- Testy: `138 passed`.

## [Fáza 6] – 2026-09-21

Automatická kontrola (scheduler) + preznačenie zahraničných inzerátov.

### Pridané
- `src/scheduler.py`: `Scheduler` (injektovateľný čas a čakanie), konštanty
  `MIN_INTERVAL_MINUTES=30`, `MAX_INTERVAL_MINUTES=1440`, default 60,
  `POSTPONE_WHEN_BUSY_MINUTES=5`, `MAX_CONSECUTIVE_FAILURES=3`; `compute_next_run`
  s jitterom ±10 % (nikdy menej ako 30 min); `SingleInstanceLock`
  (`data/app.lock`, OS-level zámok).
- `src/jobs.py`: `ScrapeCoordinator.run_batch_locked()` – synchronná dávka pre
  scheduler cez **ten istý** globálny zámok; `start_batch(..., trigger=...)`.
- `src/batch.py`: parameter `trigger` a zápis do `batch_runs`.
- `src/web/`: API `GET /api/scheduler`, `POST /api/scheduler/enable|disable|
  interval|run-now`, `GET /api/new-count`; panel „Automatická kontrola“,
  červený/žltý banner, odpočet, počet nových v titulku karty.
- Testy: `tests/test_scheduler.py` (fake clock, mockovaná dávka, zámok,
  perzistencia), rozšírené `test_web.py`, `test_batch.py`, `test_geo.py`.

### Zmenené
- DB schéma **v4 -> v5**: tabuľky `settings` a `batch_runs`.
- `classify_location()`: mestá `Zahraničie` / `Česká republika` (a PSČ
  `12345`, `11000`) sú `foreign`, nie `placeholder`; `placeholder` ostáva len
  pre ostatné hodnoty z `config.yaml`.
- Web UI: pri `psc_status='foreign'` odznak „Zahraničie“ (tooltip „inzerát
  mimo Slovenska“), ostatné neplatné stavy ostávajú „PSČ neplatné“.
- `main()` spúšťa scheduler raz a s `use_reloader=False`; drží `data/app.lock`.
- Žiadne nové kľúče v `config.yaml` – limity intervalu sú pevne v kóde.
- Reálna DB `data/bazos.db` sa migrovala na v5 a `refresh-kraj` preklasifikoval
  67 inzerátov (placeholder -> foreign).

### Opravené
- 67 inzerátov s PSČ `12345` (mesto „Zahraničie“) už nie je „placeholder“,
  ale „foreign“.

## [Fáza 5] – 2026-09-21

Uložené hľadania a sledované kategórie (bez plánovača a notifikácií).

### Pridané
- DB schéma **v4**: tabuľky `saved_searches` a `watched_categories`.
- `src/queries.py`: trieda `Filters` (dict-kompatibilná), zdieľaný
  `_build_query`, `count_listings` / `count_listings_split` (rovnaký WHERE
  builder ako `search_listings`, žiadne duplicitné SQL) a
  `saved_search_filters` (iba perzistentné filtre; `only_new`,
  `include_hidden`, `only_favorites`, `include_unknown_location` a stránka sa
  neukladajú).
- `src/saved.py`: `create_saved_search`, `update_saved_search`,
  `rename_saved_search`, `delete_saved_search`, `list_saved_searches`,
  `list_saved_searches_with_counts`, `watch_category`, `unwatch_category`,
  `list_watched`, `overlap_warnings`; validácia názvu (1–60, jedinečný),
  kategórie a limity.
- `src/batch.py`: `run_watched_update(force, deep)` – sekvenčné sťahovanie
  sledovaných kategórií, `skipped_recent` podľa intervalu, abort pri
  HTTP 403/429 (`not_run` pre zvyšok), zrušenie medzi stránkami/kategóriami,
  priebehový stav.
- `src/jobs.py`: `ScrapeCoordinator` – jeden globálny zámok pre samostatné
  sťahovanie aj dávku (nikdy nebežia dve naraz; 409 v slovenčine).
- `scraper.scrape_category(..., should_cancel=...)` – zrušenie medzi stránkami
  so stavom `cancelled`.
- Web UI: ľavý panel „Uložené hľadania“ a „Sledované kategórie“, panel
  priebehu dávky so stavovými ikonami a tlačidlom *Zrušiť*, upozornenia na
  prekrývajúce sa kategórie.
- API: `GET/POST /api/saved-searches`, `PUT/DELETE /api/saved-searches/<id>`,
  `GET/POST/DELETE /api/watched`, `POST /api/batch/start`,
  `POST /api/batch/cancel`, `GET /api/batch/status`.
- `config.yaml`: `max_saved_searches`, `max_watched_categories`,
  `min_batch_interval_minutes`.
- Testy: `test_saved.py`, `test_batch.py`, rozšírené `test_queries.py`,
  `test_web.py`, `test_migration.py`, `test_db.py`.

### Zmenené
- DB schéma **v3 -> v4** (Fáza 4b už obsadila v3); migrácia je idempotentná
  a existujúce dáta nemení.
- `src/queries.py` – jeden spoločný parser filtrov (`filters_from_dict` ->
  `Filters`, `filters_to_dict`) pre Flask route aj uložené hľadania;
  `search_listings` a počítanie používajú ten istý `_build_query`.
- `src/web/app.py` – pôvodný `ScrapeManager` nahradil `ScrapeCoordinator`
  z `src/jobs.py`; `/api/scrape` aj dávka používajú ten istý zámok.
- `README.md` – nová sekcia „Uložené hľadania a sledovanie“ a tabuľka API.

### Opravené
- Priebehový stav dávky sa už nehlási ako `finished` pred spustením behu.

## [Fáza 4b] – 2026-09-21

Klasifikácia lokality a filter „neznáme miesto“ – inzeráty s neplatným /
neznámym PSČ už nemiznú z výsledkov.

### Pridané
- `geo.classify_location(psc, city) -> (kraj | None, psc_status, kraj_source)` –
  jediná funkcia na klasifikáciu lokality, používa ju scraper, migračný
  backfill aj `refresh-kraj`. Stavy: `valid`, `placeholder`, `foreign`,
  `unmatched`, `missing`; zdroj kraja: `psc`, `city`, `NULL`.
- `config.yaml` / `config.py`: konfigurovateľný zoznam `placeholder_psc`
  (`12345, 00000, 11111, 99999, 01234, 54321`; hodnoty ako stringy kvôli
  vedúcim nulám).
- `src/data/obce_kraje.csv` – normalizovaný názov obce -> kraj (z rovnakého
  datasetu gunsoft), používaný ako fallback podľa mesta. Nejjednoznačné názvy
  (vo viacerých krajoch) sa zámerne nepoužívajú.
- `geo.kraj_for_city()` a `geo.normalize_city()` – normalizácia cez
  `normalize_text` + expanzia skratiek nájdených v reálnych dátach
  (`n.` / `n/` -> `nad`, napr. `Nové Mesto n.Váhom`).
- `src/queries.py`: filter `include_unknown_location` (predvolene `True`),
  voľba kraja `unknown` (len `kraj IS NULL`), návratový typ `SearchResult`
  s počtami `confirmed` / `unknown`.
- `filters_from_dict()` / `filters_to_dict()` – kanonická serializácia
  filtrov; príznak sa ukladá len keď je `False`.
- Web UI: zaškrtávacie pole „Zahrnúť inzeráty s neznámym alebo neplatným PSČ“,
  odznak „PSČ neplatné“, značka `~` s tooltipom „kraj odhadnutý podľa mesta“,
  voľba „Kraj neznámy“ a súhrn „Nájdených X (z toho Y s neznámym miestom)“.
- Testy: `classify_location` (valid/placeholder/foreign/unmatched/missing,
  jednoznačné aj nejednoznačné mesto, skratka), query s filtrom zap./vyp.,
  počty, migrácia, idempotentný `refresh-kraj`, Flask (odznaky, escaping).

### Zmenené
- DB schéma **v2 -> v3**: pribudli `psc_status`, `kraj_source` a index
  `idx_listings_psc_status`; všetky existujúce riadky sa preklasifikujú.
- `upsert_listing()` a `refresh_kraj()` počítajú `kraj`, `psc_status`
  aj `kraj_source`.
- `queries.search_listings()` – pri aktívnom filtri kraja/PSČ sa riadky s
  neznámym miestom predvolene zachovajú.
- `README.md` – dokumentácia lokalít a nových dátových súborov.

### Opravené
- Inzeráty s neplatným/neznámym PSČ (napr. `12345`, `11000`) alebo bez PSČ
  už nezmiznú z výsledkov pri filtrovaní podľa kraja alebo prefixu PSČ.

## [Fáza 4 – oprava] – 2026-09-21

Doplnenie pokrytia PSČ -> kraj pre Bratislavu.

### Pridané
- `src/data/psc_kraje_overrides.csv` – 41 prefixov Bratislavy (810–845,
  850–854) a kód `800 00`, so stĺpcom `source`. Načítava sa po hlavnom súbore.
- CLI `python -m src.cli refresh-kraj` + `Database.refresh_kraj()`.
- Testy Bratislavy naprieč prefixami a kontrola „nie príliš pažravého“ pravidla
  (susedný Trnavský kraj, `93101`).

### Zmenené
- `build_psc_kraje.py` generuje aj `psc_kraje_overrides.csv`
  (zdroj: GeoNames `SK.zip`, CC BY 4.0; `800 00` podľa Wikipédie).
- `README.md` – tip pre Windows `set PYTHONUTF8=1` / `chcp 65001`.

## [Fáza 4] – 2026-09-21

Lokálne webové rozhranie.

### Pridané
- `src/web/` – Flask aplikácia (`python -m src.web`), Jinja šablóny a vanilla
  JS; viaže sa len na `127.0.0.1`, bez CDN a build kroku.
- `src/queries.py` – parametrizované SQL vyhľadávanie (kľúčové slová,
  cena, typ ceny, PSČ/kraj, nové/obľúbené/skryté, dátumy, relevancia,
  stránkovanie).
- `src/text.py` – `normalize_text`, `parse_terms`, bezpečné zvýrazňovanie.
- `src/geo.py` + `src/data/psc_kraje.csv` – vyhľadanie PSČ -> kraj.
- `tests/test_text.py`, `test_geo.py`, `test_migration.py`, `test_queries.py`,
  `test_web.py`.

### Zmenené
- DB schéma **v1 -> v2**: `search_text`, `is_hidden`, `is_favorite`, `kraj`
  (+ indexy); existujúce riadky sa doplnia.

## [Fáza 3] – 2026-09-21

SQLite úložisko a inkrementálne scrapovanie cez CLI.

### Pridané
- `src/db.py` – schéma (`listings`, `listing_categories`, `price_history`,
  `scrape_runs`), verzované migrácie, upsert a dopyty.
- `src/scraper.py` – `scrape_category(...)`, zastaví sa po dobehnutí
  (TOP-only stránky nikdy nezastavia beh).
- `src/cli.py` – príkazy `categories`, `scrape`, `show`, `mark-seen`, `stats`.
- `src/fetcher.py` – slušná HTTP vrstva (UA, delay, timeouty, backoff,
  abort na 403/429, zákaz URL s query stringom).
- `tests/test_db.py`, `test_scraper.py`, `test_cli.py`.

## [Fáza 2] – 2026-09-21

Parser inzerátov a katalóg kategórií.

### Pridané
- `src/parser.py` – `parse_listing_page(html, category_key)` vracia
  `ParsedPage` s dataclass `Listing` (id, url, titulok, popis, cena, mesto,
  PSČ, dátum, zobrazenia, TOP).
- `src/categories.py` + `categories.yaml` – katalóg (20 sekcií + 449
  podkategórií).
- `src/build_categories.py` – regenerácia katalógu, `--verify`.
- `tests/test_parser.py`.

## [Fáza 1] – 2026-09-21

Prieskum (recon).

### Pridané
- `src/fetch_samples.py` – stiahne reálne vzorky stránok do
  `tests/fixtures/`.
- `docs/site_structure.md` – popis štruktúry webu, selektorov a robots.txt.
