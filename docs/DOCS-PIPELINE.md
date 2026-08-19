# Docs pipeline: where every published page comes from

The source-of-truth map for the HEADING OS documentation site (`docs/`, served at
`https://mishahanin.github.io/heading-os/`). Every `docs/*.html` page is either
regenerated from a Markdown source or hand-authored HTML. This file says which,
how to edit each, and how the drift guard keeps them honest.

Consumed by: `.claude/rules/documentation.md` (the propagation rule points here for
the fleet propagation chain and the shared-versus-CEO-only distribution list), and by
anyone editing a page under `docs/`.

Last Updated: 2026-08-20

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

### 1. Markdown-sourced (regenerated) - 23 pages

The `.md` is the source of truth; the `.html` is generated and must never be
hand-edited. Editing the `.md` without regenerating is caught by the drift guard.

`ARCHITECTURE`, `CANOPUS`, `CONFIGURATION`, `DEPLOYMENT`, `DESIGN-CHECK`,
`DOCS-PIPELINE` (this page), `EMERGENCY-PROCEDURES`, `EXTENDING`, `GLOSSARY`,
`HOOKS-REFERENCE`,
`INTEGRATIONS-SETUP`, `MAKE-IT-YOURS`, `MODELS-SETUP`, `PLUGINS`, `QUICKSTART`,
`RELEASE-NOTES`, `RULES-REFERENCE`, `SECURITY-MODEL`, `TELEGRAM-AND-ALERTS`,
`THREAT-MODEL`, `TROUBLESHOOTING`, `engine-data-segregation-contract`,
`memory-lifecycle`.

`memory-lifecycle` carries a Mermaid diagram. The generator renders a ```` ```mermaid ````
fence to a `<pre class="mermaid">` block and injects the vendored `assets/mermaid.min.js`
on that page only, a per-page conditional. Every other page stays byte-identical and
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

**Why not generated from SKILL.md frontmatter:** the published skill cards are far
richer than the terse `x-heading-capability` frontmatter. Each card carries What it
is, What it does, How to use, an example, and Customize. Generating from frontmatter
would gut the catalog. The cards are hand-authored HTML; their source of truth is the
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

## Propagation: from this repo to the fleet

The two sections below moved here verbatim from `.claude/rules/documentation.md`
on 2026-08-20, when that rule was made path-scoped. They give the route a docs or
template change takes to reach each executive clone. They also give the list of
pages that are exec-shared, and the list that stays CEO-only.

## Propagation Chain

The CEO manually initiates updates; each exec machine pulls automatically on a schedule.

### CEO side (manual, initiated by `/push-updates` or equivalent)

When Misha updates any shared content in ceo-main:

1. **Commit + classify** changed files per `config/routing-map.yaml` (private/engine stays CEO-side; corporate is prepared for publish).
2. **Publish to `../heading-os-corporate/`** via `/publish-corporate` or `/push-updates` (corporate-classified files + BUILD.json bump).
3. **Push `../heading-os-corporate/` to GitHub** (`origin/main`) -- manual, by Misha, after confirming the changeset.

`AIOS-for-the-CEO` is not part of this chain (independent OSS repo `mishahanin/AIOS-for-the-CEO` since 2026-04-25; the old `/export-update` skill and `scripts/export-sync.py` are archived).

### Exec side (manual git pull on each exec machine)

The old auto-sync (`workspace-sync.py`, a destructive copy-and-orphan-delete
engine) is retired; no `31C-Sync-{slug}` task / launchd agent / systemd timer is
installed anymore (only the 15-min Sentinel schedule remains). Each exec syncs
with plain git:

- **Code down:** `git pull --ff-only` on the engine clone (the exec's
  `.heading-os`). Engine code ships by cloning the engine repo, not by copying.
- **Corporate content down:** the corporate-consumption seam —
  `python scripts/sync-corporate.py` clones/pulls `heading-os-corporate` into the
  gitignored `.corporate-repo/`, read in place via `get_corporate_root()`, and
  `/sync` refreshes it (deferral lifted 2026-06-26 — CEO cutover complete).
- **Data up / backup:** `python scripts/push-all.py` pushes the exec's own data
  repo (`heading-os-data-{slug}`), which carries `crm/contacts/`. CEO aggregation
  reads each exec's data repo directly via `aggregate-crm.py`.
- **First-run record recovery:** after a clean deploy, a one-shot
  `python scripts/import-legacy-records.py --from <old-records-path>` copies the
  exec's prior `crm/contacts/`, `threads/`, `knowledge/`, and personal `context/`
  off disk (local, non-destructive, idempotent).

The convenience wrapper for the routine pull + backup is `/sync`.

### Worst-case propagation time

Up to whenever the exec next runs `git pull` on their clones. There is no fixed
1-hour cadence anymore; an online exec sees published changes the moment they
pull (or run `/sync`). Offline execs catch up on their next pull.

### What this means for a change in ceo-main

- Same session: visible to CEO immediately (file on disk).
- +minutes: visible in corporate GitHub (once CEO runs `/publish-corporate` + `git push`).
- +0-60 min after GitHub push: visible in each online exec's `corporate/` tree (via their scheduled task).
- Exec session: exec reads their local `corporate/` copy; no network call per read.

## Documentation Distribution

### Shared with all execs (via corporate repo `docs/`):
- `GETTING-STARTED.md` -- Executive onboarding guide (detailed, with all skills)
- `GETTING-STARTED.html` -- Branded HTML version (printable, shareable)
- `EMERGENCY-PROCEDURES.md` / `.html` -- What to do when sync/push/update chain breaks (CEO outage, corporate outage, credential leak, schedule failure)

> The canonical public deployment guide is `docs/DEPLOYMENT.md` (engine-routed,
> not in the templates -> docs synced set), with `docs/QUICKSTART.md` as its
> one-page short form. Neither is exec-distributed via this sync chain.
- `QUICKSTART.md` -- one-page genericized public reference, not a hand-authored CEO guide.

### CEO-only (stays in ceo-main only -- NEVER publish to corporate or exec workspaces):
- `CEO-ADMIN-GUIDE.md` -- Admin workflows, provisioning, offboarding, emergency revocation
- `CEO-ADMIN-GUIDE.html` -- Branded HTML version

The `/publish-corporate` skill and the `sync-docs.py` hook (templates/ -> docs/) include `docs/` in the publish paths. CEO-ADMIN-GUIDE files must NEVER be placed in the corporate repo or any exec workspace.

The milestone table below moved here in the same change. It tracks cleanup that
waits on the whole fleet to reach a state. That is a propagation concern, not an
authoring one.

## Migration Cruft Milestones

Some cleanup cannot land immediately because it depends on the whole fleet reaching a state, not just ceo-main. Track those here so they are not forgotten (2026-06-09 audit #62).

| Item | Blocked on | Remove when | Status |
|---|---|---|---|
| _(none open)_ | | | The last entry, four backward-compat hook shims delegating to `_dispatch.py`, cleared on 2026-08-11. Its blocker was written before the two-part topology hard-cut and outlived the condition it described: the tracked `settings.local.{linux,macos,windows}.json` templates have named `_dispatch.py` since the engine's initial import, so no workspace built from this repository ever referenced a shim. `tests/test_settings_hook_targets.py` now holds that property, which is what the row was really waiting for. |

When an item clears, delete its row and the corresponding files in the same change so this table never carries stale entries.
