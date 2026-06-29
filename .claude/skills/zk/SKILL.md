---
name: zk
description: >
  Zettelkasten knowledge base - add, enrich, find, connect, distill, garden,
  stats, and brief across the knowledge/ second brain shared with Zettlr.
  Use when the user says "zk", "add a note", "knowledge base", "distill this",
  "garden", "what do we know about", "connect this to", or asks to capture
  an idea, insight, signal, or decision. Also use when processing outputs
  from other skills into durable knowledge.
argument-hint: "[add|enrich|find|connect|distill|garden|stats|brief] [target]"
allowed-tools: "Read, Write, Edit, Glob, Grep, Bash(python3:*), WebSearch, WebFetch"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: partial
  shared_state:
    - knowledge/
    - knowledge/INDEX.md
  triggers:
    - zk
    - add a note
    - knowledge base
    - distill
    - garden
    - what do we know about
x-31c-capability:
  what: >
    Zettelkasten second-brain manager over knowledge/ - add atomic notes, enrich, find, connect, distill skill outputs into durable notes, garden for orphans/stale seeds, stats, and topic briefs.
  how: >
    Run /zk [add|enrich|find|connect|distill|garden|stats|brief] [target]. On the CEO workspace /zk is dormant - durable CEO capture instead flows to /odin log, /thread, and the auto-memory system.
  when: >
    Primary capture tool for the executive fleet. On the CEO workspace prefer /odin for learned knowledge, /thread for situations, auto-memory for cross-session facts.
---
# Zettelkasten Knowledge Base

Manage the `knowledge/` second brain shared between Claude Code and Zettlr. Atomic notes, wiki-links, status lifecycle, and anti-dumping discipline.

## Variables

- `$ARGUMENTS` - Subcommand and parameters. Format: `[action] [target]`
- Actions: `add`, `enrich`, `find`, `connect`, `distill`, `garden`, `stats`, `brief`

## Context Loading

First, read `.workspace-identity.json` to determine workspace type. Paths depend on workspace type:

**CEO workspace** (flat paths):
- `knowledge/odin-brain/INDEX.md` - current brain stats and note inventory
- `context/strategy.md` - for strategic relevance assessment
- `context/pipeline.md` - for deal/prospect context
- `context/current-data.md` - for timeline and milestone context

**Exec workspace** (tiered paths):
- `personal/knowledge/INDEX.md` - personal note inventory
- `corporate/knowledge/shared/INDEX.md` - corporate shared knowledge (if exists)
- `corporate/context/strategy.md` - for strategic relevance assessment
- `corporate/context/pipeline.md` - for deal/prospect context
- `corporate/context/current-data.md` - for timeline and milestone context

## Subcommand Dispatch

Parse the first word of `$ARGUMENTS` to determine the action. If no subcommand is given, default to `stats`.

---

### `add [type]`

Create a new note in the personal knowledge directory.

1. Parse remaining arguments for note type. Valid types: `fleeting` (default), `signal`, `decision`, `meeting`, `research`, `strategy`, `people`, `technology`
2. If the user provided content inline, use it. Otherwise ask: "What's the idea?" (one question only)
3. Generate the note ID: current timestamp as `YYYYMMDDHHMMSS`
4. Generate slug from the core idea (kebab-case, max 6 words)
5. Determine the knowledge root based on workspace type:
   - **CEO workspace:** `knowledge/`
   - **Exec workspace:** `personal/knowledge/`
6. Determine the target subdirectory from type (unified brain architecture):
   - fleeting -> `{knowledge_root}odin-brain/sources/` (with `format: fleeting`, `status: seed`)
   - signal -> `{knowledge_root}odin-brain/sources/` (with `format: signal`)
   - decision -> `{knowledge_root}odin-brain/positions/`
   - meeting -> `{knowledge_root}odin-brain/sources/` (with `format: meeting`)
   - research -> `{knowledge_root}odin-brain/sources/` (with `format: research`)
   - strategy -> `{knowledge_root}odin-brain/principles/` or `positions/` (ask user)
   - people -> Redirect: "People intel belongs in CRM. Use `/crm add`."
   - technology -> `{knowledge_root}odin-brain/reference/`
7. **Classification:** Ask "CEO-only or Corporate-wide?" (default: CEO-only; suggest Corporate for notes tagged `#propose-shared`). If classified as corporate, add a `corporate` rule for the file path to `config/routing-map.yaml` and note it should be promoted to `knowledge/shared/` during the next `/push-updates`.
8. Create the note file `{knowledge_root}{subdir}/{ID}-{slug}.md` with this template:

> **Shared knowledge:** Execs can propose notes for corporate shared knowledge by tagging with `#propose-shared` in keywords. These are reviewed during `/publish-corporate`.

```markdown
---
id: "{ID}"
title: "{Title}"
type: {type}
keywords: [{keywords}]
status: seed
created: {YYYY-MM-DD}
updated: {YYYY-MM-DD}
source: ""
confidence: medium
---

# {Title}

{Core idea - one paragraph max. Atomic: one idea per note.}

## Context

{Why this matters. What prompted it. Connection to current state.}

## Connections

{Links to related notes and workspace files - add as discovered.}

## Open Questions

- {What this note raises but doesn't answer}

---
*Origin: manual*
```

7. Run `python3 scripts/sanitize-text.py` on the created file to validate
8. Confirm with: "Note created: `{path}`. Status: seed. Hidden characters: clean."

---

### `enrich [note]`

Read a seed or growing note, research it, add connections, and upgrade its status.

1. Find the target note:
   - If a path is given, read it directly
   - If a keyword/title is given, search all knowledge directories with Grep (personal AND corporate shared for exec workspaces)
   - If multiple matches, list them and ask which one
2. Read the note content and frontmatter
3. Based on note type, perform enrichment:
   - **signal/research/technology**: Run 2-3 targeted WebSearch queries related to the note's core idea. Add findings under Context.
   - **people**: Check `crm/contacts/` for matching contact files. Cross-reference with `context/people.md`. Add relationship context.
   - **strategy/decision**: Read `context/strategy.md` and `context/pipeline.md`. Add strategic alignment notes.
   - **meeting**: Check for related CRM interactions. Add follow-up context.
   - **fleeting**: Determine if it should be reclassified to a more specific type. Suggest reclassification.
4. Search all knowledge directories (personal AND corporate shared for exec workspaces) for related notes sharing 2+ keywords. Add `[[ID|Title]]` wiki-links under Connections.
5. Search workspace files (`context/`, `reference/`, `crm/contacts/`) for relevant cross-references. Add backtick paths under Connections.
6. Update frontmatter:
   - `status`: seed -> growing (or growing -> evergreen if sufficiently enriched)
   - `updated`: today's date
   - `confidence`: adjust if research confirms or weakens the idea
7. Run `python3 scripts/sanitize-text.py` on the file
8. Present changes summary: "Enriched: {title}. Status: {old} -> {new}. Added {N} connections. Hidden characters: clean."

---

### `find [query]`

Search the knowledge base by keyword, tag, type, or content. Searches BOTH personal and corporate shared knowledge.

1. Parse the query from arguments
2. Search using Grep across all knowledge directories:
   - **CEO workspace:** `knowledge/`
   - **Exec workspace:** BOTH `personal/knowledge/` AND `corporate/knowledge/shared/` (if exists)
3. Match against:
   - Title (frontmatter)
   - Keywords (frontmatter)
   - Type (frontmatter)
   - Body text content
4. Present results as a compact list (indicate source tier -- personal or corporate shared):
   ```
   | Title | Type | Status | Keywords | Path |
   ```
4. If no results, say so and suggest `/zk add` to capture the idea

---

### `connect [note]`

Analyze a note and suggest links to other notes and workspace files.

1. Find and read the target note (same matching as `enrich`)
2. Extract the note's keywords and core idea
3. Search `knowledge/` for notes sharing 2+ keywords or containing related terms
4. Search workspace files for relevant cross-references:
   - `crm/contacts/` for people mentions
   - `context/pipeline.md` for deal references
   - `context/strategy.md` for strategic themes
   - `datastore/intelligence/` for competitive intel
5. Present suggested connections as a numbered list
6. Ask which connections to add
7. Edit the note's Connections section with approved links
8. For each connected note, add a reciprocal link back if not already present
9. Update `updated` date in frontmatter

---

### `distill [source]`

Extract atomic insights from an output file into knowledge notes. This is the bridge from skill outputs to durable knowledge.

1. Read the source file (e.g., `outputs/intel/osint/.../brief.md`, `outputs/thinking/...`, `outputs/content/...`)
2. Identify 3-7 atomic insights worth preserving. Each insight must be:
   - A single, self-contained idea
   - Worth revisiting in 6+ months
   - Not purely operational (that belongs in context/ or memory/)
3. For each insight, determine:
   - Best type (signal, research, strategy, technology, decision, people)
   - Relevant keywords
   - Confidence level based on source quality
4. Present the proposed notes as a numbered list with titles and types
5. Wait for Misha's approval (he may modify, add, or remove)
6. Create approved notes using the `add` template
7. Link the new notes to each other where relevant
8. Link back to the source file in each note's Context section
9. Set origin footer to: `*Origin: skill-output ({skill name})*`
10. Run `python3 scripts/sanitize-text.py` on each created file
11. Report: "Distilled {N} notes from {source}. Hidden characters: clean."

---

### `garden`

Maintenance pass - find orphans, stale seeds, broken links, and suggest connections. For exec workspaces, report stats for personal and corporate shared knowledge separately.

1. Run `python3 scripts/odin-brain-health.py` to get the health report

> **Note:** For full brain linting (contradictions, position candidates, gap analysis), use `/odin compile`.

2. For exec workspaces, also scan `corporate/knowledge/shared/` separately and report as "Corporate Shared" tier
3. Report findings organized by urgency (indicate which tier each finding belongs to):

   **Stale Seeds** (status=seed, created > 7 days ago):
   - List each with title, age, and path
   - For each, suggest: enrich, reclassify, or archive

   **Orphan Notes** (no incoming or outgoing links):
   - List each with title and path
   - For each, search for potential connections and suggest links

   **Broken Links** (wiki-links pointing to non-existent IDs):
   - List each broken link with the note it appears in

   **Connection Opportunities** (notes sharing 2+ keywords but not linked):
   - List pairs with shared keywords

3. Ask which actions to take
4. Execute approved actions (enrich, connect, archive, delete)
5. Run `python3 scripts/odin-brain-health.py --update-index` to regenerate INDEX.md

---

### `stats`

Regenerate INDEX.md with current knowledge base statistics.

1. Run `python3 scripts/odin-brain-health.py --update-index`
2. Read and display the updated `knowledge/INDEX.md`
3. Highlight any health concerns (stale seeds, orphans, schema issues)

---

### `brief [topic]`

Synthesize all notes related to a topic into a narrative summary. Searches both personal and corporate shared knowledge.

1. Parse the topic from arguments
2. Search all knowledge directories (personal AND corporate shared for exec workspaces) for all notes matching the topic (by keyword, title, or content). ALSO search `knowledge/odin-brain/principles/` and `knowledge/odin-brain/positions/` for files matching the topic by `domain` field or content.
3. Read each matching note
4. Synthesize into a narrative summary structured as:
   - **What we know** - confirmed insights (evergreen + high confidence). Include matching Odin principles/positions tagged with `[Odin]`.
   - **What we think** - working hypotheses (growing + medium confidence)
   - **What we're watching** - early signals (seeds + low/unverified confidence)
   - **Open questions** - aggregated from all matching notes
   - **Sources** - list of all brain files contributing to this brief
5. Present the brief inline (do not create a file unless asked)

---

## Output Conventions

- **File naming**: `{YYYYMMDDHHMMSS}-{slug}.md` - timestamp ID + human-readable slug
- **Subdirectory mapping**: type determines subdirectory (see `add` command)
- **Wiki-links**: `[[ID|Label]]` format for Zettlr graph compatibility
- **Cross-references**: backtick paths for workspace files (e.g., `crm/contacts/victor-stein.md`)
- **Validation**: Run `python3 scripts/sanitize-text.py` on every generated note. Report: "Hidden characters: clean."
- **Hyphens only** (-) never em-dashes

## Rules

1. **Atomic discipline**: One idea per note. If a note grows beyond ~4 paragraphs, split it into multiple notes and link them.
2. **Status lifecycle**: seed -> growing -> evergreen -> archived. Never skip stages. Gardening surfaces stuck notes.
3. **Anti-dumping**: The knowledge base is not a filing cabinet. Every note must have a clear reason to exist and be worth revisiting. If it's purely operational, it belongs in `context/` or `memory/`.
4. **Keyword consistency**: Before adding a new keyword, check existing keywords in the knowledge base. Prefer existing terms over synonyms to maintain a coherent tag cloud.
5. **Bidirectional linking**: When adding a link from note A to note B, also add a reciprocal link from B to A.
6. **Source attribution**: Always fill the `source` field. Use URLs, meeting names, "observation", or skill names (e.g., "/osint", "/ceo-intel").
7. **Confidence honesty**: Set confidence based on source quality, not conviction. Unverified signals stay `unverified` until corroborated.
8. **Zettlr compatibility**: Use `keywords` (not `tags`) in frontmatter. Use `[[ID|Label]]` wiki-links. These are Zettlr conventions.
