---
name: viraid
description: "Viraid virtual assistant -- reads new messages from M's VIRAID Telegram channel, analyzes and categorizes each message (CRM action, calendar, task, research, note), enriches with workspace context (CRM contacts, calendar policy, pipeline), proposes actions for approval, then executes (write tasks, log CRM interactions). Supports `/viraid sweep` to review and triage active tasks. Use when the user says 'viraid', '/viraid', 'check viraid', 'process viraid', 'viraid messages', 'what's in viraid', 'viraid sweep'."
argument-hint: "[action] -- omit for message processing, 'sweep' for task triage"
allowed-tools: "Bash(python3:*), Read, Write"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "2.1"
x-31c-orchestration:
  parallel_safe: partial
  shared_state:
    - crm/contacts/
    - context/pipeline.md
    - outputs/operations/viraid/state.json
    - outputs/operations/viraid/tasks.md
  triggers:
    - viraid
    - check viraid
    - process viraid
    - viraid sweep
x-31c-capability:
  what: >
    Reads new messages from M's VIRAID Telegram channel, categorizes each (CRM action, calendar, task, research, note), enriches with workspace context, proposes priority-tagged actions for approval, then executes - so nothing falls through the cracks.
  how: >
    Run /viraid to process new messages, or /viraid sweep to triage active tasks. State lives in outputs/operations/viraid/state.json and tasks.md; a STOP gate halts before any execution.
  when: >
    Use to drain the VIRAID inbox or triage its task backlog. For general Telegram messaging use /telegram; for the email inbox use /email-intel.
---
# Viraid -- Virtual Assistant for Telegram Task Capture

Reads new messages from the VIRAID Telegram channel (default **M's VIRAID**; set `VIRAID_CHANNEL_NAME` in `.env` to use your own), categorizes them, enriches with workspace context (CRM, calendar, pipeline), proposes structured actions, and executes after Misha's approval. Nothing falls through the cracks.

## State Files

**State lives in the DATA overlay, never the engine.** Resolve the directory once at the start of a
run (from the engine root):

```bash
VIRAID_DIR="$(python3 -c "import sys; sys.path.insert(0,'.'); from scripts.utils.workspace import get_outputs_dir; print(get_outputs_dir())")/operations/viraid"
```

Then `state.json` = `$VIRAID_DIR/state.json`, `tasks.md` = `$VIRAID_DIR/tasks.md`. Read and write
only there. Never write viraid state into the engine tree (`.heading-os/outputs/...` must not be
created) -- a bare relative path resolves against the engine git root, where `outputs/` does not exist.

**Channel name is configurable.** The Telegram channel Viraid reads is NOT hardcoded. Resolve it
once at run start; it comes from the `VIRAID_CHANNEL_NAME` env var (in `.env`), defaulting to
`M's VIRAID` when unset:

```bash
VIRAID_CH="$(python3 -c "import sys; sys.path.insert(0,'.'); from scripts.utils.workspace import load_env, get_workspace_root; load_env(get_workspace_root()); import os; print(os.environ.get('VIRAID_CHANNEL_NAME', 'M'+chr(39)+'s VIRAID'))")"
```

Use `"$VIRAID_CH"` wherever a fetch/delete command names the channel. The canonical fetch commands
below inline this resolver so they work in a single shell call.

- **Ledger:** `$VIRAID_DIR/state.json` -- single source of truth for all processed messages
- **Tasks:** `$VIRAID_DIR/tasks.md` -- active and completed action items

---

## Action Router

| Invocation | Action |
|------------|--------|
| `/viraid` (no args) | Process new messages (Steps 1-9) |
| `/viraid sweep` | Triage active tasks (see Sweep section) |

---

## Priority Classification

Every task gets a priority tag when created:

| Priority | Criteria | Examples |
|----------|----------|----------|
| **P1** | Revenue-impacting, deadline-driven, CEO-required, client/partner-facing | Protocol team assembly, Delta demo, investor follow-up |
| **P2** | Important but not time-critical, internal operations, recurring admin | Audit Tribe 1:1s, roadmap requests, UX check-ins |
| **P3** | Nice-to-have, background research, low urgency | Documentation improvements, exploratory research |

**Classification signals:**
- Explicit urgency words ("ASAP", "urgent", "today", "this week") -> P1
- Revenue/deal context (client names, demos, proposals, contracts) -> P1
- Tribe management, process improvements -> P2
- Research, documentation, nice-to-have -> P3
- When in doubt, classify one level higher (P3 -> P2, P2 -> P1)

---

## Task Aging

Tasks track their creation date. Aging rules:

- **>3 days** without resolution: flagged as **AGING** in sweep and dashboard output
- **>7 days** without resolution: flagged as **STALE** -- requires explicit keep or delete decision
- Aging is computed from the task's creation date (the `YYYY-MM-DD` in the task line)

---

## Dashboard Integration

When generating Viraid output (summary, sweep results), always report:

- **Active tasks by priority:** P1: N, P2: N, P3: N
- **Aging tasks (>3 days):** list count and highlight
- **Completion rate:** completed / (completed + active) as percentage
- This data is also available for the CEO Morning Dashboard (`/dashboard`) to consume

---

## Execution Flow (Message Processing)

Steps 1-9 of the default `/viraid` invocation -- load state, fetch new messages, filter, categorize, enrich, present proposed actions, await approval, execute, update ledger, summarize -- are catalogued in `references/message-processing.md`. The reference carries all the JSON shapes, fetch commands, enrichment rules, calendar-check protocol, decision semantics, and the Step 9 summary template. **Read `references/message-processing.md` DIRECTLY with the Read tool. Do NOT route it through a summarizing subagent -- the exact CLI invocations and JSON shapes must be used verbatim.**

Critical guardrails that stay in this SKILL.md:

- **Canonical fetch command (inlined so it survives any summary):**
  - Returning run: `cd "$(git rev-parse --show-toplevel)" && VIRAID_CH="$(python3 -c "import sys; sys.path.insert(0,'.'); from scripts.utils.workspace import load_env, get_workspace_root; load_env(get_workspace_root()); import os; print(os.environ.get('VIRAID_CHANNEL_NAME', 'M'+chr(39)+'s VIRAID'))")" && python ".claude/skills/telegram/scripts/telegram_client.py" --json read "$VIRAID_CH" --min-id [last_message_id] --limit 100 --reverse`
  - First run: same with `--limit 500` and no `--min-id`.
  - `--json` is a GLOBAL flag that MUST come BEFORE the `read` subcommand. There is NO `fetch-channel`
    subcommand and NO `--after-id` flag -- use `read --min-id`. The script lives at
    `.claude/skills/telegram/scripts/telegram_client.py` (in the engine), never at `scripts/telegram_client.py`.
  - The channel name comes from `$VIRAID_CH` (resolves `VIRAID_CHANNEL_NAME`, default `M's VIRAID`) -- see § State Files.
- **Step 6 STOP gate:** after presenting proposed actions, halt and wait for Misha's explicit approve / modify / skip / keep-in-channel response. No execution before approval.
- **Calendar check is mandatory** for any scheduling-related message -- run `sync-exchange.py --calendar --days 14`, read `reference/ceo-calendar-policy.md`, never propose a slot without verifying it's free.
- **Channel deletion is the default cleanup** for all dispositions except `keep-in-channel`.
- **NEVER create a meeting without Misha's explicit approval of the proposed time slot.**

---

## Sweep Mode (`/viraid sweep`)

Interactive triage of all active tasks. Triggered by `/viraid sweep`, "sweep viraid", or "triage viraid tasks". Full step-by-step flow -- load tasks, present by priority with aging flags, await per-task decision, execute, update state, report -- is in `references/sweep-mode.md`.

Critical guardrails that stay in this SKILL.md:

- **Sweep Step 2 STOP gate:** after presenting tasks by priority, halt and wait for Misha's explicit Complete / Keep / Delegate / Delete decisions per task.
- **Delete removes from Active entirely** (not moved to Completed -- it was never done).

---

## Edge Cases

Full edge-case catalogue (first run, no new messages, backlog draining, media-only, missing CRM contact, corrupted state, channel deletion failure, DB locked retries, legacy task priority assignment, gap detection) is in `references/edge-cases.md`. Read it when any non-happy-path condition is hit.
