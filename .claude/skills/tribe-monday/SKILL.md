---
name: tribe-monday
description: Draft the recurring Monday Tribe message - the weekly heading-set for the 31C Tribe, framing the week's operational state and priorities in Misha's voice. Use specifically for the weekly Monday cadence. Trigger when the user says "monday message", "weekly tribe message", or "monday tribe". Do NOT use for an ad-hoc, non-Monday Tribe message (use /tribe-message).
argument-hint: "[week_theme]"
allowed-tools: "Read"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - monday message
    - weekly tribe message
    - monday tribe
x-31c-capability:
  what: >
    Drafts the weekly 150-250 word Monday message from Misha to the 31C Tribe -
    reads the sea state, sets the heading for the week, and connects to a Core
    Principle in the captain's voice.
  how: >
    Run /tribe-monday <week_theme>. Produces the message draft for review; can
    declare Crunch Mode when flagged.
  when: >
    Use for the weekly Monday cadence message. For an off-cadence or one-off
    Tribe message use /tribe-message.
---
# Monday Tribe Message

Draft the weekly Monday message from Misha to the 31C Tribe.

## Variables

week_theme: [What's the central theme or operational focus this week?]
sea_state: [What are external conditions — market news, events, headwinds, tailwinds?]
crunch: yes | no [Is this a Crunch Mode week? Default: no]
highlights: [Any specific milestones, wins, or developments to acknowledge]
principle: [Which of the Five Core Principles to connect to, if any]

---

## Instructions

Before drafting, read:
- `reference/misha-voice.md` — Voice guide, especially Tribe Communications section
- `outputs/operations/workspace/31c-operational-state-model.md` — Operational vocabulary and Five Core Principles
- `context/current-data.md` — Recent milestones and active workstreams

Draft a Monday message that:
- Opens with a brief, honest read of the sea state (external conditions)
- States the heading clearly — where we're moving this week and why
- Acknowledges any difficulty without drama — honest captaincy
- Connects to a Core Principle or operational state naturally
- If Crunch Mode: declares it, explains what it means for this week, sets expectations
- Closes warmly but briefly — the captain's send-off, not a corporate pep talk

**Tone:** Speak to 50+ experienced professionals. Direct, warm, authoritative. Never condescending. Never cheerleading. Never "Amazing work everyone!"

**Length:** 150-250 words. Shorter is better. The Tribe reads on phones between time zones.

## NEVER
- "Team" / "family" / "crew" — always "Tribe"
- "Let's give it 110%!"
- "We're all in this together"
- Military references
- Generic motivation language
