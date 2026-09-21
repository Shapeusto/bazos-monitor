# Third-party software

This project bundles a small amount of third-party front-end software. All of
it is served locally from `src/web/static/`; the running application makes **no
requests to any external host** (no CDNs, no web fonts from Google, no icon
fonts).

| Component | Version | Licence | Used for | Vendored / generated file(s) |
| --- | --- | --- | --- | --- |
| [Basecoat](https://github.com/hunvreus/basecoat) (`basecoat-css`) | 1.0.2 | MIT | shadcn/ui design system for vanilla HTML/CSS/JS | Compiled into `src/web/static/app.css`; JS bundle at `src/web/static/vendor/basecoat/basecoat.min.js`; licence at `src/web/static/vendor/basecoat/LICENSE.md` |
| [Tailwind CSS](https://tailwindcss.com) | 4.3.3 | MIT | CSS engine used to compile `app.css` (build-time only) | Output: `src/web/static/app.css` |
| [@tailwindcss/cli](https://www.npmjs.com/package/@tailwindcss/cli) | 4.3.3 | MIT | Standalone build CLI (build-time only) | `tools/node_modules` (not shipped) |
| [Lucide](https://lucide.dev) (`lucide-static`) | 1.47.0 | ISC | Icon set (inline SVG) | Inlined in `src/web/templates/_icons.html` and in `src/web/static/app.js`; licence at `src/web/static/vendor/lucide-LICENSE` |
| [Geist Sans](https://vercel.com/font) (`@fontsource-variable/geist`) | 5.3.0 | OFL-1.1 | UI typeface (variable, latin + latin-ext) | `src/web/static/fonts/geist-latin-wght-normal.woff2`, `src/web/static/fonts/geist-latin-ext-wght-normal.woff2` |

## Notes

* Basecoat 1.0.2 is plain vanilla JavaScript (no React, no Alpine.js). Only the
  all-in-one bundle `dist/js/all.min.js` is vendored.
* Tailwind and `@tailwindcss/cli` are **build-time only**; the application does
  not need Node at runtime. `app.css` is committed.
* Geist is self-hosted as two `woff2` variable-font files (latin and latin-ext;
  the latter carries the Slovak diacritics). No Google Fonts.
* Icons are copied from `lucide-static` as inline SVG. Only the icons actually
  used by the UI are included.
* The `@font-face`/token declarations for the font live in
  `src/web/static/theme.css`.

Exact dependency versions are pinned in `tools/package.json`.
