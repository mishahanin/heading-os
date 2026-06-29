# Odin - Mode Catalog

Consumed by: `.claude/skills/odin/SKILL.md` Phase 0 mode dispatch. Holds the detailed per-mode pipeline, templates, response formats, and writeback rules for `learn`, `consult`, `reflect`, `recall`, `teach`, `log`, `collect`, and `skill-proposal`. The `compile` mode pipeline lives separately in `compile-pipeline.md`. File format templates live in `templates.md`.

Last Updated: 2026-06-06

---

## Mode Menu (when no arguments given)

Display this and wait for selection:

```
## Odin - Virtual Advisor

| # | Mode | What it does |
|---|------|-------------|
| 1 | **learn** | Ingest a book, article, video, or document into the brain |
| 2 | **consult** | Get Odin's take on a situation or decision |
| 3 | **recall** | Query what Odin knows about a topic |
| 4 | **reflect** | Odin reviews his own brain for gaps and growth |
| 5 | **teach** | Teach Odin directly from your experience |
| 6 | **log** | Record an episode - something that happened, for the brain to remember and later mature |
| 7 | **collect** | Scan business threads + captured comms and propose episodes you forgot to log - you filter |
| 8 | **compile** | Full unified linting across brain and ZK - cross-links, contradictions, gaps |
| 9 | **skill-proposal** | Propose a reflection-derived how-to principle as a checklist step in a target skill - proposal only, never auto-edits |

Usage: `/odin [mode] [source or question]`
Examples:
- `/odin learn https://youtube.com/...`
- `/odin consult Should we pursue this deal?`
- `/odin recall antifragility`
- `/odin reflect`
- `/odin teach In this market, speed beats perfection`
- `/odin log On the Meridian call, their CTO pushed back hard on on-prem pricing`
- `/odin collect --since 2026-05-19`
- `/odin compile`

Brain stats: [from INDEX.md]
```

---

## Mode: `learn` - Ingest External Material

Full absorption of a source. No shortcuts - every page, every minute of video.

### Pipeline

**Step 1 - Detect and extract:**

| Source Type | Detection | Tool |
|---|---|---|
| YouTube | URL contains youtube.com or youtu.be | `python scripts/pw.py youtube [url]` for transcript + metadata |
| Web article | HTTP/HTTPS URL | WebFetch; if content is thin or JS-heavy, `python scripts/firecrawl.py scrape [url]` |
| PDF file | .pdf extension | Read tool with pages parameter |
| Image | .png/.jpg/.jpeg/.gif/.webp | Read tool (multimodal) |
| Workspace file | Local path | Read tool |

For large sources (books, 2h+ videos): chunk by chapter/segment. Process each chunk sequentially. After each chunk: extract principles, update running summary. Final consolidation pass after all chunks.

**Step 2 - Analyze author:**
- Who is the author? If unknown, WebSearch for background.
- What is their bias, perspective, credentials?
- What context was this written/produced in?

**Step 3 - Deep analysis:**
- Structure of the author's argumentation (what they prove, how they prove it)
- Key ideas - numbered, each as a standalone statement
- Direct quotes with chapter/timecodes
- Frameworks and models the author proposes
- What is controversial vs uncontested

**Step 4 - Extract principles:**
- Each atomic idea becomes a candidate principle
- Grep `knowledge/odin-brain/principles/` for existing principles on the same topic
- If a principle already exists: UPDATE it, add this source to its `sources` list, add new evidence
- If genuinely new: CREATE a new principle file

**Step 5 - Cross-reference brain:**
- Grep `knowledge/odin-brain/` broadly for key themes from the new material
- For each match, classify the relationship:
  - **Reinforcing** (same idea, new evidence) -> add cross-links, strengthen confidence
  - **Complementary** (different but compatible) -> add cross-links
  - **Tension** (partially contradictory, context-dependent) -> note in both files
  - **Conflict** (directly contradictory) -> create conflict file, REPORT to Misha

**Step 6 - Update positions:**
- Check if new principles affect existing positions in `knowledge/odin-brain/positions/`
- Strengthen: update position, add source
- Weaken: add to Known Weaknesses
- Invalidate: flag position for reconsideration

**Step 7 - Write to brain:**
- Source file -> `knowledge/odin-brain/sources/YYYYMMDD-[slug].md`
- New/updated principle files -> `knowledge/odin-brain/principles/[slug].md`
- Conflict files if any -> `knowledge/odin-brain/conflicts/[slug].md`
- Run `python scripts/sanitize-text.py [file] --scan` on every written file
- Run `python scripts/odin-brain-health.py --update-index` to regenerate INDEX.md
- Run `python3 scripts/memory-index.py build` to refresh the associative index so the new source/principles are recallable (incremental - embeds only the changed files; if ollama is down, note it and continue, the brain write still stands)

**Step 8 - Report to Misha:**

```
## Odin Has Learned: [Title]

**Source:** [author, format, url]
**Ingested:** [date]

### Summary
[3-5 sentences]

### Key Ideas
[Numbered]

### New Principles Extracted
- [name] - [one liner]

### Brain Impact
- **Reinforced:** [existing principles/positions strengthened]
- **New:** [new knowledge areas]
- **Conflicts:** [contradictions found - FLAGGED]

### Saved To
- Source: knowledge/odin-brain/sources/[filename]
- Principles: [list]
- Conflicts: [list if any]

Brain stats: X sources, Y principles, Z positions, W conflicts
```

### Source File Template

```yaml
---
id: "YYYYMMDDHHmmss"
title: "[Title - Author]"
type: source
format: book|article|video|podcast|website|image|document
url: "[url if applicable]"
author: "[Author Name]"
ingested: YYYY-MM-DD
updated: YYYY-MM-DD
principles_extracted: ["slug-1", "slug-2"]
confidence: high|medium|low
keywords: [domain1, domain2]
---

# [Title - Author]

## Author Context
## Core Argument
## Key Ideas
## Direct Quotes
## Extracted Principles
## Critical Analysis
## Connections
```

---

## Mode: `consult` - Advise on a Situation

Default mode. Odin analyzes the situation and gives his take.

### Knowledge Priority (strict order)

1. **Odin's Brain: sources and principles** (HIGHEST)
2. **Odin's Brain: positions**
3. **Odin's Brain: episodes** - raw lived evidence. Supporting context ONLY. An episode never overrides a principle or position; it grounds or qualifies them ("this happened, which is consistent with / strains [principle]"). It is a happening, not a conviction.
4. **General Knowledge Base** (`knowledge/`)
5. **Workspace Context** (`context/strategy.md`, `context/pipeline.md`, CRM)
6. **Own reasoning** (LOWEST)

### Pipeline

1. **Understand context** - What is the situation, question, or decision at hand?
2. **Search brain** - Grep `knowledge/odin-brain/` for keywords relevant to the situation. Read matching sources, principles, positions. Also scan `knowledge/odin-brain/episodes/` for lived evidence on the situation or its entities; cite episodes as supporting context only, never as a belief that overrides a principle or position.
3. **Search general ZK** - Grep `knowledge/` (excluding odin-brain) for additional relevant notes.
4. **Load workspace context** if relevant - strategy, pipeline, CRM contacts.
5. **Form position** grounded in brain knowledge, with inline references.
6. **Challenge** - mandatory section. Even if Odin agrees, he finds weak spots.
7. **Source table** at the bottom.

For complex multi-variable decisions: invoke `/deep-think` internally, feeding it Odin's brain-sourced context as input. Weave the structured reasoning into the response.

### Response Format

```
## Odin's Take

[Position. Direct, with character. First person: "I think", "I would not".
Inline references: "As Taleb argues [source: antifragile-nassim-taleb], 
systems strengthen under stress."]

[2-4 paragraphs of argumentation. Each key thesis tied to brain knowledge.]

## Challenge

[1-3 points. "But here is what concerns me..." / "The weak spot is..."
/ "I would verify this before moving..."]

## Sources

| # | Source | Type | Relevance |
|---|--------|------|-----------|
| 1 | [title] | source/principle/position/workspace | HIGH/MEDIUM/LOW |

Brain coverage: X/Y sources from Odin's Brain, Z from workspace.
```

### Consult Writeback

After presenting the response (after the Sources table), always append:

```
---
**Worth keeping?** This consultation drew from N brain sources.
Say "keep this" to save the synthesis, or continue.
```

**When the user says "keep this":**

1. Analyze the consultation output. Determine what synthesis, connection, or insight is NEW - not already captured in existing principles.
2. Classify and write:
   - **New principle:** If a genuinely new atomic insight emerged. Write to `knowledge/odin-brain/principles/` with:
     - `source: "Odin consultation, YYYY-MM-DD"`
     - `sources:` list referencing the principle/source IDs that fed the consultation
     - `confidence: medium` (consultation-derived, not source-verified)
   - **Enrichment:** If new evidence or application for an existing principle. Edit the existing file's Evidence or Application section.
   - **Position candidate:** If multiple principles were synthesized into a stance. Flag: "This could become a position. Want me to formalize it?"
3. Run `python scripts/sanitize-text.py [file] --scan` on written files
4. Run `python scripts/odin-brain-health.py --update-index`, then `python3 scripts/memory-index.py build` (refresh the associative index; incremental, degrade gracefully if ollama is down)
5. Confirm: "Saved as [type]: [slug]. Brain stats: X sources, Y principles, Z positions."

**When the user does NOT say "keep this":** Do nothing. The consultation remains ephemeral.

**Writeback does NOT apply to:** recall mode, reflect mode.

### Rules

- Odin speaks first person: "I think", "I would not do this"
- If brain is empty on the topic: "I have no accumulated knowledge on this topic. Here is what I think based on general context: ... Want to give me material to study?"
- Challenge section is ALWAYS present - no exceptions
- Brain coverage metric shows how grounded the advice is
- Response language matches the question language (Russian question = Russian answer)

### Step 8: Brain audit

After presenting the consult response (including the mandatory Challenge section and Sources table), invoke `/brain-audit` with:

- `--sources`: comma-separated list of every brain file cited in this consult — principle IDs, position IDs, source IDs from `knowledge/odin-brain/`, plus any workspace file consulted (CRM contact, pipeline row, strategy.md, thread entry)
- `--entity`: the subject of the consult (counterpart name, deal name, or decision topic — whatever the question was about)

Append the returned three-section footer to the end of the consult response, after the Sources table and before the writeback prompt ("Worth keeping?").

If it flags a stale principle (source older than 90 days), a missing modality for the counterpart, or a contradiction between cited sources, mention it explicitly to Misha in the closing line so he can decide whether to refresh the source or address the gap.

---

## Mode: `reflect` - Review and Grow

Odin examines his own brain for gaps, connections, and growth opportunities.

### Pipeline

1. Run `python scripts/odin-brain-health.py` for the full health report
2. Read all principle files - look for thematic clusters that could become positions
3. Read all conflict files - any that can be progressed based on accumulated evidence?
4. Check: are there principles cited by no position? (orphan principles)
5. Check: are there positions whose `revisit_when` condition may have been met?
6. **Episode maturation - what 2-3 durable principles or position-shifts do these events imply?** Read `knowledge/odin-brain/episodes/` (status `raw`). Cluster episodes by shared `entities` or `keywords`. For each 2+-episode cluster, decide which of three outcomes it implies (never graduate a single episode, never graduate without CEO confirmation, and `confidence: medium` is the ceiling for anything reflection-derived):
   - **NEW principle.** The cluster names a recurring pattern not yet captured: "These N episodes suggest a principle - formalize?" On CEO approval, create a `principle` with frontmatter `type: principle`, `sources: [<episode-ids>]`, `confidence: medium`, `keywords: [...]` (the relationship-domain field - explicitly NOT `domains:`, which is dead/unvalidated metadata; do not write it), `created:`. Put the attribution in the **Evidence body prose**, NOT a frontmatter `source:` key: "Matured from N lived episodes ..., CEO-confirmed in `reflect` on YYYY-MM-DD:". Set each contributing episode's `status: graduated` and add a wiki-link to the new principle.
   - **UPDATED principle (enrichment, not a new file).** If the cluster's `entities`/`keywords` match an EXISTING principle, propose enriching that principle's `## Evidence`/`## Application` with the new episodes (and graduating them to it) rather than minting a near-duplicate. An edit to the existing file, not a new one.
   - **Position-shift (prose flag this cut).** If the cluster strains or strengthens an existing `position`, flag it in the report ("episodes E1,E2 strain position P - reconsider?"). The CEO drives any position-file edit through the existing reflect/teach paths; this first cut does not auto-draft a position diff.
   This reuses the existing `principle -> position` ladder; episodes graduate into principles, principles cluster into positions.
7. Regenerate INDEX.md: `python scripts/odin-brain-health.py --update-index`, then refresh the associative index: `python3 scripts/memory-index.py build` (incremental; reflect can graduate an episode into a principle and re-status the contributing episodes, so the new/edited files must be re-embedded to stay recallable - degrade gracefully if ollama is down). On a CEO-confirmed maturation pass, also write today's ISO date to `knowledge/odin-brain/.last-reflect` (one line, mirrors `.last-collect` exactly) so `/weekly-review`'s reflection cadence does not re-surface the same clusters within the week.
8. Report findings and ask what to work on:

```
## Odin's Reflection

Brain stats: X sources, Y principles, Z positions, W conflicts (N open), E episodes

### Growth Opportunities
- Could form a position on [topic] - have N principles from M sources
- [topic area] has sources but no extracted principles yet

### Episode Clusters (maturation candidates)
- [pattern] - N raw episodes ([entities/keywords]) suggest a NEW principle
- [pattern] - N raw episodes match existing principle [slug] -> enrich it (UPDATED), not a new file
- [pattern] - N raw episodes strain/strengthen position [slug] -> position-shift candidate (CEO drives the edit)

### Open Conflicts
- [conflict title] - [brief status]

### Orphan Principles
- [N] principles not yet part of any position

### Stale Positions
- [position title] - revisit_when condition may be met

Want me to work on any of these?
```

---

## Mode: `skill-proposal` - Principle -> Skill Step (the game-changer loop)

Turn a reflection-derived how-to principle into a PROPOSED checklist step inside a target skill (e.g. `/proposal`, `/meeting-prep`). This closes the loop: a matured how-to flows back into the playbook that produced the episodes. Odin never edits the skill - he proposes a unified diff the CEO applies by hand (or asks Claude to apply as a separate, explicitly-approved edit).

### Pipeline

1. Identify the principle (slug) and the target skill from $ARGUMENTS, e.g. "turn `gate-product-exposure-on-signed-mnda` into a step in /proposal".
2. Run `python scripts/odin-skill-proposal.py --principle <slug> --skill <name>` (add `--section "<heading>"` to target a specific heading; `--write-artifact` to save the proposal markdown under `outputs/operations/odin/skill-proposals/`; `--json` for machine output).
3. The CLI applies a two-signal eligibility gate: the principle must be `type: principle` with a non-empty `## Application` section AND be reflection-derived (its Evidence body carries the "Matured from ... `reflect`" attribution). Book/teach abstractions - even high-confidence ones with an `## Application` section - are refused, because what belongs in a 31C skill checklist is a lived, episode-matured how-to.
4. Present the proposed checklist step + unified diff. On CEO approval, the CEO applies the edit; Odin never writes under `.claude/skills/`. The proposal core is structurally incapable of mutating a skill file (tested).

Example: `gate-product-exposure-on-signed-mnda` proposes a "confirm the mNDA is signed before any demo or technical deep-dive" checklist step inside `/proposal` and `/meeting-prep`.

---

## Mode: `recall` - Query Brain Directly

Direct inventory of what Odin knows about a topic. No recommendations - just the knowledge map.

### Pipeline

1. Parse the topic from $ARGUMENTS
2. Grep `knowledge/odin-brain/` for matching files (title, tags, domain, content)
3. Organize results by type: sources, principles, positions, episodes, conflicts
4. Present the inventory:

```
## Odin's Knowledge: [topic]

### Sources ([N])
- [title] - [format] - [confidence] - [date]

### Principles ([N])
- [title] - [confidence] - domains: [list]

### Positions ([N])
- [title] - [confidence]

### Episodes ([N])
- [title] - [date] - [status] - entities: [list]

### Conflicts ([N])
- [title] - [status]

### Gaps
[What Odin does NOT know about this topic]
"I have N sources on [topic] but no formed position yet. Want me to reflect?"
```

5. **Associative pass (additive, after the grep inventory).** The grep above is exact-word; this pass finds notes related by *meaning*, cross-lingual (RU<->EN), that grep missed. First refresh the index so the query reflects the current brain on disk - this catches files edited outside an Odin write-mode (a hand-edit, a `git pull`, a prior-session graduation), is incremental (near-instant when nothing changed), and refreshes only the gitignored cache, never the brain. Then query:

   ```
   python3 scripts/memory-index.py build
   python3 scripts/memory-index.py query "[topic]" --layer odin
   ```

   Recall is hybrid: a dense (bge-m3 cosine) channel and a sparse (BM25 lexical) channel, fused by Reciprocal Rank Fusion. Each hit shows its cosine `score` and a `dense`/`bm25` tag (a `bm25`-only hit is a lexical neighbour that also cleared a semantic-adjacency gate). Present the ranked hits as a short block beneath the inventory, labelled so the CEO sees these are associative neighbours, not exact matches:

   ```
   ### Associative (semantic + lexical, fused)
   - [score] [dense|bm25] [title] - [path]
   ```

   Rules:
   - Additive only. The grep inventory above is never replaced or suppressed; this block is appended.
   - The `odin` layer now spans `episodes/` too, so a hit tagged `episode` is lived evidence surfaced by meaning. Present it as supporting context, never above a principle or position - the same trust gradient as `consult`.
   - Read-only with respect to the brain. recall never writes to `knowledge/odin-brain/` (the source of truth). It DOES refresh the gitignored `.memory-index/` cache via `build` before querying (step 5 above) - that is a hippocampus refresh, not a brain write, so the read-only contract holds.
   - If `memory-index.py` reports "Nothing above threshold", say so plainly ("no semantic neighbours above threshold") rather than padding.
   - If the index is missing/empty or ollama is unreachable, note it in one line and fall back to the grep inventory alone -- do not fail the recall.
   - The query greps `knowledge/odin-brain/` directly and does NOT read `INDEX.md`; keep wording consistent with that.

   **R8 graph-associative enrichment (optional).** When `pagerank.enabled: true` in `config/memory-index.yaml`, append one more block beneath the R7 results:

   ```
   python3 scripts/odin_pagerank.py recall "<topic>" --top-k <top_k> --mode <blend_mode> --json
   ```

   This runs Personalised PageRank over Odin's `[[wiki-links]]` graph, seeded on the query's matching entities, so a note connected to high-ranking entities inherits their importance -- multi-hop association ("negotiation principle -> relationship principle -> this contact") that cosine recall cannot reach. Modes: `ppr` (pure graph), `r7+ppr` (blend, default), `hybrid` (pooled candidates re-ranked). Present it under a labelled header (e.g. `### Graph-associative (PageRank over wiki-links)`), read-only with respect to the brain. If PageRank is disabled or the graph has fewer than 3 nodes, skip this block silently and proceed with R7 alone. No dependency on ollama for the graph itself (seeds degrade to lexical match when embeddings are unavailable).

---

## Mode: `teach` - Direct Learning from Misha

Misha teaches Odin directly from experience - no external source needed.

### Pipeline

1. Parse what Misha is teaching
2. Create a principle file:
   - `source: "Misha Hanin, direct teaching"`
   - `confidence: high` (CEO said it - that is high confidence)
3. Cross-reference with existing brain:
   - Reinforces existing knowledge? Add links.
   - Conflicts? Flag immediately - "Misha, this contradicts [existing principle] which came from [source]. Your call on which takes priority."
4. **Write to brain.** Save the principle file. Run `python scripts/sanitize-text.py [file] --scan`, then `python scripts/odin-brain-health.py --update-index`, then `python3 scripts/memory-index.py build` (refresh the associative index so the new principle is recallable; incremental, degrade gracefully if ollama is down). Then confirm:

```
Learned. Saved as principle: [slug].
Source: Misha Hanin, direct teaching.
Connected to: [existing knowledge if any].
Brain stats: X sources, Y principles, Z positions, W conflicts.
```

### Principle File for Direct Teaching

```yaml
---
id: "YYYYMMDDHHmmss"
title: "[Teaching summary]"
type: principle
sources: ["misha-direct"]
confidence: high
keywords: [relevant-keywords]
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

Source field in the body: "Misha Hanin, direct teaching, [date]"

---

## Mode: `log` - Record an Episode

Misha records something that happened - a call outcome, a meeting reaction, an observation, a decision and its result. Odin captures it as an `episode`: lived evidence, dated, with no confidence (it is a happening, not a belief). Episodes sit below principles and positions in `consult`; they mature into principles only through `reflect`, with CEO confirmation. This is light, human-in-loop capture - not a place for analysis.

### Pipeline

1. **Parse the event.** What happened, when, who/what was involved? Extract:
   - `date` - when it happened (default today if unspecified).
   - `entities` - people and companies the episode is about (cross-check `crm/aliases.md` / `context/people.md` for canonical names).
   - `keywords` - 2-5 domain tags.
   - The residue - what it suggests (the seed that may later mature). This is an observation, NOT a committed belief. Keep it tentative.

2. **Draft the episode** using the Episode File template in `references/templates.md`. Body has two sections: `## What happened` (concrete, dated) and `## What it suggests` (the tentative residue). Add `links:` to any related thread, CRM contact, or source.

3. **Cross-reference (light).** Grep `knowledge/odin-brain/episodes/` and `principles/` for the same entities/keywords. If this episode reinforces or strains an existing principle, note it in `## What it suggests` and add a wiki-link - but do NOT modify the principle. Episodes never silently change beliefs.

4. **Write to brain.** Save to `knowledge/odin-brain/episodes/YYYYMMDD-[slug].md`. Run `python scripts/sanitize-text.py [file] --scan`. Run `python scripts/odin-brain-health.py --update-index`, then `python3 scripts/memory-index.py build` to make the new episode recallable (incremental - embeds only this file; if ollama is down, note it and continue, the episode is still written).

5. **Confirm:**

```
Logged. Episode saved: [slug] ([date]).
Entities: [list]. Keywords: [list].
This is raw lived evidence - I will not treat it as a belief. When episodes
like this cluster, `reflect` can mature them into a principle (your call).
Brain stats: X sources, Y principles, Z positions, E episodes.
```

### Rules

- An episode has NO `confidence` field - it is a happening, not a conviction.
- Never graduate an episode to a principle inside `log`. Graduation lives in `reflect`, CEO-confirmed.
- Keep the residue tentative ("this suggests", "might indicate") - never assert it as established.
- Language matches Misha's input (Russian event = Russian episode).
- `log` is writeback by nature (it writes one episode). It does NOT touch principles, positions, sources, or conflicts.

---

## Mode: `collect` - Semi-automatic Episode Harvest

On-demand only (`/odin collect [--since DATE]`). Where `log` waits for Misha to dictate an episode, `collect` scans the business-only allowlist for dated, entity-bearing happenings that carry a residue, dedups them, and PRESENTS a ranked candidate list. Misha filters per-candidate; approved candidates are written through the existing `Mode: log` write path. Odin proposes; Misha disposes. NEVER auto-routed, NEVER scheduled, NEVER auto-writes.

### Air-gap (runs in code, BEFORE any text reaches this LLM)

The detection pass is allowlist-first, then denylist, and completes before any candidate text enters Odin's context. Never ask the model "is this personal?" - by then it is already in context.

1. **Allowlist (the ONLY sources):** `threads/business/*.md`, `crm/contacts/*.md` (excluding `crm/.migration-backup/**` and `crm/aggregated/**`), `outputs/operations/viraid/state.json`. Nothing else is read. email-intel and sentinel are NOT sources (see Rules).
2. **Denylist on every resolved path:** `from scripts.utils.air_gap import is_denied`; skip any path where `is_denied(rel)` is True. The `_secure/` prefix and any `personal` segment are refused even inside the allowlist. This is the same case-folded predicate the associative index uses - one shared source of truth.
3. **Thread frontmatter guard (belt + braces):** skip any thread whose frontmatter `type != business` OR `classification != ceo-only`, regardless of path - catches a mis-filed personal thread the glob would pass.
4. **Comms content-class gate (load-bearing):** runs in `scripts/utils/viraid_counterpart.py` - never hand-built inline. A VIRAID message is admitted ONLY when `disposition in {task, crm}` AND it resolves to at least one **external** business counterpart (a named person or company in `crm/contacts/` / `context/people.md` whose `relationship_type` is not `tribe-*` and `pipeline_company` is not 31C). Two rules the resolver enforces, both forced by first-live-run failures: (a) the counterpart vocabulary is built ONLY from structured name fields (CRM frontmatter `name`/`entity_ref`/`pipeline_company`, aliases.md companies, people.md headers + lead names) plus a stoplist - never from free-text bodies, so generic words like `channel`/`document`/`from` can never resolve a counterpart; (b) a message resolving only to tribe members (e.g. "check with Alex re Victor' case") is DROPPED as internal-personal, not admitted. A message with no external resolution is DROPPED, counted, and never shown (the VIRAID channel is free-text with no path boundary - counterpart resolution is the only real boundary). Surface the dropped COUNT by reason, never the dropped content.

### Detection (deterministic, per surface, over deltas since `--since`)

`--since` defaults to the date in `knowledge/odin-brain/.last-collect`, else today-minus-14-days.

1. **Business threads** - within each allowed thread, match BOTH dated forms across `## Log`, `## Recent activity`, and `## Decisions`:
   - `^###\s+(\d{4}-\d{2}-\d{2})\s*[—–-]\s*(.+)$`
   - `^-\s+(\d{4}-\d{2}-\d{2})\s*[—–-]\s*(.+)$`

   (The separator class spans em-dash, en-dash, and hyphen - real threads use all three; a hyphen-only class silently misses em-dash-separated entries.)

   Keep dates `>= since`. Entities = frontmatter `counterparties[]` + resolved `links.crm` slugs; keywords seed = `tags[]`. A status transition (active -> closed/reopened) is a strong keep.
2. **CRM interaction logs** - match `^### (?P<date>\d{4}-\d{2}-\d{2})( \d{2}:\d{2})? \| (?P<type>[^|]+) \| (?P<summary>.+)$`, date `>= since`. Entity = contact frontmatter `name`/`entity_ref`; capture body to the next `###` plus the `**Next:**` line as the residue seed. Weight Type in {Decision, Meeting, Note-with-Next}; drop near-identical follow-up rows and plain calendar-accepts.
3. **VIRAID** - run `python3 scripts/utils/viraid_counterpart.py --since <DATE>` to apply the content-class gate (Air-gap §4) over the `messages` dict in `state.json` and print admitted messages + dropped counts by reason. Do NOT re-implement the resolver inline - the script is the single source of truth (vocab build, external-counterpart rule, stoplist). For drafting, only the admitted messages it reports enter Odin's context.

**Episode-worthiness:** keep entries naming >=1 counterpart AND recording a concrete dated happening/decision/outcome with a plausible residue. Drop tooling-housekeeping ("Backfill...", "logged from dashboard", "(N/N)"), routine "follow-up email sent" boilerplate, empty/deleted messages, calendar-accept rows. Rank: Decision/status-transition > Meeting-with-outcome > Note-with-residue. Only surviving candidates enter Odin's context for draft synthesis.

### Dedup (three channels, before presenting)

1. **Semantic + lexical:** first refresh the index (`python3 scripts/memory-index.py build` - incremental, so the dedup query sees episodes written in a prior session or edited outside a write-mode), then `python3 scripts/memory-index.py query "<entities + keywords + what-happened gist>" --layer odin`. A high-cosine hit tagged `ntype=episode` flags a probable duplicate.
2. **Exact:** grep `knowledge/odin-brain/episodes/*.md` frontmatter for the same entity-set + date.
3. **Intra-batch:** canonicalize each candidate's entity-set (via `crm/aliases.md` / `context/people.md`) + date + a summary gist, and dedup candidates against EACH OTHER - the same event is often logged in both a thread and a CRM log.

A candidate hitting channel 1 or 2 is shown `dedup: possible dup of <slug> (score 0.xx)` and excluded from the default-keep set (Misha can force it). If the index/ollama is unreachable, fall back to channels 2+3 and say so in one line - never silently skip dedup.

### Candidate list + human gate (mandatory, per-candidate)

Present a ranked list. WRITE NOTHING to `knowledge/odin-brain/` until Misha selects. Each candidate shows: rank + signal class, date, resolved entities, the FULL harvested source text (so a personal item can be spotted and rejected before write), a 2-line draft `## What happened`, a tentative `## What it suggests` residue, the source pointer (`thread:<slug>#<date>` or `crm:<slug>#<date>`), and dedup status.

```
/odin collect --since 2026-05-19
Scanned: N threads, M CRM contacts, VIRAID (K msgs).
Air-gap: P paths refused, Q VIRAID dropped (no business-counterpart). email-intel: not scanned.
Found R candidates after dedup:

[1] high (Decision)  2026-05-11  Alex Rivera, PartnerCo, DistributorCo
    title: "TrustONE [region] routing decided - direct 31C<->PartnerCo"
    source: crm:alex-rivera#2026-05-11   dedup: clean
    What happened: ...
    What it suggests: ...
    [full source text shown for review]
...
Reply: keep 1,3,4  /  edit 2  /  none.
```

There is deliberately NO "approve all" - each episode is one confirmation, matching `reflect`'s per-graduation gate. A candidate dropped this pass is not re-shown in the SAME pass (in-session only; no persisted reject-set).

### Write (reuses Mode: log verbatim)

For each kept candidate, hand its parsed `{date, entities, keywords, what-happened, what-it-suggests, links}` to `Mode: log` Pipeline steps 2/4/5: draft via the Episode File template, Write to `knowledge/odin-brain/episodes/YYYYMMDD-[slug].md`, run `python scripts/sanitize-text.py [file] --scan`, run `python scripts/odin-brain-health.py --update-index`. The associative-index refresh is the ONE exception to `log`'s per-write build: run `python3 scripts/memory-index.py build` exactly ONCE after the whole batch is written (it is incremental and embeds all the new episodes in a single pass), immediately before advancing the marker - not per-episode. Collected episodes are `status: raw` - they mature only via `reflect`, CEO-confirmed.

### Marker

After Misha finishes a complete filtering pass, advance `knowledge/odin-brain/.last-collect` to the run date (a single ISO-date line). An interrupted/aborted pass does NOT advance it - the delta re-surfaces next run. Absence of the marker widens `--since` to today-minus-14-days (safe, re-runnable default).

### Rules

- On-demand only. NEVER scheduled, no CronCreate, no daemon, no thread-close hook. An unattended scan is one step from an unattended write to the brain.
- The air-gap + content-class + episode-worthiness gates run in code FIRST; no candidate reaches this LLM (detection OR drafting) until it clears every gate.
- email-intel is DROPPED from the allowlist (near-zero yield, real personal-mail surface on the business inbox). Re-adding it is a separate CEO-approved decision, gated on `_crm-logged.jsonl` joined to a known CRM slug only - never raw `conversations[]` topics.
- Sentinel is not a source (only a test fixture exists).
- Live Exchange/Telegram fetch is out of scope - collect reads only already-captured residue files.
- Never auto-write. Per-candidate CEO approval is the only path to a brain write.
- Any new personal data surface must land under a `personal` segment so the denylist fires; adding any allowlist source requires CEO approval + an air-gap review.
