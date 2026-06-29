---
name: investor-pitch
description: Produce full investor pitch deck content with narrative arc, slide-by-slide structure, and speaker notes. Supports strategic, venture, sovereign fund, angel, and corporate VC audiences. Use when preparing for investor meetings, fundraising presentations, or pitch deck creation. Trigger when the user says "investor pitch", "pitch deck", "fundraising deck", "investor presentation", or asks to prepare materials for an investor meeting.
argument-hint: "[investor_type]"
allowed-tools: "Read"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - investor pitch
    - pitch deck
    - fundraising deck
x-31c-capability:
  what: >
    Produces full investor pitch deck content - the investment narrative arc,
    a 14-slide structure with key messages and speaker notes, and a data
    appendix - tuned to the investor type (strategic, venture, sovereign fund,
    angel, corporate VC).
  how: >
    Type /investor-pitch [investor_type]. It reads the business, metrics,
    strategy, and growth-playbook context files and returns deck content for
    Misha to adapt and present (text content, not a rendered file).
  when: >
    Use to prepare materials for an investor meeting or fundraise. For a progress
    update to existing investors use /investor-update; to render slides as PPTX
    use /pptx-generator.
---
# Investor Pitch

Produce full investor pitch deck content — narrative, slide structure, and speaker notes.

## Variables

investor_type: strategic | venture | sovereign_fund | angel | corporate_vc
round_context: [What stage, what amount, what use of funds]
audience: [Specific investor or investor type — any known context]
format: deck_outline | full_content | speaker_notes_only — default: full_content

---

## Instructions

Before drafting, read ALL of these:
- `context/business-info.md` — 31C company, ODUN.ONE, team, partners, products
- `context/current-data.md` — Metrics, milestones, traction, market data
- `context/strategy.md` — Strategic arc, competitive positioning
- `reference/billion-growth-playbook.md` — valuation mechanics + growth model (CRITICAL for investor narrative)
- `reference/dpi-market-intelligence.md` — Market size, growth, incumbent vacuum
- `reference/geopolitical-landscape.md` — Sovereignty narrative and regional opportunity
- `reference/misha-voice.md` — Tone for the pitch narrative
- `datastore/INDEX.md` — If the document contains specific facts or numbers, validate against source documents

---

## Phase 1: The Investment Narrative (Story First)

The best pitches tell a story that makes the investment feel inevitable. Draft the core narrative arc:

**[Problem]** → **[Structural Moment]** → **[Why 31C / Why Now]** → **[Proof]** → **[Scale Path]** → **[The Ask]**

- **Problem:** 90% of internet traffic is encrypted. Traditional DPI is blind. Governments and telecoms are flying blind at the worst possible moment.
- **Structural Moment:** The legacy incumbent exited the market. [$X]B addressable across [N] countries suddenly has no incumbent. This window opens once.
- **Why 31C / Why Now:** Only clean-slate, sovereign, non-aligned DPI+ platform in the world. No Russian strings. No Chinese backdoors. No Israeli geopolitical baggage. Born for this moment.
- **Proof:** [Region] production deployment live [date]. [Second region] in progress. Strategic technology alliance. [N]+ Tribe members.
- **Scale Path:** [Region sequence]. Land-and-expand through [entry module] → full platform. Channel-led through distribution partners ([N]+ dealers). [$X]M ARR → [$Y]B valuation at [Z]x.
- **The Ask:** [Amount] for [use of funds] to execute [specific milestones].

---

## Phase 2: Slide-by-Slide Structure

For each slide provide: slide title, key message (one sentence), content bullets (3-5), and speaker note.

1. **Cover** — Company name, tagline, presenter
2. **The Problem** — DPI blindness in the encrypted era
3. **The Structural Moment** — the incumbent's exit; the [$X]B vacuum
4. **Market Opportunity** — $25.21B → $78.04B at 22% CAGR; [region] fastest at 23.56%
5. **Our Solution** — ODUN.ONE: From Deep Packet Inspection to Deep Packet Intelligence
6. **Product** — Four modules (DataONE, ControlONE, OpsONE, AnalyticsONE) + use case library; API-first architecture; sovereignty by design
7. **Traction** — flagship deployment (live), second deployment (in progress), AllianceCo alliance, PartnerCo, DistributorCo
8. **Go-to-Market** — Phase 1 (home region) → Phase 2 (adjacent regions) → Phase 3 (expansion regions); channel-led
9. **Competitive Landscape** — vs state-aligned vendors (Chinese-aligned), vs a state-aligned vendor (Russian-aligned), vs a competing vendor (pivoting away) — 31C: the only non-aligned sovereign choice
10. **Business Model** — Perpetual [$ per Gb/s] license + recurring support; land-and-expand NRR; ARR path
11. **The Milestones** — Year 1: [$X]M ARR / [$Y]M; Year 2: [$X]M / [$Y]M; Year 3: [$X]M / [$Y]B
12. **The Team** — founder/CEO, CTO, CSO, SVP Product, Research Lab (PhDs) — name the principals and tenure
13. **The Ask** — Amount, use of funds, what this milestone achieves
14. **Why Now, Why 31C** — Closing: the moment is now, the team is built, the product is live

---

## Phase 2.5: Structural pass (optional)

For a long deliverable, you may run the `/editorial-review` structural checklist over the assembled narrative before finalizing, to verify the investor-narrative arc, claim-to-evidence linkage, and that the ask is set up rather than sprung. Reference: `reference/editorial-review.md`. The prose-level voice pass (`humanization.md`) runs as usual after. Skip when the draft is short or already tight.

---

## Phase 3: Data Appendix

Prepare supporting data slides:
- Full market breakdown (global, regional, by segment)
- Detailed competitive matrix
- Financial model assumptions
- Technology architecture (for technically sophisticated investors)

---

**Output:** Full pitch deck content. Misha reviews, adapts tone for specific investor, and presents.
