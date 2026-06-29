<!-- version: 1.0.0 | last-updated: 2026-04-28 -->

# Output File Naming

Last Verified: 2026-05-15

Always-active rule. Governs how skills name files they produce in `outputs/`. Keeps the directory searchable, sortable, and traceable as it grows past several thousand files.

## Standard form

```
YYYY-MM-DD_{type}_{slug}.{ext}
```

- **`YYYY-MM-DD`** — ISO date the artifact was produced. Always present, always first. Sorts chronologically with `ls`.
- **`{type}`** — short content-type identifier in kebab-case. Examples: `meeting-prep`, `dashboard`, `linkedin-post`, `osint-brief`, `proposal`, `letter`, `dossier`, `audit`. The type tells a reader what kind of artifact this is without opening it.
- **`{slug}`** — lowercase kebab-case description, ≤40 characters total. Names the specific subject: who, what, where. Strip articles (`the`, `a`), keep proper nouns. Examples: `exampletelco-q2`, `mwc-prep-sara-okonkwo`, `humanization-rule-deep-research`, `2026-w17-state-check`.
- **`{ext}`** — `md`, `html`, `pdf`, `docx`, `pptx`, `json` as appropriate. Always lowercase.

## Examples

```
outputs/operations/workspace/2026-04-28_audit_deep-workspace.md
outputs/operations/workspace/2026-04-28_audit_deep-workspace.html
outputs/operations/scrutiny/2026-04-28_scrutiny_deep-audit-report.md
outputs/intel/osint/2026-04-15_osint_exampletelco.md
outputs/operations/dashboard/2026-04-22_dashboard_morning.html
outputs/content/linkedin/2026-04-28_linkedin-post_order-and-chaos.md
outputs/deliverables/letters/2026-04-21_letter_northgate-capital-introduction.pdf
```

## When the standard does not fit

Some skills already have locked file naming conventions. Where they exist, the locked form wins:

- **Corporate documents** (`/corporate-letter`, `/proposal`, `/partnership-doc`, `/official-doc`, `/xpager`) use `YYYY-MM-DD_{doctype}_{recipient-slug}_{short-subject-slug}.{ext}` per `.claude/rules/corporate-docs.md` and `reference/corporate-style-guide.md`. That form is authoritative for those five doctypes.
- **Operational state files** (`threads/{business,personal}/`, `plans/`) use `{YYYY-MM-DD}-{slug}.md` (date-prefixed, no `{type}` segment). The dir name conveys the type. Subdirectory + slug is sufficient for sortability and search.
- **State files** (e.g., `outputs/operations/email-intelligence/state.json`) - runtime state, name fixed.
- **Sync artifacts** (`outputs/_sync/calendar/YYYY-MM-DD.md`) - one file per day, name fixed.
- **Cache files** (`outputs/browser/firecrawl-cache/<sha>.json`) - content-addressed, name is the hash.

When a skill produces a sequence of related files for the same task, group them in a single timestamped subdirectory:

```
outputs/intel/osint/2026-04-15_osint_exampletelco/
├── brief.md
├── brief.html
├── sources.json
└── images/
```

The subdirectory name uses the standard form; files inside use short descriptive names.

## What this rule prevents

Without consistent naming, `outputs/` accumulates files like `report.md`, `final.pdf`, `dashboard-new.html`, `meeting prep ahmed (2).docx`. Search becomes impossible. Sorting by date does not work. Two artifacts about the same subject from different dates collide.

The standard form is mechanical, not aesthetic. The audit pipeline, dashboard generator, and search tooling all expect `YYYY-MM-DD_*` prefixes and benefit from consistent naming.

## Authoring guidance

When a skill produces a new artifact, decide three things in this order:

1. **Date.** Today's date in ISO format. Always present.
2. **Type.** What kind of artifact is this in one or two words? Pick from existing precedent (see Examples) before inventing a new type. If a new type is genuinely needed, it should be plainly intelligible to a future reader.
3. **Slug.** What is the most specific identifying phrase about this artifact? Strip articles. Use proper nouns where they identify (`exampletelco`, `northgate-capital`, `sara-okonkwo`). Do not include the type word again (`osint_osint-exampletelco` is redundant).

Skills that hardcode their own naming schemes should be updated to match this standard during their next edit, unless they fall under "When the standard does not fit" above.

## Change control

Changes to this rule require Misha's explicit approval. The standard form should remain stable - changing it after the workspace has accumulated outputs invalidates the search and sort pattern for everything written before the change.
