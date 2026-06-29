---
name: email-draft
description: Draft a new outbound email in Misha's voice - Voss-calibrated, concise, with a clear ask. Use for composing a fresh email to a named recipient. Trigger when the user says "draft email to", "write email to", or "email [person] about". Do NOT use for replying to an existing email (use /email-respond) or peer CEO correspondence (use /ceo-to-ceo). NEVER auto-sends - produces a draft for review.
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
argument-hint: "[to] [purpose]"
allowed-tools: "Read"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - draft email to
    - write email to
    - email [person] about
x-31c-capability:
  what: >
    Drafts a new outbound CEO email in Misha's voice - direct opener, ordered
    key points, clear next step, plus 2-3 subject line options and any
    relationship flags. Applies the Voss layer.
  how: >
    Run /email-draft <to> <purpose>. Produces a draft inline for review; it
    does not send - sending is human-gated via scripts/send-email.py.
  when: >
    Use for a new outbound email. To reply to an email you received use
    /email-respond; for peer-CEO correspondence use /ceo-to-ceo; for a formal
    letter use /corporate-letter.
---
# Email Draft

Draft an outbound CEO email from Misha Hanin.

## Variables

to: [Recipient name, title, company]
purpose: [What this email is trying to accomplish]
key_points: [Main points to cover — list them]
tone: formal | warm | direct | urgent — default: warm-direct
context: [Any specific relationship or situational context]

---

## Instructions

Before drafting, read:
- `reference/misha-voice.md` — Voice guide, especially Email section
- `context/people.md` — Check if recipient is listed; load relationship context
- `context/business-info.md` — Load relevant 31C context for the email topic

Draft an email that:
- Opens directly — reference the reason for writing in the first sentence
- Covers key points in the right order (most important first)
- Uses one idea per paragraph, 2-3 lines max per paragraph
- Closes with a clear next step or invitation
- Matches tone to recipient (peer-level with CEOs, respectful with government, warm with Tribe)

**Voss layer:** Apply per `.claude/rules/voss.md` (always-active rule). Skill-specific hooks: calibrated questions for requests, labeling before addressing tension, "That's Right" summaries on negotiation follow-ups. Full framework: `reference/voss-negotiation.md`.

After the draft, provide:
- 2-3 subject line options
- Any relationship notes from people.md if relevant
- One flag: anything sensitive to review before sending

## NEVER
- "I hope this email finds you well."
- "Dear Valued Partner,"
- "I am writing to inform you..."
- "Please don't hesitate to contact me."
- Passive voice where active works
- Burying the ask in the middle
