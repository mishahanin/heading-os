---
name: data-room
description: Prepare investor data room documents, due diligence responses, and fundraising materials. Use when preparing for investor conversations, responding to due diligence questions, or building the formal data room. Trigger when the user says "data room", "due diligence", "DD response", "investor materials", or asks to prepare documents for investors.
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
argument-hint: "[type] [investor_type]"
allowed-tools: "Read, WebSearch"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - data room
    - due diligence
    - DD response
    - investor materials
x-31c-capability:
  what: >
    Produces investor data room material - company overview, financial summary,
    market analysis, team and technology briefs, competitive landscape,
    due-diligence question responses, or a full structured data room table of
    contents - grounded in context/ files and validated against the datastore.
  how: >
    Run /data-room [type] [investor_type], e.g. /data-room dd-response vc or
    /data-room full-room. Reads business-info, current-data, strategy, pipeline,
    and the growth playbook, then drafts in Misha's voice for review.
  when: >
    Use when preparing for investor conversations or answering diligence
    questions. For a live pitch deck use /investor-pitch; for a recurring
    progress note to existing investors use /investor-update.
---
# Data Room - Investor Due Diligence & Fundraising Preparation

Prepare structured documents for investor data rooms, respond to due diligence questions, and build fundraising materials.

## Variables

type: overview | financial-summary | market-analysis | team-overview | technology-brief | competitive-landscape | dd-response | full-room
question: [For dd-response: paste the specific due diligence question or questionnaire]
investor_type: strategic | vc | sovereign | pe — default: strategic
stage: pre-meeting | diligence | term-sheet

---

## Instructions

Before preparing, read ALL relevant files:
- `context/business-info.md` — Company structure, product, partners, team
- `context/current-data.md` — Metrics, milestones, timelines, market data
- `context/strategy.md` — Strategic arc, go-to-market, valuation path
- `context/pipeline.md` — Active deals and investor conversations
- `reference/billion-growth-playbook.md` — Valuation mechanics, growth model, target-valuation path
- `reference/dpi-market-intelligence.md` — Market size, competitive landscape
- `reference/geopolitical-landscape.md` — Regional dynamics supporting the thesis
- `datastore/INDEX.md` — If the document contains specific facts or numbers, validate against source documents

---

## Document Types

### Company Overview (2-3 pages)
Executive summary for the data room front page:
- Company mission and founding story (December 2024, incumbent vacuum, strategic investor)
- Product: ODUN.ONE platform - what it does, why it matters
- Market opportunity: $25B market, 22% CAGR, 56-country vacuum
- Traction: [region] deployment (live), [region] (in progress), partner network activated
- Team: [N]+ Tribe members, Research Lab, patent portfolio
- Ask and use of funds (if applicable)

### Financial Summary
- Current burn rate and runway context
- Revenue model: perpetual license ([$ per Gb/s]) + annual support ([%]) + lifecycle extension
- Bundle pricing: Essential (1.0x) / Professional (1.40x) / Enterprise (1.65x)
- Unit economics: single deployment value ($2.5M+ for 180 Gb/s country)
- Revenue projections framework based on pipeline and geographic phasing
- Path to profitability thesis

### Market Analysis
- Global DPI market: $25.21B (2024) to $78.04B (2030), 22.05% CAGR
- incumbent vacuum: [N] countries, ~[$X]B addressable
- Geographic breakdown: [region] (23.56% CAGR), [region] ($420M to $1.15B), [region] (26.59% CAGR)
- By segment: Telecom (36.18%), BFSI (24.56% CAGR), Government/Defense
- Competitive landscape: why the market is open
- Sovereign DPI demand drivers: data sovereignty mandates, 5G, encrypted traffic

### Team Overview
- Leadership bios from business-info.md
- Hiring velocity (1,500+ interviews, ~20 hires in first 5 months)
- Research Lab: PhDs in Mathematics, Quantum Physics, AI/ML, Cryptography
- Advisory board context
- Geographic distribution and hiring roadmap (50 to 200)

### Technology Brief
- ODUN.ONE architecture: 4 modules, API-first, Kubernetes-native
- Key differentiators: AI-native, clean-slate, encrypted traffic classification
- Patent portfolio: 1 filed, 2nd in progress
- Xynthor AI acquisition integration
- Deployment model: standard x86/ARM, on-premises, sovereign

### Competitive Landscape
Pull from `outputs/deliverables/documents/global-dpi-vendor-landscape-2025.md`:
- Pure-play DPI vendors: the defunct incumbent, pivoting and state-aligned vendors
- Infrastructure bundlers: state-aligned and Western infrastructure vendors
- Why ODUN.ONE wins: sovereign, non-aligned, AI-native, platform architecture

### Due Diligence Response
For specific investor questions:
- Analyze the question and identify what the investor is really asking
- Pull relevant data from workspace files
- Frame the response to reinforce the investment thesis
- Be precise with numbers - reference sources
- Flag anything that needs Misha's direct input or that shouldn't be shared at this stage

### Full Room Build
Produce a structured data room table of contents with all documents:
1. Company Overview
2. Market Opportunity & Competitive Landscape
3. Product & Technology
4. Team & Organization
5. Financial Model & Projections
6. Customer & Partner Evidence
7. IP & Patents
8. Legal & Corporate Structure
9. Strategic Roadmap

For each section, produce the document or flag what additional input is needed.

---

## Structural pass (optional)

For a long deliverable, you may run the `/editorial-review` structural checklist over the assembled draft before finalizing, to verify the argument arc, claim-to-evidence linkage, and section hierarchy. Reference: `reference/editorial-review.md`. The prose-level voice pass (`humanization.md`) runs as usual after. Skip when the draft is short or already tight.

---

## Tone

Investor materials are:
- **Confident but precise** - earned confidence, not hype
- **Data-driven** - every claim has a number behind it
- **Story-supported** - the narrative matters as much as the metrics
- **Forward-looking without overpromising** - "the math works because..." not "we guarantee..."
- **In Misha's voice** - authentic, not generic pitch deck language

## NEVER
- "We're disrupting..." (say what we're building, not what we're destroying)
- Unsubstantiated revenue projections without clear assumptions
- Competitor bashing (position strengths, don't attack)
- "Conservative estimates" (every founder says this - just show the math)
- Sharing specific financial data without Misha's explicit approval
