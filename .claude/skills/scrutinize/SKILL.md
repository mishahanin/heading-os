---
name: scrutinize
disable-model-invocation: true
description: >
  Ultrathink principal-engineer review gate. Runs a Validate-Identify-Improve-Adjust
  (VIIA) pass over a target - a plan awaiting approval, just-executed work, a specific
  file or directory, or the entire workspace - then runs an adversarial refutation
  layer (Phase 2.5) with cross-family judge rotation (Claude and Kimi k3) and
  optional two-agent debate on BLOCKER + HIGH findings before presenting findings
  with concrete proposed fixes for batched approval. Blocks forward progress until
  approved.
  Triggers on "scrutinize", "stress-test this", "principal review", "validate and
  improve", "review the plan before I approve", "audit what you just did",
  "ultrathink review". Do NOT trigger for artifact grading alone (use /evaluate),
  fact-checking drafts (use /validate), or strategic reasoning (use /deep-think).
argument-hint: "[plan | execution | file:<path> | dir:<path> | workspace | trajectory:<run_id>] [--relentless] [--no-refute]"
allowed-tools: "Read, Glob, Grep, Bash(python3:*), Bash(python:*), Bash(git:*), Edit, Write, Agent"
context: fork
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "2.3"
x-heading-orchestration:
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
x-heading-capability:
  what: >
    Maximum-effort review gate over a plan, executed work, a file or dir, the workspace, or a past /implement trajectory. Produces evidence-backed findings with confidence scores and proposed fixes, after an adversarial layer drops false positives. Blocks progress until approved.
  how: >
    Explicit-invocation only (disable-model-invocation). Run /scrutinize [plan | execution | file:<path> | dir:<path> | workspace | trajectory:<run_id>] [--relentless] [--no-refute]. Reports save to outputs/operations/scrutiny/.
  when: >
    Use to stress-test a plan before approval or audit changes after /implement. For artifact grading against a rubric use /evaluate; for draft fact-checking use /validate; for decision reasoning use /deep-think.
x-heading-routing:
  category: Operations
  label: /scrutinize [target] [--relentless] [--no-refute] [--include-low-confidence] [--include-ambiguous]
  triggers:
    - NEVER auto-trigger. Explicit `/scrutinize [target] ...` only.
  exclusions:
    - Artifact grading only -> /evaluate
    - fact-check drafts -> /validate
    - reasoning on a decision -> /deep-think
  compound: 'No'
  router: manual
---
# Scrutinize

Manually-invoked quality gate. Runs a maximum-effort VIIA pass (Validate - Identify - Improve - Adjust) over a target, applies an adversarial refutation layer that filters false positives, produces findings with concrete fixes and per-finding confidence scores, and blocks forward progress until the user explicitly approves the fix batch. Principal-engineer posture: find what is wrong, not what works. Every finding requires evidence; every BLOCKER and HIGH survives an adversarial debate before reaching the approval block; no shortcut exits. Version history (v2.0-v2.2): `references/version-history.md`.

---

## Variables

- `target` (optional) - `plan` | `execution` | `file:<path>` | `dir:<path>` | `workspace` | `trajectory:<run_id>`; if omitted, auto-detect per `references/target-detection.md`.
- `--relentless` (flag) - auto-apply-and-loop with adaptive termination per `references/relentless-adaptive.md`: pre-approves all proposed fixes, applies them, re-runs Phase 1 on the same target, loops until terminated. Not compatible with `target=plan`.
- `--no-refute` (flag) - skip Phase 2.5 (refutation + debate); findings emit directly to the approval block with scorer-emitted confidence only. Recorded in the saved report.
- `--include-low-confidence` (flag) - show findings below the confidence threshold (default 75) in the approval block; default hides them but logs them in the saved report.
- `--include-ambiguous` (flag) - surface AMBIGUOUS debate verdicts with an `[AMBIGUOUS]` tag for CEO adjudication; default drops them.
- `--no-code-review` (flag) - skip the Kimi code-specialist voice on code targets (see Phase 1 + `references/code-review-voice.md`); default runs it on code.

---

## When to Engage

Manual invocation only. **Use** before approving a high-stakes plan, after `/implement` to audit changes against it, on a file or dir when something feels off, or periodically on the workspace to catch drift. **Do NOT use** for artifact grading (`/evaluate`), draft fact-checking (`/validate`), decision reasoning (`/deep-think`), or content-quality review of a post or email (`/evaluate`).

---

## Phase 0 - Context Loading

1. **Load every reference file** under `.claude/skills/scrutinize/references/`. The directive is the directory, not a list: each file states what consumes it.

2. **Load applicable rules:** `.claude/rules/{development-standards,hidden-chars,security,classification,voice}.md`.

3. **Resolve target** per `references/target-detection.md`: parse an explicit argument from the invocation if present; else apply priority order plan > execution (git) > menu, printing a confirmation line for priorities 2-4 and waiting for the user (use the new target if redirected).

4. **Resolve scope** per target:
   - Plan: extract the most recent plan text from conversation.
   - Execution: git status + session commits (see references/target-detection.md for the full resolver).
   - File: read the file.
   - Dir: glob the dir with standard exclusions.
   - Workspace: no scope loading here - Phase 2 dispatches specialists.

5. **Open the run record:** `python scripts/scrutinize-dispatch.py --pass-start --run-id <id> --target <t>`. A pass with no `pass_start` row fails `--validate`.

6. **Open Langfuse trace** (skipped in vault mode or when `LANGFUSE_ENABLED=false`); tags per `references/observability.md`; trace ID appended to the saved report.

7. **Optional - prime the Identify pass with named methods.** For a hard/unfamiliar target, pull 2-5 critique methods (Pre-mortem, Inversion, Assumption Audit, ...) from `reference/elicitation-methods.md` via `python scripts/elicit.py list --category risk|core` then `show "<Method>"`, to structure the VIIA Identify stage. Composes with — does not replace — Phase 2.5; skip when viia-framework subchecks already cover the target.

---

## Phase 1 - VIIA Pass (non-workspace targets)

For targets `plan`, `execution`, `file`, `dir`:

Apply the four phases from `references/viia-framework.md`:

**For target type `trajectory:<run_id>`** — dispatch the trajectory lens in `references/trajectory-evaluation.md` instead of the file/dir VIIA lens: universal subchecks 1-9 re-interpreted for the sequential-decision shape; subchecks 10-14 default to `N/A (out of scope)` unless `step_end` events list content/comms/doctype files. Deterministic tool-call records win over rationale prose on disagreement.

1. **Validate** - run all 14 subchecks (9 universal + 5 workspace-specific compliance gates per the target-to-subcheck map); no shortcut exits.
2. **Identify** - adversarial pass; each finding gets ID, Severity, **Confidence (0-100)**, Location, Statement, Evidence.
3. **Improve** - concrete proposed fix for every `BLOCKER`/`HIGH`/`MEDIUM`, optional for `LOW`/`NIT` if cheap.
4. (Adjust happens in Phase 3, after user approval.)

For any `SKILL.md`, `scripts/*.py`, rule, or reference file in scope: call `python3 scripts/artifact-evaluator.py --path <file> --json` first to pick up deterministic findings, then add qualitative VIIA layer on top.

**Code targets - external code voice (auto-on unless `--no-code-review`):** on a code `file:`/`dir:`, or an `execution` whose diff touches code, dispatch the external code voice in parallel with Identify. Its candidates merge into the Identify set, de-duped, then face Phase 2.5 like any other. Skip silently for `plan`/`workspace`/`trajectory` and when the proxy is unavailable (note it, never fail the pass). Firing rules, dispatch, merge and logging: `references/code-review-voice.md`.

**Role lenses (auto, by path trigger):** `python scripts/scrutinize-dispatch.py --role-scan --paths <scope>` fires the ops, scheduler and boundary checklists whose globs match; each writes a `role` row. A lens finding citing neither its commands nor its artifacts is not a lens finding. Full taxonomies: `references/role-lenses.md`.

**Currency (code scopes):** `--currency --paths <scope>` checks each third-party import's pinned version against the latest known, writing `ok`/`mismatch`/`inconclusive`. A mismatch whose behaviour changed is a BLOCKER. It never fails the pass.

**Sentinel execution targets:** when the target includes `scripts/sentinel.py` or any `tests/integration/` file, also run `python3 scripts/run-integration-tests.py --quiet --no-cov`; treat any failure as an automatic `HIGH` (or `BLOCKER` if it is a daemon crash-safety regression).

Engage maximum reasoning effort throughout. Ultrathink.

---

## Phase 2 - Parallel Dispatch (workspace target only)

For `target = workspace`: dispatch the 5 area specialists in one background message using the brief in `references/workspace-areas.md`; announce it; wait for all 5. A failed area is flagged partial, not fatal; with no Agent tool, run them sequentially and say so in the header. Then synthesise across areas and consolidate for Phase 2.5. Brief, areas, degradation rules and how the role lenses compose with them: `references/workspace-areas.md`.

---

## Phase 2.5 - Adversarial Refutation Layer

Inserted in v2.0. Full protocol in `references/refutation-protocol.md`. Bias mitigations in `references/bias-mitigation.md`.

**Skip entirely when:**

- `--no-refute` flag is set
- `target = plan` (refutation has poor grip on conversational text)
- Phase 1/2 emitted zero BLOCKER/HIGH/MEDIUM findings
- `SENSITIVE_MODE` active AND cross-family rotation not available
- The user just typed `--include-low-confidence` and only LOW/NIT findings exist

Announce the skip explicitly in the approval block header.

Run the two sub-passes — **2.5a** single-pass refutation (BLOCKER + HIGH + MEDIUM) then **2.5b** two-agent debate (BLOCKER + HIGH survivors only) — per the full briefs, dispatch commands, outcome rules (REFUTED/REFUTE_PARTIAL/REFUTATION_FAILED; CORRECT[_DOWNGRADE]/INCORRECT/AMBIGUOUS), and confidence-adjustment tables in `references/refutation-protocol.md` (judge-family rotation in `bias-mitigation.md`).

**Every judge call goes through `scripts/scrutinize-dispatch.py --judge`**, never by hand: it assigns the family (Skeptic and Meta-Judge never share one), gates the external side on a DECLARED sensitive session, and writes the verdict row. A reproduction outranks a debate: `--reproduce` runs the command and records the exit, and only the harness may write `REPRODUCED`/`FALSIFIED`.

**Logging (mandatory):** every Phase 2.5 pass appends a `## Judge layer` section to the
saved Phase 5 report — family per call, swap bit, Meta-Judge verdicts — for the
human-agreement benchmark.

---

## Phase 3 - Approval Block

**If `--relentless` is active:** SKIP this phase — every finding with a concrete proposed fix is treated as approved; findings without one are marked `deferred` for the iteration report. Proceed directly to Phase 4.

Produce the approval block inline per the exact layout in `references/approval-block.md` (loaded in Phase 0). Do NOT apply any change before user approval.

- **Grade** is one of `PASS | PASS-WITH-NOTES | NEEDS-REWORK | BLOCKED`. The header MUST carry the `Refutation:` line: it is the one signal `--validate` reconciles the record against.
- **Only explicit commands act.** Silence, ambiguity, or "looks good" means WAIT.
- **Confidence threshold 75** by default; `--include-low-confidence` shows the rest.
- Block layout, every per-command semantic, the no-findings and BLOCKED lines: `references/approval-block.md`.

---

## Phase 4 - Apply Approved Fixes (sequential)

For each approved finding, in order:

1. Apply the fix using `Edit` or `Write`.
2. Run post-apply checks on the edited file: hidden-chars (`python3 scripts/sanitize-text.py <file> --scan`); Python syntax (`python3 -m py_compile <file>`, `.py` only); frontmatter YAML parse + required fields (`SKILL.md` only, per `scripts/skill-metadata-check.py`).
3. If all checks pass, print `"[OK] <file> - applied and checks passed."`
4. If any check fails, halt further applies. Print the failure, ask the user whether to continue or rollback.

For each `flag-as-fp <ids>`: `python scripts/scrutinize-flag-fp.py --scrutiny-id <stem> --ids <ids> --notes <note>`, which writes the flag into the run record beside the verdicts it disagrees with. Print `"Flagged N as FP."`

For `workspace` target: apply per area, with one-line confirmation per area completion.

**In `--relentless` mode:** use the adaptive termination + verbal memory ledger from `references/relentless-adaptive.md`; track `improvement_marginal` per iteration, detect fix-revert oscillation, terminate on first of {two-zero, marginal-twice, hard-cap (10), check-failure, oscillation}.

---

## Phase 4.5 - Eval-Case Promotion (single-pass only, CEO-gated)

Full eligibility rules, draft-case generation, auto-scaffold workflow, target-type artefact shapes (skill JSON / script pytest / rule YAML), and the approval-block format live in `references/eval-case-template.md`; read it before proposing any promotion.

**Skip entirely when:** `--relentless` is active; `target = plan`; or no applied finding qualifies.

**Flow:** filter Phase 4 applied findings to qualifiers → offer CEO-gated auto-scaffold if the target lacks `evals/cases/` (`scaffold and promote all|<ids>`, `scaffold only`, `skip`) → build a draft case per the target-type shape → present the approval block with every draft inline and **wait for explicit `promote all|<ids>` / `skip all` / `revise <id>`** (silent-write-forbidden; "looks good" means WAIT) → on promote, write + `sanitize-text.py --scan` + shape validation (JSON/py_compile/YAML), halting on failure → record for Phase 5. When ≥1 candidate exists, announce before the block: `"Phase 4.5: <N> finding(s) eligible for eval-case promotion. CEO approval required per case."`

---

## Phase 5 - Report Persistence (tiered)

`plan` — inline output only, save nothing. Every other target saves to `outputs/operations/scrutiny/YYYY-MM-DD-<slug>.md` (the `Write` tool auto-creates the dir): slug is `execution` and `workspace` literally, `file:<path>` → last path segment without extension, `dir:<path>` → last dir segment, `trajectory:<run_id>` → `trajectory-<run_id-slug>` (the part after the final `_`).

After saving, run `python scripts/scrutinize-record.py --validate --run-id <id> --report <path>` and surface any defect verbatim. It sees omission, not intent: the Claude-side verdict is still session-supplied.

**Saved report sections** (single-pass 10-section layout) and the **`--relentless` consolidated-report shape** are defined in `references/report-format.md`.

---

## Voice

- Findings are statements, not suggestions: `"Line 47 uses os.path instead of pathlib"`, not `"Consider using pathlib"`.
- Use hyphens (`-`), never double dashes (`--`). Respect 31C terminology per `.claude/rules/terminology.md` (ODUN.ONE, DPI+, Tribe). Be direct - the burden of proof is on the target, not the reviewer.

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
- Never cross-feed one judge's refutation to another's debate (each agent reasons independently)
- Never skip the `Judge Layer` section in the saved report when Phase 2.5 ran (audit trail required for human-agreement benchmark)
- Never run every judge on Claude without naming the cause in `Judge Layer`
- Never dispatch the external judge when a session is DECLARED sensitive (`sensitivity_is_declared()`, never `is_sensitive()`, whose unset default would disable half the roster) - fall back to 2.5a on Claude, write the `degraded` row, surface the cause in the header
- Never pin a Claude model version anywhere in this skill - that judge IS the running session, so it is always the latest Opus; `tests/test_scrutinize_no_model_pins.py` fails on a literal
- Never write a verdict, role or currency claim into a report without its row in the record
- Never write `REPRODUCED` or `FALSIFIED` from a narrated command - the harness observes both exit codes or neither verdict exists
- Never silently disable Langfuse observability - the saved report's Observability footer must always state whether it was on, off (by env var), or disabled (`SENSITIVE_MODE`)
