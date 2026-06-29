---
name: osint
description: "Deep OSINT intelligence gathering on any target - company, person, market/region, or technology. The master intelligence skill that feeds all other intel skills. Produces executive-grade intelligence briefs with confidence ratings, source attribution, and 31C business relevance. Use when the user says 'OSINT', 'investigate', 'dig into', 'deep dive on', 'intelligence on', 'find out everything about', 'who is', 'what do we know about', 'research [company/person/market/tech]', 'background check', 'due diligence on', 'profile [target]', 'recon on', 'intel on', 'dossier on', or asks for comprehensive intelligence gathering on any entity, person, region, or technology. This is the raw intelligence foundation that /competitor-intel, /deal-strategy, /meeting-prep, and /market-brief consume. Always trigger when the user needs deep, comprehensive, multi-source intelligence on a specific target."
argument-hint: "[target]"
context: fork
allowed-tools: "WebSearch, WebFetch, Read, Bash(python3:*)"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.4"
x-31c-orchestration:
  parallel_safe: true
  shared_state:
    - outputs/intel/cases/
  triggers:
    - investigate
    - research
    - dig into
    - dossier
    - background on
    - due diligence on
    - who is
    - intelligence on
    - deep dive on
x-31c-capability:
  what: >
    Deep multi-source intelligence on a company, person, market, or
    technology - produces an executive brief with confidence ratings, source
    attribution, and 31C relevance. The raw-intel foundation other intel
    skills build on.
  how: >
    Run /osint <target>. Executes as a forked-context research pass (web
    search + fetch) and writes a brief to outputs/intel/osint/.
  when: >
    Use for comprehensive recon on a specific named target. For competitor
    comparison use /competitor-intel; for a sector or region use
    /market-brief; for global geopolitics use /ceo-intel.
---
# OSINT - Open Source Intelligence

Master intelligence gathering skill. Produces executive-grade intelligence briefs on any target using parallel multi-source research, confidence-rated findings, and source attribution.

## Variables

target: [Name of the company, person, market/region, or technology to investigate]
mode: company | person | market | technology - default: auto-detect from target
depth: standard | deep | maximum - default: deep
focus: [Optional - specific angle to emphasize, e.g., "financial health", "hiring signals", "regulatory exposure", "patent activity", "digital footprint"]
context: [Optional - why this intelligence is needed now, e.g., "pre-meeting", "due diligence", "competitive encounter", "partnership evaluation"]

---

## Phase 0: Context Loading

**Customization (optional).** This skill is customization-aware (pilot). Resolve any per-exec overrides first: `python scripts/resolve_customization.py --skill .claude/skills/osint`. Apply any `activation_steps_prepend`, `persistent_facts`, and output-path overrides from the merged result. On any failure, proceed with the defaults below - never block. Layout + authoring guide: `config/skill-custom/README.md`.

Read before any research:

- `reference/search-domains.md` - Domain filtering configuration (MANDATORY for all searches)
- `context/strategy.md` - Strategic priorities for 31C relevance framing
- `context/pipeline.md` - Active deals and investor conversations
- `context/people.md` - Key contacts and relationship context
- `context/business-info.md` - ODUN.ONE capabilities, partner ecosystem
- `datastore/INDEX.md` - Check if source documents exist for this target (especially `datastore/intelligence/competitors/`)

**Mode auto-detection:** If mode is not specified:
- Company names, ticker symbols, known organizations -> company
- Personal names, titles, "who is" -> person
- Country names, regions, sectors, "market for X" -> market
- Product names, protocols, "how does X work" -> technology

**CRM cross-reference:** If target is a person, search `crm/contacts/` for existing files. If target is a company, search CRM for contacts at that company. Load any matching records.

**DataStore cross-reference:** Check `datastore/intelligence/competitors/` and `datastore/INDEX.md` for existing intelligence on this target. Note what exists and what gaps remain.

---

## Phase 0.5: Entity Resolution

Before Phase 1 fans out, run the deterministic resolver to convert the target string into a structured plan: canonical name, aliases, parent/subsidiary, key people, social handles (X / LinkedIn / GitHub), ticker, products, competitor handles, regulators. Phase 1 streams use the resolved fields as alternative search terms instead of just the literal target string.

```bash
RESOLUTION=$(timeout 60 python scripts/resolve_entity.py "$TARGET" --mode "$MODE" --output json 2>/dev/null)
```

The helper takes 30-60s. It calls Tavily Search (primary) with Brave fallback for 2-3 targeted queries, then runs an Anthropic Haiku 4.5 tool-use call to extract the structured plan. Output is JSON with: `canonical`, `social`, mode-specific blocks (people/competitors/regulators for company; affiliations for person; key_players/regulators for market; vendors/communities for technology), plus `field_sources` (a `{field_path: source_index}` map), `resolution_status` (`high` / `partial` / `low`), `backend_used`, `model_used`, `search_queries_used`, `sources`.

**Use the resolved plan in Phase 1.** When a Phase 1 stream calls for `[company]` or `[person]` or `[target]`, substitute the canonical name AND aliases AND known handles. For example, if `canonical.aliases = ["e&", "Etihad ExampleTelco"]` and `social.x_handle = "@exampletelco"`, the Stream 1 query becomes `WebSearch: "ExampleTelco OR e& OR @exampletelco founded headquarters ownership"`.

**Fallback behaviour.** If `$RESOLUTION` is empty (timeout fired), contains an `"error"` field (no backends configured, search failed, extraction failed), or `"resolution_status": "low"`, fall back to literal-target queries and note the gap in the brief's Intelligence Gaps section.

**Surface in output.** The resolved plan renders as a `## Resolved Entities` block in Phase 2's brief output - see Output Format below.

---

## Phase 1: Research Streams (Parallel)

Execute streams in parallel. Always apply `blocked_domains` from `reference/search-domains.md` on every WebSearch call. For person/company-specific searches, use `blocked_domains` only. For topic-focused searches, apply `allowed_domains` from the relevant topic group AND `blocked_domains`.

When WebSearch returns URLs that need full content, use `python scripts/firecrawl.py batch` to scrape them in parallel instead of individual WebFetch calls.

---

Read ONLY the references file for the resolved mode - do not load the other three. This keeps Phase 1 context lean.

- **MODE: COMPANY** (10 streams) - `references/streams-company.md`
- **MODE: PERSON** (8 streams) - `references/streams-person.md`
- **MODE: MARKET** (8 streams) - `references/streams-market.md`
- **MODE: TECHNOLOGY** (7 streams) - `references/streams-technology.md`

Each reference carries the variable-substitution rules (which fields from Phase 0.5 to inject), the parallel query catalogue, and any sector-specific domain filtering. Refactored 2026-05-15 to close P2.2 from the workspace deep audit - inline catalogues bloated SKILL.md to 464 lines.

---

## Phase 1.5: Evidence Classification & Case File

Between research and synthesis, grade the evidence and reconcile it against the target's persistent case file. Full spec: `reference/forensic-evidence-grading.md`.

1. **Grade each material finding** Confirmed / Deduced / Hypothesized:
   - **Confirmed** - two or more independent sources, or one authoritative official record.
   - **Deduced** - a single credible source, or a defensible inference (show the chain + the assumption).
   - **Hypothesized** - plausible but unconfirmed; state what evidence would confirm or refute it.
   These grades coexist with the Phase 3 `[CONFIDENCE: ...]` tags - they do not replace them (Confirmed->HIGH, Deduced->MEDIUM, Hypothesized->LOW/UNVERIFIED). Emit both, e.g. `[CONFIDENCE: MEDIUM | Deduced - single source: 2026 annual report]`.

2. **Reconcile the case file** at `outputs/intel/cases/[target-slug].md`:
   - If it exists, read it FIRST. Update hypothesis statuses (Open/Confirmed/Refuted/Stale), append new hypotheses, and **never delete** a hypothesis - a refuted one flips `Status: Refuted` with a one-line `Resolution`.
   - If it does not exist, create it from `reference/templates/intel-case-file.md` and seed the hypothesis ledger.
   - The case file is ceo-only (lives under `outputs/`). It is the persistent memory; the per-run brief is the snapshot derived from it.

---

## Phase 2: Synthesize Intelligence Brief

After all research streams complete, synthesize into a structured brief.

**Brief markdown template, per-mode section templates, and the HTML-report specification all live in `references/output-format.md`.** The brief carries: classification header, Executive Summary, Resolved Entities table (Phase 0.5 plan), confidence-tagged sections with inline source attribution, Intelligence Gaps, 31C Relevance Assessment, Recommended Actions, Skill Chain Recommendations, and a Source Registry. Section sets differ by mode (company / person / market / technology) — see the reference.

---

## Phase 3: Confidence Rating System

Apply to every section:

| Level | Criteria |
|-------|----------|
| **HIGH** | Multiple independent sources confirm. Official documents, financial filings, government records. |
| **MEDIUM** | Two or more sources suggest but not fully confirmed. Press coverage, analyst reports, credible industry sources. |
| **LOW** | Single source or indirect inference. Job postings, social media, community forums. Plausible but unverified. |
| **UNVERIFIED** | Logical inference from available data. No direct source. Flagged explicitly as analytical judgment. |

Every factual claim must be tagged with its source. If a claim cannot be sourced, mark it UNVERIFIED and explain the reasoning.

---

## Phase 4: Output & Validation

1. **Create output directory:** `outputs/intel/osint/YYYY-MM-DD-[target-slug]/`
   - `[target-slug]` = kebab-case of target name (e.g., "competitor-ltd", "regional-telecom")

2. **Write intelligence brief:** `outputs/intel/osint/YYYY-MM-DD-[target-slug]/brief.md`
   - After writing, run the post-synthesis audit and append its footer to the brief (per development-standards: any synthesis-over-sources skill composes /brain-audit):
     `/brain-audit --sources outputs/intel/osint/YYYY-MM-DD-[target-slug]/brief.md,outputs/intel/osint/YYYY-MM-DD-[target-slug]/research-notes.md --entity "[target]"`

3. **Write raw research notes:** `outputs/intel/osint/YYYY-MM-DD-[target-slug]/research-notes.md`
   - All raw search results, URLs, extracted data organized by stream
   - Evidence file that supports the brief

4. **Write HTML report:** `outputs/intel/osint/YYYY-MM-DD-[target-slug]/report.html` — ALWAYS generate a professional, self-contained, dark executive-grade report ("CEO Eyes Only" banner, color-coded confidence badges, stats dashboard, all brief sections, responsive + print-friendly, all CSS inline, no external deps). Full element-by-element spec: `references/output-format.md` § "HTML report specification".

5. **CRM integration:**
   - PERSON mode, no CRM file: recommend `/crm add`
   - PERSON mode, CRM file exists: suggest updating with new intelligence
   - COMPANY mode, key contacts discovered: list for CRM consideration

6. **Validate:**
   ```bash
   python scripts/sanitize-text.py outputs/intel/osint/YYYY-MM-DD-[target-slug]/brief.md --scan
   python scripts/sanitize-text.py outputs/intel/osint/YYYY-MM-DD-[target-slug]/report.html --scan
   ```

7. **Report:** File paths, word count, hidden character status, skill chain recommendations.

---

## Depth Levels

**Standard:** 5 most relevant streams per mode. Single search per stream. Concise 1-2 page brief.

**Deep (default):** All streams. Multiple searches per stream. Perplexity deep research. Full brief with all sections.

**Maximum:** Everything in Deep, plus:
- Agent Browser (`agent-browser`) for JS-rendered pages if WebFetch fails on key targets
- Fetch World Monitor variants for current event context
- Cross-reference every finding against DataStore documents
- Double-source verification on critical claims
- Extended SWOT or comparison matrix
- Historical trend analysis (search across multiple years)

---

## Voice Rules

- **Intelligence sections:** Third-person, objective, factual. State what is known, how, and confidence level.
- **31C Relevance:** Direct, first-person. Speak to Misha as the captain. "This means...", "Your pipeline...", "The risk here is..."
- **Recommended Actions:** Imperative. Specific. Time-bound. "Call [person] this week to..."
- **Overall:** Hyphens only (never em-dashes). ODUN.ONE styled correctly. DPI+ with the plus.

## What Makes This Different

- `/competitor-intel` compares against ODUN.ONE. OSINT gathers raw intelligence on ANY target.
- `/deal-strategy` focuses on pricing, objections, modules. OSINT feeds INTO deal strategy.
- `/meeting-prep` produces talking points. OSINT produces the intelligence meeting-prep consumes.
- `/ceo-intel` scans world events. OSINT is target-specific, not event scanning.
- `/market-brief` is a fast snapshot. OSINT market mode is deep intelligence with competitive mapping and entry assessment.
- This skill is the intelligence foundation layer. It FEEDS the other skills.

## NEVER

- Fabricate sources or statistics. If it cannot be found, say so explicitly.
- Present UNVERIFIED information as fact. Always flag confidence level.
- Include information from authenticated or private sources the tools cannot access. Flag as intelligence gaps.
- Share output externally without sanitizing confidential information.
- Use em-dashes, corporate filler, or hedging language.

## Knowledge Base

After delivering the brief, offer: "Want me to capture the key signals? `/odin log` records them as an episode in Odin's brain (CEO-only); `/zk distill` extracts the durable signals and research fragments into the knowledge base."
