---
name: queue-draft
description: "Compose a short message and deposit it into the Action Queue as a GATED draft card that waits for an explicit human approve before it can ever send. Use to stage an outbound draft (e.g. a demo email) for later review, and as the reference draft-tier skill the headless runner (`heading skill queue-draft`) exercises. NEVER auto-trigger - explicit `/queue-draft` (or an explicit headless run) only. It deposits a draft; it NEVER approves, sends, or calls a send transport."
allowed-tools: "Read, Write, Bash(python3 scripts/action-queue.py deposit:*)"
disable-model-invocation: true
argument-hint: "[recipient] :: [subject] :: [message body]"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-heading-orchestration:
  parallel_safe: false
  shared_state: ["outputs/operations/action-queue/queue.json"]
  triggers: []
x-heading-capability:
  what: >
    Deposits one GATED draft card (an email_send draft) into the Action Queue
    from a short message, so an outbound draft is staged for human review. It
    stages only; the card cannot send until a human runs the approve gate.
  how: >
    Explicit invocation only - type /queue-draft "[recipient] :: [subject] ::
    [body]", or run it headless via `heading skill queue-draft`. It writes a
    one-card JSON and runs `action-queue.py deposit`; the deposited card lands
    tier=gated and status=pending.
  when: >
    Use to stage a draft for later approval, or as the reference draft-tier
    skill proving the headless send boundary. To actually send an approved
    card use /queue approve (human only); to write an email draft to chat with
    no queue entry use /email-draft.
x-heading-routing:
  category: Operations
  label: /queue-draft
  triggers:
    - NEVER auto-trigger. Explicit `/queue-draft` only. Deposits one GATED draft card into the Action Queue; never approves or sends.
  exclusions:
    - approve/send an existing card -> /queue (human approve only)
    - draft an email to chat with no queue entry -> /email-draft
  compound: 'No'
  router: manual
---

# Queue Draft

Stage a short message as a **gated** draft in the Action Queue. This skill is the
reference draft-tier skill. It demonstrates, end to end, that a headless run can
DRAFT and DEPOSIT but can never APPROVE or SEND. The deposited card floors to
tier `gated` (an `email_send` draft), so it sits `pending` until a human runs the
approve gate. Nothing here sends.

## Voice rules

- Use hyphens (`-`), never double dashes. ODUN.ONE and DPI+ styled correctly.
- The card is a draft, not a sent message. Never imply it was sent.

## Phase 0 - Parse the input

The argument is `recipient :: subject :: body` (double-colon separated). Any
missing field falls back to a clearly-labelled placeholder so the card is always
well-formed:

- recipient -> `someone@example.com` (a placeholder; the human edits it before approving)
- subject -> `Draft from /queue-draft`
- body -> the whole argument if no `::` separators were given, else empty

Never invent a real recipient. A placeholder is correct; a fabricated real
address is not.

## Phase 1 - Compose the card

Build a single-element JSON array with one `email_send` draft card:

```json
[
  {
    "action_type": "email_send",
    "status": "pending",
    "priority": "P3",
    "title": "Draft: <subject>",
    "to": "<recipient>",
    "subject": "<subject>",
    "draft_body": "<body>",
    "reasoning": "Drafted by /queue-draft; GATED, awaiting explicit human approval before any send."
  }
]
```

`action_type: email_send` is send-capable, so `append_cards` stamps `tier: gated`
automatically - you do not set the tier, and you must not try to lower it.

## Phase 2 - Deposit (never send)

Resolve the Action Queue directory from the data root. Never hardcode a data
path, because the data-path-redirect hook does not rewrite Bash. Write the
one-card JSON array there with the Write tool, then deposit that file:

```bash
AQ_DIR=$(python3 -c "from scripts.utils.workspace import get_outputs_dir; print(get_outputs_dir() / 'operations' / 'action-queue')")
# Write the JSON array to "$AQ_DIR/_queue-draft-card.json" (Write tool, using the resolved absolute path)
python3 scripts/action-queue.py deposit --file "$AQ_DIR/_queue-draft-card.json"
```

`deposit` appends the card to `queue.json` and prints `deposited added=1`. That is
the ONLY Action Queue command this skill runs. It does NOT run `approve`, `retry`,
`edit`, or any send transport.

## Phase 3 - Report

Report the outcome plainly:

- the deposited card's title and that it is `tier=gated`, `status=pending`;
- that sending requires an explicit human approve (`python scripts/action-queue.py approve <id>` or `/queue approve`), which THIS skill and the headless runner cannot perform;
- the placeholder recipient, if one was used, so the human edits it before approving.

## NEVER

- NEVER run `action-queue.py approve`, `retry`, or `edit`; this skill only deposits.
- NEVER call `scripts/send-email.py` or any outbound send transport.
- NEVER set or lower a card's `tier`; `email_send` floors to `gated` by design.
- NEVER fabricate a real recipient address; use the placeholder.
- NEVER imply the draft was sent - it is staged for human approval only.
