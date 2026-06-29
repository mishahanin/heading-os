---
name: pre-impl
description: >
  Pre-implementation gate: recommended structured checkpoint between plan approval and /implement.
  Use before any non-trivial implementation: combines success-criteria definition, an inline
  contrarian stress-test (the /devil discipline), an optional /council architecture review with
  Kimi as devil's advocate, harness gap audit, and test-contract writing. Produces a GO/NO-GO
  artifact with a ready-to-paste /implement prompt. Skip for trivial one-liner fixes, typo
  corrections, and config-only changes.
argument-hint: "[plan description or @path/to/plan.md]"
allowed-tools: "Read, Write, Bash(python3:*), Skill"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: false
  shared_state: []
  triggers: ["pre-implementation gate", "gate before implement", "are we ready to implement", "before we implement", "stress-test plan before building", "pre-impl check"]
x-31c-capability:
  what: >
    6-phase gate before /implement: success criteria, /devil critique, /council architecture
    review (Kimi as devil's advocate), harness audit, test contract, GO/NO-GO decision.
  how: >
    /pre-impl [plan description or @plans/YYYY-MM-DD-slug.md]. Runs an inline /devil-style
    critique and an optional /council review. Saves artifact alongside the plan it gates at
    plans/YYYY-MM-DD-pre-impl-{slug}.md (via get_plans_dir()).
  when: >
    After /create-plan approval, before /implement, for any non-trivial work. Skip for
    one-liner fixes and trivial config changes. Full chain: /create-plan -> /pre-impl -> /implement -> /scrutinize.
---

# Pre-Implementation Gate

**The full implementation chain: `/create-plan` → approve plan → `/pre-impl` → GO → `/implement` → `/scrutinize`**

Embodies the core principle from "The New SDLC With Vibe Coding" (Osmani, Saboo, Kartakis, May 2026): tests and success criteria come BEFORE code generation. The gate runs an inline contrarian critique (the `/devil` discipline) and an optional `/council` review to stress-test the plan from distinct angles before a single line of code is written.

This gate is **recommended, not harness-enforced** — nothing blocks `/implement` if it is skipped. The chain `/create-plan → /pre-impl → /implement → /scrutinize` is a discipline, not a lock. `/implement` runs a soft pre-Phase-0 reminder (`scripts/check-preimpl-gate.py`) that warns when no `pre-impl` artifact exists for the plan and asks whether to proceed — it never blocks.

## NEVER

- NEVER proceed to `/implement` from within this skill — this skill produces a handoff prompt, not the implementation.
- NEVER skip a phase — each phase gates the next.
- NEVER fabricate success criteria. Derive from plan or ask.
- NEVER send external communication.
- NEVER write to CRM, threads, or shared operational state.

---

## Phase 0 — Context Load

1. Identify the plan source:
   - If argument is a file path → Read it in full.
   - If argument is a description → use as-is.
   - If no argument → look for the most recent `plans/YYYY-MM-DD-*.md` in this session. If none found, ask Misha: "Describe what we're building in one paragraph."

2. Extract these four points (ask if any are missing):
   - **What**: one-sentence description of what is being built
   - **Why**: the business or operational motivation
   - **Scope**: which files, systems, or services will be touched
   - **Constraints**: deadlines, non-negotiable dependencies, hard limits

3. Output a "Context block" with the four points confirmed.

---

## Phase 1 — Success Criteria

Write 3-5 measurable, binary, testable success criteria (SC).

Rules for each SC:
- **Testable**: a test or eval can verify it — no "the code is clean" or "it feels better"
- **Binary**: yes/no, not "improved" or "faster"
- **Specific**: names the exact behavior, output metric, or system state

Include:
- At least one happy-path criterion
- At least one failure-mode criterion
- At least one integration criterion

Format:
```
SC-1 [happy-path]: ...
SC-2 [edge-case]: ...
SC-3 [failure-mode]: ...
SC-4 [integration]: ...
SC-5 [observability, if relevant]: ...
```

If Misha included success criteria in the plan, restate them here and flag as "(from plan)". If derived, flag as "(derived — confirm)".

---

## Phase 2 — Devil's Critique (inline)

`/devil` carries the harness flag `disable-model-invocation: true`, so it CANNOT be invoked from this skill — not by natural language, not via the Skill tool, not by chaining. Only an explicit user-typed `/devil` fires it. This is a hard block, not a convention. (Contrast Phase 3's `/council`, which has no such flag and IS a genuine call.) So run the critique **inline here**, applying the `/devil` discipline directly — the same way `/scrutinize` runs its own critique rather than chaining the locked skill.

Produce 5 contrarian critique points against the plan summary from Phase 0. Each point:
- Attacks from a **distinct angle** (correctness, scope, cost, timing, alternatives, second-order effects)
- Carries a **severity tag**: `BLOCKER` / `HIGH` / `MEDIUM` / `LOW`
- Is a committed paragraph, not a hedge

**Honesty floor:** if fewer than 5 defensible angles exist, stop early rather than fabricate weak points. Note "Plan passed the inline devil check with limited attack surface (N points)."

For each point, then assign:
- **Disposition**: `MUST FIX BEFORE` / `MONITOR DURING` / `ACCEPTABLE RISK`
- **Remediation**: one concrete action

(If Misha wants a fully independent pass, he can run `/devil 5: <summary>` himself and paste the result — but the gate does not depend on it.)

---

## Phase 3 — Architecture Council (Kimi as devil's advocate, optional)

Unlike `/devil`, `/council` is NOT `disable-model-invocation` — it is a **genuine call** (real Kimi / Gemini / Grok voices, not an inline imitation). The one catch is `context: fork`: `/council` reasons in an isolated context, so its output does NOT flow back into this skill's context. It DOES persist its synthesized result to disk under `outputs/operations/council/`. So this phase is a **handoff-and-read**: invoke `/council`, then Read its artifact back before synthesizing — the fork is about context isolation, not a block on invocation. This phase is optional — skip it for small, low-architectural-risk plans and note "Architecture council skipped (low architectural risk)."

1. Invoke `/council` with this framing:
   ```
   Using /council for architecture review — Kimi as devil's advocate.
   ```
   Frame the council question as:
   > "Architecture stress-test for: [what + why + scope from Phase 0]. Kimi: be the devil's advocate — what architectural assumption is wrong, what is most likely to break at scale or under failure, what have we not considered? Claude and Gemini: confirm or refute Kimi's concerns."

2. After `/council` completes, Read its latest artifact from `outputs/operations/council/` (the council skill writes there per its `shared_state`). If no artifact is found, treat council as unavailable and use the fallback below.

3. Synthesize the council artifact into:
   - **Architectural risks** — things that will bite later
   - **Confirmed concerns** — flagged by ≥2 council members
   - **Dissenting view** — if council disagrees, name the disagreement

Fallback (if `/council` is unavailable or wrote no artifact): run an inline architecture pass — "Name the 3 most likely architectural failure modes of this plan and how to mitigate them." (`/deep-think` may be invoked explicitly if deeper reasoning is warranted.)

---

## Phase 4 — Harness Audit

Review what the `/implement` agent will need. Check each item:

| Check | Question | Finding |
|---|---|---|
| Rules | Is there a `.claude/rules/` file covering this domain? | ✓ exists / ⚠ missing / — not needed |
| Skills | Will `/implement` need to invoke another skill? Does it exist with clear instructions? | ✓ ready / ⚠ gap |
| Tools | Are all required tools available? (API keys in `.env`, scripts exist, deps installed) | ✓ verified / ⚠ unverified |
| Guardrails | Are existing hooks/checks covering the risky operations this plan touches? | ✓ covered / ⚠ gap |
| Context | Will context fill up mid-implementation? Estimate: [LOW / MEDIUM / HIGH] | → recommend chunking if HIGH |

For each ⚠: state whether it must be resolved BEFORE `/implement` or can be monitored during.

---

## Phase 5 — Test Contract

Write the test suite that proves the implementation correct. This becomes the CONTRACT for `/implement`.

Format:
```
TEST-1 [happy-path]: Given [input], expect [output/state]. Verify: [how].
TEST-2 [edge-case]: Given [boundary input], expect [graceful handling]. Verify: [how].
TEST-3 [failure-mode]: When [external failure], system does [Y], not [Z]. Verify: [how].
TEST-4 [integration]: End-to-end: [trigger] → [process] → [expected final state]. Verify: [how].
TEST-5 [performance, if relevant]: [Operation] completes within [T]s for [N] items. Verify: [how].
```

For Python scripts: write the tests as `assert` statements in a throwaway harness block, **clearly labelled "PROPOSED TEST CONTRACT — CEO-UNAPPROVED DRAFT"**. The label matters: a downstream `/scrutinize` or `/implement` must treat these as a contract draft, not as sanctioned regression tests (the `/scrutinize` guardrail forbids emitting unapproved assertion-bearing regression tests). The CEO approves the contract at the gate decision; `/implement` then promotes the approved tests into the real suite.
For skills/rules: write as behavioral evals ("Given prompt X, output must contain Y and must not contain Z").

Close with: "Implementation is DONE when TEST-1 through TEST-N all pass AND /scrutinize reports no new findings."

---

## Phase 6 — Gate Decision

**GO** if ALL:
- Every `MUST FIX BEFORE` item from Phase 2 is resolved or explicitly accepted by Misha
- No critical (⚠ BEFORE) harness gaps from Phase 4
- Success criteria specific enough for Phase 5 tests

**NO-GO** if ANY:
- A `MUST FIX BEFORE` critique from Phase 2 is unresolved
- A critical harness gap exists
- Success criteria too vague for meaningful tests

**Output block:**

```
═══════════════════════════════════════════════
PRE-IMPL GATE: [GO ✓ | NO-GO ✗]
═══════════════════════════════════════════════

Before /implement:
□ [action — from Phase 2 / Phase 4]
□ [action]

During /implement (watch):
• [risk from Phase 2 / Phase 3]
• [risk]

After /implement (verify):
→ Run tests: TEST-1, TEST-2, TEST-3 ...
→ Run: /scrutinize execution

═══════════════════════════════════════════════
HANDOFF TO /implement:
═══════════════════════════════════════════════
[Ready-to-paste /implement prompt: one paragraph with
 the updated plan + embedded success criteria + test contract.]
```

---

## Output Artifact

Save the full gate report (all 6 phases) **alongside the plan it gates**, in the plans directory, following the locked plans naming convention `{YYYY-MM-DD}-{slug}.md` (see `output-naming.md` and the Plans Lifecycle in `documentation.md`):

```
plans/YYYY-MM-DD-pre-impl-{slug}.md
```

Resolve the plans directory via the data-root helper rather than hardcoding — `python3 -c "import sys; sys.path.insert(0,'scripts'); from utils.workspace import get_plans_dir; print(get_plans_dir())"` — then Write under that path. (The plans dir resolves under the data overlay, not the engine tree.) Keeping the gate report in `plans/` puts it beside the plan it gates and lets it participate in the plans-lifecycle archival flow (`plans/archive/{YYYY}/`).

Confirm with:
> "Gate complete. [GO ✓ | NO-GO ✗]. Artifact: [full path]. Before proceeding: [# blocking items]."

---

## Voice & Terminology

This gate produces internal engineering prose, not outbound communication — keep it terse and concrete. Still observe the workspace floor:

- Never use `--` (two ASCII hyphens) as punctuation; use a single em-dash or restructure. Real em-dashes (`—`), en-dashes, and curly quotes are fine.
- Use 31C terminology exactly: **ODUN.ONE** (not "Odun" / "ODUN ONE"), **DPI+**, **Tribe** (never "team"/"crew"), **TrustONE**.
- No hidden Unicode characters in the artifact.
- Success criteria and test contracts are factual claims — never fabricate a metric, threshold, or behavior. Derive from the plan or ask Misha.
