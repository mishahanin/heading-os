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
    Jordanum-effort principal-engineer review gate over a target - a plan, just-executed work, a file/dir, the whole workspace, or a past /implement trajectory - producing evidence-backed findings with confidence scores and concrete proposed fixes, after an adversarial refutation layer drops false positives. Blocks forward progress until approved.
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
- `--no-code-review` (flag) - skip the Kimi-Code code-specialist voice on code targets (see Phase 1 + `references/code-review-voice.md`); default runs it on code.

---

## When to Engage

Manual invocation only; does NOT auto-trigger from conversation. **Use** before approving a structural/high-stakes plan from `/create-plan`, after `/implement` to audit changes against the plan, on a file/dir when something feels off, or periodically on the whole workspace to catch drift, rule conflicts, and stale docs. **Do NOT use** for artifact grading against a rubric (`/evaluate`), draft fact-checking against DataStore (`/validate`), strategic reasoning on a decision (`/deep-think`), or content-quality review of a post/email/deliverable (`/evaluate` or `/validate`).

---

## Phase 0 - Context Loading

1. **Load all reference files** under `.claude/skills/scrutinize/references/`: `severity-grid.md` (severity + confidence rubric), `target-detection.md`, `viia-framework.md` (subchecks), `workspace-areas.md` (workspace target), `eval-case-template.md` (Phase 4.5), `refutation-protocol.md` + `bias-mitigation.md` (Phase 2.5), `approval-block.md` (Phase 3 block format + strict semantics), `relentless-adaptive.md` (--relentless), `observability.md` (Langfuse), `trajectory-evaluation.md` (trajectory target).

2. **Load applicable rules:** `.claude/rules/{development-standards,hidden-chars,security,classification,voice}.md`.

3. **Resolve target** per `references/target-detection.md`: parse an explicit argument from the invocation if present; else apply priority order plan > execution (git) > menu, printing a confirmation line for priorities 2-4 and waiting for the user (use the new target if redirected).

4. **Resolve scope** per target:
   - Plan: extract the most recent plan text from conversation.
   - Execution: git status + session commits (see references/target-detection.md for the full resolver).
   - File: read the file.
   - Dir: glob the dir with standard exclusions.
   - Workspace: no scope loading here - Phase 2 dispatches specialists.

5. **Open Langfuse trace** (skipped in vault mode or when `LANGFUSE_ENABLED=false`); tags per `references/observability.md`; trace ID appended to the saved report.

6. **Optional - prime the Identify pass with named methods.** For a hard/unfamiliar target, pull 2-5 critique methods (Pre-mortem, Inversion, Assumption Audit, ...) from `reference/elicitation-methods.md` via `python scripts/elicit.py list --category risk|core` then `show "<Method>"`, to structure the VIIA Identify stage. Composes with — does not replace — Phase 2.5; skip when viia-framework subchecks already cover the target.

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

**Code targets — Kimi-Code + GLM specialist voices (auto-on unless `--no-code-review` / `--no-glm-review`):** when the target is code (a `file:`/`dir:` of code, or an `execution` whose diff touches code), dispatch the external code voice(s) in parallel with the Identify pass — independent non-Claude code-specialist reads (GLM's 1M context takes large targets whole). Their candidate findings merge into the Identify set (de-duped against Claude's and each other) and then face Phase 2.5 refutation like any other. Full firing rules, dispatch commands, merge/attribution, and `## Judge layer` logging: `references/code-review-voice.md`. Skip silently for `plan`/`workspace`/`trajectory`, and when ollama/a code-voice tag is unavailable (note it in the header, never fail the pass).

**Sentinel execution targets:** when the target includes `scripts/sentinel.py` or any `tests/integration/` file, also run `python3 scripts/run-integration-tests.py --quiet --no-cov`; treat any failure as an automatic `HIGH` (or `BLOCKER` if it is a daemon crash-safety regression).

Engage maximum reasoning effort throughout. Ultrathink.

---

## Phase 2 - Parallel Dispatch (workspace target only)

For `target = workspace`: dispatch 5 specialist agents (code surface, governance, documentation, knowledge & data, operations state) in a single background message (`run_in_background: true`) using the brief template in `references/workspace-areas.md`; announce the dispatch; wait for all 5. On any agent failure, flag that area partial and continue. If the Agent tool is unavailable, run the 5 area passes sequentially in the main session and note the serialized execution in the approval-block header. Then run the synthesis phase (cross-area rule-vs-skill conflicts, CLAUDE.md drift, documentation drift, classification coherence, skill-router completeness) and consolidate findings for Phase 2.5. Full brief + degradation rules: `references/workspace-areas.md`.

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

Run the two sub-passes — **2.5a** single-pass refutation (BLOCKER + HIGH + MEDIUM) then **2.5b** two-agent debate (BLOCKER + HIGH survivors only) — per the full briefs, dispatch commands, outcome rules (REFUTED/REFUTE_PARTIAL/REFUTATION_FAILED; CORRECT/INCORRECT/AMBIGUOUS), and confidence-adjustment tables in `references/refutation-protocol.md` (judge-family rotation in `bias-mitigation.md`).

**Logging (mandatory):** every Phase 2.5 pass appends a `## Judge layer` section to the
saved Phase 5 report — family per call, swap bit, Meta-Judge verdicts — for the
human-agreement benchmark.

---

## Phase 3 - Approval Block

**If `--relentless` is active:** SKIP this phase — every finding with a concrete proposed fix is treated as approved; findings without one are marked `deferred` for the iteration report. Proceed directly to Phase 4.

Produce the approval block inline per the exact layout in `references/approval-block.md` (loaded in Phase 0). Do NOT apply any change before user approval.

- **Grade** is one of `PASS | PASS-WITH-NOTES | NEEDS-REWORK | BLOCKED`. Header carries Target, Findings counts, Refutation state, Judge rotation; each finding shows `[id] (conf: N)`, Location, Evidence, Proposed fix.
- **Approval commands:** `approve all` / `approve <ids>` / `reject all` / `revise <id>: <note>` / `skip <ids>` / `flag-as-fp <ids>`. Only explicit commands act — silence, ambiguity, or "looks good" means WAIT. `skip`/partial-approve marks unnamed findings `deferred` (not applied, not lost). `approve all` on a workspace target still applies one area at a time. Full block layout + strict per-command semantics: `references/approval-block.md`.
- If Grade is `BLOCKED`: print exactly one line after the block: `"Forward progress halted pending approval."`
- If there are no findings: print the header, the Grade line, and `No findings. No approval required.` — skip the Findings and Approval sections.
- **Confidence threshold:** by default only findings with confidence >= 75 appear; below-threshold findings are logged in the saved report under `## Findings Below Threshold`. `--include-low-confidence` shows all.

---

## Phase 4 - Apply Approved Fixes (sequential)

For each approved finding, in order:

1. Apply the fix using `Edit` or `Write`.
2. Run post-apply checks on the edited file: hidden-chars (`python3 scripts/sanitize-text.py <file> --scan`); Python syntax (`python3 -m py_compile <file>`, `.py` only); frontmatter YAML parse + required fields (`SKILL.md` only, per `scripts/skill-metadata-check.py`).
3. If all checks pass, print `"[OK] <file> - applied and checks passed."`
4. If any check fails, halt further applies. Print the failure, ask the user whether to continue or rollback.

For each `flag-as-fp <ids>` command (before/after/alongside approves): call `python scripts/scrutinize-flag-fp.py --scrutiny-id <stem> --ids <ids> --notes <note>`, then `python scripts/scrutinize-fp-aggregate.py` to refresh `_fp_aggregate.md`, then print `"Flagged N as FP. Aggregate refreshed."`

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
- Never cross-feed Gemini's refutation to Grok's debate or vice versa (each agent reasons independently)
- Never skip the `Judge Layer` section in the saved report when Phase 2.5 ran (audit trail required for human-agreement benchmark)
- Never run cross-family rotation in vault mode - fall back to `SCRUTINIZE_JUDGE_ROTATION=fixed-claude` and surface the degradation in the approval block header
- Never silently disable Langfuse observability - the saved report's Observability footer must always state whether it was on, off (by env var), or disabled (vault)
