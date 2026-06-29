---
name: crm
description: "Personal CRM - add, log, radar, find, update contacts"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.3"
argument-hint: "[add|log|radar|find|update|next] [contact] [details]"
allowed-tools: "Read, Write, Edit, Glob"
model: sonnet
x-31c-orchestration:
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
x-31c-capability:
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

### `radar` (default — also runs when no arguments provided)

Display the relationship health dashboard.

1. Run `python scripts/crm-health.py` to get the health report
2. Present the output organized by urgency:
   - **RED** contacts first (overdue — need attention today)
   - **YELLOW** contacts (approaching — plan a touch this week)
   - **Active commitments** due in the next 7 days
   - **GREEN** contacts (on track — for reference)
3. For each RED contact, suggest a specific action (email, call, meeting)
4. **Company-Wide Radar (admin only):** If `.workspace-identity.json` has `role: "admin"`:
   a. Run `python scripts/aggregate-crm.py` to refresh aggregated data
   b. Read the following from `../31c-crm-central/aggregated/`:
      - `company-radar.md` - all contacts with health status across all execs
      - `shared-contacts.md` - contacts tracked by multiple execs
      - `ownership-map.md` - who owns which relationships
   c. Present **Company-Wide View** after personal radar:
      - **Fleet summary:** X execs active, Y total contacts, Z shared contacts
      - **Health breakdown:** RED/YELLOW/GREEN/GRAY counts across all execs
      - **Ownership stats:** contacts per exec, overdue per exec
      - **Shared contacts:** same person tracked by multiple execs (highlight potential conflicts)
      - **Top overdue:** 10 most overdue contacts company-wide with owner
   d. Note: Exec workspaces do NOT get the company-wide view

5. **Full CRM View (admin only):** If the user says "full CRM view" or "CRM dashboard":
   a. Run `python scripts/generate-crm-dashboard.py` to produce the HTML dashboard
   b. Present the output path and a summary of what's in it

After surfacing radar output, suggest: `Run /crm next to see the top 3 follow-ups with drafts ready for review.`

### `next`

Surface the top-3 priority follow-ups with checking-in drafts ready for manual review and send. v0 of this subcommand presents drafts; the CEO decides per-draft and sends manually via `send-email.py`. Auto-send-on-approval is a Phase 3 follow-up.

1. Run `python3 scripts/crm_next.py` to generate today's queue. (Note: filename is snake_case for Python importability — `crm_next.py`, not `crm-next.py`.)
2. Read the output path printed by the script (e.g., `outputs/operations/crm/next-{TODAY}.md`).
3. Present the 3 candidates inline:
   - Candidate index + name + company
   - Stage + days overdue
   - Last interaction excerpt (if any)
   - Draft body (ready for manual send)
4. For each candidate the CEO wants to send: invoke `python3 scripts/send-email.py --to <addr> --subject "<subject>" --body "<body>"` using the draft body. The auto-log hook in send-email.py (Phase 1) handles the last_touch bump + interaction log entry automatically.
5. For drafts the CEO wants to revise: discuss the requested change inline, regenerate the draft body, repeat step 4.
6. For drafts the CEO wants to skip: do nothing. The contact stays in the radar and reappears in tomorrow's queue.

### `add`

Create a new contact. Two-tier model: address book entity (corporate) + relationship record (exec-private).

1. Parse arguments for: name, company, relationship type, region, timezone, email (REQUIRED for auto-log entity resolution to work).
2. Generate slug: `firstname-lastname` (kebab-case).
3. **Check if entity exists.** Look for `crm/address-book/{slug}.md`.
   - If exists: skip address book creation, proceed to relationship record only.
   - If not exists AND user is CEO (admin role): create the address book entry with YAML frontmatter (slug, name, canonical_email, employer, canonical_owner per type-tier table, created today) plus a Profile section.
   - If not exists AND user is exec: append to `personal/.sync/pending-address-book.jsonl` for CEO promotion. Proceed with relationship record creation using the slug; entity will resolve once CEO promotes.
4. **Canonical owner lookup** (only when creating an address book entry):
   - prospect, customer, partner, partner-active, reseller -> `alex-rivera`
   - investor-active, investor-passive, shareholder -> `misha-hanin`
   - tribe, tribe-leadership -> `misha-hanin`
   - government, regulator, media, press, advisor -> `misha-hanin`
   - ecosystem, service-provider, vendor -> `lee-park`
5. Create the relationship record at `crm/contacts/{slug}.md` (CEO) or `personal/crm/contacts/{slug}.md` (exec) with:
   - YAML frontmatter: `entity_ref: {slug}`, `relationship_type`, `last_touch: today`, `created: today`. Optional fields per user input: `cadence`, `source`, `pipeline_company`.
   - **Relevant principles (CEO workspace only, brain-gated):** if on the CEO workspace AND `knowledge/odin-brain/` exists AND `relationship_type` is a deal-bearing/external type (NOT `tribe`/`tribe-leadership`/`inactive`), also stamp an optional `relevant_principles:` YAML list by running `python scripts/odin-principles.py --type {relationship_type} [--stage {stage}] --json` and taking the top slugs. Internal types and exec workspaces (no brain): skip silently, never write the field, never error.
   - Empty Active Commitments + Interaction Log sections.
6. **Tribe member case:** if `type` is `tribe` or `tribe-leadership`, also add a `corporate` rule for the file to `config/routing-map.yaml` (CEO only; execs surface a note).
7. **@31c.io warning:** if `email` contains `@31c.io` AND type is NOT tribe/tribe-leadership, surface a warning: "This contact has a @31c.io email but is not classified as tribe. Update type to 'tribe' if they are a Tribe member."
8. Add to `context/people.md` radar table.
9. Confirm creation with summary including the entity_ref slug.

### `log`

Log an interaction for an existing contact.

1. Parse arguments for: contact name (fuzzy match) and interaction details
2. Search `crm/contacts/` for the matching contact file (match against filename or frontmatter name field)
3. If multiple matches, list them and ask which one
4. If no match, suggest creating a new contact with `/crm add`
5. Determine interaction type from context (Meeting, Call, Email, Event, Note)
6. Add a new entry at the TOP of the Interaction Log section:
   ```
   ### YYYY-MM-DD | Type | Brief Title
   Description of the interaction.
   **Next:** What happens next (if applicable).
   ```
7. Update `last_touch` in the YAML frontmatter to today's date
8. Check Active Commitments — if this interaction addresses any, mark them done
9. Confirm the log entry

### `find`

Search across all CRM contact files.

1. Parse the query from arguments
2. Search using Grep across all files in `crm/contacts/` for matches in:
   - Name, company, title (frontmatter)
   - Region, type (frontmatter)
   - Interaction history (body text)
   - Any keyword match
3. Present results as a compact list: Name | Company | Type | Last Touch
4. If no results in CRM files, also check `context/people.md` for contacts not yet migrated

### `update`

Update a contact's profile information.

1. Parse arguments for: contact name, field to update, new value
2. Find the contact file (same fuzzy match as `log`)
3. Update the specified field in the YAML frontmatter or profile section
4. If updating `type`, recalculate cadence from `crm/config.md` defaults (unless cadence was manually set)
5. **Relevant principles refresh (CEO workspace only, brain-gated):** if `relationship_type` or `pipeline_company` changed AND `knowledge/odin-brain/` exists AND the type is deal-bearing/external (NOT `tribe`/`tribe-leadership`/`inactive`), re-derive `relevant_principles` via `python scripts/odin-principles.py --type {relationship_type} [--stage {stage}] --json`. Skip silently on exec workspaces or internal types.
6. Confirm the update

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
