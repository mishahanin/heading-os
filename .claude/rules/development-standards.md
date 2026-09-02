---
paths:
  - ".claude/skills/**"
  - ".claude/rules/**"
  - ".claude/hooks/**"
  - "scripts/**"
  - "tests/**"
  - "reference/**"
  - "templates/**"
---

<!-- version: 1.4.0 | last-updated: 2026-09-02 -->
# Development Standards

Last Verified: 2026-09-02

Quality gates for every workspace artifact (skills, scripts, reference files, rules) and all development work, not just specific features.

## The Evidence Standard

Added 2026-09-02, derived from the 144 commits of the hardening campaign that
ran 24 August to 2 September. Every obligation below is a practice that campaign
used, and each names what holds it. Where nothing mechanical holds it, that is
stated, because a rule that implies enforcement it does not have is the same
defect it exists to prevent.

**The one sentence: a change carries evidence that it can fail.** Not an
argument that the code is right. A measurement at which the code goes red.

### Why this is a rule and not advice

Of the 327 scripts the campaign repaired, 295 already had tests. The tests were
green. So the problem was never missing tests, and "write good tests" would have
prevented almost none of it. Stronger: the campaign wrote 564 test files under
maximum attention and had to rewrite 312 of them in the same campaign, 184 in
one commit named "a suite that passed over the defects it was written to catch".
Discipline is not sufficient here. Machines are.

### The obligations

1. **Reproduce before repairing.** Never act on a written finding, an audit
   report, or a memory of the code. Open the current file and make the defect
   happen. Four in five findings in that campaign described code that no longer
   existed. *No gate. Judgement.*

2. **Measure, then state, with the date.** A claim in a commit body, a docstring
   or a rule carries `MEASURED YYYY-MM-DD` and the observed before/after. An
   argument is not a measurement. *Held for prose-about-code by
   `scripts/check-path-references.py`, `scripts/dev/check-readme-numbers.py`,
   `scripts/check-version-sync.py` and `tests/test_scope_claims.py`.*

3. **Fix at the shared root.** This repository's dominant defect shape, 35
   commits, is a fix that landed in one of N copies. Before repairing, ask what
   else in the tree solves the same problem. Extract one function; repoint every
   caller. *No gate. Judgement, and the most expensive one to get wrong.*

4. **One test file per defect, named as a sentence about it.**
   `tests/test_a_wall_that_read_the_present_and_shipped_the_past.py`. The module
   docstring carries the pre-fix measurement, so the evidence lives beside the
   regression test rather than only in a commit message. *Convention.*

5. **Both directions, always.** The case that must be refused AND the case that
   must still pass. A guard that refuses everything satisfies every refusal test
   and breaks every honest caller. *No gate; the anchor is the author's.*

6. **Drive the real entry point, assert the observable consequence.** An exit
   code, bytes not written, a subprocess not spawned, a send not made. Never a
   restated copy of the guard's own condition inside the test: a copy passes
   while the real command still exits 0. *No gate. Judgement.*

7. **A floor under every corpus.** A test whose only assertions sit inside a
   loop over a discovered corpus passes when the corpus is empty. Assert the
   size outside the loop, with the measured number and the date beside it. The
   same obligation applies to a guard: it refuses rather than reporting clean
   when its input collapses. *Held by `scripts/check-test-vacuity.py`
   (pre-commit + CI, ratchet over `config/test-vacuity-baseline.json`).*

8. **Ask the AST, not the text.** A substring scan goes red the moment a fix
   quotes the bad pattern to explain it, which teaches people to stop
   explaining. Roughly 49 commits made this swap. *No gate; a recurring review
   finding.*

9. **Mutation-verify the fix.** Plant deliberate breaks in the SOURCE, never in
   an assertion, and every one must be caught. State the fraction. A survivor is
   resolved as a real gap, an equivalent mutant recorded with its reason, or an
   ambiguous anchor the harness refused to score; it is never made to pass by
   weakening the test. Use `scripts/utils/mutation_harness.py`, which bounds the
   child in wall clock and address space, backs up before the edit and verifies
   the restore against a digest. *No gate: `scripts/canopus.py probe` is
   uncalibrated and must not be wired into one. This is the largest remaining
   piece of the standard that a machine does not hold.*

10. **Name what was NOT fixed and what is NOT claimed.** In its own paragraph,
    not implied by silence. *Governed by `.claude/rules/scope-claims.md`, held
    for tool output by `tests/test_scope_claims.py`.*

11. **Every gate has a scope and a test.** A `files:` pattern matching nothing
    passes every commit vacuously; a wall no test names has never been observed
    refusing. *Held by `scripts/check-gate-integrity.py` (pre-commit + CI).*

12. **A control fails closed.** A PreToolUse wall that raises now BLOCKS rather
    than advising and stepping aside; reads and edits under `.claude/hooks/`
    stay open so the crash can be diagnosed and repaired. *Held by
    `crashed_wall_block` in `.claude/hooks/_dispatch.py` and
    `tests/test_a_wall_that_reported_its_own_failure_and_stepped_aside.py`.*

### Staying in working order

An audit that happens once, late, costs ten days. `scripts/audit-rotation.py`
replaces the campaign with a rotation: it selects a small slice of artifacts, and
records each verdict against the artifact's CONTENT HASH. A file that changes
re-enters the queue by itself; a file that is new enters at the front, because
the inventory is derived from `git ls-files` on every run rather than stored.
`--status` prints the honest share of the tree that carries a verdict against
the bytes on disk right now.

It never audits anything and never calls a model. Selecting and recording are
its whole job, and the separation is asserted in its tests, so a ledger cannot
quietly become a standing campaign.

**A finding is fixed, not filed.** Operator instruction, 2026-09-02. An artifact
whose audit found defects is recorded `open` with one line per finding, each
carrying a severity and an estimate in minutes, and `open` does NOT count toward
coverage. The artifact stays in `--report` until the findings are closed.
Recording a defect and moving on would mark the file done, which is the failure
this rotation exists to prevent, one level above the code it audits.

`--report` is the daily digest: what is open, in severity order, with the total
time. `--notify` sends the same to the operator's own sink, which
`.claude/rules/lethal-trifecta.md` exempts from the send gate because it can
only reach the person who already holds the data. An estimate is a number a
person or an agent wrote down; nothing derives one, and a missing estimate
counts zero rather than being guessed into a total the operator schedules
around.

**Night repairs, morning acceptance.** `scripts/night-repair.py --approve` turns
the open findings into a batch the operator approved, cut at a time budget.
`--run` starts a headless session over it. Three bounds, each asserted by a test
rather than promised: the prompt carries no word from the release gate's own
authorising lists, imported from the hook so a word added there tomorrow is
checked tonight; the batch is consumed before the session starts, so a crash
cannot repeat a half-done pass; and the session writes nothing to the rotation
ledger, because an agent that repairs and then certifies its own repair is
marking its own homework. It leaves the tree dirty. The operator reads the diff
and the evidence, and their word closes the finding.

Nothing here is armed by default. There is no installed timer, and arming one is
an explicit operator action, not a consequence of this rule existing.

## Before Building Anything

1. **Research first.** Use Context7 (`python scripts/context7.py`) to validate against the latest documentation for any library, framework, or platform being used. Never rely solely on training data.
2. **Think before acting.** For non-trivial decisions (architecture, multi-file changes, trade-offs), engage `/deep-think` before implementation. Surface assumptions, evaluate paths, produce a reasoned recommendation.
3. **Check what exists.** Search the workspace for existing patterns, utilities, and conventions before creating new ones. Reuse `scripts/utils/` modules. Follow established skill/script patterns.
4. **Read full files.** Read tool defaults to `limit: 10000` for source files, notes, long markdown, and generated outputs. Silent truncation at the 2000-line default hides later functions, success criteria, and end-of-file declarations. Use smaller limits only for known-small files or specific ranges.

## Restraint

Scope discipline for every workspace edit. Two principles from Karpathy's LLM-coding guidelines; the other two he lists are already covered ("think before coding" by `.claude/rules/prompt-refinement.md`, "goal-driven execution" by `/create-plan` and `/implement`).

Tradeoff: these principles bias toward caution over speed. For trivial tasks, use judgment.

Simplicity governs the code you add. Surgical changes governs the code already there. They do not overlap, and neither overrides the other.

### Simplicity first

Minimum artifact that solves the problem. Nothing speculative. Applies to what you are writing now.

- No features, flags, or options beyond what was asked.
- No abstractions for single-use code.
- No error handling for scenarios that cannot occur.
- If the code you are adding runs long and half of it would do, rewrite it before declaring done.

The test: would a senior engineer call what you wrote overcomplicated? If yes, simplify.

### Surgical changes

Touch only what the task requires. Clean up only your own mess. Applies to code that already exists.

- Do not "improve" adjacent code, comments, or formatting.
- Do not refactor what is not broken, even when you would write it differently.
- Match existing style in the file you are editing.
- Remove imports, variables, and helpers that your change orphaned. Leave pre-existing dead code in place. Surface it instead: name it in your summary to the CEO, or log it to the relevant thread. Do not delete it.

The test: every changed line traces directly to the request.

### Carve-out: mandatory fixes override restraint

Restraint limits scope. It never excuses skipping a fix the workspace already requires. Two obligations override it:

- **Security findings.** Open items in `docs/security/findings-registry.md` for files you are about to touch are fixed FIRST, before the requested change, per the global security rule in `~/.claude/CLAUDE.md`.
- **Review findings.** Code-review and `/scrutinize` findings are all fixed before declaring done, never triaged into fix-now versus fix-later.

A mandatory fix that reaches into adjacent code is not a "surgical changes" violation. When it widens the diff, say so explicitly in your summary so the wider change stays visible. If restraint and a security control genuinely conflict, the security control wins and the conflict is surfaced to the CEO.

## Debugging Discipline

Applies whenever a script errors, a daemon misbehaves, output is wrong, or a previously working tool starts failing.

The rule: **do not hypothesise about a bug until you can reproduce it on demand.** Staring at code is not debugging. A fast pass/fail signal is. Skip a phase only when you can explicitly justify it.

### Phase 1 -- Build a feedback loop (this is the work)

Before forming any theory, build the fastest deterministic way to make the bug appear and disappear. Everything downstream just consumes that signal. Try, in rough order:

1. A failing test at the seam that reaches the bug (`tests/`, or `tests/security/` for a security defect).
2. Re-run the script with a fixed fixture input, diffing stdout against known-good output.
3. Hit a daemon's health surface (`scripts/bridge-daemon.py --health`, `scripts/daemon-fleet-health.py`) or replay its state file.
4. A throwaway harness that calls the suspect function directly.
5. For intermittent bugs: loop the trigger many times, add stress, narrow timing windows. The goal is a higher reproduction rate, not a clean one-shot.

Then sharpen the loop itself -- faster, more deterministic. A 2-second deterministic loop is a debugging superpower; a 30-second flaky one is barely better than nothing.

**If you genuinely cannot build a loop, stop and say so.** List what you tried. Ask the CEO for the environment, a captured artifact (log dump, state snapshot, screen recording with timestamps), or permission to add temporary instrumentation. Do not proceed to guesswork.

### Phase 2 -- Reproduce

Run the loop. Watch the bug appear across multiple runs. Capture the exact symptom for later verification.

### Phase 3 -- Hypothesise

Generate 3-5 ranked, falsifiable hypotheses before testing any of them -- single-hypothesis debugging anchors on the first plausible idea. Each states a prediction: "If X is the cause, changing Y makes the bug disappear." Show the ranked list before you start probing.

### Phase 4 -- Instrument

One variable at a time. Each probe maps to a specific prediction from Phase 3. Prefer a debugger or REPL; fall back to targeted logs at the boundaries that distinguish hypotheses. Never "log everything and grep." Tag every debug log with a unique prefix (`[DEBUG-a4f2]`) so cleanup is one grep. For performance regressions logs usually mislead -- measure a baseline and bisect instead.

### Phase 5 -- Fix plus regression test

Write the regression test before the fix, when a correct seam exists -- one that exercises the real bug pattern as it occurs at the call site. Watch it fail, apply the fix, watch it pass, then re-run the Phase 1 loop against the original scenario. If no correct seam exists, document why.

A good test verifies **behaviour through the public interface, not implementation detail.** If renaming an internal function breaks the test, the test was wrong -- it was coupled to structure, not behaviour. Write one test, make it pass, then the next; never a batch of tests against imagined behaviour ahead of the code.

### Phase 6 -- Cleanup and post-mortem

Before declaring done: the original repro no longer reproduces; the regression test passes (or the absent seam is documented); every `[DEBUG-...]` line is removed; throwaway harnesses are deleted; the correct hypothesis is stated in the commit message. Then ask what would have prevented the bug. If the answer is a structural change, surface it to the CEO or log it to the relevant thread -- do not silently fix beyond scope (see Restraint).

## Skill Standards

Every skill in `.claude/skills/{name}/SKILL.md` must have:

**Frontmatter (YAML between `---` markers):**

Top-level fields (per Anthropic's SKILL.md spec):
- `name` -- kebab-case identifier (required)
- `description` -- detailed trigger description including when to use AND when not to use (required)
- `argument-hint` -- expected input format, e.g., `"[target]"` (recommended)
- `allowed-tools` -- explicit tool list, e.g., `"WebSearch, WebFetch, Read, Bash(python3:*)"` (recommended)
- `context: fork` -- if the skill needs isolated context (recommended for complex skills)
- `metadata.author` -- `Misha Hanin` (required)
- `metadata.email` -- `misha.hanin@odinix.com` (required)
- `metadata.version` -- semantic version, e.g., `"1.0"` (required)

Invocation control flags (optional, harness-enforced — verified against current Claude Code SKILL.md spec, 2026-05-17):

- `disable-model-invocation: true` -- the model cannot trigger the skill from natural language or as a tool. Only an explicit user-typed slash command fires it. Use for any skill whose description already says "EXPLICIT INVOCATION ONLY" or "NEVER auto-trigger", and for high-blast-radius actions where accidental routing is unacceptable. Adopters include `/prime`, `/osint-advanced`, `/workspace-deep-audit`, `/align`, `/devil`, `/burst`, `/bridge-health`, `/modem-tune`, `/checkpoint`, and others — for the live set run `grep -rl "disable-model-invocation: true" .claude/skills/`.
- `user-invocable: false` -- inverse of the above. Hides the skill from the `/` menu so the CEO never invokes it directly, while leaving the model free to trigger it contextually. Use for background-context or internal-only skills that other skills depend on but should not appear as user-facing commands. No current adopters in this workspace.

Both flags are top-level frontmatter fields (siblings of `name`, `description`, `allowed-tools`). They are harness-enforced and supersede any prose policy ("NEVER auto-trigger") written into `description`.

Workspace orchestration extension (under `x-heading-orchestration:` namespaced block, required):
- `parallel_safe` -- `true`, `partial`, or `false`. Controls orchestrator dispatch safety. `true` = read-only or isolated outputs, `partial` = safe research phase + unsafe write phase, `false` = shared state or inherently sequential.
- `shared_state` -- list of file/directory paths this skill writes to, e.g., `["crm/contacts/", "context/pipeline.md"]`. Empty list `[]` if read-only.
- `triggers` -- list of natural-language phrases that should invoke this skill, e.g., `["investigate", "research", "dig into"]`. Empty list `[]` if not auto-routable.

The `x-` prefix signals "workspace extension, not part of Anthropic's standard SKILL.md spec." Anthropic's tooling ignores unknown frontmatter fields today; the namespaced block keeps that contract intact even if a future release tightens validation. New skills use the `x-heading-*` namespace; as of v0.5.0 the parsers (`scripts/skill-metadata-check.py`, `scripts/bridge_daemon/sources/capabilities.py`) accept only `x-heading-*` — the legacy 31C-prefixed key was removed.

Example shape:

```yaml
---
name: example-skill
description: One-paragraph trigger description
allowed-tools: "Read, Bash(python3:*)"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-heading-orchestration:
  parallel_safe: false
  shared_state: []
  triggers: ["example phrase", "another trigger"]
x-heading-routing:
  category: Operations
  triggers: ["example phrase", "another trigger"]
  exclusions: ["<signal> -> /<other-skill>"]   # or ["N/A"]
  compound: "No"
  router: auto                                  # or manual (NEVER auto-trigger skills)
  # label: "/name [args]"                       # only when the Skill cell is not the plain /name
x-heading-capability:
  what: >
    Plain one-to-two-sentence statement of what the skill produces or does.
  how: >
    How to invoke it (slash command + argument-hint) and the typical flow or
    where output lands.
  when: >
    When to use it, and when NOT to (name the alternative skill).
---
```

Capability self-explanation (under `x-heading-capability:` namespaced block, recommended):
- `what` / `how` / `when` -- plain-language folded scalars rendered on the bridge dashboard's Capabilities page (`scripts/bridge_daemon/sources/capabilities.py` reads them via `yaml.safe_load`; the page falls back to the `description` when the block is absent). Keep each field 1-2 sentences, ASCII-only, grounded in the skill's real behaviour and router exclusions. This is the field that makes the Capabilities page a genuine "what does each skill do and how do I use it" reference rather than a bare list.

Router registry (under `x-heading-routing:` namespaced block, required for every routable skill; F-5.1):
- `category` -- one of `Intel | Communication | Content | CRM | Design | Strategy | Operations`; which registry table the skill's row lands in.
- `triggers` -- list of the router-cell trigger phrases (the Triggers column). Distinct from `x-heading-orchestration.triggers`, which is the orchestrator's compound-dispatch subset; the two serve different consumers and may differ.
- `exclusions` -- list of the router-cell disambiguation rules (the Exclusions column), e.g. `["\"validate\" -> /validate"]`, or `["N/A"]`.
- `compound` -- the Compound column string, e.g. `"No"` or `"Yes: Meeting Prep, Deal Intel"`.
- `router` -- `auto` (routable from natural language) or `manual` (NEVER auto-trigger / `disable-model-invocation` skills).
- `label` -- OPTIONAL; only when the Skill-column cell is not the plain `/name` (e.g. an arg-hint like `/scrutinize [target] ...`).

**The skill router is GENERATED from these blocks** by `scripts/generate-skill-router.py`, in a two-layer split (F-5.2): a compact core index (Skill + Triggers) between the `<!-- BEGIN GENERATED REGISTRY -->` / `<!-- END GENERATED REGISTRY -->` markers in the always-on `.claude/rules/skill-router.md`, plus a full per-category detail table (Skill | Triggers | Exclusions | Compound) in each `reference/skill-router/<category>.md`. Never hand-edit either layer: edit the skill's `x-heading-routing` frontmatter, then run `python scripts/generate-skill-router.py`. CI and pre-commit run `--check` (regen -> diff across BOTH layers), which fails on any content drift, a missing/orphan category file, or a skill missing its block (the last fails with the file path and a paste-ready fix-it snippet). `--flat` prints the legacy flat monolith to stdout for debugging. The migration that first populated these blocks is `scripts/dev/extract-router-rows.py` (one-shot, kept for provenance).

**Body:**
- Size budget (F-5.3): each `SKILL.md` is capped at **500 lines AND 18432 bytes (18 KB)** hard, with a **16384-byte (16 KB) warn** threshold; use the `references/` subdirectory for overflow. `scripts/skill-metadata-check.py` enforces the budget in CI (flagless) and pre-commit (`skill-size-budget` hook) - a HARD violation exits 1 unconditionally; a WARN prints but does not block.
- Phased execution (Phase 0: context loading, Phase 1: execution, Phase 2: synthesis, Phase 3: output)
- Reference files must include: H1 title, "Consumed by" pointer, "Last Updated" date
- Voice rules section matching workspace standards (hyphens, ODUN.ONE, DPI+)
- NEVER section listing explicit prohibitions

**Skill artifacts:**
- `triggers.json` (**mandatory for routing-sensitive skills**, recommended for all) -- a JSON array of `{ "query": "...", "should_trigger": true|false }` cases, 6-10 positives and 6-10 negatives, with negatives drawn from the skill's documented router exclusions. Regression-tested by `scripts/skill-trigger-test.py` (LLM-judge, advisory). When a skill's triggers or exclusions change, update its `triggers.json` and re-run the harness. Classified the same as the skill. A skill is *routing-sensitive* when it shares trigger vocabulary with another skill or carries a non-trivial exclusions list in `.claude/rules/skill-router.md` -- the surface where a new skill can silently hijack an existing skill's queries. **Growth policy:** any new or re-scoped routing-sensitive skill ships with `triggers.json` in the same change; `/push-updates` Phase 0 runs `skill-trigger-test.py --changed --strict --threshold 0.85` as a **soft gate** (tests only changed skills; surfaces regressions, the CEO confirms to override). Tracked, not yet enforced: quarterly consolidation of thin single-use skills into subcommand families (the `/crm`, `/marp` model), and a deterministic keyword pre-classifier feeding the model router if routing precision degrades.
  - **Coverage is now mechanically gated (F-6.1).** `scripts/skill-metadata-check.py` classifies every skill's corpus and exits 1 (UNCONDITIONALLY, so the flagless CI "Skill metadata contract" step and the `skill-size-budget` pre-commit hook both enforce it) when an **auto-routable** skill lacks a valid `triggers.json`. "Auto-routable" means `x-heading-routing.router: auto` AND NOT `disable-model-invocation: true`; a `router: manual` or `disable-model-invocation: true` skill is EXEMPT (it never auto-routes, so a routing corpus is meaningless). A valid corpus is a JSON array of `>= 6` `{query, should_trigger}` cases with `>= 4` positives and `>= 2` hard negatives. Pre-F-6.1 uncovered skills are grandfathered by the committed, only-shrinks `config/triggers-coverage-baseline.json` (regenerated shrink-only via `--write-baseline`: it removes now-covered skills, never adds a newly-shipped one), so a NEW auto-routable skill must ship a corpus. `/skill-creator` refuses to finish a new auto-routable skill without one.

### Post-synthesis brain audit

Any skill that produces a synthesized answer over a source set MUST invoke `/brain-audit` at the end of its synthesis phase and append the returned footer to its output. The audit reports newest-source dates, modality coverage, and source disagreements.

Invocation pattern:

> Invoke `/brain-audit --sources <comma-separated paths> --entity <name>`

If the skill is not entity-scoped (e.g., a multi-section dashboard), omit the entity flag. The audit gracefully degrades to a no-entity footer.

For the live set of composers, ask the tree, never this paragraph: `grep -rl "brain-audit" .claude/skills/ --include=SKILL.md`. A hand-written list here named three (`/meeting-prep`, `/odin`, `/deal-strategy`) while nine skills composed it, six of them citing this very rule as the reason, and it had been wrong since the initial import. New synthesis skills MUST adopt the same pattern. A future `scripts/artifact-evaluator.py` check will flag missing composition; until it exists, the grep is the only current answer.

## Script Standards

Every Python script in `scripts/` must follow:

**Naming convention:**
- `kebab-case.py` for CLI scripts invoked via `python scripts/name.py ...`. Example: `scripts/generate-dashboard.py`, `scripts/classification-health.py`.
- `snake_case.py` for (a) anything in `scripts/utils/` (library modules always), and (b) any script imported as a Python module from elsewhere in the workspace. Hyphens are illegal in Python module names; `from scripts.marp-render import ...` is a syntax error. Example: `scripts/marp_render.py`, `scripts/browser.py`.
- Before renaming any script, run `grep -r "from scripts.{name}"` across the workspace. If any Python file imports it, it must stay snake_case.

**File structure:**
- Shebang: `#!/usr/bin/env python3`
- Module docstring with Usage examples
- Workspace imports: `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`
- Use `from scripts.utils.workspace import get_workspace_root` (not manual path resolution)
- Use `from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET`
- Use `from scripts.utils.api import load_api_key` for single API keys; `load_env()` for multi-variable `.env` reads (Exchange credentials, OAuth configs)
- Use `pathlib.Path` objects, not string paths
- Catch `HTTPError` before `URLError` (HTTPError is a subclass -- Context7 validated)
- `argparse` for CLI interface
- `if __name__ == "__main__":` guard
- No hardcoded paths -- use workspace utility functions

**Structure for long scripts (>500 lines):**
Use `# ===` section banners to delimit major blocks (Config, State Management, Data Sources, Processing, Output, etc.). Phase-based execution (Phase 0/1/2/3) is the SKILL convention and does not apply to scripts - scripts run to completion in a single pass and use section banners for navigability.

Example:
```python
# ============================================================
# Configuration
# ============================================================
class Config: ...

# ============================================================
# State Management
# ============================================================
class StateManager: ...
```

**Scheduled tasks (systemd-user timers) MUST survive reboot.** Every scheduled task in this workspace -- every existing timer and every future one -- ships as a systemd-user timer built to fire after an unattended reboot, never as a session-scoped `CronCreate` task (those are NOT durable -- see `reference/scheduled-tasks.md`, which took this content out of `.claude/rules/skill-router.md` on 2026-08-20). Reboot survival requires all THREE mechanisms, and an installer must include every one (copy an existing sibling installer such as `scripts/install-router-accuracy-timer.sh`, which already bakes them in -- do not hand-roll a partial one):

1. **`Persistent=true`** in the `.timer` unit -- runs a fire missed while the machine was off, on the next boot.
2. **`systemctl --user enable`** with `WantedBy=timers.target` in the unit -- the timer starts at boot.
3. **`loginctl enable-linger "$USER"`** in the installer -- user units run without an interactive login session. This is the one most often forgotten; without it a user timer stays silent after an unattended reboot.

Make reboot survival an explicit Constraint + Validation item in any plan that adds a timer, and verify after install: `systemctl --user is-enabled <name>.timer` = `enabled`, `loginctl show-user "$USER"` shows `Linger=yes`, and the rendered `~/.config/systemd/user/<name>.timer` contains `Persistent=true`. Timer/service templates live in `scripts/templates/systemd/` and carry no geographic literal -- timezone via the `{{TZ}}` substitution token the installer fills from `HEADING_OS_TZ`.

**In-process APScheduler jobs MUST NOT be dropped for lateness.** The reboot rule above governs OS-level timers. Its in-process sibling governs jobs inside a daemon: APScheduler's `misfire_grace_time` defaults to 1 second, so a job whose due moment slips past that is DISCARDED with only a journal warning. Measured on 2026-07-30 before this rule existed, a 2-hour Exchange sync ran twice in twenty four hours instead of twelve times while systemd reported the daemon healthy, and a 1-minute heartbeat lost 1059 of 1440 runs. Tick latency, not load, is the cause.

Construct every scheduler with the shared defaults, never per job:

```python
from scripts.utils.scheduler_defaults import JOB_DEFAULTS
scheduler = AsyncIOScheduler(timezone=get_default_tz(), job_defaults=JOB_DEFAULTS)
```

Passing the options to `add_job` instead is what failed the first time: the safe values sat on one `add_job` call in `scripts/bridge_daemon/scheduler.py` while the five jobs `scripts/bridge-daemon.py` adds to that same scheduler silently kept the 1 second default, two lines below a comment that diagnosed the bug. A scheduler-level default is inherited by jobs registered later, by authors who never read this rule; a per-job argument is not. `tests/test_scheduler_misfire_guard.py` fails any scheduler under `scripts/` built without `job_defaults`, and any whose `job_defaults` is a dict LITERAL omitting `misfire_grace_time`. Its stated limit: a spread, a call, or an unrecognised name (`job_defaults=build()`, `{**base}`) passes unexamined, because source inspection cannot settle it. So pass the constant by name and nothing else.

## Reference File Standards

Every file in `reference/` must include:

- H1 title on line 1
- One-line description
- "Last Updated: YYYY-MM-DD" date (when the content last changed)
- Clear section organization with `##` headers

For files with operational or cadence content (publishing schedules, meeting policies, time-budget rules), also include a "Last Verified: YYYY-MM-DD" date. `Updated` advances when content changes; `Verified` advances when the content has been re-confirmed as accurate even if no edits were needed. Both dates older than 90 days on a cadence file is a signal to re-check practice before relying on the document.

Skill reference files in `.claude/skills/{name}/references/` additionally need:
- "Consumed by:" pointer to the skill that uses them

## Validation Gates (Before Declaring Done)

1. **Hidden character scan:** `python scripts/sanitize-text.py {file} --scan` on every new/modified file
2. **Python syntax check:** `python3 -m py_compile {script}` for all Python files
3. **Frontmatter validation:** Verify YAML parses correctly, all required fields present
4. **Size budget:** `python scripts/skill-metadata-check.py` enforces <= 500 lines AND <= 18432 bytes per `SKILL.md` (warn >= 16384 bytes), in CI + the `skill-size-budget` pre-commit hook
5. **Documentation propagation:** Update `templates/GETTING-STARTED.md` (per documentation propagation rule). On the CEO workspace, also update `reference/workspace-overview.md`.
6. **Context7 validation:** For any code using external libraries or APIs, fetch current docs via Context7 and validate patterns
7. **Artifact evaluation:** Run `python scripts/artifact-evaluator.py --path {artifact}` on new skills, scripts, reference files, and rules. Or use `/evaluate {artifact-path}` for full qualitative + deterministic assessment. Use `/implement --evaluate` to integrate the feedback loop into implementation

## Live Tool/API Validation

When integrating external tools, APIs, or services:

1. **Test before documenting.** WebFetch each endpoint with a real query. Record actual HTTP status, response format, and whether useful data comes back.
2. **Document access method accurately.** WORKING / BLOCKED / REQUIRES_AUTH / CLI-only. Never mark a tool as "WORKING" without testing it.
3. **Update on failure.** If a previously working tool starts failing, update the registry immediately and switch to the fallback chain.
4. **Never auto-update from upstream.** External tool registries (like awesome-osint) change constantly. New entries must be validated before adding. Human approval required.
