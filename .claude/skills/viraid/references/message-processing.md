# Viraid - Message Processing Flow

Consumed by: `.claude/skills/viraid/SKILL.md` when `/viraid` is invoked with no arguments. Steps 1-9 of the default message-processing pipeline.

> **Data-root note.** `state.json` and `tasks.md` live in the DATA overlay, NOT the engine git root.
> Resolve `$VIRAID_DIR` once (see SKILL.md § State Files):
> `VIRAID_DIR="$(python3 -c "import sys; sys.path.insert(0,'.'); from scripts.utils.workspace import get_outputs_dir; print(get_outputs_dir())")/operations/viraid"`.
> Every `outputs/operations/viraid/...` path below means `$VIRAID_DIR/...`. A bare relative path
> resolves against the engine git root (empty `outputs/`) and must never be created there.

### Step 1 -- Load State

1. Read `$VIRAID_DIR/state.json` (resolves under the data overlay -- see the Data-root note above)
2. If the file doesn't exist (first run), initialize in memory:
   ```json
   {
     "channel_name": "M's VIRAID",
     "last_run": null,
     "last_message_id": 0,
     "stats": {
       "total_processed": 0,
       "total_tasks_created": 0,
       "total_crm_entries": 0,
       "total_ignored": 0,
       "total_deleted": 0,
       "completed_count": 0,
       "completion_rate": 0.0
     },
     "messages": {}
   }
   ```
3. If the file exists but JSON parsing fails, warn the user: "State file corrupted -- starting fresh. All messages will be re-processed." Initialize with empty state as above.

### Step 2 -- Fetch New Messages (Incremental)

1. Determine `last_message_id` from state:
   - If `last_message_id` field exists and > 0, use it.
   - Otherwise, compute from highest numeric key in the `messages` object. If empty, use 0.
   - **Integrity check:** Compare `last_message_id` against the highest key in the `messages` object. If `last_message_id` is more than 5 ahead of the highest tracked key, warn: "Gap detected: last_message_id=[N] but highest tracked message is [M]. Messages [M+1]-[N] may have been skipped." This catches sessions that advanced the pointer without saving all ledger entries.
2. Fetch:
   - If `last_message_id` > 0 (returning run):
     ```bash
     cd "$(git rev-parse --show-toplevel)" && python ".claude/skills/telegram/scripts/telegram_client.py" --json read "M's VIRAID" --min-id [last_message_id] --limit 100 --reverse
     ```
     The `cd` anchors the shell at the workspace root -- a prior skill can leave it in a subdirectory, which breaks the root-relative script path. This fetches ONLY messages newer than the last processed ID, server-side. Zero wasted bandwidth.
   - If `last_message_id` == 0 (first run):
     ```bash
     cd "$(git rev-parse --show-toplevel)" && python ".claude/skills/telegram/scripts/telegram_client.py" --json read "M's VIRAID" --limit 500 --reverse
     ```
     If exactly 500 returned, re-fetch with `--limit 1000` (first-run only).
3. Parse JSON. Messages arrive oldest-first (`--reverse`), no client-side sorting needed.
4. If 100+ new messages accumulate between runs, the first run picks up the oldest 100 (thanks to `--reverse`), advances `last_message_id`, and the next run picks up more. Multiple runs drain the backlog naturally.

### Step 3 -- Filter to Unprocessed

1. Compare fetched message IDs against the `messages` object in `state.json` (safety net for interrupted runs -- with `--min-id`, all fetched messages should be new).
2. Any message ID **NOT** in the ledger = unprocessed -> goes into the working set.
3. Skip messages with empty text AND no media (auto-ignore, but still log in ledger as `"disposition": "ignored", "action_summary": "Empty message, skipped"`).
4. If zero unprocessed messages remain after filtering:
   - Report: "No new Viraid messages. [N] total tracked in ledger. Last run: [date]"
   - Stop.
5. Messages are already sorted oldest-first from `--reverse`.

### Step 4 -- Categorize & Prioritize Each Message

Assign ONE primary category per message:

| Category | Signals |
|----------|---------|
| **CRM Action** | Person name, follow-up, call, email, relationship context |
| **Calendar** | Schedule, meeting, deadline, date/time reference |
| **Task** | Action verb, check, do, make, prepare |
| **Research** | Investigate, find out, look into, what's the status |
| **Note** | Thought, observation, no clear action needed |

Assign ONE priority tag (P1/P2/P3) per the Priority Classification table in SKILL.md.

Rules:
- Ambiguous messages -> assign the most actionable category + "(ambiguous)" flag
- Media-only messages (no text but has media) -> Note + "(media-only)" flag
- A single message can generate multiple actions (e.g., CRM + Task), but gets ONE primary category
- Deal stage-advance language ("demo scheduled/complete", "proposal sent", "in negotiation", "won/lost the deal") additionally triggers a **Pipeline** action when the company is in `context/pipeline.md` -- it co-occurs with the primary category (usually CRM Action). Execution: Step 7 **Pipeline** (a `pipeline_update` notify card + pipeline.md edit).

### Step 5 -- Enrich

For each unprocessed message, enrich based on content:

**Person mentioned:**
- Search `crm/contacts/` for matching contact files
- Pull: name, company, title, last_touch, active commitments, days since last touch
- If person NOT in CRM: note "Not in CRM -- consider `/crm add`"

**Scheduling or meeting mentioned -- MANDATORY calendar check:**
1. Run `python scripts/sync-exchange.py --calendar --days 14` to sync latest calendar
2. Read `outputs/_sync/calendar/upcoming.md` for all upcoming events
3. Read `reference/ceo-calendar-policy.md` for protected blocks, day themes, duration rules
4. Identify available time slots that respect ALL constraints:
   - Protected blocks: no Wed AM (CEO Deep Work 09:30-12:00), no Fri PM (CEO Weekly Review 14:00-15:30, then close after 15:30), no before 09:30, no weekends
   - Day themes: external calls on Wed PM, 1:1s on Thu, etc.
   - Minimum 15-min gap between meetings
   - Maximum 3 consecutive meetings
5. Propose specific available time slots -- **NEVER suggest a time without verifying it's free**
6. Even for focus time blocking (not a meeting with others), still check availability

**Project or topic mentioned:**
- Check `context/pipeline.md` and `context/strategy.md` for related workstreams

### Step 6 -- Present Proposed Actions

**Rendering format (CEO preference, 2026-06-09).** Use **plain-text blocks** with real
newlines. **NEVER use a markdown table or HTML `<br>`** here -- Misha's terminal renders both
badly (columns wrap, `<br>` shows as literal text). One block per message:

```
**Viraid · YYYY-MM-DD · N сообщений**

**1 · P[1/2/3] · [short subject]** *(#[id])*
[one-line framing of the recommendation — what + where it lands]
- [recommendation as a FINISHED result, not "create a task to do X"]
- [each sub-action on its OWN line; letter them a) / b) when there is more than one,
  so the CEO can accept/reject pointwise — "2a yes, 2b no"]

**2 · P1 · [subject]** *(#[id])*
...
```

Rules for the recommendation column:

- **Write the finished result, not a meta-task.** Don't multiply entities. If the deliverable
  is one sentence (e.g. a LinkedIn comment, a Telegram reply), write the actual sentence inline.
  Only frame it as "create a task" when the deliverable is genuinely a multi-step work item.
- **One sub-action per line.** Letter them `a)` / `b)` / `c)` when a single message yields more
  than one action, so the CEO can approve a subset.
- Keep enrichment (CRM context, calendar constraints, pipeline links) to the framing line or a
  short parenthetical — do not pad.
- For a `[send-gated]` / outbound item (LinkedIn comment, email, Telegram), say explicitly that
  it will be shown for approval **before** anything is published or sent.

After all blocks, present the approval grammar on its own line:

```
**Как ответить:** «все» · «1, 2a, 3» (точечно) · «2b не надо» · «4 — перепиши короче» · «skip 3»
```

**Decision semantics:**
- **approve** = execute actions + delete message from channel (default cleanup)
- **modify** = execute with changes + delete from channel
- **skip** = no action, mark as reviewed in ledger, delete from channel
- **keep-in-channel** = execute actions but leave message in Telegram (exception, not default)

**STOP HERE and wait for Misha's response before proceeding.**

### Step 7 -- Execute Approved Actions

For each message, based on Misha's decision:

| Decision | What happens |
|----------|-------------|
| **Approve** | Execute all proposed actions, then delete message from Telegram channel |
| **Modify** | Execute with Misha's specified changes, then delete from channel |
| **Skip** | No action taken; recorded in ledger as `"disposition": "ignored"`; deleted from channel |
| **Keep-in-channel** | Execute actions but leave message in Telegram (exception -- use when Misha explicitly wants to keep a reminder visible) |

**Channel cleanup (default behavior):** After executing actions for any approved/modified/skipped message, automatically delete it from Telegram:
```bash
cd "$(git rev-parse --show-toplevel)" && python ".claude/skills/telegram/scripts/telegram_client.py" delete "M's VIRAID" [msg_id]
```
This prevents stale messages from accumulating in the channel. Only `keep-in-channel` skips deletion.

Execution details:

**Tasks** -> Append to `outputs/operations/viraid/tasks.md` under the `## Active` section (newest at top):
```markdown
- [ ] **YYYY-MM-DD** | `P[1/2/3]` | [Action description] | *[Category]* | Source: Viraid #[id]
```

**CRM** -> For each contact mentioned:
1. Open the contact file in `crm/contacts/`
2. Add interaction log entry (format per `crm/config.md`): type `Note`, date, summary
3. Update Active Commitments if a new commitment was identified

**Pipeline** -> When an approved message signals a deal stage advance (e.g. "demo scheduled/complete", "proposal sent", "in negotiation", "won the deal", "deal lost"):
1. Search `context/pipeline.md` for the company. If not found, skip (note only) -- no pipeline write.
2. For a CLEAR advance, route it through the Action Queue as a `pipeline_update` **notify** card (R4). Deposit DAEMON-FREE (terminal-native queue since 2026-06-27) via the in-process helper, passing the DATA root (the queue lives under `get_data_root()`, NOT the engine root) `scripts/bridge_daemon/sources/action_queue.append_cards(get_data_root(), [card])` — or from a shell, `python scripts/action-queue.py deposit --file <cards.json>`:
   - `action_type="pipeline_update"`, `source="viraid"`, `priority="P1"`
   - `title=f"{company} {current_stage} -> {new_stage} (Viraid #{msg_id})"`
   - `reasoning="Viraid: <message excerpt>. Current stage <current_stage> from pipeline."`
   - `citations=[{"source": f"Viraid #{msg_id}", "excerpt": <message excerpt>}]`
   - `company=<name>` (dedup key)
   - `prev_value={"stage": <current_stage>, "stage_date": <stage_date from pipeline.md>}` -- STAMP BEFORE applying (undo restores this)
   - `applied_value={"stage": <new_stage>, "stage_date": <today>}`
3. Then edit `context/pipeline.md` to the new Stage + today's Stage Date (the producer writes pipeline state; the daemon only marks the card applied). The card gives the CEO a one-click undo record.
4. Ambiguous advances are NOT auto-applied -- present them inline (Step 6) for the CEO to decide, or deposit a `note` card. Dedup is by `company`; a 14-day cooldown suppresses re-proposal of a dismissed card.

**Calendar** -> Propose specific available time slots in the action summary. After Misha approves a specific slot:
1. Create the meeting: `python scripts/sync-exchange.py --create-meeting "Title" --time "HH:MM" --duration N --location "Zoom"`
2. Also add to task file with *Calendar* category for tracking
3. **NEVER create a meeting without Misha's explicit approval of the proposed time slot**

If `outputs/operations/viraid/tasks.md` doesn't exist yet, create it:
```markdown
# Viraid Action Items

> Captured from M's VIRAID Telegram channel. Managed by `/viraid`.
> Last updated: YYYY-MM-DD

## Active

## Completed
```

### Step 8 -- Update State (Ledger)

For **EVERY** message processed in this run (regardless of disposition), add an entry to the `messages` object in `state.json`:

```json
"[msg_id]": {
  "text": "[first 100 chars of message text]",
  "date": "[message date from Telegram]",
  "disposition": "task|crm|ignored|note",
  "priority": "P1|P2|P3",
  "action_summary": "[what was done, e.g. 'Added task: Follow up with Alex re ISO']",
  "channel_deleted": true,
  "processed_at": "[current ISO timestamp]"
}
```

The `channel_deleted` field tracks whether the message was removed from Telegram (default: `true` for all dispositions except `keep-in-channel`).

Also update:
- `last_run` -> current ISO timestamp
- `last_message_id` -> highest message ID seen in this run (or keep existing value if no new messages)
- `stats` counters -> increment based on dispositions in this run
- `stats.completed_count` -> count of items in the Completed section of tasks.md
- `stats.completion_rate` -> `completed_count / (completed_count + active_count)` as float (0.0 to 1.0)

Write the updated state to `$VIRAID_DIR/state.json` (data overlay -- never the engine tree; see the Data-root note at the top).

### Step 9 -- Summary

```
### Viraid Complete -- [date]
- Messages processed: [N]
- Tasks added: [N] (P1: [N], P2: [N], P3: [N])
- CRM entries: [N]
- Ignored: [N]
- Deleted: [N]
- Total tracked in ledger: [N]
- Active tasks: [N] (P1: [N], P2: [N], P3: [N]) | Aging: [N]
- Completion rate: [N]%
```
