<!-- version: 1.1.2 | last-updated: 2026-07-20 -->
# Documentation Propagation Rule

Last Verified: 2026-07-12

## Always Update Documentation

Documentation lives in two coexisting systems, and one change may touch both:

- **Public docs site** (`docs/*.md`, engine, public). Authored directly under
  `docs/` and rendered to `.html` by the single generator
  `scripts/regenerate-docs-html.py` (full map: `docs/DOCS-PIPELINE.md`). Markdown
  pages regenerate to `.html`; the skills-catalog pages are hand-authored HTML.
- **Operator private templates and overview** (`templates/` and
  `reference/workspace-overview.md`, both in the private DATA overlay, so absent in
  a bare public engine clone but present on the operator workspace). `templates/`
  holds the exec-facing `CLAUDE.md.template`, `GETTING-STARTED`, `CEO-ADMIN-GUIDE`,
  and `EMERGENCY-PROCEDURES`; the active `sync-docs.py` PostToolUse hook mirrors the
  shared ones into `docs/`, and `workspace-health.py` guards that sync.

Whenever ANY of the following change, update the matching docs:

1. **New rule created or existing rule modified** -> Add or update its row in the
   public catalogue `docs/RULES-REFERENCE.md` (right functional group), then
   `python scripts/regenerate-docs-html.py docs/RULES-REFERENCE.md`. On the operator
   workspace, also update the rules list in `templates/CLAUDE.md.template`.
2. **New skill created or existing skill modified** -> Update the public skills
   catalogue page `docs/skills-<category>.html` (hand-authored cards, NOT generated
   from frontmatter), then `python scripts/regenerate-docs-html.py --nav-sync`. On
   the operator workspace, also update the skill table in
   `templates/GETTING-STARTED.md` and `reference/workspace-overview.md`. For the
   router registry, do NOT hand-edit the generated layers
   (`.claude/rules/skill-router.md`, `reference/skill-router/<category>.md`): set the
   skill's `x-heading-routing` frontmatter and run
   `python scripts/generate-skill-router.py` (CI + pre-commit enforce `--check`).
3. **New script created or modified** -> On the operator workspace, update
   `reference/workspace-overview.md`; update the public page that documents the
   behaviour (`docs/EXTENDING.md`, `docs/INTEGRATIONS-SETUP.md`, etc.) and regenerate it.
4. **Workspace structure changes** -> Update `templates/CLAUDE.md.template` and
   `templates/GETTING-STARTED.md` (operator workspace) and the public
   `docs/ARCHITECTURE.md`.
5. **New admin tool created** -> Update `templates/CEO-ADMIN-GUIDE.md` (CEO-only,
   never published to corporate or execs).
6. **Always run the docs drift guard before committing anything under `docs/`:**
   `python scripts/regenerate-docs-html.py --all && git diff --exit-code docs/`.
   Run the generator from the repo `.venv` so its pygments matches the pinned
   toolchain the `docs-html-drift` pre-commit hook uses, avoiding false drift from an
   ambient interpreter.

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

## Version Tracking

Every shared doc in `templates/` and its auto-synced counterpart in `docs/` carries an HTML-comment version marker. For `.md` and `.template` files the marker sits on line 1. For `.html` files the marker is embedded at the top of the `<main>` body during markdown-to-HTML rendering, not on line 1 (which is `<!DOCTYPE html>`).

```
<!-- version: MAJOR.MINOR.PATCH | last-updated: YYYY-MM-DD -->
```

Bump semantics:
- **PATCH** - typo fixes, clarifications that don't change meaning
- **MINOR** - new sections, meaningful content additions, reworded guidance
- **MAJOR** - structural reorganization, removal of sections, breaking changes for anyone following the doc

When editing a template, always update both fields. `workspace-health.py` verifies both markers are present and the date is not older than 90 days (implemented in `check_doc_versions`, with the marker regex and 90-day threshold); it runs as part of the standard health check before `/push-updates`.

## Plans Lifecycle

Active implementation plans live at `plans/{YYYY-MM-DD}-{slug}.md`. A plan is active while its work is in progress; `plans/` root should hold only plans currently being executed or about to be executed.

When a plan is complete (success criteria met or work abandoned):

1. Move it to `plans/archive/{YYYY}/`: `git mv plans/{filename} plans/archive/{year}/`
2. Optionally add a `status:` line to the plan file top (e.g., `status: completed`, `status: abandoned`, `status: superseded by {other-plan}`) to make the outcome searchable.

Archived plans are permanent records - they remain git-tracked and searchable via `grep -r plans/archive/`. Never delete a plan; archive it. This preserves the decision trail for future scrutiny passes and post-mortems.

CEO triage cadence: archive any plan whose success criteria have been met or whose work has been abandoned before the next perf sprint.

## Migration Cruft Milestones

Some cleanup cannot land immediately because it depends on the whole fleet reaching a state, not just ceo-main. Track those here so they are not forgotten (2026-06-09 audit #62).

| Item | Blocked on | Remove when | Status |
|---|---|---|---|
| Four backward-compat hook shims -- `.claude/hooks/{prevent-secrets,protect-corporate,protect-docs,protect-personal-threads}.py` (28-line delegators to `_dispatch.py`) | Every exec's `settings.local.json` referencing `_dispatch.py` directly instead of the per-hook script names | All provisioned execs have re-synced a `settings.local.json` that points PreToolUse at `_dispatch.py` (verify via the next fleet `git pull` round, then `git rm` the four shims in one change) | Open -- shims still referenced by older exec settings; deliberate, never invoked on the CEO machine. (The fifth, `protect-secure.py`, was removed with the vault in Plan 5.) |

When an item clears, delete its row and the corresponding files in the same change so this table never carries stale entries.

## Never Deliver Barebones Documentation

All documentation presented to executives must be:
- **Comprehensive** -- every skill, every workflow, every rule documented
- **Branded** -- HTML versions use 31C design system (dark theme, GT Standard font from `corporate/datastore/brand/fonts/` with Inter fallback, gradient headers)
- **Current** -- version number and last-updated date in footer
- **Actionable** -- step-by-step with examples, not abstract descriptions
