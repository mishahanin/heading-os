---
name: brain-audit
description: >
  Post-synthesis audit for any skill that produces a synthesized answer over
  a source set. Returns a stable three-section footer reporting newest-source
  dates per cited file, comms/intel modalities not found for a named entity,
  and disagreements between cited sources. Composed by /meeting-prep,
  /odin (consult), /deal-strategy; future synthesis skills adopt it with one
  line. No daemon, no persistence, no workspace scan.
argument-hint: "--sources <paths> [--entity <name>] [--modalities <list>]"
allowed-tools: "Read, Glob, Grep, Bash(git log:*), Bash(python:*)"
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
    Runs three checks over a set of cited source files and returns a stable
    "## Brain audit" footer: newest modified date per source (flagging anything
    over 90 days stale), comms/intel modality coverage for a named entity, and
    disagreements detected between the sources.
  how: >
    Not a user slash command. Composed by /meeting-prep, /odin (consult), and
    /deal-strategy at the end of their synthesis via
    /brain-audit --sources <paths> [--entity <name>]; the caller pastes the
    returned footer beneath its output. The CEO may also invoke it directly for
    an ad-hoc audit.
  when: >
    Use whenever a skill synthesizes an answer over multiple sources and needs a
    freshness, coverage, and contradiction check. It is a leaf primitive - it
    never synthesizes or writes; the composing skill does that.
---

# /brain-audit - post-synthesis audit primitive

A small composable skill. Takes a list of source file paths and an optional entity name. Runs three checks against the sources. Returns a stable markdown footer that the composing skill pastes beneath its synthesis.

The skill does not synthesize, does not query a workspace-wide index, does not write state. It reads the files passed in, scans canonical workspace locations for the named entity, runs one LLM call to detect source disagreements, and emits a three-section footer.

CEO may also invoke directly for ad-hoc audits.

## Phase 1: Argument parsing and sanity checks

### 1.1 Parse arguments

Read `$ARGUMENTS`:

- `--sources <paths>` (required): comma-separated. Each path is workspace-relative (paths starting with `/`, `c:\`, or containing `..` traversal are rejected as invalid sources in Phase 1.2). Workspace paths in this repo do not contain commas; if a future path does, the composer must quote it. If `--sources` is absent or empty, return the no-sources footer (Phase 3.5) and exit.
- `--entity <name>` (optional): scopes the modality scan. If absent, modality check is skipped and the footer notes "no entity specified."
- `--modalities <list>` (optional): comma-separated subset of the canonical list. Default is "all from `references/modalities.md`."

### 1.2 Validate source paths

For each path:

- File exists -> keep in source set.
- File missing -> drop from source set, list under "sources not found" in the footer.
- Path outside workspace root -> drop, list under "invalid sources" in the footer.

### 1.3 Degenerate cases

- Zero valid sources -> return no-sources footer (Phase 3.5), exit.
- Single valid source -> run Phase 2.1 (dates) and Phase 2.2 (modality), skip Phase 2.3 (contradictions). Footer notes "single source, no comparison."
- More than 20 sources -> cap at 20 for the contradiction check (LLM context budget), keep all sources for the date check. Footer notes the cap.

Phase 1 produces no user-visible output on success. All edge cases produce a valid (degraded) footer rather than an error. The composing skill must never see a stack trace.

## Phase 2: Three checks

### 2.1 Source dates

For each valid source path, run:

```bash
git log -1 --format=%ai -- <path>
```

If the file is untracked or git returns nothing, fall back to a portable Python call:

```bash
python -c "import os, datetime; p='<path>'; print(datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime('%Y-%m-%d %H:%M:%S +0000'))"
```

Bash `stat` is intentionally NOT used because the flag set differs between GNU coreutils (Linux/WSL) and BSD (macOS), and exec workspaces run on both.

Compute days-since-modified relative to today's local date. Mark sources older than 90 days with `[STALE]`. Output one line per source:

```
- <path>: modified YYYY-MM-DD (N days ago)[ [STALE]]
```

### 2.2 Modality coverage

Skip entirely if no `--entity` was provided.

Read `references/modalities.md` to get the canonical modality list. For each modality in scope:

1. Resolve the search location.
2. If the location does not exist on the workspace, record "modality location unavailable."
3. Glob and/or Grep for the entity slug or full name.
4. If at least one match -> "found." Render the file path and last-modified date.
5. If zero matches -> "not found." Render the modality name only.

Output shape (mixed found and not-found, all rendered together):

```
- email: not found (no exchange thread mentions "<entity>" in last 90 days)
- telegram: not indexed
- osint: outputs/intel/osint/2026-02-14_osint_<slug>.md (modified 2026-02-14, 103 days ago) [STALE]
- crm-log: crm/contacts/<slug>.md last_touch 2026-05-15 (13 days ago)
- calendar: not found (no upcoming or past 30-day event mentions entity)
```

Footer header for this section is "Modality coverage" (not "Modalities not found") because both found and not-found lines render.

### 2.3 Source disagreements

Skip if the valid source count is less than 2.

Read the contents of each capped source (up to 20). Concatenate with file-path headers. Make one LLM call with this prompt skeleton:

> You are auditing N sources for disagreements about `<entity>` (or "the topic" if no entity provided). Sources are labelled with their file paths.
>
> For each source, extract explicit claims about the entity involving:
> - Numbers (revenue, headcount, deal size, dates, prices)
> - States (deal stage, decision status, employment status, project phase)
> - Relationships (employer, partner, investor, customer)
>
> Then compare claims across sources. Report only disagreements: pairs of sources that make incompatible claims about the same dimension.
>
> For each disagreement, name both sources, the dimension, and each side's claim. If no disagreements are found, return the single line: "none detected".

The LLM returns either "none detected" or a list of disagreement entries.

Disagreement output shape:

```
- Deal stage: <source A> says "Demo (2026-05-10)", <source B> says "moved to Proposal 2026-05-22"
- Headcount: <source A> says "12 engineers", <source B> says "team of 14"
```

If the LLM call fails (timeout, error), record "contradiction check unavailable (LLM call failed)" and proceed to Phase 3. Never abort the parent skill.

## Phase 3: Compose the footer

Return one of five footer shapes as the entire skill output. Stable header `## Brain audit` (or `## Brain audit:` for the no-sources case). No preamble, no commentary, no extra blocks.

### 3.1 Standard footer (all three checks ran)

```
## Brain audit

**Newest source per claim:**
<2.1 output - per-line list>

**Modality coverage for <entity>:**
<2.2 output - per-line list>

**Disagreements among sources:**
<2.3 output - per-line list or "none detected">
```

### 3.2 Single-source footer

```
## Brain audit

**Newest source per claim:**
<2.1 output for the one source>

**Modality coverage for <entity>:**
<2.2 output - per-line list>

**Disagreements among sources:** single source, no comparison
```

### 3.3 No-entity footer

```
## Brain audit

**Newest source per claim:**
<2.1 output - per-line list>

**Modality coverage:** no entity specified, modality check skipped

**Disagreements among sources:**
<2.3 output - per-line list or "none detected">
```

### 3.4 Degraded footer (LLM call failed)

```
## Brain audit

**Newest source per claim:**
<2.1 output - per-line list>

**Modality coverage for <entity>:**
<2.2 output - per-line list>

**Disagreements among sources:** contradiction check unavailable (LLM call failed)
```

### 3.5 No-sources footer

```
## Brain audit: no sources provided, audit skipped
```

Footer always renders three sections (or the single-line skipped case). Never partial.

## Voice rules

- Single hyphens `-` in prose, never `--`.
- No em-dashes in any output this skill generates.
- Output is English-only regardless of conversation language. The footer is structural, not conversational.
- Workspace terminology: ODUN.ONE, DPI+, Tribe (never "team" / "family" / "crew") where it appears in flagged claims.

## NEVER

1. Never write to disk. Pure read-and-report. The skill produces the footer string only.
2. Never modify CRM, threads, knowledge, datastore, context, or any other workspace file.
3. Never abort the parent skill on a check failure. Every failure mode produces a degraded but valid footer.
4. Never produce preamble or commentary in the output. The footer is the entire output.
5. Never invoke other skills. /brain-audit is a leaf; it is composed BY other skills, it does not compose any.
6. Never scan the workspace pairwise (O(n²)). The contradiction check operates only on the source set passed in.
7. Never fabricate disagreements. If no disagreements exist, the LLM call returns "none detected" verbatim.
8. Never auto-trigger from natural language. Slash-only or explicit invocation by a composing skill.
