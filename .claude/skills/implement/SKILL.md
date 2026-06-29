---
name: implement
description: Execute an approved implementation plan step by step, with optional trajectory logging and a post-run evaluation pass. Use to build out a plan produced by /create-plan. Trigger when the user says "implement", "execute the plan", or "build it". Do NOT use for planning itself (use /create-plan).
argument-hint: "[plan-path] [--no-trajectory] [--evaluate]"
allowed-tools: "Read, Write, Edit, Bash(python3:*), Bash(python:*), Glob, Grep"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.5"
x-31c-orchestration:
  parallel_safe: false
  shared_state: []
  triggers:
    - implement
    - execute the plan
    - build it
x-31c-capability:
  what: >
    Executes an implementation plan created by /create-plan step by step, writing complete files
    and emitting a structured JSONL trajectory for every phase so the run can be audited later.
  how: >
    Run /implement <plan-path>. Trajectory writes to outputs/operations/implement/_trajectory_<run_id>.jsonl
    (suppress with --no-trajectory). Pass --evaluate to grade each artifact with /evaluate after execution.
  when: >
    Use to build out an approved plan. To write the plan first use /create-plan; to audit a finished
    run use /scrutinize trajectory:<run_id>.
---
# Implement

Execute an implementation plan created by `/create-plan`. Read the plan thoroughly, execute each step in order, emit a structured trajectory for every phase boundary, and report on the completed work.

**v1.3 (2026-05-27):** Trajectory emission added per R12 of the /scrutinize meta-review. Every run writes structured JSONL events to `outputs/operations/implement/_trajectory_<run_id>.jsonl` unless `--no-trajectory` is passed. The trajectory is the input for `/scrutinize trajectory:<run_id>` audits (Agent-as-a-Judge pattern, DevAI benchmark).

**v1.4 (2026-06-04):** Emission-discipline tightening from the BMAD Wave-1 trajectory scrutiny (M1/L1/N1). Sequential waves now require each `step_end` before the next `step_start` and `wave_end` after all member `step_end`s (M1); `wave_start` must precede the wave's first `step_start` and `wave_end.successes` must equal the bracketed `step_end` count (L1); `files_affected` must be literal paths, no globs/shorthand/counts (N1). Instruction-only change — the helper script and event schema are unchanged.

**v1.5 (2026-06-16):** Whole-wave deferrals are now first-class structured records. From the L1 finding of the engine-to-ideal trajectory scrutiny: a wave deferred entirely (not executed this run) emits one wave-scoped `deviation` event keyed to the wave's first step (`scope: "wave"`, `wave: N`) instead of being recorded only in the `run_end` summary string. Wave-execution mode step 6. Instruction-only change — the helper accepts the `deviation` type and arbitrary payload already; the scrutinize trajectory lens recognises the `scope: "wave"` form.

## Variables

plan_path: $ARGUMENTS (path to the plan file, e.g., `plans/2026-01-28-add-guest-research-command.md`)

--no-trajectory: opt out of trajectory emission for this run only. Skips Phase 0: Trajectory Setup and all per-phase emission calls (it does NOT skip the Phase 0 (pre) pre-impl gate check, which is independent of trajectory). Use for throwaway / smoke-test runs.

--evaluate: run /evaluate on each created or modified artefact after Phase 3 (existing Phase 4 behaviour, unchanged).

---

## Instructions

### Phase 0 (pre): Pre-impl gate check (soft, non-blocking)

Runs whenever a `plan_path` is supplied — **independent of `--no-trajectory`** (this check is unrelated to trajectory). Skip only when `/implement` is driven from a description with no plan file.

1. Run the advisory helper:

   ```bash
   python scripts/check-preimpl-gate.py --plan {plan_path}
   ```

   It always exits 0 and prints one of `FOUND` / `MISSING` / `SKIPPED`.

2. Act on the result:
   - **FOUND** or **SKIPPED**: proceed silently to Phase 0.
   - **MISSING**: surface a one-line reminder, then ask once whether to proceed —
     > "No `/pre-impl` gate artifact found for this plan. The gate is recommended before implementing (see `/pre-impl`). Proceed anyway, or run `/pre-impl {plan_path}` first?"

     This is a **soft** reminder, not a block. If Misha says proceed (or has already implied it), continue to Phase 0. Never refuse to implement on a MISSING result — CEO sovereignty holds.

### Phase 0: Trajectory Setup

**Skip this entire phase if `--no-trajectory` was passed.**

1. Mint a new `run_id` via the trajectory helper:

   ```bash
   run_id=$(python scripts/implement-trajectory-log.py --new --plan {plan_path})
   ```

   The helper derives slug as `Path(plan_path).stem` with leading `YYYY-MM-DD-` stripped if present, then mints `run_id` = `YYYY-MM-DD_HHMMSS_<slug>`. The `run_start` event (including plan path, slug, working dir, git HEAD) is written automatically.

2. Capture the `run_id` for use in every subsequent emission call.

3. Print: `Trajectory: run_id={run_id}. JSONL: outputs/operations/implement/_trajectory_{run_id}.jsonl`.

4. Carry `run_id` through Phases 1-5. Every emission point below uses it.

### Phase 1: Understand the Plan

1. **Read the plan file completely.** Do not skim — understand every section.
2. **Verify prerequisites:**
   - Are there open questions that need answers before proceeding?
   - Are there dependencies on external resources or user decisions?
   - If blockers exist, stop and ask the user before proceeding.
3. **Confirm the plan is ready:**
   - Status should be "Draft" or "Ready"
   - All sections should be filled out (no placeholder text remaining)

4. **Emit the step-0 (plan-load) summary as a PAIRED `step_start`/`step_end`** — unless `--no-trajectory`:
   - Write JSON to a temp file at `outputs/operations/implement/_tmp/{run_id}_001_plan_load.json` via the `Write` tool: `{"step": 0, "title": "plan loaded", "plan_path": "<plan_path>", "status_field": "<plan Status value>"}`
   - Call `python scripts/implement-trajectory-log.py --event --run-id {run_id} --type step_start --data-file <temp-path>`
   - Immediately emit the matching `step_end` so step 0 is paired like every real step (the trajectory audit lens flags any unpaired `step_start`). Write a temp file `{run_id}_001_plan_load_end.json`: `{"step": 0, "files_affected": [], "status": "ok", "notes": "plan-load marker; no work performed"}` and call `python scripts/implement-trajectory-log.py --event --run-id {run_id} --type step_end --data-file <temp-path>`

---

### Phase 2: Execute the Plan

**Step 1: Detect wave headers.**

Scan the plan for `### Wave` headers.

- If **no wave headers found**: execute all steps sequentially using the standard rules below.
- If **wave headers found**: switch to wave execution mode (see below).

**Standard sequential execution (no waves):**

1. **Follow the Step-by-Step Tasks in exact order.**
   - Complete each step fully before moving to the next
   - If a step involves creating a file, write the complete file — not a stub
   - If a step involves modifying a file, read the file first, then apply changes precisely

2. **For each task — trajectory emission discipline** (skip if `--no-trajectory`):
   - **At step start**: Write JSON to a temp file at `outputs/operations/implement/_tmp/{run_id}_step{N}_start.json`: `{"step": N, "title": "<step title from plan>"}`. Call `python scripts/implement-trajectory-log.py --event --run-id {run_id} --type step_start --data-file <temp-path>`.
   - **Read any files that will be affected**
   - **Make the changes specified**
   - **Verify the change is correct before proceeding**
   - **At step end**: Write JSON to a temp file at `outputs/operations/implement/_tmp/{run_id}_step{N}_end.json`: `{"step": N, "files_affected": [<list>], "status": "ok|issues|deviation", "notes": "<optional>"}`. Call `--event --type step_end --data-file <temp-path>`. **`files_affected` MUST list literal, fully-qualified paths** — one entry per file actually touched. No globs (`.claude/skills/*/triggers.json`), no brace-shorthand (`{a,b,c}/SKILL.md`), no count strings (`+3 customize.toml`). Expand to the real paths so the deterministic file-diff audit can reconcile them (the N1 emission defect).

3. **Handle issues gracefully:**
   - If a step can't be completed as written, note the issue and adapt if the intent is clear
   - If you're unsure how to proceed, ask the user rather than guessing
   - **Emit a `deviation` event** when adapting a step: write JSON `{"step": N, "reason": "<why>", "what_changed": "<vs plan>"}` to temp file, call `--event --type deviation --data-file <temp-path>`.

**Wave execution mode:**

Execute waves in order. For each wave:

1. **Parse all steps** (`####` headings) within the wave.

2. **Emit `wave_start` event** (skip if `--no-trajectory`): write JSON `{"wave": N, "step_count": <count>, "parallel": <true|false>}` to temp file, call `--event --type wave_start --data-file <temp-path>`. **`wave_start` MUST be emitted before the wave's first `step_start`** — never after a member step has already opened. A step whose `step_start`/`step_end` fall outside its wave's brackets cannot be reconstructed from the bracketed record (the L1 emission defect).

3. **If the wave is marked `(parallel)` AND has 2+ steps:**
   - Announce: "Executing Wave N: {count} parallel tasks"
   - Dispatch each step as an independent agent using `superpowers:dispatching-parallel-agents`
   - **Each child sub-agent brief MUST include**: (a) the `run_id`, (b) the path `scripts/implement-trajectory-log.py`, (c) instructions to emit its own `step_start` / `step_end` / `deviation` events via `--data-file` only (never `--data-json`), (d) a reminder that the JSONL is shared state and the helper handles atomic-append concurrency.
   - Each agent receives: the step's full text (not a file reference), relevant context files, and instructions to commit its work atomically
   - Wait for ALL agents in the wave to complete
   - Collect results (successes and failures)
   - If ANY step failed: report ALL results (successes and failures), STOP, and ask the user how to proceed. Do not advance to the next wave.

4. **If the wave is NOT marked `(parallel)` OR has only 1 step:**
   - Execute the steps sequentially using the standard rules above (including the per-step `step_start` / `step_end` emission).
   - **Sequential ordering invariant:** emit each step's `step_end` *before* the next step's `step_start`. Never open step N+1 while step N's bracket is still open, and never let a later step's `step_end` land after `wave_end`. If the work genuinely reorders (a step finishes out of declared sequence), emit a `deviation` event recording the swap — do not let the markers interleave silently (the M1 emission defect).

5. **After a wave completes successfully:**
   - Verify all files mentioned in the wave's steps exist
   - **Emit `wave_end` event** (skip if `--no-trajectory`): write JSON `{"wave": N, "successes": <count>, "failures": <count>}` to temp file, call `--event --type wave_end --data-file <temp-path>`. Emit `wave_end` only *after* every member step's `step_end` has been emitted. `successes` MUST equal the number of `step_end` events with `status` ok/deviation that fall inside this wave's `wave_start`/`wave_end` brackets — not the plan's declared membership count. The two must reconcile from the bracketed record alone.
   - Report: "Wave N complete: {count} tasks done"
   - Proceed to the next wave

6. **If an entire wave is deferred — not executed at all this run** (deferred to a focused session, blocked, or descoped): do NOT emit `wave_start`/`wave_end` for it (no steps ran, so there would be no bracketed `step_end`s to reconcile against). Instead emit exactly ONE **wave-scoped `deviation` event keyed to the wave's first step number**, so a whole-wave skip is a first-class structured record rather than only a `run_end` summary string (the L1 trajectory-audit defect, 2026-06-16). Skip if `--no-trajectory`. Write JSON `{"step": <first step number of the deferred wave>, "scope": "wave", "wave": N, "reason": "<why deferred>", "what_changed": "wave N (steps X-Y) deferred — not executed this run"}` to a temp file, call `--event --type deviation --data-file <temp-path>`. The `run_end` summary should still mention the deferral, but the wave-scoped deviation event is the authoritative structured record. (A per-step deviation per skipped step is also acceptable when only part of a wave is deferred; the `scope: "wave"` form is for the whole-wave case.)

---

### Phase 3: Validate

1. **Run through the Validation Checklist** from the plan
   - Check off each item
   - Note any that fail
   - **For each check** (skip if `--no-trajectory`): write JSON `{"check": "<name>", "passed": true|false, "detail": "<one-line>"}` to a temp file, call `--event --type validation_check --data-file <temp-path>`.

2. **Verify Success Criteria** are met
   - Confirm each criterion is satisfied
   - Note any gaps

3. **Check cross-references and consistency:**
   - Ensure new files are referenced where they should be
   - Verify CLAUDE.md is updated if workspace structure changed
   - Confirm naming conventions are followed

---

### Phase 4: Evaluate (Optional)

If the user included `--evaluate` in the arguments or explicitly requested evaluation after implementation:

1. **Run `/evaluate`** on each created or modified artifact (skills, scripts, reference files, rules).
2. **If grade is PASS or PASS WITH NOTES**: Proceed to Phase 5.
3. **If grade is NEEDS REWORK**:
   - Read the rework instructions from the evaluation report
   - Apply the specific fixes listed
   - Re-run `/evaluate` on the fixed artifact
   - Maximum 3 rework cycles. If still NEEDS REWORK after 3 cycles, report remaining issues to the user.
4. **If grade is FAIL**: Stop and report the full evaluation to the user. Do not attempt automatic rework on FAIL grades.
5. **Track iteration count** in the Implementation Notes section.
6. **For each evaluation** (skip if `--no-trajectory`): write JSON `{"artefact": "<path>", "grade": "PASS|PASS WITH NOTES|NEEDS REWORK|FAIL", "iteration": <n>}` to temp file, call `--event --type evaluation_result --data-file <temp-path>`.

---

### Phase 5: Update Plan Status

After implementation (and optional evaluation), update the plan file:

1. Change `**Status:** Draft` to `**Status:** Implemented` (or `**Status:** Implemented (Evaluated)` if Phase 4 was run)
2. Add an Implementation Notes section at the end:

```markdown
---

## Implementation Notes

**Implemented:** <YYYY-MM-DD>

### Summary

<Brief summary of what was done>

### Deviations from Plan

<List any changes made during implementation, or "None">

### Issues Encountered

<List any problems hit and how they were resolved, or "None">

### Trajectory

`outputs/operations/implement/_trajectory_<run_id>.jsonl` — audit with `/scrutinize trajectory:<run_id>`.
```

**Final emission** (skip if `--no-trajectory`): write JSON `{"run_id": "<id>", "trajectory_path": "<path>", "plan_status": "Implemented", "summary": "<one-line>"}` to temp file, call `--event --type run_end --data-file <temp-path>`. Then clean up any leftover temp files under `outputs/operations/implement/_tmp/{run_id}_*.json`.

---

## Quality Standards

- **Thoroughness:** Every step in the plan is executed, not skipped
- **Precision:** Changes match what the plan specifies
- **Completeness:** Files are fully written, not stubbed out
- **Consistency:** All cross-references and documentation updated
- **Traceability:** Deviations are documented

---

## Report

After implementation, provide:

1. **Summary:** Bulleted list of work completed
2. **Files changed:** List all files created, modified, or deleted
3. **Validation results:** Status of each checklist item
4. **Deviations:** Any changes from the original plan
5. **Next steps:** Any follow-up actions needed (if applicable)
6. **Audit offer:** `"Execution complete. Run /scrutinize to audit what was done?"`

Format:

```
## Implementation Complete

### Summary
- <What was done>
- <What was done>

### Files Changed
**Created:**
- `path/to/new-file.md`

**Modified:**
- `path/to/modified-file.md`

**Deleted:**
- (none)

### Validation
- [x] <Passed check>
- [x] <Passed check>

### Deviations from Plan
<None, or list deviations>

### Plan Status
Updated `plans/YYYY-MM-DD-{name}.md` status to "Implemented"
```

---

## NEVER

- Never skip a planned step without documenting why in the Deviations section
- Never finish implementation without running security evaluation - scan for hidden characters (`sanitize-text.py --scan`), compile-check Python files (`py_compile`), and verify no secrets in new/modified files
- Never stub out a file - write complete, production-ready content or do not create the file at all
- Never modify a file without reading it first - blind edits cause regressions and data loss
- Never proceed past a blocker or ambiguity without asking the user - guessing at unclear steps produces wrong work
- Never skip documentation propagation - if you created or modified a skill, script, reference file, or rule, update CLAUDE.md, templates/GETTING-STARTED.md, and any other affected documentation targets before declaring done
- Never skip trajectory emission unless `--no-trajectory` was explicitly passed - trajectory is the input to `/scrutinize trajectory:<run_id>` audits
- Never call `scripts/implement-trajectory-log.py` with `--data-json` from inside `/implement` - that mode is bash-only / hand-runs only; `/implement` MUST use `--data-file` for cross-platform safety
- Never write to `outputs/operations/implement/_trajectory_*.jsonl` directly - only through the helper script (it handles atomic-append concurrency on POSIX + Windows)
