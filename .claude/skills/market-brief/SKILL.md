---
name: market-brief
description: Market or regional intelligence brief for a sector or geography - TAM/sizing, competitive landscape, regulatory environment, entry timing, and 31C positioning, framed through the sovereign/DPI+ lens. Use for sector- or region-level analysis, not a single company. Trigger when the user says "market intel", "market for [sector]", "regional analysis", "sector overview", or "TAM for [sector]". Do NOT use for a specific named company (use /competitor-intel or /osint) or global geopolitical briefs (use /ceo-intel).
argument-hint: "[topic]"
context: fork
allowed-tools: "WebSearch, WebFetch, Read, Bash(python3:*)"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - market intel
    - market for
    - regional analysis
    - sector overview
    - TAM for
    - market size
x-31c-capability:
  what: >
    Produces a fast market intelligence brief on a region, country, sector, or
    technology trend - market size and drivers, competitive landscape, and 2-3
    actionable insights framed through 31C's sovereign DPI+ lens.
  how: >
    Run /market-brief [topic]. Forked-context web research; default depth is
    surface (3-5 points), pass detailed for full analysis.
  when: >
    Use for a sector, region, or TAM question. For a specific named company use
    /osint or /competitor-intel; for global geopolitics use /ceo-intel.
---
# Market Brief

Produce an instant market intelligence brief on a region, country, sector, or competitor.

## Variables

topic: [Region / country / competitor / technology trend — e.g., "[target-region] telco market", "a competitor DPI position", "[region] sovereignty regulations", "5G security spending in [region]"]
depth: surface | detailed — default: surface (3-5 key points); detailed (full analysis)
purpose: [What decision or action this brief supports — optional]

---

## Instructions

Before drafting, read:
- `reference/dpi-market-intelligence.md` — DPI market data and competitor landscape
- `reference/geopolitical-landscape.md` — Regional sovereignty and tech context
- `reference/search-domains.md` — Domain filtering for web searches
- `context/strategy.md` — Strategic priorities to frame relevance
- `context/current-data.md` — Current positioning and active markets

When searching the web for market intelligence, apply domain filtering from `reference/search-domains.md`:
- Use `allowed_domains` from the topic group(s) most relevant to the query (e.g., Telecom & DPI for the DPI market, the relevant regional group for regional telecom)
- Always apply Blocked Domains as `blocked_domains`
- For broad market overview queries, use `blocked_domains` only

Produce a brief covering:

**Surface (3-5 key points):**
- Market size and growth rate (if applicable)
- Key dynamics and drivers
- 31C's position and opportunity
- 2-3 actionable insights for Misha

**Detailed (full analysis):**
- All of the above plus:
- Competitive landscape in this region/sector
- Regulatory environment
- Entry timing assessment
- Recommended positioning approach
- Specific risks and mitigations

Frame everything through 31C's lens: sovereign, non-aligned, DPI+ category leader, the legacy incumbent vacuum opportunity.

---

## Markets Are Pricing (conditional, Polymarket)

If the topic matches the Polymarket coverage whitelist (AI / big tech / elections / geopolitics / crypto / macro / global sports / corporate events - see `reference/polymarket-coverage.md`), include a "Markets Are Pricing" section in the brief output.

**Disambiguation rule (P4):** if the topic is one or two words and could match multiple entities (e.g., "Apple" could match Apple Inc OR apple-fruit markets), pass `--keywords` with 2-3 disambiguators.

```bash
python scripts/polymarket.py "$TOPIC" --output markdown
# OR with disambiguation:
python scripts/polymarket.py "Apple" --keywords "company,stock,iphone" --output markdown
```

Include the rendered markdown table verbatim in the brief, including the trailing internal-use footer line. If `skip_reason` is `outside_whitelist`, `no_matches`, or `fetch_error`, omit the section silently - do not flag the absence.

**External-use boundary (CRITICAL):** Polymarket data is internal signal only. NEVER quote in proposals, letters, partnership documents, OnePagers, RFP responses, LinkedIn posts, or any external 31C communication. Boundary pinned in `reference/polymarket-coverage.md`.

---

## Post-synthesis audit (required)

Per development-standards, this skill synthesizes over a source set. After writing the brief, run `/brain-audit --sources <the brief + cited datastore/reference files> --entity "<market or sector>"` (omit `--entity` for a broad multi-region brief) and append the returned footer.
