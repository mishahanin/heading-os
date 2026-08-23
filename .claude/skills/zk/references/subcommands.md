# /zk - subcommand procedures

Consumed by: `.claude/skills/zk/SKILL.md`, section "Subcommand dispatch". Holds
the full step-by-step procedure for each of the eight subcommands, plus the note
file template used by `add` and `distill`. Read the section for the subcommand
you are about to run, before you run it.

Last Updated: 2026-08-20

---

### `add [type]`

Create a new note in the personal knowledge directory.

1. Parse remaining arguments for note type. Valid types: `fleeting` (default), `signal`, `decision`, `meeting`, `research`, `strategy`, `people`, `technology`
2. If the user provided content inline, use it. Otherwise ask: "What's the idea?" (one question only)
3. Generate the note ID: current timestamp as `YYYYMMDDHHMMSS`. **The ID must be
   unique across the knowledge root.** A second-resolution stamp is not unique on
   its own: `distill` creates three to seven notes in one batch, inside the same
   second, and `connect` and `garden` address notes by `[[ID|Title]]`, so a
   repeated ID silently points two links at one note. Before writing, glob
   `{knowledge_root}**/{ID}-*.md`; if anything matches, add one second and check
   again until the ID is free.
4. Generate slug from the core idea (kebab-case, max 6 words)
5. Determine the knowledge root based on workspace type:
   - **CEO workspace:** `knowledge/`
   - **Exec workspace:** `personal/knowledge/`
6. Determine the target subdirectory from type (unified brain architecture).
   These are RELATIVE to the knowledge root chosen in step 5, because step 8
   prepends that root. Do not repeat it here, or the path doubles into
   `knowledge/knowledge/odin-brain/...`, which nothing else in this file ever
   scans:
   - fleeting -> `odin-brain/sources/` (with `format: fleeting`, `status: seed`)
   - signal -> `odin-brain/sources/` (with `format: signal`)
   - decision -> `odin-brain/positions/`
   - meeting -> `odin-brain/sources/` (with `format: meeting`)
   - research -> `odin-brain/sources/` (with `format: research`)
   - strategy -> `odin-brain/principles/` or `odin-brain/positions/` (ask user)
   - people -> Redirect: "People intel belongs in CRM. Use `/crm add`."
   - technology -> `odin-brain/reference/`
7. **Classification:** Ask "CEO-only or Corporate-wide?" (default: CEO-only; suggest Corporate for notes tagged `#propose-shared`). If classified as corporate, add a `corporate` rule for the file path to `config/routing-map.yaml`. Note that the next `/push-updates` should promote it to `knowledge/shared/` on THIS workspace. That is the publish-side path; execs read the published copy at `corporate/knowledge/shared/`, which is why `find` and `garden` name the other one. The two are the two ends of one pipeline, not a contradiction.
8. Create the note file `{knowledge_root}{subdir}/{ID}-{slug}.md`.

   **The frontmatter depends on where the note lands.** `scripts/odin-brain-health.py`
   holds a DIFFERENT required-field list per brain directory, and a note missing
   one of them is reported as a schema violation on every `/zk stats` and every
   `/zk garden` run. Take the base template below, then add the extra fields for
   the destination:

   <!-- zk-required-extras:start -->
   | Destination | Brain kind | Extra frontmatter fields, on top of the base |
   |---|---|---|
   | `odin-brain/sources/` | source | `format`, `author`, `ingested` |
   | `odin-brain/principles/` | principle | `sources` |
   | `odin-brain/positions/` | position | `principles`, `sources`, `revisit_when` |
   | `odin-brain/episodes/` | episode | `date` |
   | `odin-brain/conflicts/` | conflict | `side_a`, `side_b` |
   | `odin-brain/reference/` | reference | none |
   <!-- zk-required-extras:end -->

   This is where step 6's `format:` annotation goes: it is a real required field
   for a `sources/` note, not a decoration. `tests/test_zk_template_matches_the_brain_schema.py`
   fails if this table and `REQUIRED_FIELDS` ever drift apart.

   A `conflicts/` note needs `status` too, but the base template already writes
   it, so change its VALUE (`open` / `resolved`) rather than adding the key a
   second time. A duplicated YAML key is a silent overwrite, not an error.

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
8. Confirm with: "Note created: `{path}`. Status: seed." followed by the
   confirmation line from `.claude/rules/hidden-chars.md`, carrying what the
   scan actually reported.

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
4. Search all knowledge directories for related notes sharing 2+ keywords. That means personal, plus corporate shared on exec workspaces.
5. Add `[[ID|Title]]` wiki-links under Connections.
5. Search workspace files (`context/`, `reference/`, `crm/contacts/`) for relevant cross-references. Add backtick paths under Connections.
6. Update frontmatter:
   - `status`: seed -> growing (or growing -> evergreen if sufficiently enriched)
   - `updated`: today's date
   - `confidence`: adjust if research confirms or weakens the idea
7. Run `python3 scripts/sanitize-text.py` on the file
8. Present changes summary: "Enriched: {title}. Status: {old} -> {new}. Added {N} connections."
   plus the confirmation line from `.claude/rules/hidden-chars.md`.

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
3. Search the knowledge root for notes sharing 2+ keywords or containing related
   terms. Step 1 says "same matching as `enrich`", and `enrich` searches personal
   PLUS corporate shared on an exec workspace, so do the same here: `knowledge/`
   on the CEO workspace, `personal/knowledge/` and `corporate/knowledge/shared/`
   on an exec one. The literal `knowledge/` that used to stand here missed both
   on every exec workspace.
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
11. Report: "Distilled {N} notes from {source}." plus the confirmation line from
    `.claude/rules/hidden-chars.md`.

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
2. Read and display the updated `INDEX.md` from the knowledge root: `knowledge/`
   on the CEO workspace, `personal/knowledge/` on an exec one. Reading the
   literal `knowledge/INDEX.md` on an exec workspace shows a file step 1 did not
   regenerate, or nothing at all.
3. Highlight any health concerns (stale seeds, orphans, schema issues)

---

### `brief [topic]`

Synthesize all notes related to a topic into a narrative summary. Searches both personal and corporate shared knowledge.

1. Parse the topic from arguments
2. Search all knowledge directories for notes matching the topic by keyword, title, or content. On exec workspaces that means personal and corporate shared. ALSO search `odin-brain/principles/` and `odin-brain/positions/` UNDER THE KNOWLEDGE ROOT (`knowledge/` on the CEO workspace, `personal/knowledge/` on an exec one) for files matching the topic. Match on content: the note template in `add` step 8 defines no `domain` field, so nothing this skill creates has one, and a `domain`-only match can never hit.
3. Read each matching note
4. Synthesize into a narrative summary structured as:
   - **What we know** - confirmed insights (evergreen + high confidence). Include matching Odin principles/positions tagged with `[Odin]`.
   - **What we think** - working hypotheses (growing + medium confidence)
   - **What we're watching** - early signals (seeds + low/unverified confidence)
   - **Open questions** - aggregated from all matching notes
   - **Sources** - list of all brain files contributing to this brief
5. Present the brief inline (do not create a file unless asked)

---
