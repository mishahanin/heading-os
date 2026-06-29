---
name: queue
description: Terminal-native Action Queue - the one lane where proactive agents (cold-sweep, email-intel, viraid) deposit drafted actions for the CEO's go/no-go. Runs scripts/action-queue.py to list drafts, show one, approve (= SYNCHRONOUS send, watched, in the same command), edit a draft, dismiss, or retry a failed send. Daemon-free and browser-free. Use when the user says "queue", "action queue", "show my drafts", "what's waiting to send", "approve/send the first one", "retry that failed send", or wants to review the pending-draft backlog. Do NOT use to see what is overdue across the whole workspace (use /radar), to draft cold-contact nudges (use /cold-sweep), or for inbox triage (use /email-intel). This is the approve/send surface those skills FEED.
argument-hint: "[list | show <id> | approve <id> | edit <id> | dismiss <id> | retry <id>]"
allowed-tools: "Read, Bash(python3:*), Bash(python:*)"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: false
  shared_state:
    - "outputs/operations/action-queue/"
  triggers:
    - queue
    - action queue
    - show my drafts
    - what's waiting to send
    - approve the first one
    - retry that failed send
x-31c-capability:
  what: >
    The terminal-native Action Queue: lists drafted actions awaiting go/no-go and
    sends an approved one SYNCHRONOUSLY (the CEO watches it land). Daemon-free; the
    send-gate is intact (only the human approve sends; nothing auto-sends).
  how: >
    Run /queue to list; /queue show <id> for the full draft; /queue approve <id>
    to send it now (watched); /queue edit <id> to rewrite a draft; /queue dismiss
    <id> to suppress it; /queue retry <id> to re-send a failed card. All via
    scripts/action-queue.py, no bridge daemon required.
  when: >
    Use to clear the pending-draft backlog. For what is overdue workspace-wide use
    /radar; to draft cold nudges use /cold-sweep; for inbox triage use /email-intel.
---
# /queue

The terminal-native Action Queue. Proactive agents deposit drafts here; this skill is where the CEO reviews them and SENDS - synchronously, watching each send land. It drives `scripts/action-queue.py` entirely in-process: no bridge daemon, no browser. The send-gate is untouched - `approve` is the explicit human click, and nothing is ever auto-sent.

## Phase 0 - Route the request

- bare "queue" / "show my drafts" / "what's waiting" -> **list** (Phase 1).
- "show <id>" -> print the full draft (Phase 2).
- "approve <id>" / "send the first one" -> **synchronous send** (Phase 3).
- "edit <id>" -> rewrite the draft (Phase 4).
- "dismiss <id>" / "retry <id>" -> Phase 5.

## Phase 1 - List

```bash
python scripts/action-queue.py list
```

Read-only. Shows the active cards banded into the approve/send lane (gated sends awaiting a click) and read-only FYI context. Present them plainly with their short ids, priority, source, and `draft_status`. If the queue is clear, say so.

## Phase 2 - Show

```bash
python scripts/action-queue.py show <id-or-prefix>
```

Print the full card so the CEO can read the recipient, subject, and body before deciding. Never paraphrase the draft as if it were sent.

## Phase 3 - Approve = synchronous send (the watched moment)

```bash
python scripts/action-queue.py approve <id-or-prefix>
```

This SENDS the card right now and prints `sent` or `send failed (reason)` in the same command. For an `email_send` card it requires `draft_status: ready_for_review` (edit it first otherwise) and refuses anything that does not resolve `gated`. Report the outcome exactly as the command returned it - if it failed, surface the reason and note the card is kept as `send_failed` for `retry`. Approve ONE card per explicit instruction; "approve the first one" means only the first.

## Phase 4 - Edit a draft

```bash
python scripts/action-queue.py edit <id> --subject "..." --body-file <path>
```

Rewrite the subject and/or body (flips `draft_status` to `ready_for_review`). For voice, follow `reference/misha-voice.md` and the humanisation rule; hyphens, never em-dashes. Write the body to a temp file and validate (`sanitize-text.py --scan`, `humanization-check.py`) before the edit.

## Phase 5 - Dismiss / retry

```bash
python scripts/action-queue.py dismiss <id> [--reason "..."]   # suppress re-proposal (14-day cooldown)
python scripts/action-queue.py retry <id>                      # re-send a send_failed card
```

## NEVER

- NEVER send, edit a card to `sent`, or mark anything sent on the CEO's behalf - only `approve`/`retry` send, and only on the CEO's explicit instruction for that specific card.
- NEVER write `queue.json` directly - all mutations go through `scripts/action-queue.py` (which routes through the in-process helpers; the disposition-log audit depends on it).
- NEVER approve/send a batch from one instruction - each card needs its own explicit go-ahead.
- NEVER claim a card was sent when the command reported `send_failed` - surface the reason.
