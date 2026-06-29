---
name: deal-strategy
description: Full deal strategy analysis for a specific prospect or opportunity. Produces prospect intelligence, competitive positioning, pricing recommendation, objection handling, and next steps. Use when evaluating a new deal, preparing for a sales engagement, or building competitive positioning. Trigger when the user says "deal strategy", "analyze this deal", "how do we win this", "competitive positioning for [prospect]", or asks to evaluate a prospect or opportunity.
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.4"
argument-hint: "[prospect]"
allowed-tools: "WebSearch, WebFetch, Read, Bash(python3:*)"
model: sonnet
x-31c-orchestration:
  parallel_safe: true
  shared_state:
    - outputs/negotiations/
    - outputs/intel/cases/
  triggers:
    - deal strategy
    - how do we win
    - competitive positioning for
    - pricing strategy
x-31c-capability:
  what: >
    Full deal strategy for a named prospect - prospect intelligence,
    competitive positioning, Voss-informed pricing and objection handling,
    and recommended next steps in one briefing document.
  how: >
    Run /deal-strategy <prospect>. Keeps an append-only memlog at
    outputs/negotiations/<prospect>/ to survive compaction and writes the
    brief there; closes with a /brain-audit footer.
  when: >
    Use to build the win plan for a specific opportunity. For competitor
    comparison alone use /competitor-intel; for market intel use /market-brief.
---
# Deal Strategy

Full deal strategy analysis for a specific prospect or opportunity.

## Variables

prospect: [Company name, country, size/type]
context: [What we know — how they came to us, what they're evaluating, current vendor, key contact]
deal_size: [Estimated bandwidth requirements and deal value if known]
timeline: [Decision timeline if known]
competitive: [Known or suspected competitive consideration]

---

## Instructions

Before analyzing, read:
- `context/business-info.md` — ODUN.ONE modules, use case library, and competitive positioning
- `context/strategy.md` — Go-to-market strategy and competitive positioning
- `reference/dpi-market-intelligence.md` — Competitor profiles (state-aligned and pure-play vendors)
- `reference/billion-growth-playbook.md` — Land-and-expand model and pricing strategy
- `context/pipeline.md` — Current pipeline context
- `reference/geopolitical-landscape.md` — Regional context for this deal

---

## Session Memory (memlog)

A deal strategy is worked across many turns and often survives a context compaction. Keep an append-only working memory so a fresh session can resume without losing the decisions made so far.

- **On start:** if `outputs/negotiations/[prospect-slug]/.memlog.md` is absent, `python scripts/memlog.py init --workspace outputs/negotiations/[prospect-slug] --field topic="[prospect] deal strategy" --field mode=deal`. If it already exists (incomplete prior session), do NOT re-run `init` (it errors by design) — read the file yourself to resume, then continue with `append`/`set`.
- **As you go:** record each material decision/insight at the moment it lands — `python scripts/memlog.py append --workspace outputs/negotiations/[prospect-slug] --text "anchor at 347,850, hold the discount" --type decision`. Keep entries one line and minimal; this is working memory, not the deliverable.
- **On wrap-up:** `python scripts/memlog.py set --workspace outputs/negotiations/[prospect-slug] --key status --value complete`.

The memlog is the resume spine; the strategy brief is derived from it. The `.memlog.md` file is gitignored.

---

## Phase 1: Prospect Intelligence

**Who they are:**
- Organization type and size
- DPI use case (traffic management, lawful intercept, cybersecurity, analytics, policy enforcement?)
- Decision-making structure (government tender, telco procurement, enterprise IT)
- Sovereignty profile (non-aligned? US-aligned? China-risk?)

**Why they're evaluating now:**
- Trigger (incumbent exit? Existing contract expiry? Regulatory requirement? New 5G deployment?)
- Urgency level

---

## Phase 2: Competitive Analysis

**Most likely competitor(s) and positioning against each:**

vs. state-aligned vendors:
- Their position: Bundled with hardware, price aggressive, China-aligned, data sovereignty risk
- Our position: Non-aligned, sovereign architecture, data never leaves client control, no hidden backdoors
- Win condition: Sovereignty and alignment concerns (especially if country has China-risk sensitivity)

vs. a state-aligned vendor:
- Their position: Russian DPI, technically capable, now carries Russia-alignment risk post-2022
- Our position: Non-aligned, Western-grade engineering, no geopolitical baggage
- Win condition: Any country seeking to diversify away from Russian tech dependence

vs. a competing vendor:
- Their position: Pivoting to cloud security; DPI capability degrading; legacy customers at risk
- Our position: Pure DPI+ focus; next-gen architecture; not a product pivot, a category creation
- Win condition: a competing vendor customers who feel abandoned; greenfield buyers who need depth

vs. other competing vendors:
- Their position: Enterprise/carrier focus, expensive, complex deployment
- Our position: Sovereign deployment model, faster time-to-value, better for government/telco in emerging markets
- Win condition: Cost, deployment simplicity, sovereignty architecture

---

## Phase 3: Positioning Strategy

**Lead module:** [Which ODUN.ONE module leads based on their primary use case]
**Expansion path:** [Essential (DataONE) → Professional (+ ControlONE + OpsONE) → Enterprise (+ AnalyticsONE) + use case activation]
**Sovereignty angle:** [How to position the data sovereignty architecture for this prospect]
**Category story:** ["From Deep Packet Inspection to Deep Packet Intelligence" — how to adapt for this audience]

**Relevant Odin principles (CEO workspace only):** brain-gated - IF on the CEO workspace AND `knowledge/odin-brain/` exists, cite up to 3 positioning / partnership / channel principles for this prospect via the contact's `relevant_principles` and/or `python scripts/odin-principles.py --type <relationship_type> --stage <stage> --json` (use `--type partner` or `--type reseller` for channel/partner deals, `--type prospect` otherwise). Frame each against this deal. Fabrication floor: cite only what the helper returns, else "No Odin principle matched this relationship domain". On any exec workspace (no brain) SKIP silently. Single hyphens.

---

## Phase 4: Pricing Recommendation

Based on `reference/billion-growth-playbook.md` pricing model:
- Estimated bandwidth: [Gb/s]
- Perpetual license: [Gb/s × $ per-Gb/s rate]
- Year 1 support: included
- Year 2-5 support: 20% annual
- Recommended bundle: Essential / Professional / Enterprise
- Any strategic pricing rationale (lighthouse value, reference country, competitive displacement)

---

## Phase 5: Objection Handling (Voss-Informed)

Apply Voss per `.claude/rules/voss.md` (always-active rule) and `reference/voss-negotiation.md` (full framework, including Ackerman Model detail and Tactical Phrases).

**Accusation Audit:** List 3-5 negative assumptions the prospect likely holds about 31C (too new, unproven, small team, unknown brand). Address these preemptively in the engagement strategy.

**Objection responses:**
1. [Objection 1 - e.g., "You're new, unproven"] -> Label the concern + calibrated question response
2. [Objection 2 - e.g., "a state-aligned vendor is cheaper"] -> Normative leverage + calibrated question
3. [Objection 3 - specific to this prospect] -> Label + redirect

**Pricing:** If price negotiation is expected, structure via Ackerman Model (see `reference/voss-negotiation.md`) with precise non-round numbers per `.claude/rules/voss.md`.

**Relevant Odin principles (CEO workspace only):** brain-gated - IF on the CEO workspace AND `knowledge/odin-brain/` exists, cite up to 3 negotiation / persuasion principles keyed off the deal stage (especially `Negotiation` / `Proposal`) via `python scripts/odin-principles.py --type prospect --stage <stage> --json` and/or the contact's `relevant_principles`. Same fabrication floor and same silent skip when the brain/helper is absent. Single hyphens.

---

## Phase 6: Recommended Next Steps

1. [Immediate action — who does what]
2. [Follow-up action — timeline]
3. [Partner involvement — PartnerCo / DistributorCo / AllianceCo if applicable]

## Phase 7: Brain Audit

After completing the deal strategy analysis (Phases 1-6), invoke `/brain-audit` with:

- `--sources`: comma-separated list of every file consulted — the prospect's CRM contact, `context/pipeline.md` row, every thread entry referenced, every OSINT output referenced, every competitor-intel output referenced, the relevant Voss and DPI market reference files
- `--entity`: the prospect's company name (e.g., "ExampleTelco")

Append the returned three-section footer to the deal strategy document. The audit reports newest-source dates, modality coverage for the prospect, and disagreements among cited sources. If it flags stale intel (OSINT older than 90 days, no recent CRM touch) or a contradiction (e.g., pipeline says one stage but a thread says another), surface those findings in the closing recommendation as risks to the strategy.

---

**Output:** Complete deal strategy document. Ready to use as briefing for Misha or partner sales team.

## Knowledge Base

After delivering the strategy, offer: "Want me to capture the positioning rationale? `/odin log` records it as an episode in Odin's brain (CEO-only); `/zk distill` extracts the durable decision insights into the knowledge base."
