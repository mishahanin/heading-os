# Adaptive --relentless - /scrutinize Loop Mechanics

**Consumed by:** `.claude/skills/scrutinize/SKILL.md` (Relentless Mode section)
**Last Updated:** 2026-05-27

Adaptive termination + verbal-memory ledger for `--relentless` runs. Closes R8 from the 2026-05-27 meta-review of /scrutinize.

The v1.2 `--relentless` mode used a hard 5-iteration cap. Self-Refine (Madaan et al. 2023) and Reflexion (Shinn et al. 2023) showed adaptive termination + verbal memory of past reflections outperforms fixed caps. Easy targets terminate in 2 iterations; hard targets get up to 10. The cap is raised to 10 because mechanical exits handle most cases earlier.

## Termination criteria (first to trigger wins)

| # | Condition | Reason code |
|---|---|---|
| 1 | Two consecutive iterations with zero findings | `two-zero` (clean exit, unchanged from v1.2) |
| 2 | Two consecutive iterations where the model emits `improvement_marginal: true` | `marginal-twice` (new in v2.0) |
| 3 | Hard cap: 10 iterations (raised from 5) | `hard-cap` |
| 4 | Post-apply check failure | `check-failure` |
| 5 | Verbal memory detects a fix-revert oscillation cycle (same fix applied then undone within 3 iterations) | `oscillation` (new in v2.0) |

The previous 5-iteration cap is raised to 10. With adaptive termination active, hard-cap rarely fires - the typical termination path is `two-zero` or `marginal-twice` well before 5 iterations.

## The `improvement_marginal` signal

After each Phase 1 pass (whether findings are non-zero or zero), the model emits a structured judgement:

```json
{
  "iteration": 3,
  "findings_count": 2,
  "fixes_applied_prev_iter": 4,
  "improvement_marginal": true,
  "marginal_reasoning": "Remaining findings are stylistic. Last iteration's
  fixes already addressed the structural issues. Further iterations will
  produce diminishing returns."
}
```

The model is the same one running the scrutinize pass. It self-reports based on observation of the iteration log (verbal memory ledger, see below). The signal is reliable when grounded in concrete observations; spurious when the model is told to "just decide" without context.

**`improvement_marginal: true` does NOT terminate immediately.** It needs to fire twice in a row (criterion 2). Single occurrences happen on intermediate iterations where the next pass might surface something new.

## Verbal memory ledger

Across iterations, the skill maintains a verbal memory ledger - a structured log carried forward to each new iteration's prompt. The ledger contains:

| Field | Description |
|---|---|
| `iteration_log` | Per-iteration: findings count by severity, fixes applied, fixes deferred, files touched, hidden-char scans run |
| `fix_history` | Per file: every change applied this run, in order (path, brief description, iteration N) |
| `recurring_findings` | Findings that appear in two or more iterations (potential oscillation signal) |
| `stable_files` | Files that have not received fixes in the last 2 iterations |

The ledger is passed back into the next iteration's Phase 1 prompt under a "Memory from prior iterations" section. The new pass reads it and can reference: "Last iteration we tightened the error handling on line 47; iteration before that, we expanded the docstring; the structural fix is settled - remaining findings should be voice-level only."

This is the Reflexion-style verbal reflection adapted to /scrutinize. It costs ~500-1500 tokens per iteration of prompt overhead - cheap compared to the multi-thousand-token cost of redoing scrutiny work that the model already settled in a prior pass.

## Oscillation detection

When the verbal memory's `fix_history` shows a file being changed in opposing directions across iterations (e.g., a line was rewritten three iterations ago, then reverted two iterations ago, then rewritten this iteration), the skill detects an oscillation and terminates immediately with reason code `oscillation`.

Detection rule: if any file has 3+ fixes applied in the same run AND the net diff (current state vs pre-relentless state) is smaller than the cumulative diff (sum of all applied fixes), oscillation is likely. The skill prints the file path, the iterations involved, and the cumulative-vs-net diff sizes, then exits.

Why this matters: oscillation usually signals contradictory rules or two findings whose fixes conflict. Continuing to loop costs tokens without converging.

## Per-iteration prompt structure

Each iteration's Phase 1 prompt has three new sections compared to the v1.2 single-pass prompt:

```text
## Memory from prior iterations

{verbal memory ledger - findings by iteration, fixes by file, recurring findings}

## Stability hint

The following files have not received fixes in the last 2 iterations
(they are likely stable):
{list of files}

## Improvement-marginal judgement

After completing Phase 1 of this iteration, emit structured judgement:
  improvement_marginal: bool
  marginal_reasoning: <2-sentence explanation>

This judgement contributes to the termination decision. Be honest:
if remaining findings are real and impactful, mark false. If they are
stylistic tail or unlikely to surface new structural issues, mark true.
```

## Termination message format

Updated termination messages per reason code:

- `Relentless mode: CLEAN. Two consecutive zero-findings iterations. (Iterations: N)`
- `Relentless mode: MARGINAL. Improvement reached diminishing returns. (Iterations: N)`
- `Relentless mode: CAPPED at 10 iterations. <M> findings remain - see report.`
- `Relentless mode: HALTED on post-apply check failure at iteration <n>. See failure details above.`
- `Relentless mode: OSCILLATION. File {path} received conflicting fixes across iterations {i1, i2, i3}. Halting before further churn.`

## What stays unchanged from v1.2

- Pre-approval of fixes (no per-finding approval block during the loop)
- Sequential apply per iteration with post-apply checks (sanitize-text, py_compile, frontmatter)
- Incompatibility with `target=plan` (plans are conversational, not file-editable)
- Per-iteration close line printing `applied / deferred / remaining` counts
- One consolidated report saved at the standard path with cumulative applied-fix log

## What is incompatible with `--relentless` (unchanged)

- `target=plan` - hard incompatibility, skill refuses with the v1.2 error message
- Phase 4.5 eval-case promotion - relentless pre-approves fixes but promotion is a separate CEO decision per case
