# Marp theme fonts

The 31C marp theme (`../31c.css.tmpl`) references two proprietary typefaces:

- **GT Standard** (Grilli Type) — `GT-Standard-*.woff2`
- **31C custom display face** — `31CHorizontalT03-*.woff2`

These are **licensed commercial fonts and are not redistributed in this repo.**
They are gitignored. To render the branded theme with its intended typography,
drop your own licensed `.woff2` files into this directory, then register each
filename in your own `<data-root>/config/brand-assets.json` under the key the
theme's `{FONT_*}` placeholder names (`{FONT_GT_M_MEDIUM}` reads the manifest key
`font_gt_m_medium`, and so on). The theme no longer spells any filename itself;
`scripts/utils/brand_assets.py` explains why.

When the files are absent, marp falls back to a system sans-serif — the deck still
renders, it just is not brand-exact.
