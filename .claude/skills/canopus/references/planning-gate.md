# The planning gate - steps 1 to 6 of a Canopus slice

Consumed by: `.claude/skills/canopus/SKILL.md` (`/canopus plan`)
Last Updated: 2026-08-02

The body below is the former `/pre-impl` skill, MOVED and not rewritten. It was
the only place the planning half of the standard was written down in full, and
a rewrite would have quietly dropped what it had learned the hard way. Read
every `/pre-impl` in the prose below as `/canopus plan`, and its six phases as
steps 1 to 6 of the thirteen.

**The full chain:** `/canopus plan` -> Approval 1 (step 6) -> `/canopus lock`
(step 7) -> build (step 8) -> `/canopus check` (steps 9 to 11) -> Approval 2
(step 12) -> `/canopus release` (step 13).


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

**Write each one in the EARS shape:** `WHEN <trigger>, THE SYSTEM SHALL
<response>`. Not for its own sake — one trigger and one response is what makes a
criterion bindable to ONE test. A compound criterion holding two triggers cannot
be, and Phase 5 will refuse it. Exceeding the 3-5 band is the cheaper error when
the alternative is folding two claims into one line.

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

## Phase 5 — Test Contract (real files, not prose)

The contract is written as **real test files**, not as a draft in this document:
a prose contract and the tests that later decide whether the work is done are two
artifacts joined by good intentions.

Write them to `tests/contract/{YYYY-MM-DD}-{slug}/test_*.py`.

**One authoring rule, and it is enforced:** import the code under test INSIDE the
test body, never at module scope. The implementation does not exist yet, so a
module-scope import stops the file collecting, and a file that collects nothing
cannot be frozen.

```python
def test_frozen_contract_records_a_baseline():
    from scripts.utils.canopus_freeze import build_manifest   # inside the body

    assert build_manifest(...)["baseline"] == {"tests/contract/s/test_a.py": 7}
```

**A second authoring rule, earned by two measured failures rather than by
taste.** A contract test must not couple itself to the environment it runs in.
Both failures were invisible until the build, and both cost a `/canopus back`:

- 2026-08-02, `canopus-skill`: two tests described the between-slices state and
  ran against the ENGINE root, which carried their own slice's lock. They could
  never be green while frozen.
- 2026-08-02, `gate-yield`: a test compared raw stderr from two runs in two
  different roots, so it compared where each ran rather than what each did.

The rule those two give: **a test that reads working-tree state takes its own
scratch root, and a test that compares two runs compares the INVARIANT, never
the raw text.** Ask it of every test at step 4, while changing it is still free.
After the freeze it costs a window, a re-approval and a re-attestation.

**A third rule, from the same slice, and the cheapest of the three.** Before
freezing, run the commit gates against the contract file itself:

    pre-commit run --files tests/contract/{YYYY-MM-DD}-{slug}/test_contract.py

Measured 2026-08-02, `gate-yield`: a test variable named `secret` tripped
detect-secrets' keyword heuristic. The value was assembled by concatenation
exactly as the workspace requires; the NAME was the problem. Nobody found out
until the slice was built, attested, and being committed, at which point the
whole thing was uncommittable and the only sanctioned way out was a window --
because a baseline entry, a pragma and `--no-verify` are all forbidden here, and
correctly so. One command at step 4 would have cost nothing.

**A fourth rule, and this is the one the machine enforces.** Every criterion from
Phase 1 must be claimed in the DOCSTRING of at least one contract test, and no
docstring may claim a criterion Phase 1 never stated. **The claim OPENS the
docstring** — `"""SC-2. What this decides."""` — and anything deeper in the prose
claims nothing, so a test can describe what it is testing without binding to
every identifier it names. `approve` and `freeze` refuse a contract that leaves a
criterion unclaimed or claims one out of nowhere. Check it before either command:

    python scripts/sc-trace.py --anchor <gate artifact> --contract tests/contract/{YYYY-MM-DD}-{slug}/

Measured 2026-08-02, which is why this exists: the `gate-yield` artifact stated
seven criteria, its contract carried 28 tests, and five of the seven were
traceable to nothing at all. Read the guarantee narrowly — it proves a test
CLAIMS to decide a criterion, never that it does. A green trace does not excuse
reading the tests.

**A fifth rule, and it is the one that would have caught the worst defect any of
these slices shipped.** A fixture must be able to produce the shape the REAL
source produces. Before freezing, check every fabricated input against a real
sample: a line from the live log, an actual file on disk, a genuine API
response. Not the shape the code expects, and not the shape the docstring
describes — the shape the source actually emits.

Measured 2026-08-02, `gate-yield`: a 28-test frozen contract failed to catch
that the report could not parse the denial log's timestamps at all, because
EVERY fixture in it stamped an ISO string and no real denial record has ever
carried one — the log writes `time.time()` floats. The mismatch was untestable
by construction. Worse than a gap: the failure was silent, since an unparsed
stamp answers `None` and `None` renders as a 0-day window rather than as an
error, so the report's central verdict was unreachable for half its inputs and
nothing said so. It was found by running the shipped tool against the live logs,
which is the check this rule turns into a habit.

A test whose fixture cannot produce the real shape is green and proves nothing
about the real shape.

Then show what the contract looks like before any code exists:

    python scripts/canopus.py probe tests/contract/{YYYY-MM-DD}-{slug}/

Paste the table into the gate output. Three groups need naming, and each asserts
nothing yet: every `passed` test is already green with no implementation, every
`vacuous` one is red only because the code is absent and passes against a mock,
and every `skipped` one never ran (nothing refuses a skipped contract test, so it
is yours to catch here). Strengthen or justify all three. A
contract whose every red test is vacuous is refused at approve and freeze time,
and a skipped or `xfail` test does not buy it a pass. `vacuity was NOT measured`
means nothing was proved either way; say so, it is not a clean bill.

Close with: "Implementation is DONE when the frozen contract is green and
`canopus status` reports both LOCK HELD and ATTESTED, AND /scrutinize reports no
new findings."

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

### On approval: lock on Canopus

The moment the operator approves, run `approve`:

    python scripts/canopus.py approve \
      --label "{slug}" \
      --anchor {this gate artifact's absolute path in the data overlay} \
      --contract tests/contract/{YYYY-MM-DD}-{slug}/ \
      --content scripts/utils/canopus_freeze.py \
      --content scripts/utils/canopus_gate.py \
      --content scripts/utils/canopus_tree.py \
      --content scripts/utils/canopus_git.py \
      --content scripts/utils/atomic.py \
      --content scripts/utils/colors.py \
      --content scripts/utils/production_shape.py \
      --content scripts/utils/venv.py \
      --content scripts/run-tests.py \
      --content tests/conftest.py

Read the already-green COUNT it prints (the per-test table is `probe`'s), then
COMMIT the gate artifact. That commit is Fix 1: it carries an author and a
timestamp and is the only thing making the approval durable. Then re-run the
identical command with `freeze` in place of `approve` (`python
scripts/canopus.py freeze --label ... --contract ...`, same flags), which takes
the lock. Confirm with `python scripts/canopus.py verify`: LOCK HELD and
APPROVED.

Ten files, not four, because the gate's own import tail is inside the
guarantee. `tests/test_canopus_freeze.py::test_the_documented_enforcer_set_covers_its_import_closure`
recomputes that tail against THIS file and fails when a new import escapes the
documented set. It read `SKILL.md` until 2026-08-06, when the skill moved to the
seven steps and stopped documenting `freeze` at all; the property is unchanged,
only the document carrying the command moved. `freeze` refuses a contract that is not red for a reason that means
something, and a root the COMMITTED artifact contradicts. Both refusals, with
their exceptions, are in `references/canopus-gate.md` — read it before the first
retake. Three rules there bite mid-slice:

- **A release names its kind:** `--window` while the slice runs, `--ship` when it
  is over. Passing neither exits 2. An open window makes every pytest session start
  print an amber line saying no lock is held, so a green suite proves nothing.
- **A retake of a contract the slice has legitimately turned green needs
  `--contract-satisfied "<why>"`** on BOTH `approve` and `freeze`. It waives only
  the redness refusal, the reason is mandatory, and it lands in the committed
  artifact on a `canopus-contract-satisfied:` line — grep for THAT, not for
  `CONTRACT WAIVED`, which is only the label `pack`, `verify` and `status`
  render. Never pass the contract directory positionally to get past the
  refusal: that drops the baseline and the subset check.
- **Coming back from a window is six commands, not one.** The enforcer bytes
  moved, so the root moved with them, and the committed approval still records
  the previous root — precisely what `freeze` refuses. `approve --replace
  --reason "<why>"`, a fresh COMMIT of the artifact, and a re-run of the gate are
  not optional; releasing a freeze clears the attestation with it.

**When the slice ships, retire the contract**: promote still-valid coverage to
the ordinary suite and remove `tests/contract/{YYYY-MM-DD}-{slug}/`; left in
place it binds every later slice to this one's behaviour.

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
