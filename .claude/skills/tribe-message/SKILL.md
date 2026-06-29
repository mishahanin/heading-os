---
name: tribe-message
description: Draft an internal message to the 31C Tribe in Misha's voice - heading-setting, Navigation-Principle framing, maritime register where natural. Use for any internal all-hands or Tribe-wide communication. Trigger when the user says "tribe message", "message to the tribe", or "write to the tribe". Do NOT use for the recurring Monday cadence message (use /tribe-monday) or external-facing content (use /linkedin-post, /corporate-letter).
argument-hint: "[topic]"
allowed-tools: "Read"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - tribe message
    - message to the tribe
    - write to the tribe
x-31c-capability:
  what: >
    Drafts a 100-250 word message from Misha to the 31C Tribe in the captain's
    voice - any day of the week - using operational vocabulary (sea state, heading,
    Five Core Principles) and a Voss layer that labels before asking. Handles
    updates, policy, milestones, and Crunch Mode declarations.
  how: >
    Run /tribe-message [topic]. Reads misha-voice, the operational state model, and
    current-data, then returns the draft.
  when: >
    Use for an internal Tribe communication any day. For the Monday weekly cadence
    message specifically, use /tribe-monday.
---
# Tribe Message

Draft a message from Misha to the 31C Tribe. Can be sent any day - not limited to Mondays.

## Variables

topic: [What is this message about? Provide the key point or theme]
sea_state: [Optional - external conditions if relevant]
crunch: yes | no [Is this a Crunch Mode message? Default: no]
highlights: [Optional - milestones, wins, or developments to acknowledge]
principle: [Optional - which of the Five Core Principles to connect to]

---

## Instructions

Before drafting, read:
- `reference/misha-voice.md` - Voice guide, especially Tribe Communications section
- `outputs/operations/workspace/31c-operational-state-model.md` - Operational vocabulary and Five Core Principles
- `context/current-data.md` - Recent milestones and active workstreams

Draft a Tribe message that:
- Gets to the point fast - the Tribe reads on phones between time zones
- States the topic clearly and directly
- Connects to the operational context when relevant (sea state, heading, principle)
- If Crunch Mode: declares it, explains what it means, sets expectations
- Closes warmly but briefly - the captain's voice, not a corporate memo
- **Voss layer:** Apply per `.claude/rules/voss.md`. For Tribe messages: label the Tribe's emotional state before the ask, invite ownership with calibrated questions rather than top-down directives. Full framework: `reference/voss-negotiation.md`.

**Tone:** Speak to 50+ experienced professionals. Direct, warm, authoritative. Never condescending. Never cheerleading. Never "Amazing work everyone!"

**Length:** 100-250 words. Shorter is better. Say what needs to be said and stop.

## Message Types

This skill handles any Tribe communication:
- Weekly updates (sea state + heading)
- Policy or culture messages
- Milestone announcements
- Pre-event or post-event messages
- Operational updates
- Crunch Mode declarations

## NEVER
- "Team" / "family" / "crew" - always "Tribe"
- "Let's give it 110%!"
- "We're all in this together"
- Military references
- Generic motivation language
- Em-dashes - use hyphens only
