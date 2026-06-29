<!-- version: 1.0.0 | last-updated: 2026-04-28 -->
---
paths:
  - "outputs/content/**"
  - "outputs/deliverables/**"
  - "datastore/**"
---

# DataStore -- Source of Truth

Last Verified: 2026-06-08

The `datastore/` directory contains authoritative original documents (PDFs, contracts, presentations, spreadsheets). When making factual claims about 31C -- team size, pricing, partner details, market data, product capabilities -- **validate against the DataStore** before stating as fact.

## Structure (2026-04-20 restructure)

- `products/odun-one/` -- everything about the ODUN.ONE platform (architecture, datasheets, hardware, presentations, sales, reference)
- `products/trustone/` -- TrustONE product materials (datasheets, presentations)
- `corporate/presentations/` -- Company Brief and corporate-level decks
- `investment/decks/` -- investor decks (public-facing)
- `investment/ceo-only/` -- CEO-only financial correspondence (never synced to executives)
- `intelligence/competitors/` -- competitor docs by vendor
- `intelligence/industry/` -- analyst reports and industry research
- `intelligence/market-reference/` -- telco use-case and monetization reference material
- `operations/cybersecurity/` -- our cyber posture (internal)
- `operations/partnerships/` -- partner enablement, partnership framework
- `operations/engineering/` -- testing frameworks, pcap captures
- `events/{event}/` -- event-specific contact DBs and schedules
- `content/linkedin-archive/` -- Misha's published LinkedIn content
- `brand/` -- design system (logos, fonts, templates, examples)
- Each folder may contain an `_archive/` subfolder for superseded files.

## Validation Workflow

1. Browse `datastore/` by subject (products/, corporate/, investment/, intelligence/, operations/) to find relevant source documents
2. Read the source document (or its `-extract.md` companion for binary files)
3. Cross-reference your claim against the source
4. If the DataStore contradicts context/ or reference/ files, the DataStore wins

## When to Validate

Any external-facing content (LinkedIn posts, proposals, investor materials, partner communications, keynote decks) that contains specific facts, numbers, or claims.

## Corporate Templates

`datastore/brand/templates/` contains 31C brand identity 2026 templates. **Primary PowerPoint template:** `31C - Generic PP template.pptx` - use as the base for all presentations. **Brand reference:** `datastore/products/odun-one/presentations/31C - ODUN.ONE Product Presentation (Master, 12-Apr-2026).pptx` - use for design ideas and visual reference. Word template: `.dotx` v1.01.

## Corporate Fonts

The 31C corporate font set lives at `datastore/brand/fonts/` on the CEO workspace, and at `corporate/datastore/brand/fonts/` on executive workspaces (mirrored via the corporate sync). **Primary typeface:** GT Standard (Light + Medium weights, three optical sizes L/M/S, with oblique variants). Available in OTF, TTF, WOFF, and WOFF2. Use for all branded outputs - embed WOFF2 in HTML documents.

## Production Document Examples

`datastore/brand/examples/` contains real production documents used by 31C. This folder is updated periodically with new or revised versions. **Always check this folder before creating new corporate materials** to match current style and standards.

## Competitive Intelligence

`datastore/intelligence/competitors/` contains competitor product documents organized by vendor. When comparing ODUN.ONE against competitors, preparing proposals, positioning content, or answering customer questions about competitive differentiation -- read the relevant competitor documents to ground claims in actual product capabilities, not assumptions.
