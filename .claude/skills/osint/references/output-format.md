# OSINT — Output Format

Consumed by: `.claude/skills/osint/SKILL.md` (Phase 2 synthesis + Phase 4 output).
Last Updated: 2026-06-16

The full intelligence-brief markdown template, the per-mode section templates, and the
HTML-report specification. Kept out of the SKILL body so the phase logic stays under the
inline budget.

## Brief markdown template (Phase 2)

```
# OSINT Intelligence Brief: [Target Name]

**Classification:** Internal - CEO Eyes Only
**Date:** YYYY-MM-DD
**Mode:** [Company | Person | Market | Technology]
**Analyst:** Claude OSINT Engine
**Depth:** [Standard | Deep | Maximum]
**Context:** [Why this intelligence was gathered]

---

## Executive Summary

[3-5 bullet assessment. Lead with the most important finding. Each bullet: one decisive sentence. No filler.]

---

## Resolved Entities

[Render the Phase 0.5 plan as a table. One row per populated field. Source column references `sources[N]` from the resolver JSON.]

| Field | Value | Source |
|---|---|---|
| canonical.name | [value] | [N] |
| canonical.aliases | [list] | [N] |
| social.x_handle | [value or "(unresolved)"] | [N or "-"] |
| ... (mode-specific rows) | ... | ... |

**Resolution status:** [high / partial / low] | **Backend:** [tavily / brave] | **Model:** [haiku / sonnet]

If `resolution_status: low`, helper returned an `error` field, or timeout fired: render `Phase 0.5 resolution: low confidence (or unavailable). Brief used literal-target queries.` and skip the table.

---

## [Section Name] [CONFIDENCE: HIGH/MEDIUM/LOW/UNVERIFIED]

[Content with inline source attribution]

**Sources:** [List]

---

## Intelligence Gaps

[What could NOT be found. What remains unverified. What requires additional access or HUMINT. Gaps are as valuable as findings.]

---

## 31C Relevance Assessment

- **Pipeline impact:** Does this affect any active deals in pipeline.md?
- **Competitive implications:** Does this change competitive positioning?
- **Partnership opportunity:** Does this reveal partnership or channel potential?
- **Threat assessment:** Does this represent a risk to 31C's strategy?
- **Market timing:** Does this affect GTM phasing?

---

## Recommended Actions

1. **[Action]** - [Why] - [Timeline]

---

## Skill Chain Recommendations

[Based on findings, recommend which skills to run next:]
- `/competitor-intel [company]` - if target is a competitor
- `/deal-strategy [prospect]` - if target is a prospect
- `/meeting-prep [person]` - if a meeting is upcoming
- `/market-brief [region]` - if regional context needs more depth
- `/crm add [person]` - if key contacts were discovered

---

## Source Registry

| # | Source | URL | Stream | Confidence |
|---|--------|-----|--------|------------|
| 1 | [Name] | [URL] | [Stream #] | HIGH/MEDIUM/LOW |
```

## Section templates by mode

**COMPANY:** Corporate Identity & Ownership, Financial Intelligence, Technology & Product Assessment, Leadership Map, Legal & Regulatory Exposure, Digital Footprint, Competitive Position, Partnership Ecosystem, Market Sentiment, SWOT Assessment (synthesized)

**PERSON:** Professional Profile, Career Trajectory, Public Presence & Thought Leadership, Board & Advisory Positions, Communication Style Assessment, Network & Relationships, Engagement Strategy

**MARKET:** Market Overview & Dynamics, Regulatory Landscape, Competitive Environment, Digital Infrastructure, Investment Climate, Key Players & Decision Makers, Geopolitical Context, 31C Entry Assessment

**TECHNOLOGY:** Architecture & Capabilities, IP & Patent Landscape, Technical Community Assessment, Talent & Hiring Signals, Standards & Interoperability, Competitive Comparison Matrix, Adoption & Market Traction

## HTML report specification (Phase 4, step 4)

ALWAYS generate a professional, self-contained HTML report as the final deliverable at
`outputs/intel/osint/YYYY-MM-DD-[target-slug]/report.html`:

- Dark executive-grade theme with "CEO Eyes Only" classification banner.
- Key stats dashboard row (experience years, exits, advisory positions, verified roles —
  adapt metrics to mode).
- Executive summary with bullet highlights.
- All sections from `brief.md` rendered with:
  - Color-coded confidence badges (HIGH=green, MEDIUM=amber, LOW=orange, UNVERIFIED=red)
    on every section header.
  - Tables for career timeline, exits, advisory positions, conference history, source registry.
  - Role-type tags on exits (Co-Founder, CEO, CPO, Advisor etc.).
  - Profile grid cards for key personal/company details.
  - Quote blocks for notable quotes.
  - Analysis boxes (blue) for assessments and synthesis.
  - Warning boxes (red) for unverified claims.
  - Platform grid for digital footprint (active/indexed/not found status).
  - Network groups organized by company affiliation.
  - Numbered intelligence gaps with closed/open status.
  - Relevance assessment grid (label + description layout).
  - Numbered action items with timeline badges.
  - Full source registry table with stream numbers and confidence badges.
- Responsive design (mobile breakpoints), print-friendly (@media print).
- Footer with 31C Intelligence Division branding, date, stream count, source count.
- No external dependencies - all CSS inline in `<style>` block.
