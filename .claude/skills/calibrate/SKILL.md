---
name: calibrate
disable-model-invocation: true
description: >
  Reflective end-of-session self-improvement. Scans the current Claude Code
  session for corrections, preferences, repeated patterns, errors, success
  patterns, and voice violations, then proposes numbered concrete patches to
  memory, settings, ceo-only skills, and ceo-only rules. Corporate files
  route to a separate review queue and are NEVER auto-applied. Use at end of
  every working session. Light mode for low-token state or quick sweeps.
  CEO-only - never propagates to execs.
argument-hint: "[light]"
allowed-tools: "Read, Edit, Write, Bash(python3:*, git:*), Glob, Grep"
context: fork
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: false
  shared_state:
    - "memory/"
    - ".claude/skills/"
    - ".claude/rules/"
    - ".claude/settings.local.json"
    - "outputs/operations/calibrate/"
  triggers:
    - "calibrate"
    - "self-improve agent"
    - "end of session capture"
    - "/calibrate"
    - "/calibrate light"
x-31c-capability:
  what: >
    Reflective end-of-session self-improvement - scans the session for corrections,
    preferences, errors, and voice violations, then proposes numbered patches to memory,
    settings, and ceo-only skills/rules. Corporate-classified patches route to a review
    queue and are never auto-applied. CEO-only.
  how: >
    Run /calibrate (explicit-invocation-only, never auto-triggers). Full mode parses the
    session JSONL; /calibrate light scans only the in-context conversation. Applies approved
    patches and makes one atomic git commit; any corporate queue lands in outputs/operations/calibrate/.
  when: >
    Use at the end of a working session to capture learnings. For cross-session memory
    consolidation use /dream; for grading a single artifact use /evaluate.
---
# /calibrate - End-of-Session Self-Improvement

Scans the current session for friction and successes, proposes numbered
patches grouped by target (Memory / Settings / Skills / Rules), applies
approved patches, and makes a single atomic git commit. Corporate files
route to a separate review queue, never auto-applied.

Design spec: `docs/superpowers/specs/2026-05-13-calibrate-skill-design.md` (data overlay: `.heading-os-data/docs/superpowers/specs/2026-05-13-calibrate-skill-design.md`).
Detection prompts: `references/detection-prompts.md`.

## Phase 0 - Pre-flight + mode dispatch

Run these checks before any other work. Each failure has a specific recovery
message; never abort silently.

1. **Git available + in repo.** Run `git rev-parse --is-inside-work-tree`.
   If exit != 0: abort with "git not available or not in a repo. /calibrate
   cannot safely auto-commit."

2. **Working tree state.** Run `git status --porcelain`. If any modified
   files are present outside `outputs/`, `crm/aggregated/`, `.sessions/`,
   `datastore/operations/tribe/fireside-state/` (the typical transient set):
   warn: "Working tree has uncommitted changes that aren't typical transients.
   /calibrate's auto-commit will include them. Stage and commit first, or
   confirm to proceed? (proceed / cancel)". Wait for explicit answer.

3. **Mode dispatch.** Parse `$ARGUMENTS`:
   - Empty / no flag -> full mode
   - `light` or `--light` -> light mode

4. **Full mode only:** verify `scripts/calibrate.py` exists. If missing,
   abort: "Parser script missing. Cannot run full mode. Try `/calibrate light`."

5. **All modes:** verify `config/routing-map.yaml` exists. If missing,
   abort with: "Routing map missing. /calibrate's safety filter
   cannot run. Aborting."

## Phase 1 - Acquire signal envelope

### Full mode

Invoke the parser:

```bash
python scripts/calibrate.py > /tmp/calibrate-envelope.json
```

Read `/tmp/calibrate-envelope.json` via the Read tool. Validate the envelope
has the expected keys (`user_turns`, `assistant_turns`, `tool_errors`,
`system_reminders`, `workspace`). If validation fails: report parser error
and abort.

If `envelope["truncated"]` is true, surface a one-line notice in Phase 4
preamble: "Note: session exceeded budget. Oldest user turns truncated. Recent
corrections still captured."

If `envelope["event_count"]` is 0: exit cleanly with "Session produced no
actionable events. Nothing to calibrate."

### Light mode

Skip the parser. Use the current in-context conversation directly. Announce in
the Phase 4 preamble:

> Light mode active. Scanning only in-context conversation (no JSONL parse).
> Categories 3 (repeated patterns) and 4 (errors / friction) skipped - they
> require the structured envelope. Long sessions may have auto-compacted early
> turns - those signals are not recoverable here. Run `/calibrate` (full)
> for the complete pass.

## Phase 2 - Six-category detection

Load `references/detection-prompts.md` via Read. Walk the envelope through the
six detection prompts in order. Each detected signal becomes a candidate of
the shape specified in detection-prompts.md.

| # | Category | Full mode | Light mode |
|---|---|---|---|
| 1 | Corrections | kept | kept |
| 2 | Preferences | kept | kept |
| 3 | Repeated patterns | kept | SKIPPED |
| 4 | Errors / friction | kept | SKIPPED |
| 5 | Success patterns | kept | kept |
| 6 | Voice violations | kept | kept |

For each candidate, also run the **idempotency check** from
detection-prompts.md: Grep the proposed target file for substring match of
the proposed diff body. If matched, drop the candidate silently. This
prevents duplicate patches.

Also: NEVER propose patches to files under `_secure/` regardless of category.
Hard block, even if a signal points there.

Also: NEVER propose patches to `~/.claude/CLAUDE.md` (the global private
CLAUDE.md). V1 scope is per-project memory only.

## Phase 3 - Classification filter

For each candidate, resolve the target file's classification:

```bash
python scripts/utils/workspace.py get_classification <target-path>
```

Returns "ceo-only" or "corporate". On any error from the resolver, treat as
**corporate** (fail-closed - never auto-apply on doubt).

Routing:
- `ceo-only` -> proceeds to Phase 4 numbered list
- `corporate` -> moves to the corporate review queue (Phase 5 step 5)

For new-file candidates (proposed target does not yet exist):
- Path starts with `~/.claude/projects/.../memory/` -> ceo-only
- Path starts with `outputs/` -> ceo-only
- Otherwise -> corporate (fail-closed)

## Phase 4 - Grouped numbered presentation

Sort the ceo-only candidates per the sort order in detection-prompts.md.
Group by target category (Memory / Settings / Skills / Rules). Number
sequentially across groups.

### Auto-include: thread-log capture

Before presenting Phase 4, check whether the session edited any file under
`threads/business/` or `threads/personal/`. If yes, prepend a special
candidate to the Memory group (or Skills group if the thread skill itself
needs an entry):

```
0. [PROJECT-LOG]  Append a session-log entry to threads/{layer}/{slug}.md
   -> threads/{layer}/{slug}.md
   Session touched this thread; capture key decisions, files changed, open
   questions, and what's next so the next session resumes cleanly.
```

If multiple threads were touched, list one candidate per thread. Skip
entirely if no thread files were read or written.

This is a CEO-only auto-include - threads/ is CEO-only per the workspace
classification rule, and the auto-include logic itself never fires in
exec workspaces because /calibrate is CEO-only.

### Numbered candidate presentation

Display format and strict input grammar: `references/presentation-format.md`.

Wait for user input after rendering. Re-prompt rather than guess on
unrecognised input.

## Phase 5 - Apply + commit

After user approval, in this exact order:

### Step 5.1: Apply ceo-only patches

For each approved candidate (in sorted order):
- **Memory files (`~/.claude/projects/.../memory/`):**
  - If patch is "append": Edit to add the new bullet under the body's main rule
  - If patch is "update": Edit to replace the affected section
  - If patch is "create new": Write the new `feedback_{slug}.md` file with
    full frontmatter, then Edit `MEMORY.md` to add the one-line index pointer
- **Settings file (`.claude/settings.local.json`):** Edit to set the new property
- **Skills (`.claude/skills/{name}/SKILL.md`, ceo-only):** Edit the specific section named in the proposal
- **Rules (`.claude/rules/{file}.md`, ceo-only):** Edit the specific section named

If any Edit fails (old_string no longer unique, file changed since Phase 1, etc.):
stop immediately. Report: "Patch {N} failed: {reason}. Patches 1 through {N-1}
applied. Patches {N} through {M} skipped. Run /calibrate again to retry. No
commit made." Skip Steps 5.2-5.5 entirely.

### Step 5.2: Write corporate review queue (only if any corporate candidates)

If any candidates routed to corporate review, write a single file:

```
outputs/operations/calibrate/{YYYY-MM-DD}_corporate-review.md
```

Format per `references/detection-prompts.md` section on corporate review queue
patches: source quote, category, proposed target, proposed diff in fenced
code block (read-only format, NOT git-applyable), rationale, "to apply: review
the diff manually, edit the target file by hand, run /push-updates."

If no corporate candidates: skip this step. Do not write an empty file.

### Step 5.3: Sanitisation pass

Run `python scripts/sanitize-text.py {modified-files} --scan` on every file
that was modified or created by Steps 5.1 and 5.2. If hidden Unicode is
detected, auto-run with `--fix` to clean. Note in commit body if cleanup
happened.

### Step 5.4: Stage + commit

Stage `.claude/`, `outputs/operations/calibrate/`, and any other modified
workspace files. Memory files at `~/.claude/projects/.../memory/` are outside
the workspace tree - NOT staged. Settings file at
`.claude/settings.local.json` is gitignored - staged but git ignores it.

Commit message template + behaviour on pre-commit hook rejection:
`references/patch-application-protocol.md`.

### Step 5.5: Report final state

Final state report template: `references/patch-application-protocol.md`.

## NEVER

1. **Never auto-apply corporate patches.** Corporate-classified files only
   route to the review queue. Edits to corporate files require manual
   review + `/push-updates`.
2. **Never write to `_secure/`.** Detection skips _secure/ targets entirely.
3. **Never write to `~/.claude/CLAUDE.md` (global).** V1 scope is per-project
   memory only.
4. **Never rollback automatically on partial failure.** Stop on first error,
   report, let the user decide.
5. **Never run silently on ambiguous input.** Strict grammar in Phase 4 -
   re-prompt rather than guess.
6. **Never propose duplicate patches.** Idempotency check before every
   candidate goes into the numbered list.
7. **Never propagate to execs.** This skill is ceo-only. Not in
   `templates/GETTING-STARTED.md`, not in the corporate repo, not in
   `corporate/` sync.

## Error handling

Specific recovery messages per failure mode. See spec section 9 for the
complete matrix. Always provide actionable recovery; never raw tracebacks.

## Voice rules

- Single hyphens `-` in prose, never `--`.
- No em-dashes in any prose Claude generates within this skill.
- ODUN.ONE when referencing 31C platform.
- DPI+ for deep packet intelligence.
- Language matches the user's language (Russian question = Russian output).

### Output-prose plain-English mandate

Candidate summaries, bodies, and rationales must be plain-language so the
user can scan and pick `apply 1, 3, 5` in seconds. Banned-vocabulary table,
plain-form replacements, and worked good/bad examples:
`references/output-prose-style.md`.

If a 12-year-old reading the candidate out loud would not roughly
understand it, rewrite it.

## Examples

Two end-to-end runs (light mode with 2 candidates, and a clean session
with no actionable events): `references/examples.md`.
