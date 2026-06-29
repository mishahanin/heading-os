<!-- version: 1.3.0 | last-updated: 2026-05-21 -->
---
paths:
  - ".claude/skills/**"
  - ".claude/rules/**"
  - "scripts/**"
  - "reference/**"
  - "templates/**"
---

# Development Standards

Last Verified: 2026-05-28

Quality gates for every workspace artifact -- skills, scripts, reference files, rules, and components. These standards apply to ALL development work, not just specific features.

## Before Building Anything

1. **Research first.** Use Context7 (`python scripts/context7.py`) to validate against the latest documentation for any library, framework, or platform being used. Never rely solely on training data.
2. **Think before acting.** For non-trivial decisions (architecture, multi-file changes, trade-offs), engage `/deep-think` before implementation. Surface assumptions, evaluate paths, produce a reasoned recommendation.
3. **Check what exists.** Search the workspace for existing patterns, utilities, and conventions before creating new ones. Reuse `scripts/utils/` modules. Follow established skill/script patterns.
4. **Read full files.** Read tool defaults to `limit: 10000` for source files, notes, long markdown, and generated outputs. Silent truncation at the 2000-line default hides later functions, success criteria, and end-of-file declarations. Use smaller limits only for known-small files or specific ranges.

## Restraint

Scope discipline for every workspace edit. Two principles imported from Andrej Karpathy's LLM-coding guidelines, cherry-picked because the workspace does not already enforce them. The other two are already covered: "think before coding" by `.claude/rules/prompt-refinement.md`, "goal-driven execution" by `/create-plan` and `/implement`.

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

Imported from the `/diagnose` discipline in `mattpocock/skills`, adapted to a workspace that runs daemons and scripts rather than a single application codebase. Applies whenever a script errors, a daemon misbehaves, output is wrong, or a previously working tool starts failing.

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

- `disable-model-invocation: true` -- the model cannot trigger the skill from natural language or as a tool. Only an explicit user-typed slash command fires it. Use for any skill whose description already says "EXPLICIT INVOCATION ONLY" or "NEVER auto-trigger", and for high-blast-radius actions where accidental routing is unacceptable. Adopters include `/prime`, `/osint-advanced`, `/workspace-deep-audit`, `/align`, `/devil`, `/burst`, `/bridge-health`, `/promote-corporate`, `/rollback-corporate`, `/modem-tune`, `/checkpoint`, and others — for the live set run `grep -rl "disable-model-invocation: true" .claude/skills/`.
- `user-invocable: false` -- inverse of the above. Hides the skill from the `/` menu so the CEO never invokes it directly, while leaving the model free to trigger it contextually. Use for background-context or internal-only skills that other skills depend on but should not appear as user-facing commands. No current adopters in this workspace.

Both flags are top-level frontmatter fields (siblings of `name`, `description`, `allowed-tools`). They are harness-enforced and supersede any prose policy ("NEVER auto-trigger") written into `description`.

Workspace orchestration extension (under `x-31c-orchestration:` namespaced block, required):
- `parallel_safe` -- `true`, `partial`, or `false`. Controls orchestrator dispatch safety. `true` = read-only or isolated outputs, `partial` = safe research phase + unsafe write phase, `false` = shared state or inherently sequential.
- `shared_state` -- list of file/directory paths this skill writes to, e.g., `["crm/contacts/", "context/pipeline.md"]`. Empty list `[]` if read-only.
- `triggers` -- list of natural-language phrases that should invoke this skill, e.g., `["investigate", "research", "dig into"]`. Empty list `[]` if not auto-routable.

The `x-` prefix signals "workspace extension, not part of Anthropic's standard SKILL.md spec." Anthropic's tooling ignores unknown frontmatter fields today; the namespaced block keeps that contract intact even if a future release tightens validation.

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
x-31c-orchestration:
  parallel_safe: false
  shared_state: []
  triggers: ["example phrase", "another trigger"]
x-31c-capability:
  what: >
    Plain one-to-two-sentence statement of what the skill produces or does.
  how: >
    How to invoke it (slash command + argument-hint) and the typical flow or
    where output lands.
  when: >
    When to use it, and when NOT to (name the alternative skill).
---
```

Capability self-explanation (under `x-31c-capability:` namespaced block, recommended):
- `what` / `how` / `when` -- plain-language folded scalars rendered on the bridge dashboard's Capabilities page (`scripts/bridge_daemon/sources/capabilities.py` reads them via `yaml.safe_load`; the page falls back to the `description` when the block is absent). Keep each field 1-2 sentences, ASCII-only, grounded in the skill's real behaviour and router exclusions. This is the field that makes the Capabilities page a genuine "what does each skill do and how do I use it" reference rather than a bare list.

**Body:**
- Under 500 lines. Use `references/` subdirectory for overflow content.
- Phased execution (Phase 0: context loading, Phase 1: execution, Phase 2: synthesis, Phase 3: output)
- Reference files must include: H1 title, "Consumed by" pointer, "Last Updated" date
- Voice rules section matching workspace standards (hyphens, ODUN.ONE, DPI+)
- NEVER section listing explicit prohibitions

**Skill artifacts:**
- `triggers.json` (**mandatory for routing-sensitive skills**, recommended for all) -- a JSON array of `{ "query": "...", "should_trigger": true|false }` cases, 6-10 positives and 6-10 negatives, with negatives drawn from the skill's documented router exclusions. Regression-tested by `scripts/skill-trigger-test.py` (LLM-judge, advisory). When a skill's triggers or exclusions change, update its `triggers.json` and re-run the harness. Classified the same as the skill (corporate by default; ceo-only if the skill's SKILL.md is ceo-only). A skill is *routing-sensitive* when it shares trigger vocabulary with another skill or carries a non-trivial exclusions list in `.claude/rules/skill-router.md` -- exactly the surface where a new skill can silently hijack an existing skill's queries (the documented failure mode behind the 2026-06-09 audit's routing-entropy finding). **Growth policy (2026-06-09 audit #63):** as the catalog grows past one hand-maintained router, (1) any new or re-scoped routing-sensitive skill ships with `triggers.json` in the same change; (2) `/push-updates` Phase 0 now runs `skill-trigger-test.py --changed --strict --threshold 0.85` as a **soft gate** (tests only changed routing-sensitive skills; surfaces regressions and the CEO confirms to override) -- landed 2026-06-26; promote to a hard block only once the judge's false-positive rate is characterized over several weeks of soft runs; (3) a quarterly consolidation pass merges thin single-use skills into family skills with subcommands (the `/crm` and `/marp` subcommand model is the template); (4) a deterministic keyword pre-classifier feeding a candidate set into the model router is the next structural step if routing precision degrades. Item 2 landed as a soft gate (2026-06-26); items 3-4 remain tracked initiatives, not yet enforced.

### Post-synthesis brain audit

Any skill that produces a synthesized answer over a source set MUST invoke `/brain-audit` at the end of its synthesis phase and append the returned footer to its output. The audit reports newest-source dates, modality coverage, and source disagreements.

Invocation pattern:

> Invoke `/brain-audit --sources <comma-separated paths> --entity <name>`

If the skill is not entity-scoped (e.g., a multi-section dashboard), omit the entity flag. The audit gracefully degrades to a no-entity footer.

Skills currently composing `/brain-audit`: `/meeting-prep`, `/odin` (consult mode), `/deal-strategy`. New synthesis skills MUST adopt the same pattern. A future `scripts/artifact-evaluator.py` check will flag missing composition.

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
4. **Line count check:** SKILL.md files under 500 lines
5. **Documentation propagation:** Update `templates/GETTING-STARTED.md` (per documentation propagation rule). On the CEO workspace, also update `reference/workspace-overview.md`.
6. **Context7 validation:** For any code using external libraries or APIs, fetch current docs via Context7 and validate patterns
7. **Artifact evaluation:** Run `python scripts/artifact-evaluator.py --path {artifact}` on new skills, scripts, reference files, and rules. Or use `/evaluate {artifact-path}` for full qualitative + deterministic assessment. Use `/implement --evaluate` to integrate the feedback loop into implementation

## Live Tool/API Validation

When integrating external tools, APIs, or services:

1. **Test before documenting.** WebFetch each endpoint with a real query. Record actual HTTP status, response format, and whether useful data comes back.
2. **Document access method accurately.** WORKING / BLOCKED / REQUIRES_AUTH / CLI-only. Never mark a tool as "WORKING" without testing it.
3. **Update on failure.** If a previously working tool starts failing, update the registry immediately and switch to the fallback chain.
4. **Never auto-update from upstream.** External tool registries (like awesome-osint) change constantly. New entries must be validated before adding. Human approval required.
