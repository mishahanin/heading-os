---
name: investor-update
description: Draft a progress update for existing investors, quarterly or milestone-triggered. Includes key metrics, milestone tracking, wins, challenges, next 90 days, and specific asks. Use when sending investor updates, board updates, or shareholder communications. Trigger when the user says "investor update", "board update", "shareholder update", "quarterly update", or asks to draft a progress report for investors.
argument-hint: "[period]"
allowed-tools: "Read"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - investor update
    - board update
    - quarterly update
x-31c-capability:
  what: >
    Drafts a ready-to-send progress update for existing investors - opening
    captain's note, a key-metrics table, milestones (said vs did), wins, honest
    challenges, next-90-days commitments, and any specific ask.
  how: >
    Run /investor-update [period], e.g. /investor-update "Q2 2026". Reads
    current-data, pipeline, people.md, and the growth playbook for the metrics
    that matter and writes 1-2 pages in Misha's voice.
  when: >
    Use for a recurring quarterly or milestone update to people already invested.
    For a first pitch to new investors use /investor-pitch; for a formal diligence
    package use /data-room.
---
# Investor Update

Draft a progress update for existing investors — quarterly or milestone-triggered.

## Variables

period: [Q1 2026 / Q2 2026 / Post-MWC / Post-Milestone / etc.]
investor: [Investor name or "all investors" — use people.md for context]
highlights: [Key developments to feature]
challenges: [Honest challenges to acknowledge — 31C operates with integrity]
ask: [Any specific asks or decisions needed from investors — if none, leave blank]

---

## Instructions

Before drafting, read:
- `context/current-data.md` — Current metrics, milestones, and active workstreams
- `context/pipeline.md` — Pipeline status
- `context/people.md` — Investor relationship context
- `reference/billion-growth-playbook.md` — Metrics that matter for valuation story (ARR, NRR, GM, CAC)
- `reference/misha-voice.md` — Voice guide
- `datastore/INDEX.md` — If the document contains specific facts or numbers, validate against source documents

---

## Structure

**Subject: 31C Update — [Period] | [One-line headline]**

**Opening (1 paragraph):**
Misha's personal note — where we are, the captain's read of the sea state. Honest, direct, warm.

**Key Metrics:**
| Metric | This Period | vs. Last Period | Target |
|--------|------------|----------------|--------|
| Revenue / ARR | | | |
| Active deployments | | | |
| Pipeline (qualified) | | | |
| Tribe size | | | |
| Countries active | | | |

**The Milestones:**
What we said we'd do → What we did. No spin. Real numbers.

**What's Working:**
2-3 specific wins with evidence.

**What's Challenging:**
1-2 honest challenges. This is Partnership for Life — investors need the real picture.

**Next 90 Days:**
3 specific commitments with measurable outcomes.

**Ask (if any):**
Specific, direct. CEOs make specific asks, not vague updates.

**Closing:**
Brief, confident, forward-leaning. The captain's note.

---

**Output:** Ready-to-send investor update. 1-2 pages. Honest, metric-driven, Misha's voice.
