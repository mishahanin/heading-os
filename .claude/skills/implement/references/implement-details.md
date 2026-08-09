# Implement — wave execution mechanics & version history

The full wave-execution procedure and the per-version emission-discipline changelog for the `/implement` skill, moved out of `SKILL.md` to keep it under the size budget. Read the wave section on demand — it matters only when a plan has `### Wave` headers (most runs are sequential and never load this file); the version history is reference-only.

Consumed by: `.claude/skills/implement/SKILL.md`

Last Updated: 2026-07-08

## Wave execution mode

Execute waves in order. For each wave:

1. **Parse all steps** (`####` headings) within the wave.

2. **Emit `wave_start` event** (skip if `--no-trajectory`): `python scripts/implement-trajectory-log.py --event --run-id {run_id} --type wave_start --wave N --step-count <count> --parallel|--no-parallel`. **`wave_start` MUST be emitted before the wave's first `step_start`** — never after a member step has already opened. A step whose `step_start`/`step_end` fall outside its wave's brackets cannot be reconstructed from the bracketed record (the L1 emission defect).

3. **If the wave is marked `(parallel)` AND has 2+ steps:**
   - Announce: "Executing Wave N: {count} parallel tasks"
   - Dispatch each step as an independent agent using `superpowers:dispatching-parallel-agents`
   - **Each child sub-agent brief MUST include**: (a) the `run_id`, (b) the path `scripts/implement-trajectory-log.py`, (c) instructions to emit its own `step_start` / `step_end` / `deviation` events via the typed flags (never `--data-json`), (d) a reminder that the JSONL is shared state and the helper handles atomic-append concurrency.
   - Each agent receives: the step's full text (not a file reference), relevant context files, and instructions to commit its work atomically
   - Wait for ALL agents in the wave to complete
   - Collect results (successes and failures)
   - If ANY step failed: report ALL results (successes and failures), STOP, and ask the user how to proceed. Do not advance to the next wave.

4. **If the wave is NOT marked `(parallel)` OR has only 1 step:**
   - Execute the steps sequentially using the standard rules above (including the per-step `step_start` / `step_end` emission).
   - **Sequential ordering invariant:** emit each step's `step_end` *before* the next step's `step_start`. Never open step N+1 while step N's bracket is still open, and never let a later step's `step_end` land after `wave_end`. If the work genuinely reorders (a step finishes out of declared sequence), emit a `deviation` event recording the swap — do not let the markers interleave silently (the M1 emission defect).

5. **After a wave completes successfully:**
   - Verify all files mentioned in the wave's steps exist
   - **Emit `wave_end` event** (skip if `--no-trajectory`): `python scripts/implement-trajectory-log.py --event --run-id {run_id} --type wave_end --wave N --successes <count> --failures <count>`. Emit `wave_end` only *after* every member step's `step_end` has been emitted. `successes` MUST equal the number of `step_end` events with `status` ok/deviation that fall inside this wave's `wave_start`/`wave_end` brackets — not the plan's declared membership count. The two must reconcile from the bracketed record alone.
   - Report: "Wave N complete: {count} tasks done"
   - Proceed to the next wave

6. **If an entire wave is deferred — not executed at all this run** (deferred to a focused session, blocked, or descoped): do NOT emit `wave_start`/`wave_end` for it (no steps ran, so there would be no bracketed `step_end`s to reconcile against). Instead emit exactly ONE **wave-scoped `deviation` event keyed to the wave's first step number**, so a whole-wave skip is a first-class structured record rather than only a `run_end` summary string (the L1 trajectory-audit defect, 2026-06-16). Skip if `--no-trajectory`. Emit: `python scripts/implement-trajectory-log.py --event --run-id {run_id} --type deviation --step <first step number of the deferred wave> --scope wave --wave N --reason "<why deferred>" --what-changed "wave N (steps X-Y) deferred, not executed this run"`. The `run_end` summary should still mention the deferral, but the wave-scoped deviation event is the authoritative structured record. (A per-step deviation per skipped step is also acceptable when only part of a wave is deferred; the `scope: "wave"` form is for the whole-wave case.)

## Version history

The current, authoritative statement of the emission invariants is the **Trajectory emission contract** in `SKILL.md`. These per-version notes are retained for changelog provenance.

**v1.8 (2026-08-09):** `--verify` stops being blind to the bracketing defects it was supposed to catch, after `/scrutinize trajectory:2026-08-08_215406_impeccable-detector-integration` found a run in which three of five declared waves ran with no bracket at all and the self-check reported one defect out of five. Four changes: an ORPHAN `wave_end` now still reconciles its `successes` claim, against the implicit bracket since the last wave boundary (it used to `continue` past the check, so the claim was compared to nothing); a run that uses waves at all is now flagged for any `step_end` outside every bracket, step 0 exempt; a `wave_start` whose payload omits `step_count`/`parallel` is flagged advisory, since the wave's declared shape is otherwise unrecoverable; and a backwards `timestamp` is flagged advisory as clock or emission skew, never as a sequencing fault. `cmd_verify` also prints the files reconciliation's scope on every run — it covers the ENGINE tree only, so a data-overlay write can never be flagged, and a clean line must not be read as full coverage. Reconciling the overlay too was rejected: its unrelated background churn would bury real findings. Emission contract, schema, and the `/scrutinize` lens are unchanged; the fixes are all on the verification side.

**v1.7 (2026-07-08):** The helper now enforces the sequencing invariant at emit time — a `step_start` opened while another step is open (outside a parallel wave), or a `step_end` for an unopened step, is REJECTED with exit `5`; emit the missing marker first (or open a parallel `wave_start` for legitimate interleaving). `--verify` gains a run-level files reconciliation: any engine file changed since `run_start.git_head` but recorded in no step's `files_affected` is flagged as an advisory defect (meaningful only immediately after the run, before any commit / `git pull`; degrades to a no-op when git is unavailable). Phase 5 now MUST surface a non-zero `--verify` verbatim in the Report Deviations (still advisory — no hard-fail). Schema and `/scrutinize` lens unchanged.

**v1.6 (2026-07-07):** Emission now uses typed flags on the helper (one `Bash` call per event, no temp file, no `_tmp/` cleanup): `--step`, `--title`, `--file` (repeatable), `--status`, `--wave`, `--successes`, `--check`, `--passed`/`--failed`, and the rest. `--data-file`/`--data-stdin` remain the escape hatch for an arbitrary payload; `--data-json` is still forbidden from `/implement`. After `run_end`, Phase 5 runs `--verify` to self-check the trajectory (advisory). The **Trajectory emission contract** in `SKILL.md` is the single consolidated statement of the invariants that the prior per-version notes (v1.3 emission, v1.4 M1/L1/N1 pairing and literal-path discipline, v1.5 whole-wave deferral) built up; the event schema and the `/scrutinize` lens are unchanged.

**v1.3-v1.5 (2026-06):** earlier emission-discipline milestones now consolidated into the Trajectory emission contract in `SKILL.md`: v1.3 introduced structured per-phase emission; v1.4 added the M1/L1/N1 pairing and literal-path discipline; v1.5 added whole-wave deferral. The contract in `SKILL.md` is authoritative; these version numbers are retained for provenance.
