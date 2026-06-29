---
name: validate
description: "Fact-check any draft against DataStore source documents"
argument-hint: "[content or file path]"
allowed-tools: "Read, Glob"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - validate
    - fact-check
    - verify claims
x-31c-capability:
  what: >
    Fact-checks a draft against the 31C DataStore, extracting every claim and bucketing it Validated / Unverified / Contradicted with a forensic evidence grade (Confirmed / Deduced / Hypothesized) and a risk level.
  how: >
    Run /validate <content or file path>; it reads datastore/INDEX.md plus source documents (DataStore wins over context/ over reference/) and returns a structured report with prioritized corrections.
  when: >
    Use to verify facts, numbers, and superlatives before external-facing content ships. For grading an artifact's craft and quality use /evaluate.
---
# Validate

Fact-check content against the 31C DataStore — the source-of-truth repository of authoritative original documents.

## Variables

- `$ARGUMENTS` — The content to validate. Either paste the draft text directly, or provide a file path to read.

## Instructions

### 1. Load the Content

If `$ARGUMENTS` is a file path, read the file first. Otherwise, treat it as the draft text to validate.

### 2. Read the DataStore Index

Read `datastore/INDEX.md` to see what source documents are available.

### 3. Extract Claims

Scan the provided content and extract every factual claim:

**Explicit claims:**
- Numbers (team size, revenue, market data, percentages, pricing, performance specs)
- Names (people, companies, partners, products, titles/roles)
- Dates (founding, launches, events, milestones, timelines)
- Technical claims (capabilities, architecture, performance, specifications)
- Market claims (TAM, growth rates, market share, competitive positioning)
- Partnership claims (territories, terms, relationships, scope)
- Financial claims (valuations, funding amounts, deal economics, pricing)

**Implicit claims:**
- Superlatives and comparisons ("fastest," "first," "only," "leading")
- Attributed quotes or statistics ("according to Mordor Intelligence...")
- Implied uniqueness or exclusivity ("no other vendor offers...")

### 4. Validate Against Sources

For each claim, validate using this source hierarchy (highest authority first):

1. **DataStore** — Read relevant source documents (PDFs up to 20 pages, or `-extract.md` companions for binary files)
2. **context/ files** — Cross-reference with current-data.md, business-info.md, pipeline.md, etc.
3. **reference/ files** — Check market intelligence, competitive data, strategic frameworks

If sources conflict: **DataStore wins** over context/, which wins over reference/.

### 5. Assess Risk Level

For each unverified or contradicted claim, assign a risk level:

| Risk | When to Apply |
|------|---------------|
| **High** | Financial figures, legal claims, investor-facing data, partner terms, performance specs cited to prospects, pricing |
| **Medium** | Team size, timelines, market data, competitive comparisons, customer claims |
| **Low** | Qualitative descriptions, internal-only references, general positioning language |

### 6. Report

Produce a structured validation report. Attach a **forensic evidence grade** to every verdict alongside its status, per `reference/forensic-evidence-grading.md`:

- **Confirmed** - backed by the DataStore or >=2 independent sources (maps to the Validated bucket).
- **Deduced** - a single source or a defensible inference from confirmed facts; show the source or the chain.
- **Hypothesized** - plausible but unconfirmable from available sources (maps to Unverified); state which document would settle it.

The grade sharpens the existing Validated/Unverified/Contradicted buckets - it does not replace them. A Contradicted claim is reported as such with the source that overrides it.

#### Summary

| Status | Count |
|--------|-------|
| Validated | X |
| Unverified | Y |
| Contradicted | Z |

#### Validated

Claims confirmed by sources. For each:
- The claim
- Source document and location
- **Confirmed**

#### Unverified

Claims not found in any available source. Not necessarily wrong — just unconfirmable from what's available. For each:
- The claim
- Risk level (High / Medium / Low)
- Which document would validate it
- **Manual verification recommended**

#### Contradicted

Claims that conflict with sources. For each:
- The claim as written
- What the source says instead
- Source document and location
- Risk level (High / Medium / Low)
- Suggested correction

#### Source Inconsistencies

Cases where context/ or reference/ files disagree with each other or with the DataStore. These don't affect the draft directly, but flag workspace maintenance needs:
- The inconsistency
- Which files conflict
- Which source is authoritative (per hierarchy)
- Suggested fix

_Skip this section if no inconsistencies are found._

#### Recommendations

- Suggested edits to align content with verified facts, **prioritized by risk level**
- If DataStore is sparse, note which documents should be added to improve future validation
- If context/ files need updating to match DataStore, flag that here

## Rules

- Never fabricate a validation. If you can't find a source, say "Unverified" — don't guess.
- If the DataStore has no relevant documents, say so clearly and recommend which documents Misha should add.
- DataStore is the upstream authority. If context/ files contradict the DataStore, the DataStore wins.
- Check for staleness: if a claim matches a context/ file but the DataStore has a different (likely newer) figure, flag the discrepancy.
- Superlatives and marketing language ("first," "only," "leading") require source backing to be marked Validated. Otherwise, mark as Unverified with a note.
- Be thorough but concise. List every claim, but keep the report scannable.
