---
name: competitor-intel
description: Competitive analysis of a named vendor versus 31C/ODUN.ONE - product gaps, geographic overlap, honest strengths, exploitable weaknesses, win strategy, and recommended actions. Use when comparing 31C against a specific competitor or sizing a competitive landscape. Trigger when the user says "competitor analysis", "how does [company] compare to [competitor]", "competitive advantage vs [competitor]", or "competitive landscape for [sector]". Do NOT use when the target is a person (use /osint), for market sizing (use /market-brief), or a generic "vs" with no named second party (ask to disambiguate first).
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
argument-hint: "[company]"
context: fork
allowed-tools: "WebSearch, WebFetch, Read, Bash(python3:*)"
model: sonnet
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - competitor analysis
    - competing vendor
    - how does [company] compare
    - competitive landscape
x-31c-capability:
  what: >
    Deep competitive analysis of a named vendor against ODUN.ONE - product
    comparison table, geographic overlap, honest strengths, exploitable
    weaknesses, and a win strategy, with each claim evidence-graded.
  how: >
    Run /competitor-intel <company>. Forked-context web research pass that
    maintains a persistent case file at outputs/intel/cases/ and outputs a
    competitor brief.
  when: >
    Use for head-to-head against a named competitor. For a person or company
    recon use /osint; for market sizing use /market-brief.
---
# Competitor Intelligence

Deep competitive analysis on a specific company or vendor.

## Variables

company: [Company name — e.g., "a competing vendor", "a state-aligned vendor", "a state-aligned vendor's DPI division"]
trigger: [Why this analysis now — deal competition, market move, partnership evaluation, investor question]
depth: quick | deep — default: deep

---

## Instructions

Before analyzing, read:
- `reference/dpi-market-intelligence.md` — Existing competitive data
- `outputs/deliverables/documents/global-dpi-vendor-landscape-2025.md` — Full vendor landscape (check if this company is already profiled)
- `context/strategy.md` — 31C's competitive positioning
- `context/business-info.md` — ODUN.ONE capabilities for comparison

Read `reference/search-domains.md` for domain filtering configuration.

Search the web for the latest information on this company using two search passes:

**Pass 1 -- Industry sources** (filtered):
- WebSearch with `allowed_domains` from Telecom & DPI + General Tech groups
- `blocked_domains`: Blocked Domains list
- Query: "[company name] DPI deep packet inspection product launch partnership 2026"

**Pass 2 -- Broad company search** (blocklist only):
- WebSearch with `blocked_domains` only (Blocked Domains list)
- Query: "[company name] earnings revenue leadership acquisition market moves"

**Pass 3 -- Website crawl** (for comprehensive product intelligence):
- Use `python scripts/firecrawl.py crawl "[company website]" --limit 10 --include "/product|/pricing|/solution"` to get full product and pricing pages.

---

## Evidence Grading & Case File

Before writing the analysis, grade the evidence and maintain the competitor's persistent case file. Full spec: `reference/forensic-evidence-grading.md`.

- **Grade each material claim** Confirmed (>=2 independent sources or an official filing) / Deduced (single source or defensible inference - show the chain) / Hypothesized (unconfirmed - state what would confirm or refute it). Surface the grade next to the claim, especially in Product Comparison, Their Weaknesses, and Financial health signals - a competitor weakness graded Hypothesized must not be sold as Confirmed.
- **Case file** at `outputs/intel/cases/[competitor-slug].md` (ceo-only): if it exists, read it first and update hypothesis statuses (append-only, never delete a refuted theory - flip its `Status` to `Refuted` with a `Resolution`); else create it from `reference/templates/intel-case-file.md`. This protects the head-to-head from narrative lock-in across quarters.

---

## Quick Analysis (1 page)

- Who they are (one paragraph)
- What they offer vs. what ODUN.ONE offers
- Their weakness that matters most for our current situation
- Recommended positioning in 2-3 sentences
- One thing Misha should know right now

---

## Deep Analysis

### Company Profile
- HQ, ownership, revenue, market cap (if public)
- Employee count, leadership
- Recent strategic moves (last 12 months)
- Financial health signals

### Product Comparison

| Capability | [Competitor] | ODUN.ONE | Advantage |
|------------|-------------|----------|-----------|
| Architecture | | | |
| Encrypted traffic | | | |
| AI/ML capabilities | | | |
| Deployment model | | | |
| Scalability | | | |
| Sovereignty | | | |
| API/Integration | | | |

### Geographic Overlap
- Where they operate vs. where 31C operates/plans to operate
- Regions where we'll compete directly
- Regions where they can't operate (geopolitical barriers)

### Their Strengths (Honest Assessment)
- What they do well
- Where they have advantages we need to respect
- Their installed base / switching cost moat

### Their Weaknesses (Exploitable)
- Technical gaps
- Strategic missteps
- Geopolitical liabilities
- Financial vulnerabilities
- Market positioning gaps

### Win Strategy
- How to position against them in a head-to-head evaluation
- Key messages for different audiences (telco CTO, government buyer, investor)
- Objection handling: what they'll say about us, and our response
- The killer argument (one sentence that ends the comparison)

### Recommended Actions
- Immediate: what to do right now
- Medium-term: how to position over the next 6 months
- What to watch: signals that indicate their next move

---

**Output:** Competitor intelligence brief. Actionable, honest, and specific to 31C's current situation.

**Post-synthesis audit (required).** Per development-standards, this skill synthesizes over a source set, so after writing the brief run `/brain-audit --sources <the brief + any cited intel/datastore files> --entity "<competitor>"` and append the returned footer (newest-source dates, modality coverage, source disagreements).
