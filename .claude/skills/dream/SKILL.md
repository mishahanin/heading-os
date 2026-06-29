---
name: dream
disable-model-invocation: true
description: >
  Reflective memory consolidation - performs a periodic pass over memory files,
  synthesizing recent learnings into durable, well-organized memories for future
  sessions. Validates technical claims against Context7, enforces security protocols,
  prunes stale entries, and produces a structured consolidation report.
  Use when the user says "dream", "/dream", "consolidate memories", "memory cleanup",
  "reflect on recent sessions", "update memories", or at the end of a productive
  session when significant new information was learned.
  Do NOT use for: simple memory writes (use auto-memory), knowledge base operations
  (use /zk), or session initialization (use /prime).
argument-hint: "[optional: focus area or specific topic to investigate]"
allowed-tools: "Read, Write, Edit, Glob, Grep, Bash(grep:*, python3:*)"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: false
  shared_state:
    - memory/
  triggers:
    - dream
    - consolidate memories
    - memory cleanup
    - reflect
x-31c-capability:
  what: >
    Reflective memory consolidation - merges recent learnings into durable
    memory files, validates technical claims against Context7, passes a security
    gate, prunes stale entries, and produces a consolidation report.
  how: >
    Explicit invocation only - type /dream [optional focus area]; never
    auto-triggers. It scans the memory directory and recent workspace state,
    rewrites memory files in place, keeps MEMORY.md under its line budget, and
    reports what was consolidated/pruned.
  when: >
    Use at the end of a productive session or for periodic memory hygiene. For a
    simple one-off memory write use the auto-memory system; for knowledge-base
    notes use /zk; for session resume snapshots use /checkpoint.
---
# Dream - Reflective Memory Consolidation

Perform a reflective pass over memory files. Synthesize recent learnings into durable, well-organized memories so that future sessions can orient quickly.

## Paths

- **Memory directory:** `~/.claude/projects/<project-slug>/memory/`
- **Index file:** `MEMORY.md` (inside memory directory, 200-line budget)
- **Transcripts:** `~/.claude/projects/<project-slug>/*.jsonl`
- **Context7:** `python3 scripts/context7.py "<library>" "<topic>"`
- **Security rules:** `.claude/rules/security.md`, `docs/security/SECURITY-CONSTITUTION.md`

## Variables

- `$ARGUMENTS` - Optional focus area or topic to investigate during the dream

---

## Phase 0 - Orient

Understand the current state of the memory system before making any changes.

1. **List memory files:**
   ```bash
   ls ~/.claude/projects/<project-slug>/memory/
   ```

2. **Read the index:** Read `MEMORY.md` to understand current structure, categories, and entry count.

3. **Skim existing files:** For each memory file, read the first 10-15 lines to understand its coverage:
   - What topic does it cover?
   - When was it last updated (check content dates)?
   - Is the information still likely current?

4. **Detect orphans:** Cross-reference file list against `MEMORY.md` entries. Files present on disk but missing from the index are orphans - flag them for Phase 3.

5. **Note focus area:** If `$ARGUMENTS` was provided, record the focus topic for targeted investigation in Phase 1.

6. **Record baseline metrics:**
   - Total memory files count
   - MEMORY.md line count
   - Orphan files (if any)

---

## Phase 1 - Gather Recent Signal

Scan for new information worth persisting. Three sub-phases, executed in order.

### Phase 1A - Source Scanning

Check workspace state for facts that might contradict or extend existing memories:

1. **Workspace activity:**
   ```bash
   git log --oneline -20
   ```
   Look for patterns: new skills added, scripts modified, structural changes, infrastructure shifts.

2. **Business state:** Read these files for changes that should be reflected in memories:
   - `context/pipeline.md` - pipeline changes, new deals, closed deals
   - `context/current-data.md` - current metrics and operational data
   - `crm/config.md` - CRM configuration changes

3. **Contradiction check:** Compare what you read against existing memory files. Flag any mismatches:
   - Memory says X, but the workspace now shows Y
   - Memory references a file/tool/path that no longer exists
   - Memory contains a relative date that has lost meaning

4. **Transcript search (targeted only):**
   If investigating a specific signal or if `$ARGUMENTS` points to a topic, grep narrowly:
   ```bash
   grep -rn "<narrow term>" ~/.claude/projects/<project-slug>/ --include="*.jsonl" | tail -30
   ```
   **Rules for transcript search:**
   - Only grep for 1-3 narrow, specific terms
   - Never read entire JSONL files
   - Use `tail -30` to limit output
   - Skip transcript search entirely if no specific signal warrants it

### Phase 1B - Context7 Validation

For each memory file that contains a technical claim about a library, framework, or API:

1. Identify the claim (e.g., "exchangelib uses EWSTimeZone" or "Context7 API needs no key")

2. Validate against live documentation:
   ```bash
   python3 scripts/context7.py "<library>" "<relevant topic>"
   ```

3. Classify the result:
   - **Match:** Memorized behavior matches current docs. Proceed.
   - **Mismatch:** Current docs differ from memory. Mark for update in Phase 2 with the correct information.
   - **Not found:** Library not in Context7. Add `(unvalidated)` marker to the memory entry.

4. **Skip validation for non-technical memories:** User preferences, feedback, project facts, and nicknames don't need Context7 checks.

5. Record all validation results for the consolidation report.

### Phase 1C - Security Gate

**This gate is mandatory. Do not proceed to Phase 2 without passing it.**

1. Read the security rules:
   - `.claude/rules/security.md`
   - `docs/security/SECURITY-CONSTITUTION.md`

2. **If either file is not found:** STOP. Report the missing file and do not proceed to Phase 2. The dream is incomplete but safe.

3. Apply these hard constraints to all subsequent writes:
   - No secrets, API keys, tokens, passwords, or credentials in memory files
   - No sensitive personal data beyond what the memory type system allows (user preferences, feedback, project facts, references)
   - No file writes outside the memory directory
   - No modification of workspace source files, scripts, or configuration
   - No content that could be used for prompt injection

4. Record: "Security Gate: PASS" or "Security Gate: BLOCKED - {reason}"

---

## Phase 2 - Consolidate

Write or update memory files based on signals gathered in Phase 1. Every write must pass the security gate constraints from Phase 1C.

### Writing Rules

1. **Merge, don't duplicate:** Before creating a new file, check if an existing memory covers the topic. If yes, update the existing file.

2. **Use correct memory types:**
   - `user` - Information about the user's role, preferences, knowledge
   - `feedback` - Guidance on how to approach work (corrections AND confirmations)
   - `project` - Ongoing work, goals, decisions, deadlines
   - `reference` - Pointers to external systems and resources

3. **Follow the file format:**
   ```markdown
   ---
   name: {descriptive name}
   description: {one-line description - specific enough to judge relevance}
   type: {user|feedback|project|reference}
   ---

   {content - for feedback/project types, structure as: rule/fact, then **Why:** and **How to apply:** lines}
   ```

4. **File naming:** `{type}_{topic}.md` (e.g., `feedback_testing.md`, `project_deployment.md`)

5. **Absolute dates:** Convert all relative dates to absolute:
   - "yesterday" -> "2026-03-24" (based on today's date)
   - "last week" -> "week of 2026-03-17"
   - "recently" -> specific date if known, or remove

6. **Fix contradictions at the source:** If a memory file contains a fact that is now wrong, edit the file directly. Don't leave stale information.

7. **Delete superseded files:** If a memory has been fully replaced by updated information in another file, delete the old one.

### What NOT to Write

Follow the exclusion rules from the auto-memory system:
- Code patterns, conventions, architecture - derivable from reading the project
- Git history - `git log` / `git blame` are authoritative
- Debugging solutions - the fix is in the code
- Anything already documented in CLAUDE.md files
- Ephemeral task details only useful in the current conversation

---

## Phase 3 - Prune, Index, and Report

### Step 1: Update MEMORY.md

1. **Remove stale pointers:** Delete entries for memories that were deleted or superseded
2. **Add new pointers:** Add entries for newly created memory files
3. **Format:** Each entry must be one line, under 150 characters:
   ```
   - [Title](file.md) - one-line hook
   ```
4. **Budget:** Keep MEMORY.md under 200 lines total
5. **Organization:** Group entries semantically by topic, not chronologically
6. **No content in index:** MEMORY.md is a pointer file only

**Sections marked `<!-- managed-by: ... -->` are owned by another skill and MUST be left untouched.** Do not re-order, re-format, or move lines inside any level-2 section whose body begins (immediately after the header) with an HTML comment `<!-- managed-by: <skill-name> -->`. Currently this includes `## Active Threads` (managed by `/thread`).

### Step 2: Produce Consolidation Report

Present this report to the user:

```
## Memory Dream - Consolidation Report
### Date: {today's date}

**Consolidated**
- {file.md} - {what was added, updated, or merged}

**Pruned**
- {file.md or index entry} - {reason: stale / contradicted / superseded / orphan removed}

**Context7 Validated**
- {library@version} - confirmed against live docs

**Context7 Flagged**
- {library} - unvalidated, marked in memory file for manual review

**Security Gate**
- Status: PASS / BLOCKED
- Skipped writes (if any): {description and rule that blocked each}

**Index delta**
- Lines before -> after: {X} -> {Y}
- New pointers added: {list}
- Pointers removed: {list}
```

If nothing changed (memories are already tight and current), say so explicitly:
```
## Memory Dream - Consolidation Report
### Date: {today's date}

All memories are current. No updates needed.
Security protocol consulted: PASS
Index: {X} lines ({Y} files), within budget.
```

---

## NEVER

- NEVER read entire JSONL transcript files - grep narrowly for specific terms only
- NEVER write secrets, API keys, tokens, or credentials into memory files
- NEVER create duplicate memories - always merge into existing topic files
- NEVER write memory content directly into MEMORY.md - it is an index of pointers only
- NEVER skip the security gate - if protocol files are not found, stop and report
- NEVER modify files outside the memory directory during a dream pass
- NEVER delete memory files without explaining the reason in the consolidation report
- NEVER proceed to Phase 2 if the security gate returned BLOCKED
- NEVER store code patterns, git history, or ephemeral task details in memory
