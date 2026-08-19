---
paths:
  - "docs/**"
  - "templates/**"
  - "reference/**"
  - ".claude/skills/**"
  - ".claude/rules/**"
  - "scripts/**"
  - "plans/**"
---

<!-- version: 2.0.0 | last-updated: 2026-08-20 -->
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

## Propagation Chain and Documentation Distribution

Moved to `docs/DOCS-PIPELINE.md` (sections "Propagation: from this repo to the
fleet", "Propagation Chain", "Documentation Distribution", "Migration Cruft
Milestones"). Read it before publishing to the fleet, and before deciding whether
a page is exec-shared or CEO-only. One prohibition from there is absolute, so it stays resident here too.
CEO-ADMIN-GUIDE files must NEVER be placed in the corporate repo or any exec workspace.

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

## Never Deliver Barebones Documentation

All documentation presented to executives must be:
- **Comprehensive** -- every skill, every workflow, every rule documented
- **Branded** -- HTML versions use 31C design system (dark theme, GT Standard font from `corporate/datastore/brand/fonts/` with Inter fallback, gradient headers)
- **Current** -- version number and last-updated date in footer
- **Actionable** -- step-by-step with examples, not abstract descriptions
