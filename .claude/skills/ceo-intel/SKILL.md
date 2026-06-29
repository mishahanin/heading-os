---
name: ceo-intel
description: Global, geopolitical, and cross-domain intelligence brief for the CEO - world events, sovereign-tech shifts, macro and threat signals relevant to 31C, rendered as a dated HTML/PDF brief. Use for world-scale situational awareness, not a single entity. Trigger when the user says "world intel", "geopolitical brief", "what's happening globally", "global threats", or "CEO intelligence brief". Do NOT use for a specific company or person (use /osint) or an externally published newsletter (use /intel-briefing-newsletter).
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
argument-hint: "[focus]"
context: fork
allowed-tools: "WebSearch, WebFetch, Read, Bash(python3:*)"
model: sonnet
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - world intel
    - geopolitical brief
    - what's happening globally
    - global threats
    - CEO intelligence brief
x-31c-capability:
  what: >
    A confidential CEO-eyes-only world intelligence brief - geopolitics, cybersecurity, markets, and the priority regions regional intel, each section framed through 31C's lens with direct business implications and action items.
  how: >
    Run /ceo-intel [focus]. Forked-context research pass that writes an HTML + PDF brief to outputs/intel/briefs/world-intel-YYYY-MM-DD.html. Names partners, deals, and investors - never shared externally.
  when: >
    Use for internal world/geopolitical situational awareness with 31C action items. For the public-safe version use /intel-briefing-newsletter; for a specific company or person use /osint.
---
# CEO Intelligence Brief

Generate a confidential world intelligence brief for CEO eyes only. Covers geopolitics, cybersecurity, markets, and regional developments - framed through 31C's strategic lens with direct business implications and action items.

## Variables

focus: [Optional - specific topic or region to emphasize, e.g., "a regional conflict", "regional defense", "regional telecom"]

---

## Instructions

**Classification:** Internal - CEO Eyes Only. This document contains 31C-specific strategic analysis, business implications, and action recommendations. NEVER share externally.

Before drafting, read:
- `reference/misha-voice.md` - Voice and tone
- `reference/dpi-market-intelligence.md` - DPI market data
- `reference/search-domains.md` - Domain filtering for research
- `context/strategy.md` - Current strategic priorities
- `context/current-data.md` - Current metrics and workstreams
- `context/pipeline.md` - Active deals and investor conversations
- `context/people.md` - Key contacts and relationship context

---

## Phase 1: Research (Parallel)

Before searching, read `reference/search-domains.md` for domain filtering configuration.

Run these searches in parallel to gather live intelligence. Use `allowed_domains` from the matching topic group(s):

1. **Geopolitics & Security** - WebSearch for major world events, conflicts, policy changes
   - `allowed_domains`: Geopolitics & Defense group
2. **Cybersecurity** - WebSearch for cyber incidents, threat actors, vulnerabilities, breaches
   - `allowed_domains`: Cybersecurity + General Tech groups
3. **[Priority Region 1]** - WebSearch for defense, cybersecurity, telecom developments
   - `allowed_domains`: [Priority Region 1] + Geopolitics & Defense groups
4. **[Priority Region 2]** - WebSearch for the priority region and regional developments
   - `allowed_domains`: [Priority Region 2] + Geopolitics & Defense groups
5. **[Priority Region 3]** - WebSearch for telecom, cybersecurity, infrastructure investment
   - `allowed_domains`: [Priority Region 3] + Telecom & DPI groups
6. **Markets & DPI** - WebSearch for capital markets, DPI industry, telecom trends
   - `allowed_domains`: Markets & Finance + Telecom & DPI groups
7. **Perplexity Deep Research** - Run with domain filtering:
   ```bash
   python scripts/perplexity-research.py --domains "reuters.com,bbc.com,ft.com,bleepingcomputer.com,lightreading.com,darkreading.com,therecord.media" "current world events [focus area] cybersecurity telecom defense markets [current month] [current year]"
   ```

Also fetch World Monitor for aggregated intelligence:
- https://worldmonitor.app/
- https://tech.worldmonitor.app/
- https://finance.worldmonitor.app/

After WebSearch returns article URLs, batch-scrape top articles via `python scripts/firecrawl.py batch` for full content instead of relying on search snippets.

---

## Phase 2: Write the Brief

Generate a single-page HTML intelligence brief with these sections:

### Header
- Title: "World Intelligence Brief"
- Subtitle: "31 Concept - Strategic Intelligence"
- Date: Current date
- Classification: "Internal - CEO Eyes Only"

### Threat Level Bar
- Set to one of: LOW, GUARDED, ELEVATED, HIGH, CRITICAL
- Color: green (LOW/GUARDED), orange (ELEVATED), red (HIGH/CRITICAL)

### Key Metrics Strip
- 5 metrics with large values, small labels, and color coding (up/danger/neutral)
- Focus on: DPI market, threat counts, commodity prices, regional indicators

### Sections (each with a "31C Relevance" callout)

Each section gets:
- **Section title** (uppercase, bordered)
- **Body text** - 1-2 paragraphs of factual intelligence
- **31C Relevance callout** - Orange left-border box with direct business implications

Write these sections:

1. **Geopolitical** - The dominant world events affecting 31C's markets. What's happening and why it matters for sovereign technology procurement.

2. **Economic / DPI Market** - DPI market data, telecom adoption trends, investment climate. Reference `reference/dpi-market-intelligence.md` for baseline figures. Include fresh data from research.

3. **Markets Are Pricing** (conditional, Polymarket) - if the focus topic matches the Polymarket coverage whitelist (`reference/polymarket-coverage.md`), include this section. Run:

   ```bash
   python scripts/polymarket.py "$FOCUS_OR_TOP_THEME" --output markdown
   # Disambiguation rule (P4): for one-or-two-word topics that could match multiple entities,
   # pass --keywords with 2-3 disambiguators (e.g., "Apple" --keywords "company,stock,iphone").
   ```

   Include the rendered markdown table verbatim in the brief, INCLUDING the trailing internal-use footer line. If `skip_reason` is `outside_whitelist`, `no_matches`, or `fetch_error`, omit this section silently.

   **External-use boundary (CRITICAL):** Polymarket data is internal signal only. NEVER quote in proposals, letters, partnership documents, RFP responses, LinkedIn posts, or any external 31C communication. Boundary pinned in `reference/polymarket-coverage.md`.

4. **Cybersecurity** - Specific incidents, threat actors, attack statistics. Focus on threats relevant to telecom, government, and critical infrastructure - 31C's target verticals.

5. **Regional Intelligence** - Three-column layout:
   - **[Priority Region 1]**: Defense, cybersecurity, telecom developments.
   - **[Priority Region 2]**: Digital infrastructure, sovereignty moves.
   - **[Region 3]**: regional telecom, cybersecurity investment, infrastructure projects.
   - Each region gets its own "31C Relevance" callout.

6. **Strategic Implications** - Numbered list of 3-5 specific action items for Misha. Be direct:
   - Reference specific pipeline deals or partners where relevant
   - Suggest timing ("push now", "re-engage this week", "prepare for Q2")
   - Connect intelligence to fundraising narrative, deal positioning, or partner enablement
   - Flag risks or opportunities that need immediate attention

### Footer
- "31 Concept - Confidential Intelligence Brief"
- Source attribution line

---

## Phase 3: Generate Output

1. **Create the HTML file** at `outputs/intel/briefs/world-intel-YYYY-MM-DD.html`

   Use this design system:
   - Font: Segoe UI / Helvetica Neue / Arial
   - Body: 10pt, line-height 1.55
   - Page: A4 with 20mm margins
   - Colors: #1a1a1a (ink), #c77b30 (accent/relevance), #c0392b (danger), #1a7a3a (up)
   - Relevance callouts: #f7f3ed background, 3px left border in #c77b30
   - Metrics strip: bordered boxes, large values (14pt bold), small uppercase labels (7pt)
   - Regional columns: flex layout, 3 equal columns
   - Section titles: 11pt, 800 weight, uppercase, letter-spacing 1.5px

2. **Convert to PDF:**
   ```bash
   python scripts/html-to-pdf.py outputs/intel/briefs/world-intel-YYYY-MM-DD.html
   ```

3. **Validate:**
   ```bash
   python scripts/sanitize-text.py outputs/intel/briefs/world-intel-YYYY-MM-DD.html --scan
   ```

4. Report both file paths (HTML + PDF), and hidden character status.

5. **Post-synthesis audit:** run `/brain-audit --sources <the brief source files>` (omit `--entity` — this is a multi-region brief, not entity-scoped) and append the returned footer. Per development-standards, any synthesis-over-sources skill composes /brain-audit.

---

## Voice Rules

- **Intelligence sections**: Third-person, objective, factual. Authoritative.
- **31C Relevance callouts**: Direct, tactical, first-person ("you", "your"). Speak to Misha as the captain.
- **Strategic Implications**: Action-oriented. Specific. No hedging.
- **Overall**: Hyphens only (never em-dashes). ODUN.ONE styled correctly. DPI+ with the plus.
- **Length**: Total brief should fit on 1-2 A4 pages. Concise is better.

## What Makes This Different from /intel-briefing-newsletter

This brief is the OPPOSITE of the public newsletter:
- It names partners, clients, pipeline deals, and investors where relevant
- It contains direct business advice and action items
- It references internal strategy, pricing, and competitive positioning
- It frames every piece of intelligence through "what should Misha do about this?"
- It is NEVER shared externally

## NEVER
- Share this document outside CEO context
- Publish any content from this brief without sanitizing confidential information
- Use this format for external communications

## Knowledge Base

After delivering the brief, offer: "Want me to capture the lasting signals? `/odin log` records them as an episode in Odin's brain (CEO-only); `/zk distill` adds durable intelligence to the knowledge base."
