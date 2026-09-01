---
name: crm
x-heading-requires: ["email"]   # F-7.1: optional-dependency extras this skill needs
description: "Personal CRM - add, log, radar, find, update contacts"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.3"
argument-hint: "[add|log|radar|find|update|next] [contact] [details]"
allowed-tools: "Read, Write, Edit, Glob"
model: sonnet
x-heading-orchestration:
  parallel_safe: partial
  shared_state:
    - crm/contacts/
    - context/people.md
    - config/routing-map.yaml
  triggers:
    - crm add
    - crm log
    - crm radar
    - crm find
    - crm update
    - crm next
    - check CRM
    - contact health
x-heading-capability:
  what: >
    The personal CRM - add contacts, log interactions, update records, find
    people, and surface a health radar of who has gone overdue across your
    relationship base.
  how: >
    Run /crm <add|log|radar|find|update|next> <contact> <details>. Writes to
    crm/contacts/ and keeps context/people.md in sync.
  when: >
    Use to record or query a relationship. For drafting nudges to many
    overdue contacts use /cold-sweep; for Google address book use
    /google-contacts.
x-heading-routing:
  category: CRM
  triggers:
    - crm add
    - crm log
    - crm radar
    - crm find
    - crm update
    - check CRM
    - contact health
  exclusions:
    - N/A
  compound: 'No'
  router: auto
---
# Personal CRM

Manage contacts, log interactions, track relationship health, and surface follow-up priorities.

## Workspace-Aware Paths

This skill works across both CEO and exec workspaces. On startup, read `.workspace-identity.json` from the workspace root to determine workspace type.

| Resource | CEO Workspace | Exec Workspace |
|----------|--------------|----------------|
| Personal contacts | `crm/contacts/` | `personal/crm/contacts/` |
| Tribe contacts (shared) | `crm/contacts/` (type: tribe*) | `corporate/crm/contacts/` (read-only) |
| CRM config | `crm/config.md` | `corporate/crm/config.md` |
| People index | `context/people.md` | `context/people.md` (personal root) |

All path references below (e.g., `crm/contacts/`) should be resolved using this mapping based on the detected workspace type.

**Tribe contacts are corporate-wide.** In exec workspaces, Tribe member contacts live in `corporate/crm/contacts/` (read-only, synced from CEO workspace). When searching or displaying contacts, always check BOTH personal and corporate CRM directories. When logging interactions for Tribe members in exec workspaces, log to the personal copy if it exists, otherwise note that the contact is read-only corporate.

## Variables

- `$ARGUMENTS` — Subcommand and parameters. Format: `[action] [details]`

## Actions

Parse the first word of `$ARGUMENTS` to determine the action:

**Read `references/actions.md` before you run any action below.** It holds the
full step sequence, the canonical-owner lookup, and the record templates. The
table names only the command lines and the guards.

| Action | Commands and guards | What it does |
|---|---|---|
| `radar` (default, and the no-argument case) | `python scripts/crm-health.py`. Admin role only, from `.workspace-identity.json`: `python scripts/aggregate-crm.py` for the company-wide view, and `python scripts/generate-crm-dashboard.py` for the full HTML dashboard. Execs never get the company-wide view. | Relationship health dashboard, ordered RED, then YELLOW, then commitments due within 7 days, then GREEN. |
| `next` | `python3 scripts/crm_next.py` to build today's queue. Present all 3 drafts and WAIT. Send only what the CEO names, one at a time, with `python3 scripts/send-email.py --to <addr> --subject "<subject>" --body "<body>"`. | Top-3 priority follow-ups with drafts ready for per-draft CEO review. |
| `add` | Warn when `email` sits on the configured corporate mail domain and the type is neither `tribe` nor `tribe-leadership` (resolution: `references/actions.md`). On the CEO workspace only, and only for a deal-bearing external type: `python scripts/odin-principles.py --type {relationship_type} [--stage {stage}] --json`. | Create the address book entity plus the relationship record, then index the contact in `context/people.md`. |
| `log` | No script. Ask which contact when several match. Suggest `/crm add` when none match. | Add a dated interaction entry at the TOP of the Interaction Log, then bump `last_touch`. |
| `find` | Grep over `crm/contacts/`. No writes. | Search name, company, title, region, type, and interaction history. |
| `update` | On the CEO workspace only, and only for a deal-bearing external type: `python scripts/odin-principles.py --type {relationship_type} [--stage {stage}] --json`. | Change one profile field, then recalculate cadence when `type` changed. |

After surfacing radar output, suggest: `Run /crm next to see the top 3 follow-ups with drafts ready for review.`

`next` presents drafts only. The CEO decides per draft and sends manually.
Auto-send-on-approval is a Phase 3 follow-up, not current behaviour.

## Context Loading

Read `.workspace-identity.json` first to determine workspace type, then load these files using the resolved paths from the Workspace-Aware Paths table above:
- CRM config (cadence defaults, health thresholds) - CEO: `crm/config.md`, exec: `corporate/crm/config.md`
- `context/people.md` — quick-reference index (from personal root)
- `context/pipeline.md` — deal context (when logging deal-related interactions)

## Rules

- Always use today's date (YYYY-MM-DD format) when logging interactions
- Interaction log entries are reverse-chronological (newest first)
- When creating contacts, match the voice and format of existing contact files in `crm/contacts/`
- If a logged interaction changes the relationship dynamic, note it in the profile section
- Keep interaction summaries concise — 1-3 sentences max
- Use hyphens (-), never em-dashes
- When adding or editing any contact, always ask where they live (for timezone). Use IANA timezone names (e.g., `America/New_York`, `America/Winnipeg`, `Asia/Jerusalem`). If the user does not explicitly define a different operating timezone, omit `operating_timezone` from the YAML - it defaults to `timezone`.
- The `owner` field in contact frontmatter is auto-injected by the CRM sync script. Execs should never set it manually - it is populated automatically during sync based on the workspace identity.
