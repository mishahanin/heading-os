---
name: scrutinize
disable-model-invocation: true
description: >
  Ultrathink principal-engineer review gate. Runs a Validate-Identify-Improve-Adjust
  (VIIA) pass over a target - a plan awaiting approval, just-executed work, a specific
  file or directory, or the entire workspace - then runs an adversarial refutation
  layer (Phase 2.5) with cross-family judge rotation (Claude / Gemini / Grok) and
  optional two-agent debate on BLOCKER + HIGH findings before presenting findings
  with concrete proposed fixes for batched approval. Blocks forward progress until
  approved.
  Triggers on "scrutinize", "stress-test this", "principal review", "validate and
  improve", "review the plan before I approve", "audit what you just did",
  "ultrathink review". Do NOT trigger for artifact grading alone (use /evaluate),
  fact-checking drafts (use /validate), or strategic reasoning (use /deep-think).
  Usage: /scrutinize [plan | execution | file:<path> | dir:<path> | workspace | trajectory:<run_id>] [--relentless] [--no-refute] [--include-low-confidence] [--include-ambiguous]
argument-hint: "[plan | execution | file:<path> | dir:<path> | workspace | trajectory:<run_id>] [--relentless] [--no-refute]"
allowed-tools: "Read, Glob, Grep, Bash(python3:*), Bash(python:*), Bash(git:*), Edit, Write, Agent"
context: fork
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "2.2"
x-31c-orchestration:
  parallel_safe: partial
  shared_state:
    - outputs/operations/scrutiny/
    - "any file approved for fix-apply (unknown in advance)"
  triggers:
    - scrutinize
    - stress-test this
    - principal review
    - validate and improve
    - review the plan before I approve
    - audit what you just did
    - ultrathink review
x-31c-capability:
  what: >
    Maximum-effort principal-engineer review gate over a target - a plan, just-executed work, a file/dir, the whole workspace, or a past /implement trajectory - producing evidence-backed findings with confidence scores and concrete proposed fixes, after an adversarial refutation layer drops false positives. Blocks forward progress until approved.
  how: >
    Explicit-invocation only (disable-model-invocation). Run /scrutinize [plan | execution | file:<path> | dir:<path> | workspace | trajectory:<run_id>] [--relentless] [--no-refute]. Reports save to outputs/operations/scrutiny/.
  when: >
    Use to stress-test a plan before approval or audit changes after /implement. For artifact grading against a rubric use /evaluate; for draft fact-checking use /validate; for decision reasoning use /deep-think.
---
# Scrutinize

Manually-invoked quality gate. Runs a maximum-effort VIIA pass (Validate - Identify - Improve - Adjust) over a target, applies an adversarial refutation layer that filters false positives, produces findings with concrete fixes and per-finding confidence scores, and blocks forward progress until the user explicitly approves the fix batch. Principal-engineer posture: find what is wrong, not what works. Every finding requires evidence. Every BLOCKER and HIGH survives an adversarial debate before reaching the approval block. No shortcut exits. Version history (v2.0 refutation layer, v2.1 trajectory target, v2.2 inline-budget refactor): `references/version-history.md`.

---

## Variables

- `target` (optional) - Explicit target: `plan` | `execution` | `file:<path>` | `dir:<path>` | `workspace` | `trajectory:<run_id>`. If omitted, auto-detect per `references/target-detection.md`.
- `--relentless` (optional flag) - Auto-apply-and-loop mode with adaptive termination per `references/relentless-adaptive.md`. Pre-approves all proposed fixes, applies them, then re-runs Phase 1 on the same target. Loops until terminated. Not compatible with `target=plan`.
- `--no-refute` (optional flag) - Skip Phase 2.5 (refutation + debate). Findings emit directly to the approval block with scorer-emitted confidence only. Use for quick passes when the cost of debate is not worth it. Recorded in the saved report.
- `--include-low-confidence` (optional flag) - Show findings with confidence below the threshold (default 75) in the approval block. Default behaviour hides them but logs them in the saved report.
- `--include-ambiguous` (optional flag) - Surface AMBIGUOUS debate verdicts to the approval block with an `[AMBIGUOUS]` tag for CEO adjudication. Default behaviour drops them.

---

## When to Engage

Manual invocation only. This skill does NOT auto-trigger from natural-language conversation.

**Use:** before approving a structural/high-stakes plan from `/create-plan`; after `/implement` to audit changes against the plan; on a file/dir when something feels off; periodically on the whole workspace to catch drift, rule conflicts, and stale docs.

**Do NOT use:** artifact grading against a fixed rubric (use `/evaluate`); fact-checking a draft against DataStore (use `/validate`); strategic reasoning on a decision (use `/deep-think`); content-quality review of a post/email/deliverable (use `/evaluate` or `/validate`).

---

## Phase 0 - Context Loading

1. **Load all reference files** under `.claude/skills/scrutinize/references/`: `severity-grid.md` (severity + confidence rubric), `target-detection.md`, `viia-framework.md` (subchecks), `workspace-areas.md` (workspace target), `eval-case-template.md` (Phase 4.5), `refutation-protocol.md` + `bias-mitigation.md` (Phase 2.5), `relentless-adaptive.md` (--relentless), `observability.md` (Langfuse), `trajectory-evaluation.md` (trajectory target).

2. **Load applicable rules:** `.claude/rules/{development-standards,hidden-chars,security,classification,voice}.md`.

3. **Resolve target** per `references/target-detection.md`:
   - Parse explicit argument from the user's invocation (text passed alongside `/scrutinize` or the natural-language trigger) if present.
   - Else apply priority order: plan > execution (git) > menu.
   - Print confirmation line for priorities 2-4. Wait for user response.
   - If redirected, use the new target.

4. **Resolve scope** per target:
   - Plan: extract the most recent plan text from conversation.
   - Execution: git status + session commits (see references/target-detection.md for the full resolver).
   - File: read the file.
   - Dir: glob the dir with standard exclusions.
   - Workspace: no scope loading here - Phase 2 dispatches specialists.

5. **Open Langfuse trace** (skipped in vault mode or when `LANGFUSE_ENABLED=false`). Trace tags per `references/observability.md`. Trace ID is appended to the final saved report.

6. **Optional - prime the Identify pass with named methods.** For a hard/unfamiliar target, pull 2-5 critique methods from the shared catalog (`python scripts/elicit.py list --category risk|core`, then `show "<Method>"`; e.g. Pre-mortem, Inversion, Assumption Audit) to structure the VIIA Identify stage. Composes with — does not replace — Phase 2.5. Skip when viia-framework subchecks already cover the target. Catalog: `reference/elicitation-methods.md`.

---

## Phase 1 - VIIA Pass (non-workspace targets)

For targets `plan`, `execution`, `file`, `dir`:

Apply the four phases from `references/viia-framework.md`:

**For target type `trajectory:<run_id>`** — dispatch the trajectory lens defined in `references/trajectory-evaluation.md` instead of the file/dir VIIA lens. The universal subchecks (1-9) are re-interpreted for the sequential-decision shape; subchecks 10-14 default to `N/A (out of scope)` unless the trajectory's `step_end` events list files matching content/comms/doctype patterns. Deterministic tool-call records win over rationale prose when they disagree.

1. **Validate** - Run all 14 subchecks (9 universal + 5 workspace-specific compliance gates per the target-to-subcheck map). No shortcut exits.
2. **Identify** - Adversarial pass. Each finding gets ID, Severity, **Confidence (0-100)**, Location, Statement, Evidence.
3. **Improve** - Concrete proposed fix for every `BLOCKER`, `HIGH`, `MEDIUM`. Optional for `LOW`/`NIT` if cheap.
4. (Phase 4 - Adjust - happens in Phase 3 of this skill, after user approval.)

For any `SKILL.md`, `scripts/*.py`, rule, or reference file in scope: call `python3 scripts/artifact-evaluator.py --path <file> --json` first to pick up deterministic findings, then add qualitative VIIA layer on top.

**Sentinel execution targets:** when the execution target includes `scripts/sentinel.py` or any file under `tests/integration/`, additionally run `python3 scripts/run-integration-tests.py --quiet --no-cov`. Treat any test failure as an automatic `HIGH` finding (or `BLOCKER` if the failure indicates a regression in daemon crash-safety).

Engage maximum reasoning effort throughout. Ultrathink.

---

## Phase 2 - Parallel Dispatch (workspace target only)

For `target = workspace`:

1. Dispatch 5 specialist agents per `references/workspace-areas.md` in a single message (background, `run_in_background: true`), using the agent brief template from that reference.
2. Announce: `"Dispatching 5 parallel specialist agents for workspace scrutiny. Areas: code surface, governance, documentation, knowledge & data, operations state."`
3. Wait for all 5 to complete.
4. Handle degradation per `workspace-areas.md`: if any agent fails, flag that area as partial and continue.
5. **Fallback when Agent tool is unavailable**: If Agent dispatch is not exposed in the current thread, skip parallel dispatch and run the 5 area passes sequentially in the main session, each using the same lens and brief from `references/workspace-areas.md`. Note the serialized execution in the approval block header.
6. Run synthesis phase per `workspace-areas.md`: cross-area rule-vs-skill conflicts, CLAUDE.md drift, documentation drift, classification coherence, skill-router completeness.
7. Consolidate findings into the format for Phase 2.5.

---

## Phase 2.5 - Adversarial Refutation Layer

Inserted in v2.0. Full protocol in `references/refutation-protocol.md`. Bias mitigations in `references/bias-mitigation.md`.

**Skip entirely when:**

- `--no-refute` flag is set
- `target = plan` (refutation has poor grip on conversational text)
- Phase 1/2 emitted zero BLOCKER/HIGH/MEDIUM findings
- Vault mode active AND cross-family rotation not available
- The user just typed `--include-low-confidence` and only LOW/NIT findings exist

Announce the skip explicitly in the approval block header.

Run the two sub-passes per the full briefs, dispatch commands, and confidence-adjustment
tables in `references/refutation-protocol.md` (judge-family rotation in `bias-mitigation.md`):

- **Phase 2.5a — single-pass refutation (BLOCKER + HIGH + MEDIUM):** one refutation agent
  per finding, family rotated per index, calls parallelised in a single Agent message.
  Outcomes: `REFUTATION_FAILED` → proceed (confidence +5..+15); `REFUTED` → DROP (log in
  "Refuted Findings"); `REFUTE_PARTIAL` → proceed (confidence −10..−25, severity −1 tier).
- **Phase 2.5b — two-agent debate (BLOCKER + HIGH survivors only):** Advocate + Skeptic
  from different families, position-randomised into a third-family Meta-Judge. Verdict
  `CORRECT` (≥75) → proceed with Meta-Judge score; `INCORRECT` (<60) → DROP; `AMBIGUOUS`
  (60–74) → DROP unless `--include-ambiguous`.

**Logging (mandatory):** every Phase 2.5 pass appends a `## Judge layer` section to the
saved Phase 5 report — family per call, swap bit, Meta-Judge verdicts — for the
human-agreement benchmark.

---

## Phase 3 - Approval Block

**If `--relentless` is active:** SKIP this phase. Every finding with a concrete proposed fix is treated as approved. Findings without a proposed fix are marked `deferred` for the iteration report. Proceed directly to Phase 4.

Produce the approval block inline. Do NOT apply any change before user approval.

**Format:**

```text
## /scrutinize - <target-label>
Grade: <PASS | PASS-WITH-NOTES | NEEDS-REWORK | BLOCKED>
Target: <plan | execution | file:path | dir:path | workspace | trajectory:<run_id>>
Findings: N BLOCKER, N HIGH, N MEDIUM, N LOW, N NIT (above threshold)
Refutation: <2.5a-only | 2.5a+2.5b | skipped:<reason>>
Judge rotation: <rotate | fixed-claude | overridden>

### Findings (severity-sorted; workspace target: also grouped by area, then cross-area)

[B1] (conf: 92) <one-line statement>
  Location: <file:line | plan step N | area>
  Evidence: <quote / reference>
  Proposed fix: <concrete patch or rewrite>

[H1] (conf: 88) ... [M1] (conf: 78) ...

### Approval

Reply with one of:
- "approve all"           apply every proposed fix
- "approve <ids>"         e.g., "approve B1, H1, H3" (comma-separated IDs)
- "reject all"            produce no changes, end pass
- "revise <id>: <note>"   rework a specific fix with the note, re-present, re-ask
- "skip <ids>"            approve everything except the listed IDs
- "flag-as-fp <ids>"      mark findings as false positives (logged for FP-rate
                          calibration via scripts/scrutinize-flag-fp.py; can be
                          combined with approve/skip on different IDs in same reply)
```

If Grade is `BLOCKED`: also print exactly one line after the approval block: `"Forward progress halted pending approval."`

If there are no findings, print the header, the Grade line, and `No findings. No approval required.` - skip the Findings section and the Approval section entirely.

**Confidence threshold:** by default, only findings with confidence >= 75 appear in the approval block. Findings below threshold are logged in the saved report under a `## Findings Below Threshold` section. Pass `--include-low-confidence` to show all.

**Approval semantics (strict):**

- Only explicit `approve` / `reject` / `revise` / `skip` / `flag-as-fp` commands act. Silence, ambiguity, or "looks good" - WAIT.
- `approve all` on a workspace target still applies changes sequentially, one area at a time, with a one-line per-area confirmation.
- `revise <id>: <note>` - rework that single finding's fix using the note, re-present just the revised fix, re-ask for approval on it. No revise-cycle limit.
- `skip <ids>` / partial `approve` - findings not named are marked `deferred` in the saved report. Deferred = not applied, not lost.
- `flag-as-fp <ids>` - calls `python scripts/scrutinize-flag-fp.py --scrutiny-id <stem> --ids <ids> --notes "<optional CEO note>"`. The CEO can combine this with other commands in the same reply (e.g. `"approve B1, flag-as-fp H2, skip M1"`).

---

## Phase 4 - Apply Approved Fixes (sequential)

For each approved finding, in order:

1. Apply the fix using `Edit` or `Write`.
2. Run post-apply checks for the edited file:
   - **Hidden-chars:** `python3 scripts/sanitize-text.py <file> --scan`
   - **Python syntax:** `python3 -m py_compile <file>` (only for `.py`)
   - **Frontmatter:** YAML parse + required fields (`name`, `description`, `metadata.author`, `metadata.version`, plus `x-31c-orchestration.parallel_safe`, `x-31c-orchestration.shared_state`, `x-31c-orchestration.triggers`) - only for `SKILL.md`
3. If all checks pass, print `"[OK] <file> - applied and checks passed."`
4. If any check fails, halt further applies. Print the failure, ask the user whether to continue or rollback.

For each `flag-as-fp <ids>` command (can run before/after/alongside approves):

1. Call `python scripts/scrutinize-flag-fp.py --scrutiny-id <stem> --ids <ids> --notes <note>` to append FP records.
2. Call `python scripts/scrutinize-fp-aggregate.py` to refresh `_fp_aggregate.md`.
3. Print one line: `"Flagged N as FP. Aggregate refreshed."`

For `workspace` target: apply per area, with one-line confirmation per area completion.

**In `--relentless` mode:** Use the adaptive termination + verbal memory ledger from `references/relentless-adaptive.md`. Track `improvement_marginal` per iteration, detect fix-revert oscillation, terminate on first of {two-zero, marginal-twice, hard-cap (10), check-failure, oscillation}.

---

## Phase 4.5 - Eval-Case Promotion (single-pass only, CEO-gated)

> Full eligibility rules, draft-case generation pattern, auto-scaffold workflow, target-type artefact shapes (skill JSON / script pytest / rule YAML), and the approval-block format all live in `references/eval-case-template.md`. Read that file before proposing any promotion.

**Skip this phase entirely when:** `--relentless` is active; `target = plan`; or no applied finding qualifies per the eligibility rules in `eval-case-template.md`.

**Flow (per `eval-case-template.md`):** filter Phase 4 applied findings to qualifiers → offer auto-scaffold if the target lacks `evals/cases/` (R5; CEO commands `scaffold and promote all|<ids>`, `scaffold only`, `skip`) → build a draft case per the target-type shape → present the approval block with every draft inline → **wait for explicit `promote all|<ids>` / `skip all` / `revise <id>`** (silent-write-forbidden; "looks good" means WAIT) → on promote, write + run `sanitize-text.py --scan` and shape validation (JSON/py_compile/YAML), halting on failure → record the outcome for Phase 5.

**Announcement:** when at least one candidate exists, print before the approval block: `"Phase 4.5: <N> finding(s) eligible for eval-case promotion. CEO approval required per case."`

---

## Phase 5 - Report Persistence (tiered)

| Target | Action |
|---|---|
| `plan` | Inline output only. Save nothing. |
| `execution` | Save to `outputs/operations/scrutiny/YYYY-MM-DD-execution.md` |
| `file:<path>` | Save to `outputs/operations/scrutiny/YYYY-MM-DD-<slug>.md` |
| `dir:<path>` | Save to `outputs/operations/scrutiny/YYYY-MM-DD-<slug>.md` |
| `workspace` | Save to `outputs/operations/scrutiny/YYYY-MM-DD-workspace.md` |
| `trajectory:<run_id>` | Save to `outputs/operations/scrutiny/YYYY-MM-DD-trajectory-<run_id-slug>.md` |

**Slug derivation:** `execution` - no slug; `file:path` - last path segment without extension; `dir:path` - last dir segment; `workspace` - literal `workspace`; `trajectory:<run_id>` - the run_id's own slug (the part after the final `_`).

**Saved report sections** (single-pass 10-section layout) and the **`--relentless` consolidated-report shape** are defined in `references/report-format.md`. The `Write` tool auto-creates `outputs/operations/scrutiny/` on first save.

---

## Voice

- Findings are statements, not suggestions. `"Line 47 uses os.path instead of pathlib"` - NOT `"Consider using pathlib"`.
- Use hyphens (`-`), never double dashes (`--`).
- Respect 31C terminology per `.claude/rules/terminology.md` (ODUN.ONE, DPI+, Tribe).
- Be direct. The burden of proof is on the target, not the reviewer.

---

## NEVER

- Never apply a fix without explicit user approval of that specific fix (or `approve all`)
- Never short-circuit Phase 1 subchecks after finding an early issue - every required subcheck runs (universal 1-9 always, workspace-specific 10-14 when target-applicable)
- Never emit a finding without evidence (quote or reference)
- Never emit a finding without a confidence score
- Never grade `PASS` if any `BLOCKER` or `HIGH` exists in the above-threshold set
- Never write to `_secure/` from outside the vault
- Never dispatch more than 5 parallel agents per concurrency cap
- Never auto-commit after apply - the user decides whether to commit
- Never invoke `/scrutinize` recursively
- Never exceed 10 iterations in `--relentless` mode (raised from 5 with adaptive termination)
- Never roll back applied fixes in `--relentless` mode on post-apply check failure - halt and surface
- Never skip post-apply checks in `--relentless` mode
- Never run `--relentless` against `target=plan`
- Never auto-create the `evals/cases/` directory - offer auto-scaffold as a CEO-gated step (`scaffold and promote ...`, `scaffold only`, or `skip`)
- Never overwrite an existing eval-case JSON; always pick the next free `case-{N}-{slug}.json`
- Never emit a script regression test with a real assertion the CEO has not approved
- Never cross-feed Gemini's refutation to Grok's debate or vice versa (each agent reasons independently)
- Never skip the `Judge Layer` section in the saved report when Phase 2.5 ran (audit trail required for human-agreement benchmark)
- Never run cross-family rotation in vault mode - fall back to `SCRUTINIZE_JUDGE_ROTATION=fixed-claude` and surface the degradation in the approval block header
- Never silently disable Langfuse observability - the saved report's Observability footer must always state whether it was on, off (by env var), or disabled (vault)
