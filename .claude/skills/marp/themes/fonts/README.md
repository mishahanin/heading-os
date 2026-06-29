# Marp theme fonts

The 31C marp theme (`../31c.css.tmpl`) references two proprietary typefaces:

- **GT Standard** (Grilli Type) — `GT-Standard-*.woff2`
- **31C custom display face** — `31CHorizontalT03-*.woff2`

These are **licensed commercial fonts and are not redistributed in this repo.**
They are gitignored. To render the branded theme with its intended typography,
drop your own licensed `.woff2` files into this directory using the filenames the
theme expects (see the `src: url('{FONTS_DIR}/...woff2')` lines in `31c.css.tmpl`).

When the files are absent, marp falls back to a system sans-serif — the deck still
renders, it just is not brand-exact.
