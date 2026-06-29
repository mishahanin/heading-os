---
name: osint-advanced
description: "EXPLICIT INVOCATION ONLY -- never auto-trigger. This skill activates ONLY when the user literally types '/osint-advanced' or says 'run osint-advanced'. DO NOT trigger from 'investigate', 'research', 'dig into', 'OSINT', 'intelligence on', 'background check', 'due diligence', 'profile', 'recon', 'dossier', 'deep dive', or ANY general intelligence request -- those belong to /osint. This skill queries specialized OSINT databases (sanctions lists, corporate registries, breach databases, threat actor platforms, infrastructure scanners) that the base /osint does not cover. Requires explicit user approval before each execution."
disable-model-invocation: true
argument-hint: "[target]"
context: fork
allowed-tools: "WebSearch, WebFetch, Read, Bash(python3:*)"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers: []
x-31c-capability:
  what: >
    Queries specialized OSINT databases that base /osint does not reach -
    sanctions and compliance lists, corporate registries, breach databases,
    threat-actor platforms, infrastructure scanners, face and username search -
    and produces a structured brief with confidence ratings and a sanctions banner.
  how: >
    Explicit invocation only - type /osint-advanced <target>. Never auto-triggers
    from "investigate" or "research". Runs in a forked context and writes brief.md,
    tool-responses.md, and report.html to outputs/intel/osint-advanced/.
  when: >
    Use after /osint has run and a specific dimension still needs depth -
    compliance screening for a deal, breach exposure, infrastructure recon. For
    the first broad pass on any target, use /osint.
---
# OSINT-Advanced -- Specialized Database Investigation

Complements `/osint` with purpose-built OSINT database queries. Where `/osint` uses WebSearch and Perplexity for broad intelligence, this skill queries curated specialized tools for dimensions general search cannot reach.

**This skill is NEVER auto-triggered.** It runs ONLY on explicit `/osint-advanced` invocation.

## When to Use This vs. /osint

| Use `/osint` when... | Use `/osint-advanced` when... |
|----------------------|-------------------------------|
| First investigation on any target | /osint already ran but gaps remain |
| Broad intelligence picture needed | Specific dimension needs depth (sanctions, breaches, infrastructure) |
| General company/person/market/tech research | Compliance screening for a deal |
| Standard due diligence | Deep due diligence for the priority regions |
| Quick background check | Username enumeration, face search, breach check |

## Variables

target: [Name, company, email, username, domain, or region to investigate]
mode: company | person | technology | market - default: auto-detect
streams: all | comma-separated list - default: auto-select based on mode
depth: quick | full - default: full

---

## Phase 0: Context Loading

Read before any investigation:

1. `reference/osint-advanced-toolkit.md` -- Tool registry with endpoints, access methods, validation status (MANDATORY)
2. `.claude/skills/osint-advanced/references/tool-integration-guide.md` -- Per-tool query patterns and response parsing
3. `reference/search-domains.md` -- Domain filtering for WebSearch calls
4. `context/strategy.md` -- Strategic priorities for 31C relevance framing
5. `context/pipeline.md` -- Active deals to cross-reference
6. `context/people.md` -- Key contacts and relationship context
7. `context/business-info.md` -- ODUN.ONE capabilities
8. Check `outputs/intel/osint/` for existing base OSINT briefs on this target
9. Check `crm/contacts/` if target is a person

**Mode auto-detection:**
- Company names, ticker symbols, organizations -> company
- Personal names, titles, "who is" -> person
- Country, region, sector, "market for X" -> market
- Product, protocol, "how does X work" -> technology

---

## Phase 1: Execute Streams

### Stream Selection by Mode

| Stream | Company | Person | Market | Technology |
|--------|---------|--------|--------|------------|
| Sanctions/Compliance | **MANDATORY** | **MANDATORY** | optional | -- |
| Corporate Registry | yes | -- | yes | -- |
| Digital Footprint | -- | yes | -- | -- |
| Email Intelligence | -- | yes | -- | -- |
| Infrastructure Recon | yes | -- | -- | yes |
| Threat Intelligence | yes | -- | -- | yes |
| Image/Face Search | -- | yes | -- | -- |
| Geospatial/Conflict | -- | -- | yes | -- |
| Data Breach | yes | yes | -- | -- |
| Username & People Search | -- | yes | -- | -- |
| Social Media OSINT | -- | yes | -- | -- |
| Fact Check | yes | yes | yes | yes |

### Tool Fallback Chain

For every tool query, follow this priority:
1. **WebFetch** to API endpoint (if tool has WORKING status in toolkit registry)
2. **Firecrawl** (`python scripts/firecrawl.py scrape {url}`) if WebFetch fails
3. **WebSearch** with `site:` operator as last resort
4. If all fail: log in Tool Access Log, note in Intelligence Gaps

---

### Stream Catalogue

Per-stream query templates, API patterns, parsing hints, and fallback notes live in `references/streams-deep-osint.md`. Read that file once at the start of Phase 1 and substitute the resolved target, domain, email, username, or region into the bracketed slots. Streams covered:

- Sanctions/Compliance (MANDATORY for company/person)
- Corporate Registry (company/market)
- Digital Footprint (person)
- Username & People Search (person)
- Social Media OSINT (person)
- Email Intelligence (person)
- Infrastructure Recon (company/technology)
- Threat Intelligence (company/technology)
- Image/Face Search (person)
- Geospatial/Conflict (market)
- Data Breach (company/person)
- Fact Check (all modes)

For detailed per-tool query patterns, response parsing, confidence scoring, rate limits, and fallback chains, also consult `references/tool-integration-guide.md`.

---

## Phase 2: Synthesize Intelligence Brief

After all streams complete, produce a structured brief following the same format and quality standards as `/osint`.

The canonical brief structure - section order, classification banner, sanctions banner format, tool access log table, CLI recommendation block, source registry layout - lives in `references/output-format.md`. The same file carries the four-level confidence rating system (HIGH / MEDIUM / LOW / UNVERIFIED). Apply confidence ratings to every section.

---

## Phase 3: Output & Validation

1. **Create output directory:** `outputs/intel/osint-advanced/YYYY-MM-DD-[target-slug]/`

2. **Write brief:** `brief.md`

3. **Write raw tool responses:** `tool-responses.md` (all raw query results organized by stream)

4. **Write HTML report:** `report.html`
   - Same dark executive theme as `/osint` reports
   - Sanctions status banner (green CLEAR / red MATCH / amber PARTIAL)
   - Color-coded confidence badges (HIGH=green, MEDIUM=amber, LOW=orange, UNVERIFIED=red)
   - Tool Access Log table with method and result columns
   - CLI Tool Recommendations section
   - Responsive design, print-friendly
   - No external dependencies (all CSS inline)
   - Footer: "31C Intelligence Division -- OSINT-Advanced Engine"

5. **Validate:**
   ```bash
   python scripts/sanitize-text.py outputs/intel/osint-advanced/YYYY-MM-DD-[target-slug]/brief.md --scan
   python scripts/sanitize-text.py outputs/intel/osint-advanced/YYYY-MM-DD-[target-slug]/report.html --scan
   ```

6. **Report:** File paths, word count, hidden character status, streams executed, tools queried, sanctions status.

7. **Offer skill chain:** Based on findings, recommend next skills to run.

---

## Voice Rules

- **Intelligence sections:** Third-person, objective, factual. State what is known, how, and confidence level.
- **31C Relevance:** Direct, first-person. Speak to Misha as the captain. "This means...", "Your pipeline...", "The risk here is..."
- **Sanctions:** Explicit CLEAR/MATCH/PARTIAL status. Never ambiguous.
- **Recommended Actions:** Imperative. Specific. Time-bound.
- **Overall:** Hyphens only (never em-dashes). ODUN.ONE styled correctly. DPI+ with the plus.

## NEVER

- Fabricate tool results or claim a tool returned data it did not
- Present a sanctions CLEAR without actually querying the databases
- Skip the sanctions stream in company or person mode (it is MANDATORY)
- Auto-trigger from general intelligence requests (this skill is explicit-only)
- Access tools that require authentication without noting this limitation
- Use em-dashes, corporate filler, or hedging language
