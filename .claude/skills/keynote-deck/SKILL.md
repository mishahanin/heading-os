---
name: keynote-deck
description: Produce a presentation deck for an event, VIP meeting, or government engagement. Creates slide-by-slide structure with titles, key messages, content, and speaker notes. Supports MWC, regional expos, Black Hat, government meetings, and investor presentations. Trigger when the user says "keynote deck", "event presentation", "conference slides", "speaking deck", or asks to prepare a presentation for an event or high-level meeting.
argument-hint: "[event] [topic]"
allowed-tools: "Read"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - keynote
    - event presentation
    - conference slides
    - speaking deck
x-31c-capability:
  what: >
    Produces a full slide-by-slide presentation script for an event, VIP, or
    government engagement - opening hook, problem, solution, proof, future, and
    close, each with title, key message, content, and speaker notes in Misha's
    voice.
  how: >
    Run /keynote-deck <event> <topic>. Outputs the deck structure and speaker
    script for review; it does not render the .pptx file itself.
  when: >
    Use for event and conference speaking decks. For an investor fundraising
    deck use /investor-pitch; to render slides as a PPTX use /pptx-generator.
---
# Keynote Deck

Produce a presentation deck for an event, VIP meeting, or government engagement.

## Variables

event: [Event name — MWC, regional expos, Black Hat, government meeting, investor presentation, etc.]
audience: [Who is in the room — telco executives, government officials, investors, technical decision-makers]
topic: [Core message or presentation theme]
duration: [Presentation length in minutes — this determines slide count]
context: [Any specific context, asks, or goals for this presentation]

---

## Instructions

Before drafting, read ALL of these:
- `context/business-info.md` — ODUN.ONE, company story, credentials
- `context/current-data.md` — Proof points, deployments, milestones
- `reference/dpi-market-intelligence.md` — Market data for relevant sections
- `reference/geopolitical-landscape.md` — Regional context for the audience
- `context/strategy.md` — Strategic positioning
- `reference/misha-voice.md` — Voice and presentation style
- `datastore/INDEX.md` — If the document contains specific facts or numbers, validate against source documents

**Slide count guideline:** 1 minute per slide for technical/detailed content; 2 minutes per slide for storytelling/high-level. Calculate accordingly.

---

## Phase 1: Opening Hook (2-3 slides)

- Slide 1: A single powerful statement, striking statistic, or story opening
- Slide 2: The tension / the problem (why this audience should care)
- Slide 3: The promise of what they'll leave knowing

**Voice note:** Misha opens with stories and counterintuitive observations — not "Today I'm going to talk to you about..."

---

## Phase 2: The Problem (2-3 slides)

Frame the problem in terms this specific audience experiences:
- For telcos: encrypted traffic blindness, 5G complexity, regulatory burden
- For government: sovereignty risk, dependency on aligned vendors, lawful intercept limitations
- For investors: market vacuum, timing, structural opportunity

---

## Phase 3: The Solution (3-4 slides)

- ODUN.ONE platform narrative (not a product tour — a capability story)
- "From Deep Packet Inspection to Deep Packet Intelligence"
- What changes when you have real DPI+: what you can see, what you can do
- Architecture of sovereignty: data never leaves sovereign control

---

## Phase 4: Proof (2-3 slides)

Pull the current proof points at runtime from `context/current-data.md` (deployments, alliances, partner-network and Tribe figures) rather than hardcoding them — those numbers drift. Typical shape:
- Lead production deployment(s) and their status
- Commercial deployments in progress
- Strategic alliances (e.g. AllianceCo)
- Partner network breadth (named partners + dealer count)
- The Tribe: headcount and hiring rigor

Read `context/current-data.md` for the live figures before building these slides.

---

## Phase 5: The Future (1-2 slides)

- home region → adjacent regions → expansion regions
- Category creation: DPI+ as a new standard
- What the world looks like when data sovereignty is architecture, not contract

---

## Phase 6: Close + Call to Action (1-2 slides)

- The one thing you want the audience to do or think after leaving
- Clear, specific ask or invitation (meeting, follow-up, partnership conversation)
- Misha's contact / next step

---

## Deliverables

For each slide provide:
- **Slide title**
- **Key message** (one sentence — what the audience should take from this slide)
- **Content** (bullets, data points, visual suggestion)
- **Speaker notes** (what Misha says — written in his voice, not formal notes)

**Output:** Complete presentation script and slide structure. Misha reviews, refines, and presents.
