# Docs pipeline: where every published page comes from

The source-of-truth map for the HEADING OS documentation site (`docs/`, served at
`https://mishahanin.github.io/heading-os/`). Every `docs/*.html` page is either
regenerated from a Markdown source or hand-authored HTML; this file says which,
how to edit each, and how the drift guard keeps them honest.

Last Updated: 2026-07-08

## The one generator

`scripts/regenerate-docs-html.py` is the single docs generator. It has four modes:

| Mode | What it does |
| --- | --- |
| `python scripts/regenerate-docs-html.py <md>` | Regenerate one md-sourced page from its `.md`. |
| `--all` | Regenerate every tracked `.md`/`.html` pair, then nav-sync + search-inject the hand-authored pages, then rebuild the search index. |
| `--nav-sync` | Rewrite the sidebar nav block and inject the search box on hand-authored pages (those with no `.md`), then rebuild the search index. |
| `--search-index` | Rebuild `docs/assets/search-index.json` only (one record per `<main class="content">` section across every `docs/*.html`). |
| `--check` | List stale pairs (a `.md` newer than its `.html`) without changing anything. A fast local staleness hint, not the authoritative guard. |

The sidebar nav is defined once in `SITE_NAV_GROUPS`; the light theme lives in
`docs/assets/docs.css`; client-side search is `docs/assets/search.js` backed by
`docs/assets/search-index.json`.

## Two page classes

### 1. Markdown-sourced (regenerated) - 19 pages

The `.md` is the source of truth; the `.html` is generated and must never be
hand-edited. Editing the `.md` without regenerating is caught by the drift guard.

`ARCHITECTURE`, `CONFIGURATION`, `DEPLOYMENT`, `DOCS-PIPELINE` (this page),
`EMERGENCY-PROCEDURES`, `EXTENDING`, `GLOSSARY`, `HOOKS-REFERENCE`,
`INTEGRATIONS-SETUP`, `MAKE-IT-YOURS`, `MODELS-SETUP`, `PLUGINS`, `QUICKSTART`,
`RULES-REFERENCE`, `SECURITY-MODEL`, `TELEGRAM-AND-ALERTS`, `THREAT-MODEL`,
`TROUBLESHOOTING`, `engine-data-segregation-contract`, `memory-lifecycle`.

`memory-lifecycle` carries a Mermaid diagram. The generator renders a ```` ```mermaid ````
fence to a `<pre class="mermaid">` block and injects the vendored `assets/mermaid.min.js`
on that page only (a per-page conditional), so every other page stays byte-identical and
zero-JS. A brand-new md page has no `.html` sibling yet, so it is created once with
single-file `python scripts/regenerate-docs-html.py docs/<name>.md`; `--all` maintains it
thereafter.

**To edit:** change the `.md`, run `python scripts/regenerate-docs-html.py <md>`
(or `--all`), commit both files.

### 2. Hand-authored HTML (nav/search-injected) - 14 pages

There is no `.md`. The `.html` body IS the source of truth. The generator never
rewrites the body; `--nav-sync` only rewrites the sidebar nav block and injects
the search box, and `--search-index` re-reads the body into the search index.

Original 6 hand-authored pages: `index`, `prerequisites`, `daemons`,
`memory-odin`, `data-structure`, `skills-mcp-plugins`.

The 8 skills-catalog category pages, split out of the former 191 KB
`skills-mcp-plugins.html` monolith by `scripts/dev/split-skills-catalog.py` (a
one-time deterministic splitter kept for provenance): `skills-intel`,
`skills-communication`, `skills-content-design`, `skills-crm`, `skills-strategy`,
`skills-operations-daily`, `skills-operations-quality`,
`skills-operations-infra`. Each carries the rich per-skill cards verbatim;
`skills-mcp-plugins.html` is now the index over them plus the hand-authored MCP
servers and Plugins sections.

**Why not generated from SKILL.md frontmatter:** the published skill cards
(What it is / What it does / How to use / example / Customize) are far richer than
the terse `x-heading-capability` frontmatter, so generating from frontmatter would
gut the catalog. The cards are hand-authored HTML; their source of truth is the
`.html`, exactly like the other hand-authored pages.

**To edit:** change the `.html` `<main class="content">` body directly, then run
`python scripts/regenerate-docs-html.py --nav-sync` so the nav, search box, and
search index stay current (the `--nav-sync` mode rebuilds the index too). Commit the
page plus `search-index.json`.

## The drift guard

Two mechanical guards in CI (`.github/workflows/ci.yml` `guards` job) and the
pre-commit hook set keep the published pages honest:

1. **Docs HTML in sync (F-8.1):** `regenerate-docs-html.py --all` then
   `git diff --exit-code docs/`. The `--all` mode regenerates every md-sourced page,
   nav-syncs and search-injects the hand-authored pages, and rebuilds the search
   index in one pass. Fails on an md-sourced `.html` that was not
   regenerated from its `.md`, a stale nav injection or missing search box on a
   hand-authored page, or a stale `search-index.json`. Hand-authored page bodies
   are the source of truth, so a body edit that is committed as-is is correct and
   does not trip the guard.
2. **README numbers in sync (F-8.3):** `scripts/dev/check-readme-numbers.py`
   re-derives the security-test count and guard-layer count and fails if the
   README or `docs/index.html` "By the numbers" block disagrees.

The matching pre-commit hook (`docs-html-drift`) runs the same regenerate +
`git diff` locally on any `docs/*.md`, `docs/*.html`, or generator change, so
drift is caught before it reaches CI.

## Adding a new docs page

- **Markdown-sourced:** add `docs/<NAME>.md`, run `regenerate-docs-html.py <md>`,
  add the page to `SITE_NAV_GROUPS`, run `--nav-sync --search-index`.
- **Hand-authored:** author `docs/<NAME>.html` with the `SITE_SHELL` structure
  (sidebar skeleton + `<main class="content">` body), add it to
  `SITE_NAV_GROUPS`, run `--nav-sync` so nav + search cover it.

Either way, run the drift guard locally before committing:
`python scripts/regenerate-docs-html.py --all && git diff --exit-code docs/`.
