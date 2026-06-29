# Email-Intel - Digest Format

Consumed by: `.claude/skills/email-intel/SKILL.md` Phase 2 (Present digest) + Phase 3 (Approval).

Two parts: per-conversation **context** blocks (what came in, enriched), then ONE flat
**numbered action list** across all conversations (what to do). The numbered list -- not
per-conversation codes -- is the approval surface, and it is backed by a persisted state
file via `scripts/email-sweep.py` so a half-finished sweep is resumable.

Last Updated: 2026-06-09 (replaced per-conversation `P1-A: a,b` notation with a single
numbered action list + persisted state machine).

## Part 1 - Per-Conversation Context Block

Group conversations by priority (P1 first, then P2, P3). For each, render context only -
no per-conversation action numbers (actions are pooled into the global list in Part 2):

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[P1/P2/P3] | [INBOUND/OUTBOUND/EXCHANGE] | [message_count] emails
Thread: "[subject/topic]"
Participants: [name] <[email]>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRM: [name] | [company] | [type] | Last touch: [days]d ago [WARNING if >14d]
Pipeline: [company] | [stage] | Est. [value]  (or "No pipeline match")
Viraid: [overlap info or "No overlap"]

Summary: [one-line summary of the thread]

[If commitments detected]
Commitments:
  - THEM: [what] by [when]
  - US: [what] by [when]

Relationship: [WARMING/STABLE/COOLING]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## Internal-Skipped Block

For INTERNAL emails (all @31c.io) that were skipped:

```
INTERNAL (skipped): [N] conversations between Tribe members. No CRM action.
  - [Subject 1] ([participants])
  - [Subject 2] ([participants])
```

## Part 2 - The Numbered Action List (the approval surface)

After the context blocks, pool EVERY proposed action across ALL conversations into one
flat, sequentially-numbered list. No `P1-A`, no per-conversation `a,b` - one namespace,
1..N. Each action is verb-first and carries a tier tag on the right:

| Tier tag | Meaning | What happens on approval |
|---|---|---|
| `[crm]` `[task]` `[contact]` `[know]` | local workspace write | applied immediately (reversible by edit) |
| `[notify]` | pipeline_update | deposited as an Action-Queue **notify** card (auto-applies, one-click undo) |
| `[send-gated]` | outbound reply / reply-all / forward / new email | **nothing sends until you approve that number** (lethal-trifecta human gate, enforced procedurally by this skill via your per-number approval -- not by the Action-Queue code gate) |

Build the list by writing the proposed actions to a JSON array and calling:

```bash
python scripts/email-sweep.py propose --file <proposed.json> --date YYYY-MM-DD
python scripts/email-sweep.py list --date YYYY-MM-DD
```

`list` renders exactly what the CEO sees:

```
RECOMMENDED ACTIONS -- sweep 2026-06-09 (5 action(s))
  [ ]  1. CRM-log Chris Doyle - mNDA mutual accept             [crm]
  [ ]  2. Reply-> Pat Nolan: how was the partner meeting        [send-gated]
  [ ]  3. Pipeline: Northwind stage-date -> 2026-06-02          [notify]
  [ ]  4. Forward-> Sam + Lee: suspicious invoice               [send-gated]
  [ ]  5. Task: chase contract comments (Sam)                   [task]

> approve: 1,3,5 | 2 edit: <change> | 4 go | rest skip
```

Each proposed-action dict in the JSON payload:

```json
{
  "type": "crm_log|task|new_contact|knowledge|pipeline|send_reply|send_reply_all|send_forward|send_new",
  "title": "verb-first one-liner the CEO reads",
  "priority": "P1|P2|P3",
  "target": "contact-slug | company | recipient",
  "detail": { "type-specific payload the executor consumes (see execution-templates.md)" }
}
```

`email-sweep.py` assigns the id, resolves the tier from `type`, and sets `status=proposed`.
Unknown types floor at `[send-gated]` (friction-maximal default).

## Approval Grammar (Phase 3)

The CEO replies against the numbers. All of these are valid; plain English is also accepted
(the numbers just remove the ambiguity the old half-code/half-prose notation had):

| Reply | Effect |
|---|---|
| `1,3,5` | approve those actions |
| `all` | approve every proposed action |
| `all crm` / `all notify` / `all send` | approve every action of that tier |
| `2 edit: drop the second paragraph` | approve #2, recording the change as a note the executor applies |
| `skip 4` / `rest skip` | decline #4 / decline everything still proposed |
| `4 go` | approve #4 (natural phrasing for a send) |

Translate the reply into `email-sweep.py` calls:

```bash
python scripts/email-sweep.py approve 1 3 5 --date YYYY-MM-DD
python scripts/email-sweep.py edit 2 --note "drop the second paragraph" --date YYYY-MM-DD
python scripts/email-sweep.py skip 4 --date YYYY-MM-DD
```

**STOP after presenting the list. Wait for the CEO's explicit reply before executing.**
A `[send-gated]` action sends only after its number is approved - silence is never approval.
