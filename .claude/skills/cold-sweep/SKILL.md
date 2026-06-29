---
name: cold-sweep
description: >
  Drain overdue CRM "red debt" into the Action Queue as routed, voice-drafted
  nudges for one-click CEO approval. Use when the CEO says "cold sweep", "drain
  the cold contacts", "sweep the overdue CRM", or wants the overdue-contact
  backlog turned into ready-to-send drafts. NOT for sending email (that is the
  human-approved executor) - this skill only drafts into the queue. NOT for a
  single follow-up (use /follow-up). NOT for pipeline review (use /crm). CEO-only
  (not synced to executives) during the prove-out.
argument-hint: "[--dry-run]"
allowed-tools: "Read, Write, Bash(python3:*), Bash(python:*), Skill"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: partial
  shared_state: ["outputs/operations/action-queue/"]
  triggers: ["cold sweep", "cold-sweep", "drain cold contacts", "sweep overdue contacts", "drain the red debt"]
x-31c-capability:
  what: >
    Turns the overdue-contact "red debt" from crm-health into ~150-word
    voice-drafted nudges and deposits them in the Action Queue as email_send
    cards marked ready_for_review for one-click CEO approval.
  how: >
    Run /cold-sweep (or /cold-sweep --dry-run to preview routing only). It fills
    needs_draft cards via scripts/action-queue.py through the daemon; the CEO
    then approves on the bridge Action Queue page or with
    scripts/action-queue.py approve <id>. CEO-only, not synced to executives.
  when: >
    Use to drain the whole overdue-CRM backlog into ready drafts. For a single
    follow-up use /follow-up; for pipeline review use /crm. It never sends -
    the human-approved executor does that.
---

# /cold-sweep

Turn the overdue-contact backlog into prioritised, cited, voice-drafted nudges sitting in the Action Queue for one-click go/no-go. The deterministic routing (who, what priority) is done by `scripts/cold_sweep_core.py`; this skill does only the part that needs judgment and voice - writing the actual draft body.

Split of responsibilities (plan 2026-06-03, Design Decision 5):

- **Deterministic + headless:** `cold_sweep_core` reads `crm-health.py --json`, routes each overdue contact, and deposits `email_send` cards with `draft_status: needs_draft`. This runs either on the daemon's schedule (06:30 local time, when enabled) or via `scripts/cold-sweep.py` for a manual run (daemon-free, in-process `append_cards`).
- **Voice + judgment (this skill):** fills the `needs_draft` cards with a ~150-word nudge in Misha's voice and flips them to `ready_for_review`.

The CEO then approves+sends from the terminal with `/queue approve <id>` (or `scripts/action-queue.py approve <id>`) - a SYNCHRONOUS, watched send, daemon-free. **This skill never sends; it only drafts into the queue.**

## Phase 0 - Load context

1. Read `reference/misha-voice.md` (voice, maritime inventory, "what Misha never says").
2. Apply `.claude/rules/voice.md`, `.claude/rules/humanization.md`, `.claude/rules/hidden-chars.md`, `.claude/rules/voss.md`.
3. Read the queue store `outputs/operations/action-queue/queue.json` (read-only) to discover cards. The targets are cards with `action_type: "email_send"` AND `status: "pending"` AND `draft_status: "needs_draft"`.

## Phase 1 - Ensure cards exist

If there are no `needs_draft` cards in the queue:

- Run a manual sweep: `python3 scripts/cold-sweep.py` (deposits DAEMON-FREE via the in-process `append_cards`; works with the bridge daemon down).
- `--dry-run` (if the CEO passed it): run `python3 scripts/cold-sweep.py --dry-run` and present the routed cards without drafting. Stop after presenting.

Re-read `queue.json` after a manual sweep to pick up the new cards.

## Phase 2 - Draft each needs_draft card (in voice)

For each `needs_draft` email_send card (cap at the cards present; do not invent contacts):

1. Read the linked contact file at `card.contact_file` (e.g. `crm/contacts/<slug>.md`) for the relationship, last interaction, and any open commitments. Use the card's `citations` for the cadence-breach context.
2. Draft a ~150-word nudge in Misha's voice: direct opening, one concrete specific from the contact's history, one clear ask. Hyphens, never em-dashes. No banned vocabulary. Do not fabricate facts - if the contact file lacks a hook, keep it short and honest rather than inventing one.
3. Write the body to a temp file (e.g. `outputs/documents/_work/cold-sweep-<id>.txt`).
4. Validate the draft: `python scripts/sanitize-text.py <tmp> --scan` and `python scripts/humanization-check.py <tmp>`. Fix any findings before finalizing.
5. Finalize the card through the daemon (single-writer; flips `draft_status` to `ready_for_review`):
   `python3 scripts/action-queue.py edit <id> --subject "<subject>" --body-file <tmp>`

Draft cards sequentially (the queue is shared state; the daemon serialises writes, but sequential drafting keeps the run legible).

## Phase 3 - Report

Summarise: N cards drafted and flipped to `ready_for_review`, M skipped (and why). Remind the CEO:

- Review with `/queue` (or `python3 scripts/action-queue.py list`); approve+send with `/queue approve <id>` (or `python3 scripts/action-queue.py approve <id>`).
- `approve` SENDS synchronously and is watched (no async executor). Dismiss with `python3 scripts/action-queue.py dismiss <id>` suppresses re-proposal for 14 days.

Then run `/brain-audit --sources crm/contacts --entity cold-sweep` if a synthesized summary was produced; append the footer. (Cold-Sweep is mechanical routing + per-card drafting, so the audit is optional here.)

## Voice rules

- Misha's voice: direct, specific, committed. Hyphens, never em-dashes (`--` and `—` both banned in drafts). Tribe vocabulary, ODUN.ONE, DPI+.
- Every draft carries at least one named/dated specific from the contact's real history. If none exists, keep it short - do not fabricate.

## NEVER

- NEVER send email. This skill drafts into the queue only; the human approves and the executor sends.
- NEVER write `queue.json` directly. All card mutations go through `scripts/action-queue.py` (the daemon is the single writer).
- NEVER invent contacts, interactions, or specifics not present in the contact file.
- NEVER fabricate a hook to hit ~150 words - a shorter honest draft beats a padded one.
- NEVER mark a card sent or approved on the CEO's behalf.
