# UI build tooling (Tailwind CSS + Basecoat)

This folder is **build-time only**. The running Flask app does not need Node;
the generated `../src/web/static/app.css` is committed to the repository.

## One-time setup

```
cd tools
npm install
```

## Build the stylesheet (exact command)

```
cd tools
npm run build
```

which is exactly:

```
tailwindcss -i ./input.css -o ../src/web/static/app.css --minify
```

Run it after changing:

* `tools/input.css` (imports + project component classes),
* `../src/web/static/theme.css` (all theme tokens, colours and fonts),
* any Jinja template under `../src/web/templates/`, or
* `../src/web/static/app.js` (class names used by JS-generated markup).

## Files

| Path | Purpose |
| --- | --- |
| `package.json` | Pinned build dependencies (Basecoat, Tailwind, lucide-static, Geist). |
| `input.css` | Tailwind entrypoint: imports Tailwind + Basecoat Vega + `theme.css`, registers app semantic colours, defines project component classes. |
| `../src/web/static/theme.css` | **The only file to edit to re-skin the app.** shadcn/ui tokens for light/dark, `@font-face`, font tokens. |
| `../src/web/static/app.css` | Generated stylesheet (committed). Do not edit by hand. |

## Swapping the theme

Replace the token values in `../src/web/static/theme.css` (for example with a
theme from <https://tweakcn.com>), then rebuild:

```
npm run build
```

The vendored Basecoat files under `../src/web/static/vendor/` are never edited;
all customisation happens through `theme.css` and `input.css`.
