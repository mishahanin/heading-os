---
name: email-intel
description: >
  Daily email intelligence processor. Scans incoming + outgoing 31C Exchange emails
  (ceo@31c.io via exchangelib/EWS), groups by conversation thread, categorizes
  for CRM actions, tasks, pipeline updates, knowledge capture, and relationship signals.
  Presents digest for approval before executing. Integrates with /prime for morning briefing.
  Trigger: '/email-intel', 'process emails', 'email digest', 'check my email'.
  Do NOT trigger for: sending email (use send-email.py), reading specific emails,
  email search queries, or Gmail operations.
argument-hint: "[--hours N] [--inbox-only] [--sent-only]"
allowed-tools: "Bash(python3:*), Read, Write, Edit, Glob, Grep"
context: fork
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: partial
  shared_state:
    - crm/contacts/
    - context/pipeline.md
    - outputs/operations/email-intelligence/state.json
    - outputs/operations/email-intelligence/sweep-actions-*.json
  triggers:
    - process emails
    - email digest
    - check my email
    - inbox
x-31c-capability:
  what: >
    Scans incoming and outgoing 31C Exchange email, groups by conversation thread, and proposes CRM logs, tasks, pipeline updates, knowledge notes, and new contacts - all priority-tagged P1/P2/P3.
  how: >
    Run /email-intel (last 24h) with optional --hours N, --inbox-only, --sent-only. Presents context blocks plus one flat numbered action list; you approve by number (1,3,5 | all crm | 2 edit: ... | 4 go | rest skip), then it writes. The numbered list + per-action status persist in sweep-actions-YYYY-MM-DD.json (scripts/email-sweep.py), so an interrupted run is resumable.
  when: >
    Use to triage the inbox. It proposes every action; only the numbers you approve execute. Sends (reply/reply-all/forward) are human-gated and go through send-email.py with full threading. For a multi-channel "what's new" sweep use Morning Comms; for a single ad-hoc reply use /email-respond.
---
# Email Intelligence -- Exchange Inbox Processor

Scans 31C Exchange email (ceo@31c.io), groups conversations, categorizes actionable items, and proposes CRM logs, tasks, pipeline updates, knowledge notes, and new contacts. Nothing ships without Misha's approval.

## State Files

- **State:** `outputs/operations/email-intelligence/state.json` -- processed message IDs, conversation history, stats
- **Tasks:** `outputs/operations/email-intelligence/tasks.md` -- active and completed action items
- **Sweep actions:** `outputs/operations/email-intelligence/sweep-actions-YYYY-MM-DD.json` -- the numbered recommended-action list + per-action status (proposed/approved/skipped/executing/done/failed). Managed by `scripts/email-sweep.py`; this is the resumable trail behind the Phase 3 approval. CLI-readable (`python scripts/email-sweep.py list`).
- **Digests:** `outputs/operations/email-intelligence/digest-YYYY-MM-DD.md` -- daily run records

---

## Action Router

| Invocation | Action |
|------------|--------|
| `/email-intel` (no args) | Process last 24 hours (Phases 0-5) |
| `/email-intel --hours 48` | Process last 48 hours |
| `/email-intel --inbox-only` | Inbox only (skip sent) |
| `/email-intel --sent-only` | Sent only (skip inbox) |

---

## Priority Classification

Every conversation gets a priority tag:

| Priority | Criteria | Examples |
|----------|----------|----------|
| **P1** | Revenue-impacting, deadline-driven, client/partner-facing, investor comms | Deal follow-ups, contract negotiations, investor DD, demo requests |
| **P2** | Important but not time-critical, internal ops, recurring admin | Tribe coordination, vendor inquiries, conference logistics |
| **P3** | Nice-to-have, informational, low urgency | Newsletters, general inquiries, FYI forwards |

**Classification signals:**
- Explicit urgency ("ASAP", "urgent", "today", "by EOD") -> P1
- Revenue/deal context (pipeline companies, pricing, proposals, contracts) -> P1
- Investor or board communication -> P1
- Tribe management, process, internal ops -> P2
- Research, documentation, informational -> P3
- When in doubt, classify one level higher

---

## Execution Flow

### Phase 0 -- State Check

1. Read `outputs/operations/email-intelligence/state.json`
2. If file doesn't exist (first run), initialize in memory:
   ```json
   {
     "last_run": null,
     "last_inbox_datetime": null,
     "last_sent_datetime": null,
     "last_run_status": null,
     "stats": {
       "total_processed": 0,
       "total_conversations": 0,
       "total_crm_entries": 0,
       "total_tasks_created": 0,
       "total_pipeline_updates": 0,
       "total_contacts_created": 0,
       "total_ignored": 0
     },
     "processed_message_ids": [],
     "conversations": {},
     "learned_ignore_senders": []
   }
   ```
3. If file exists but JSON parsing fails, warn: "State file corrupted -- starting fresh." Initialize empty state.
4. Report: last run time, total emails processed lifetime, pending tasks from previous run.
5. If last run < 4 hours ago: warn "Last processed [X hours ago]. Run again? (Respond 'yes' to proceed)". **STOP and wait for confirmation.**
6. Parse arguments: `--hours N` (default 24), `--inbox-only`, `--sent-only`.

### Phase 1 -- Fetch & Process

1. Run: `python scripts/email-intelligence.py --json --hours [N]`
   - Add `--inbox-only` or `--sent-only` flags if specified
2. Parse JSON output.
3. Filter out message IDs already in `processed_message_ids`.
4. Filter out senders in `learned_ignore_senders` (auto-skip, count as noise).
5. Report: "Fetched X emails (Y inbox, Z sent). After filtering: N conversations to review."
6. If 0 conversations remain: report "No new actionable emails since [last_run]." Update state and stop.

### Phase 2 -- Present Email Intelligence Digest

Two parts, per `references/digest-format.md`:

1. **Context blocks.** Group conversations by priority (P1 first, then P2, P3). Render each with the per-conversation context block (CRM/pipeline/Viraid enrichment + summary + commitments). Emit one "internal-skipped" block for all-hands distribution-list (e.g. all-@) threads. These blocks carry NO per-conversation action numbers.
2. **Numbered action list.** Pool EVERY proposed action across ALL conversations into one flat, sequentially-numbered list (1..N, single namespace -- no `P1-A`, no per-conversation `a,b`). Build it by writing the proposed actions to a JSON array and seeding the state machine:
   ```bash
   python scripts/email-sweep.py propose --file <proposed.json> --date YYYY-MM-DD
   python scripts/email-sweep.py list --date YYYY-MM-DD
   ```
   Each action carries a tier tag: `[crm]`/`[task]`/`[contact]`/`[know]` (local write), `[notify]` (pipeline), `[send-gated]` (outbound send). Render the `list` output to the CEO. Proposed-action JSON shape: `references/digest-format.md`.

### Phase 3 -- Approval (MANDATORY)

Present the numbered list and the approval grammar line per `references/digest-format.md`.

**STOP HERE. Wait for Misha's explicit response before proceeding to Phase 4.**

Translate his reply (`1,3,5` | `all crm` | `2 edit: <change>` | `skip 4` | `rest skip` | `4 go`) into state-machine calls:
```bash
python scripts/email-sweep.py approve <ids> --date YYYY-MM-DD
python scripts/email-sweep.py edit <id> --note "<change>" --date YYYY-MM-DD
python scripts/email-sweep.py skip <ids> --date YYYY-MM-DD
```
A `[send-gated]` action sends only after its number is approved. Silence is never approval.

### Phase 4 -- Execute Approved Actions

Read the approved set: `python scripts/email-sweep.py list --date YYYY-MM-DD` (or `pending` to resume after an interruption -- the state file is the authoritative trail of what is done vs. left). Execute each approved action by type, stamping the outcome as you go so a crash mid-batch is recoverable:

```bash
python scripts/email-sweep.py set <id> --status executing --date YYYY-MM-DD   # before
python scripts/email-sweep.py set <id> --status done --note "<result>" --date YYYY-MM-DD  # after
```

By action type:
- **crm_log / task / new_contact / knowledge** (`[crm]`/`[task]`/`[contact]`/`[know]`) -- local workspace writes. Exact formats: `references/execution-templates.md`.
- **pipeline** (`[notify]`) -- deposit a `pipeline_update` notify card to the Action Queue AND write the new stage (see below).
- **send_reply / send_reply_all / send_forward / send_new** (`[send-gated]`) -- send via `scripts/send-email.py`. Use the threaded flags for replies/forwards so the thread, signature, and original attachments are preserved: `--reply`/`--reply-all`/`--forward` with `--match-from`/`--match-subject` (or `--match-id`) to locate the original; `send_new` uses the plain `--to/--subject/--body` form. Apply any `edit` note to the body before sending. send-email.py auto-logs the send to CRM -- do not also write a duplicate crm_log for the same thread.

Exact write formats for the local-write types: `references/execution-templates.md`.

**Pipeline update** -- routed through the Action Queue as a `pipeline_update` **notify** card (R4), reusing the cold-sweep deposit path. Notify is reversible, not a second hard gate (the Phase 3 digest already approved the batch). Exact card schema + producer contract: `references/execution-templates.md` (Pipeline Update Card).

- **Clear advance** (the email unambiguously signals a new stage): read the current stage + stage date from `context/pipeline.md`, then (a) stamp `prev_value={"stage": <current>, "stage_date": <current date>}` on the card BEFORE applying -- this is what `undo_card` restores; (b) edit `context/pipeline.md` to the new Stage + today's Stage Date (the producer writes pipeline state; the daemon never invents it); (c) deposit a `pipeline_update` notify card via `aq.append_cards(workspace_root, [card])` (in-process) or POST `/action-queue/deposit` (external) -- `source="email-intel"`, `priority="P1"`, `citations=[{source: email ref, excerpt: the stage signal}]`, `company=<name>`, `applied_value={"stage": <new>, "stage_date": today}`.
- **Ambiguous advance** (state genuinely unclear -- "28 days quiet, no clear next step"): do NOT auto-edit. Deposit a `note` card (or keep it inline in the digest) asking the CEO to decide -- e.g. "ExampleTelco: 28 days quiet, no clear next step. Stalled or closing?" No pipeline write until the CEO answers.

Undo semantics (honest): `undo_card` restores the card's `prev_value` record and logs the undo; reverting `context/pipeline.md` itself is a one-line manual edit back to `prev_value` (the Action Queue is a proposal/audit surface, not a direct pipeline writer).

### Phase 5 -- Save State & Report

1. Update `outputs/operations/email-intelligence/state.json`:
   - Append all processed message_ids to `processed_message_ids` (cap at 500 -- trim oldest)
   - Update `conversations` dict with thread keys (cap at 200 -- trim oldest)
   - Increment `stats` counters based on actions taken
   - Set `last_run` to current ISO timestamp
   - Set `last_inbox_datetime` / `last_sent_datetime` to latest email timestamps
   - Set `last_run_status` to `"complete"`
   - If user ignored a sender: add to `learned_ignore_senders`

2. Save daily digest: `outputs/operations/email-intelligence/digest-YYYY-MM-DD.md`
   - Full record of conversations processed, actions proposed, decisions made, actions executed
   - Append if digest for today already exists (multiple runs per day)
   - The numbered-action trail (status per action) already persists in `sweep-actions-YYYY-MM-DD.json`; reference it rather than re-tabulating per-action outcomes by hand

3. Report summary:
   ```
   Email Intelligence Complete -- [date]
   - Processed: [N] conversations ([X] emails)
   - CRM interactions logged: [A]
   - Tasks created: [B]
   - Pipeline updates: [C]
   - New contacts added: [D]
   - Commitments tracked: [E]
   - Ignored by user: [F]
   ```

---

## Enrichment Logic

For each conversation, enrich with workspace context before presenting:

**Contact matching:**
- Search `crm/contacts/` for participant names and email addresses
- Pull: name, company, title, type, last_touch, active commitments
- Compute days since last touch -- flag WARNING if >14 days, CRITICAL if >30 days
- If person NOT in CRM: note "Not in CRM -- propose `/crm add`"

**Pipeline matching:**
- Read `context/pipeline.md` for company matches
- Pull: stage, estimated value, next action, stage date
- Flag if stage date is stale (>30 days without movement)

**Viraid overlap check:**
- Read `outputs/operations/viraid/tasks.md` for active tasks mentioning the same contact or company
- Flag overlaps: "Active Viraid task: [description]"

**Internal vs. External classification:**
- All participants @31c.io -> INTERNAL (skip CRM, summarize only)
- Mixed (internal + external) -> EXTERNAL (process normally, focus on external participants)
- All external -> EXTERNAL (process normally)
- Exception: internal emails referencing external deals/contacts -> process for CRM context

---

## Edge Cases

- **First run (no state)** -> Create state with empty arrays, process all emails in time window
- **No new emails** -> Report count and last run date, stop
- **Duplicate conversation in same day** -> Skip if thread key already in today's digest
- **Contact in multiple threads** -> Separate CRM log per thread, single last_touch update
- **Unknown sender not in CRM** -> Propose new contact creation with available info
- **State file corrupted** -> Start fresh with warning
- **Message already processed** -> Skip (processed_message_ids is authoritative)
- **Learned ignore sender** -> Auto-filter, count in noise-filtered total
- **Pipeline update -- ambiguous stage** -> never auto-advance: deposit a `note` card (or keep inline) asking the CEO to decide. Only a CLEAR advance goes through the reversible `pipeline_update` notify card (R4).
- **100+ conversations** -> Process in batches of 20, present each batch for approval

---

## NEVER

- NEVER use Gmail, Google Calendar MCP, or `scripts/gmail-reader.py` -- Exchange ONLY
- NEVER auto-execute actions without Misha's explicit approval
- NEVER log CRM interactions for internal @31c.io emails unless they reference external deals/contacts
- NEVER process calendar invites (handled by Sentinel)
- NEVER create duplicate CRM entries for the same conversation thread on the same day
- NEVER auto-send. `[send-gated]` actions send only after their specific number is approved in Phase 3 (lethal-trifecta human gate); silence or "approve all crm" never sends. The processor proposes sends; the CEO's per-number approval is the gate, and the send goes through `scripts/send-email.py`. **This gate is procedural** -- enforced by this skill honouring the per-number approval, NOT by the Action-Queue code gate (`tool_risk.py`). No daemon consumes `sweep-actions-*.json`; the protection is the skill following this rule. (Pipeline `[notify]` cards, by contrast, ride the code-enforced Action Queue.)
- NEVER log the same conversation twice in the same day
- NEVER guess email addresses -- use what's in the email headers

## Voice Rules

- Use maritime/navigation vocabulary where natural (matching 31C operational language)
- Use hyphens (--) not em dashes
- Refer to the company as "31C" or "31 Concept" (never "31 Concept GmbH")
- Use "Tribe" not "team"
- Product: ODUN.ONE, DPI+
