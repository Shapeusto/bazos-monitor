# bazos.sk – site structure

Everything below is based on real pages downloaded by `src/fetch_samples.py`
into `tests/fixtures/` (no guessed selectors). The site was served as UTF-8
(`Content-Type: text/html; charset=UTF-8` and `<meta ... charset=utf-8>`).

Evidence files:

| file | source |
| --- | --- |
| `robots.txt` | `https://www.bazos.sk/robots.txt` |
| `list_notebook_p1.html` | `https://pc.bazos.sk/notebook/` |
| `list_notebook_p2.html` | `https://pc.bazos.sk/notebook/20/` |
| `list_notebook_p11.html` | `https://pc.bazos.sk/notebook/200/` |
| `list_notebook_lastpage.html` | `https://pc.bazos.sk/notebook/6380/` |
| `list_notebook_overflow404.html` | `https://pc.bazos.sk/notebook/6400/` (404) |
| `list_zvierata_p1.html` | `https://zvierata.bazos.sk/` (non-numeric prices) |
| `detail_195709118.html`, `detail_195703097.html` | two `/inzerat/<id>/...php` pages |

## 1. Listing item markup

The page is a table-like layout made of divs. Two rows share the inner column
class names, so selectors must be scoped to the item container.

* **Column header row:** `div.listainzerat.inzeratyflex`
  (contains the total count and the "Cena"/"Zobrazenie" sort headers)
* **One item:** `div.inzeraty.inzeratyflex` ← iterate these

| field | selector (scoped to an item) | notes / example |
| --- | --- | --- |
| id + url | `div.inzeratynadpis a[href*="/inzerat/"]` | href `= /inzerat/195709118/predam-...php`; id = 2nd path segment. The first anchor is the image, the second (inside `h2`) is the title — both share the href. |
| title | `div.inzeratynadpis h2.nadpis a` | text, e.g. `MacBook Pro 14" 2021, M1 Pro...` |
| short description | `div.popis` | truncated, see §7 |
| price | `div.inzeratycena span[translate="no"]` | e.g. `   389 €` (see §2) |
| location / PSČ | `div.inzeratylok` | city, `<br>`, PSČ → `Košice<br>040 12` |
| date | `div.inzeratynadpis span.velikost10` | `[21.9. 2026]` → `D.M. YYYY`, no leading zeros |
| views | `div.inzeratyview` | `126 x` |
| TOP / promoted | `span.ztop` (absent for normal ads) | `title="TOP 5x Platí do 15.10. 2026"`, text `TOP` |
| actions (ignore) | `div.inzeratyakce` | favourite / report buttons |

Total number of ads is in the header row:
`div.listainzerat div.inzeratynadpis` → `Zobrazených 1-20 inzerátov z 6 386`.

## 2. Price format

The price cell is always `div.inzeratycena > b > span[translate="no"]`.

* Numeric prices are right-padded with **ASCII spaces** (U+0020), use a space
  as thousands separator and a space before `€` (U+20AC): `   389 €`,
  `  2 250 €`, `  22 999 €`. To parse: `strip()`, remove spaces, drop `€`,
  then `int()`.
* No decimals/cents were observed.
* Non-numeric labels (whole cell) occur and mean "no fixed price":
  * `Dohodou` – negotiable
  * `Zadarmo` – free
  * `V texte` – price is written in the description
  * `Ponúknite` – "make an offer" (seen in `oblecenie`, `dom`)

## 3. Pagination

* Offset-based path pagination: `/notebook/20/`, `/notebook/40/`, …
  `offset = (page - 1) * 20`, **20 items per page**.
* Current page: `div.strankovani b span.cisla`.
* Links: `Predošlá` (previous) and `Ďalšia` (next) are plain `<a href>`.
* Page 1 (`/notebook/`) is the same as offset 0.

## 4. Detecting the last page

**Do not rely on the absence of the `Ďalšia` link.** On the real last page
(offset 6380, items 6381–6386) `Ďalšia` is still present and points to
`/notebook/6400/`; that URL returns **HTTP 404**, 0 items and a
"nenájden" message.

Note on the header range: the last page's header says `Zobrazených 6381-6386`
(arithmetically 6 ads) but only **3** ads are actually rendered. The range is
computed by the site as `offset + 20` capped at the total (6380 + 20 → 6386),
not from the number of ads present. The parser therefore reports 3 items with
`range_end = 6386` and `is_last_page = True`.

Reliable options, in order of preference:

1. Parse the total `N` from the header (`… inzerátov z 6 386`) and stop when
   the range end `B` equals `N` (last page showed `6381-6386`).
2. Stop when the page contains 0 item containers.
3. Treat HTTP 404 on the next offset as end-of-list (but a 404 is also a
   generic error, so combine with (1)/(2)).

`src/backfill.py` (resumable full-category download) uses all three: it stops
on `is_last_page` (1), on 0 item containers (2), and on a 404 for the next
offset (3, surfaced as `fetcher.NotFoundError`). The normal incremental
scraper stops earlier, when it catches up with already-stored listings.

## 5. Sorting & price filter URL parameters

`form#formt` has `method=get` and no `action`, so it submits to the current
category URL with query parameters. Relevant fields:

| param | meaning | observed value |
| --- | --- | --- |
| `order` (hidden) | sort order | `1` = Cena (price); `3` = Zobrazenie (views) |
| `cenaod` / `cenado` | price from / to | free text |
| `hledat` | keyword search | free text |
| `rubriky` | category | e.g. `pc` |
| `hlokalita` / `humkreis` | location / radius | |
| `crp`, `kitx` | internal | |

The sort controls are JS that set the hidden `order` and submit, e.g.
`onclick="document.getElementById('order').value=1;document.forms['formt'].submit();"`.

Example sorted-by-price URL:
`https://pc.bazos.sk/notebook/?hledat=&rubriky=pc&hlokalita=&humkreis=25&cenaod=&cenado=&order=1&crp=&kitx=ano`

> **robots.txt disallows `/*order=`, `/*cenaod=`, `/*cenado=`, `/*hledat=`.**
> These URLs were therefore **not fetched** (see §6). The direction of
> `order=1` (asc/desc) and whether `order=2` exists could not be verified from
> an allowed page.
>
> **Recommendation:** do server-side sorting/keyword/price filtering **locally**
> (in our own SQLite store) instead of via bazos query params. That matches the
> project goal and keeps us inside robots.txt.

## 6. robots.txt

Downloaded and saved to `tests/fixtures/robots.txt`. For `User-agent: *`:

* Disallowed (relevant to us): `/search.php`, `/*hledat=`, `/*hlokalita=`,
  `/*humkreis`, `/*order=`, `/*cenaod=`, `/*cenado=`, `/*rubriky=`,
  `/*type=`, `/*category=`, `/*idphone=`, `/*idmail=`, plus many action pages
  (`/ad-phone.php`, `/ad-mail.php`, `/report.php`, `/suggest*.php`, …).
* Several named bots are blocked entirely (`trovitBot`, `pricebot`,
  `magpie-crawler`, `SemrushBot`, `trendictionbot`, `BLEXBot`). Our honest UA
  falls under `*`.
* No `Sitemap:` directive.

**Verdict:** our paths *are* allowed — category listing pages
(`/notebook/`, `/notebook/20/`, …) and detail pages (`/inzerat/<id>/…php`).
Only the query-string based sort/filter/search URLs are off-limits.

## 7. Is the list description truncated?

Yes. `div.popis` on the list is truncated to roughly 280–305 characters and
always ends with ` ...`. The full text is only on the detail page
(`div.popisdetail`). Keyword filtering must therefore either use the short
`popis` or fetch detail pages for the full text.

## 8. TOP / promoted listings

Yes, promoted ads are shown **at the top** of the category listing, ahead of
the normal newest-first ads, and are marked with
`<span class="ztop" title="TOP <n>x Platí do <d.m.yyyy>">TOP</span>`.

Evidence (ztop count / items per page):

* page 1 (offset 0): 20 / 20
* page 2 (offset 20): 20 / 20
* page 11 (offset 200): 0 / 20
* last page (offset 6380): 0 / 3

So TOP ads occupy the first pages and are **not** repeated on every page.
**They break "newest first" ordering**: position in the list is not recency.
Use the parsed `[D.M. YYYY]` date (and optionally the `ztop` flag) to order
ads yourself.

The age window (`max_listing_age_days`, see README) relies on this: a run
stops once a page's **non-TOP** listings are all older than the cutoff,
ignoring TOP ads (whose position is not recency). Listings older than the
cutoff are never stored.

## 9. Detail page markup

| field | selector |
| --- | --- |
| title | `div.inzeratydetnadpis h1.nadpisdetail` |
| full description | `div.popisdetail` (HTML, `<br>` line breaks) |
| price | table row `Cena:` → `span[translate="no"]` |
| location / PSČ | row `Lokalita:` → PSČ text + city link |
| date | `span.velikost10` → `[21.9. 2026]` |
| views | row `Videlo:` → e.g. `126 ľudí` |
| breadcrumb / id | `div.drobky` → `Inzerát č. <id>` |

**Do not touch seller contact:** the page contains a `Telefón:` row driven by
JS (`/ad-phone.php`, `/*idphone=`), both Disallowed by robots.txt and excluded
by the project rule (never store names/phones/emails). Related ads at the
bottom (`span.nadpis`) must also be excluded.

## 10. Misc / risks

* Encoding UTF-8; numeric prices use plain spaces, currency is `€` (U+20AC).
* All recon requests returned HTTP 200 (except the intentional overflow 404);
  no captcha, rate-limit or 403/429 encountered with a 1 s delay and an honest
  User-Agent.
* Open items: direction of `order=1` and possible `order=2` (unverified by
  design — the URLs are Disallowed).
* TOP volume is time-dependent and can occupy more than one page.
