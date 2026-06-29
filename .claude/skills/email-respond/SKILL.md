---
name: email-respond
description: Draft a reply to an existing email thread in Misha's voice - reads the incoming message, mirrors context, and answers with a Voss-calibrated response. Use when responding to a received email. Trigger when the user says "respond to this email", "reply to this", or "draft reply". Do NOT use for a new outbound email (use /email-draft). NEVER auto-sends - produces a draft for review.
argument-hint: "[notes]"
allowed-tools: "Read, Bash(python3:*)"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - respond to this email
    - reply to this
    - draft reply
x-31c-capability:
  what: >
    Drafts a reply to an incoming email in Misha's voice - reads the sender's real intent,
    applies Voss tactical empathy, leads with the key point, and closes with a clear next step.
  how: >
    Run /email-respond with the incoming email pasted below the divider (plus optional notes).
    Returns a draft reply, a subject line, and one pre-send flag. Drafts only - never sends.
  when: >
    Use to reply to an email you received. For a new outbound email use /email-draft; to triage
    the whole inbox use /email-intel.
---
# Email Response

Draft a response to an incoming email in Misha's voice.

## Variables

notes: [Any specific points to make, things to avoid, or context not visible in the email — optional]

[Paste the email to respond to below the divider]

---

[PASTE INCOMING EMAIL HERE]

---

## Instructions

Before drafting, read:
- `reference/misha-voice.md` — Voice guide
- `context/people.md` — Check if sender is listed; load relationship context
- `context/pipeline.md` — Check if this relates to an active deal or conversation

Analyze the incoming email:
- What is the sender's real intent or question?
- What tone are they using?
- Is this a decision request, information request, relationship maintenance, or problem?
- **Tactical empathy read:** Apply Voss per `.claude/rules/voss.md`. Surface emotions under the words; label tension/frustration before addressing substance; redirect demands with calibrated questions. Full framework: `reference/voss-negotiation.md`.

Draft a response that:
- Addresses the sender's actual intent (not just the surface question)
- Leads with the most important point
- Is appropriately brief — never longer than the incoming email unless complexity requires it
- Closes with a clear next step or acknowledgment

After the draft, provide:
- Subject line (if replying to a new thread or re-titling)
- One flag: anything to double-check before sending

## NEVER

- NEVER send the reply. This skill drafts only — outbound send is always human-gated (lethal-trifecta control). The CEO sends or approves explicitly.
- NEVER fabricate facts or commitments not present in the incoming thread.
