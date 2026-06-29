# OSINT-Advanced - Brief Output Format

Consumed by: `.claude/skills/osint-advanced/SKILL.md` Phase 2 synthesis.

Last Updated: 2026-06-10

Canonical structure for the OSINT-Advanced intelligence brief. Mirrors `/osint` brief format with the specialised additions (sanctions banner, tool access log, CLI recommendations). Apply confidence ratings per the table below to every section.

---

## Output Format

```
# OSINT-Advanced Intelligence Brief: [Target Name]

**Classification:** Internal - CEO Eyes Only
**Date:** YYYY-MM-DD
**Mode:** [Company | Person | Market | Technology]
**Analyst:** Claude OSINT-Advanced Engine
**Depth:** [Quick | Full]
**Streams executed:** [list of streams that ran]
**Base OSINT:** [Link to existing /osint brief if one exists, or "Not yet run"]

---

## Executive Summary
[3-5 bullet assessment focusing on what specialized tools found that base /osint would miss.]

---

## Sanctions & Compliance Status
**STATUS: CLEAR / MATCH / PARTIAL MATCH**
[Screening results with database names, date, match details or clean confirmation.]

---

## [Stream-specific sections] [CONFIDENCE: HIGH/MEDIUM/LOW/UNVERIFIED]
[Content with inline source attribution]

---

## Intelligence Gaps
[What tools were inaccessible. What requires manual browser access. What requires paid API access.]

---

## Manual Investigation Recommended
[CLI tools the user should run locally for deeper coverage:]
- `maigret {username} --all-sites` - username enumeration across 3000+ sites
- `holehe {email}` - email-to-service mapping
[Manual browser tools:]
- PimEyes (face search): https://pimeyes.com
- Liveuamap (conflict map): https://liveuamap.com

---

## Tool Access Log
| Stream | Tool | Method | Result | Notes |
|--------|------|--------|--------|-------|
| sanctions | OpenSanctions API | WebFetch | Success | 0 matches |
| sanctions | OCCRP Aleph API | WebFetch | Success | 2 results |
| infra | crt.sh | WebFetch | Success | 15 certificates |
| ... | ... | ... | ... | ... |

---

## 31C Relevance Assessment
- **Pipeline impact:** [Does this affect active deals?]
- **Competitive implications:** [Does this change positioning?]
- **Partnership opportunity:** [Partnership or channel potential?]
- **Threat assessment:** [Risk to 31C's strategy?]
- **Compliance status:** [Sanctions clear for engagement?]

---

## Recommended Actions
1. [Action] - [Why] - [Timeline]

---

## Skill Chain Recommendations
- `/osint [target]` - if base intelligence not yet gathered
- `/crm add [person]` - if key contacts discovered
- `/deal-strategy [company]` - if sanctions CLEAR and deal potential identified
- `/odin log` (CEO-only) or `/zk distill` - capture durable intelligence

---

## Source Registry
| # | Source | URL | Stream | Confidence |
|---|--------|-----|--------|------------|
| 1 | [Name] | [URL] | [Stream] | HIGH/MEDIUM/LOW |
```

---

## Confidence Rating System

| Level | Criteria |
|-------|----------|
| **HIGH** | Multiple independent sources confirm. Official databases (sanctions lists, SEC filings, corporate registries). |
| **MEDIUM** | Two or more sources suggest. Press coverage, analyst reports, credible industry sources. |
| **LOW** | Single source or indirect inference. Job postings, social media, community forums. |
| **UNVERIFIED** | Logical inference from available data. No direct source. Flagged as analytical judgment. |
