---
name: implement
description: Execute an approved implementation plan step by step, with optional trajectory logging and a post-run evaluation pass. Use to build out a plan produced by /create-plan. Trigger when the user says "implement", "execute the plan", or "build it". Do NOT use for planning itself (use /create-plan).
argument-hint: "[plan-path] [--no-trajectory] [--evaluate]"
allowed-tools: "Read, Write, Edit, Bash(python3:*), Bash(python:*), Glob, Grep"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.7"
x-heading-orchestration:
  parallel_safe: false
  shared_state: []
  triggers:
    - implement
    - execute the plan
    - build it
x-heading-capability:
  what: >
    Executes an implementation plan created by /create-plan step by step, writing complete files
    and emitting a structured JSONL trajectory for every phase so the run can be audited later.
  how: >
    Run /implement <plan-path>. Trajectory writes to outputs/operations/implement/_trajectory_<run_id>.jsonl
    (suppress with --no-trajectory). Pass --evaluate to grade each artifact with /evaluate after execution.
  when: >
    Use to build out an approved plan. To write the plan first use /create-plan; to audit a finished
    run use /scrutinize trajectory:<run_id>.
x-heading-routing:
  category: Operations
  triggers:
    - implement
    - execute the plan
    - build it
  exclusions:
    - Planning -> /create-plan
  compound: 'No'
  router: auto
---
# Implement

Execute an implementation plan created by `/create-plan`. Read the plan thoroughly, execute each step in order, emit a structured trajectory for every phase boundary, and report on the completed work.

**Version history (v1.3-v1.7):** the emission discipline evolved through several versions — v1.6 typed flags (one `Bash` call per event, no temp file), v1.7 the emit-time sequencing guard (exit `5`) and the run-level `--verify` files reconciliation. The current, authoritative invariants are the **Trajectory emission contract** immediately below (and restated in Phase 2, Phase 5, and the NEVER list); the full per-version changelog lives in `references/implement-details.md`.

## Trajectory emission contract

The trajectory (`outputs/operations/implement/_trajectory_<run_id>.jsonl`) is the input to `/scrutinize trajectory:<run_id>` audits (Agent-as-a-Judge, DevAI benchmark) and a verbatim audit record: emit through the helper only, never edit it. All emission is skipped under `--no-trajectory`. The invariants, enforced by the phases below and self-checked by `--verify`:

- **Pairing.** Every `step_start` has a matching `step_end` (same step number); step 0 (plan-load) is paired too. Every `wave_start` has a `wave_end`.
- **Sequencing (sequential waves / no-wave runs).** Emit each `step_end` before the next `step_start`; never open step N+1 while step N is open. **The helper enforces this at emit time (v1.7):** a `step_start` opened while a step is still open (outside a parallel wave), or a `step_end` for an unopened step, exits `5` and does not land — emit the missing `step_end`/`step_start` first, or open a parallel `wave_start` for legitimate interleaving. If work genuinely reorders, emit a `deviation` recording the swap (the M1 discipline); likewise emit a step-scoped `deviation` when a run defers a plan-declared terminal action (the final `[0 0]` push a step and its success criteria mandate), reverses an approved decision mid-run, or diverges from explicit plan text at all (even a trivial, functionally-identical substitution such as re-sourcing an import), not only a `step_end` note, so the structured trajectory matches the plan's Deviations section. Parallel-wave member steps may legitimately interleave inside their wave bracket (an open parallel wave suspends the guard).
- **Wave bracketing.** `wave_start` precedes the wave's first `step_start`; `wave_end` follows all member `step_end`s; `wave_end.successes` equals the count of bracketed `step_end` with status `ok`/`deviation` (not the declared membership).
- **Literal paths.** `--file` takes one literal path per real file touched: no globs, brace-shorthand, or count strings (the N1 discipline).
- **Whole-wave deferral.** A wave not executed at all emits no `wave_start`/`wave_end`; instead one wave-scoped `deviation` keyed to the wave's first step (`--scope wave --wave N`).

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
     > "No planning-gate artifact found for this plan. The gate is recommended before implementing (see `/canopus`). Proceed anyway, or work the plan through `/canopus` first?"

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
   - `python scripts/implement-trajectory-log.py --event --run-id {run_id} --type step_start --step 0 --title "plan loaded"`
   - Immediately emit the matching `step_end` so step 0 is paired like every real step (the trajectory audit lens flags any unpaired `step_start`): `python scripts/implement-trajectory-log.py --event --run-id {run_id} --type step_end --step 0 --status ok --notes "plan-load marker; no work performed"` (`files_affected` defaults to `[]`).

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
   - **At step start**: `python scripts/implement-trajectory-log.py --event --run-id {run_id} --type step_start --step N --title "<step title from plan>"`.
   - **Read any files that will be affected**
   - **Make the changes specified**
   - **Verify the change is correct before proceeding**
   - **At step end**: `python scripts/implement-trajectory-log.py --event --run-id {run_id} --type step_end --step N --file <path> --file <path> --status ok|issues|deviation [--notes "<optional>"]`. Pass **one `--file` per literal, fully-qualified path** actually touched. No globs (`.claude/skills/*/triggers.json`), no brace-shorthand (`{a,b,c}/SKILL.md`), no count strings (`+3 customize.toml`); the deterministic file-diff audit reconciles literal paths (the N1 discipline). Omit `--file` entirely for a no-file step (`files_affected` defaults to `[]`).

3. **Handle issues gracefully:**
   - If a step can't be completed as written, note the issue and adapt if the intent is clear
   - If you're unsure how to proceed, ask the user rather than guessing
   - **Emit a `deviation` event** when adapting a step, OR when the work diverges from explicit plan text at all, even a trivial functionally-identical substitution (e.g. re-sourcing an import) that the plan's Deviations section would list: `python scripts/implement-trajectory-log.py --event --run-id {run_id} --type deviation --step N --reason "<why>" --what-changed "<vs plan>"`. A notes-only `step_end status: ok` is NOT sufficient for a plan-text divergence.

**Wave execution mode:**

When the plan has `### Wave` headers, execute waves in order with parallel-agent dispatch, `wave_start`/`wave_end` bracketing (`wave_start` before the wave's first `step_start`; `wave_end` after all member `step_end`s), the sequential-ordering invariant inside single-step waves, and whole-wave-deferral handling (one wave-scoped `deviation` keyed to the wave's first step, no `wave_start`/`wave_end`). The full wave procedure — the parallel sub-agent brief contents, the `wave_end.successes` reconciliation rule, and the deferred-wave `deviation` form — is in **`references/implement-details.md`**. Read it when a plan actually contains waves; most runs are sequential and never load it.

---

### Phase 3: Validate

1. **Run through the Validation Checklist** from the plan
   - Check off each item
   - Note any that fail
   - **For each check** (skip if `--no-trajectory`): `python scripts/implement-trajectory-log.py --event --run-id {run_id} --type validation_check --check "<name>" --passed|--failed --detail "<one-line>"`.

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
   - Jordanum 3 rework cycles. If still NEEDS REWORK after 3 cycles, report remaining issues to the user.
4. **If grade is FAIL**: Stop and report the full evaluation to the user. Do not attempt automatic rework on FAIL grades.
5. **Track iteration count** in the Implementation Notes section.
6. **For each evaluation** (skip if `--no-trajectory`): `python scripts/implement-trajectory-log.py --event --run-id {run_id} --type evaluation_result --artefact "<path>" --grade "PASS|PASS WITH NOTES|NEEDS REWORK|FAIL" --iteration <n>`.

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

**Final emission** (skip if `--no-trajectory`): `python scripts/implement-trajectory-log.py --event --run-id {run_id} --type run_end --summary "<one-line>" --plan-status Implemented` (`run_id` and `trajectory_path` auto-fill).

**Self-check** (skip if `--no-trajectory`): `python scripts/implement-trajectory-log.py --verify --run-id {run_id}`. This is **advisory** — it NEVER hard-fails a completed run (CEO sovereignty holds, matching the soft pre-impl gate). But if it exits non-zero, the reported defects MUST be surfaced **verbatim, as a named defect list**, in the Report's Deviations section — not glossed as an optional footnote. A non-zero self-check that is not surfaced verbatim is itself a discipline violation. Run this self-check BEFORE any `git commit` / `git pull`: the run-level files reconciliation compares the current engine working tree against `run_start.git_head`, so it is only meaningful while the tree holds just this run's changes (re-running it after a merge/pull over-flags pulled files — expected, advisory, not a regression). No temp files are written anymore, so there is no `_tmp/` cleanup step.

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
- Never work around the emit-time sequencing guard (exit `5`) by reordering or dropping markers to silence it - it means a `step_end`/`step_start` is genuinely out of order; emit the missing marker (or open a parallel `wave_start`) instead
- Never gloss a non-zero `--verify` self-check - its defects MUST be surfaced verbatim as a named list in the Report Deviations (advisory does not mean optional-to-report)
- Never call `scripts/implement-trajectory-log.py` with `--data-json` from inside `/implement` - that mode is bash-only / hand-runs only. Prefer the typed flags (`--step`, `--file`, `--status`, ...) for every event; `--data-file` / `--data-stdin` remain the escape hatch for a genuinely arbitrary payload
- Never write to `outputs/operations/implement/_trajectory_*.jsonl` directly - only through the helper script (it handles atomic-append concurrency on POSIX + Windows)
