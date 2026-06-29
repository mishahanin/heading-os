# Brain audit - modality reference

Last Updated: 2026-05-28
Last Verified: 2026-05-28
Consumed by: .claude/skills/brain-audit/SKILL.md

For each modality, the audit checks whether any indexed signal exists for the named entity. "Found" means at least one file or entry matching the entity slug or full name. "Not found" goes into the modality-coverage section of the footer. If a modality location is missing on the current workspace (e.g., telegram index not yet built), the audit reports "modality location unavailable" rather than "not found." In V1, the `telegram` modality location is documented but not yet built; expect "modality location unavailable" for every entity until a telegram sync daemon ships in V2.

## Canonical modality list

| Modality | Search location | Match heuristic |
|---|---|---|
| email | `outputs/operations/email-intelligence/state.json` and `outputs/_sync/emails/` | Entity full name or email address appears in indexed thread subject or body |
| telegram | `outputs/_sync/telegram/` (planned, not yet indexed in V1) | Entity full name or Telegram handle appears in indexed chat |
| osint | `outputs/intel/osint/` | Filename contains the entity slug (kebab-case lowercase) |
| crm-log | `crm/contacts/{entity-slug}.md` | File exists; report the `last_touch` value from frontmatter |
| calendar | `outputs/_sync/calendar/` (per-day MD files plus `upcoming.md`) | Entity name appears in event title, body, or attendees list |

## Match heuristics in detail

### Entity slug derivation

The "entity slug" is the kebab-case lowercase form of the entity name with diacritics stripped.

Examples:
- "Sara Okonkwo" -> `sara-okonkwo`
- "Yannis Cole" -> `yannis-cole`
- "31 Concept" -> `31-concept` (company-mode; rarely used in audits)

### Per-modality search

For each modality:

1. Resolve the search location. If the directory or file is missing, record "modality location unavailable."
2. Use Glob for filename matches (osint, calendar by date).
3. Use Grep for content matches (email state JSON, telegram chats, calendar event bodies).
4. Match on either the full entity name (case-insensitive) or the slug (lowercase). One hit is enough to flag the modality as "found."
5. For CRM-log, the existence of the contact file is the match; report the `last_touch` date.

## Adding a new modality

Append a row to the table above with three columns: name, search location, match heuristic. Bump `Last Updated`. The SKILL.md reads the table at runtime; no skill body changes needed unless the new modality requires a different match style than glob or grep.
