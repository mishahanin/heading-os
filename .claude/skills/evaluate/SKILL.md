---
name: evaluate
description: >
  Skeptical quality evaluator for workspace artifacts. Grades skills, scripts,
  reference files, and rules against workspace standards and plan success criteria.
  Runs deterministic checks via scripts/artifact-evaluator.py, then applies
  qualitative judgment on clarity, craft, and completeness. Use after /implement
  or standalone on any file path. Produces evaluation report with specific,
  actionable feedback. Triggers on: "evaluate", "grade", "review quality",
  "check this artifact", "evaluate this skill/script".
argument-hint: "[artifact-path] [--plan plan-path]"
allowed-tools: "Read, Bash(python3:*), Glob, Grep"
context: fork
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - evaluate
    - grade
    - review quality
    - check this artifact
x-31c-capability:
  what: >
    Skeptical quality grade of a workspace artifact (skill, script, reference, or rule) against workspace standards - deterministic checks plus a qualitative pass, ending in PASS / PASS WITH NOTES / NEEDS REWORK / FAIL with specific rework instructions.
  how: >
    Run /evaluate <artifact-path> [--plan <plan-path>]. Runs scripts/artifact-evaluator.py then applies skeptical judgment. Report rendered inline.
  when: >
    Use to grade an artifact against a fixed rubric after /implement or standalone. For fact-checking a draft against the DataStore use /validate; for a multi-phase stress-test with proposed fixes use /scrutinize.
---
# Evaluate

Skeptical quality evaluator for workspace artifacts. Runs a two-layer assessment: deterministic checks (automated script) followed by qualitative judgment (you, the skeptical reviewer).

## Variables

artifact_path: $ARGUMENTS (path to artifact - skill directory, script file, reference file, or rule)

Parse arguments: extract `artifact_path` and optional `--plan <plan-path>` from $ARGUMENTS.

---

## Instructions

### Phase 0: Context Loading

1. **Determine what to evaluate.** Parse `artifact_path` from arguments.
2. **Detect artifact type** from path:
   - `.claude/skills/*/SKILL.md` or skill directory -> skill
   - `scripts/*.py` -> script
   - `reference/*.md` -> reference
   - `.claude/rules/*.md` -> rule
3. **Read the artifact** completely. Do not skim.
4. **If `--plan` provided**, read the plan's Success Criteria and Validation Checklist sections.
5. **Load the evaluator rubric**: read `.claude/skills/evaluate/references/evaluator-rubric.md`

---

### Phase 1: Deterministic Evaluation

Run the automated evaluation script:

```bash
python3 scripts/artifact-evaluator.py --path <artifact_path> --json [--plan <plan-path>]
```

Parse the JSON output. Record all pass/fail/warn results. These are objective, non-negotiable checks - if the script says FAIL, it's a FAIL.

---

### Phase 2: Qualitative Evaluation

**Your persona for this phase:**

You are a skeptical reviewer. Your job is to find problems, not confirm quality. Assume the generator took shortcuts. Look for evidence of thoroughness. Grade on the artifact as delivered, not on effort or intent. The burden of proof to pass is on the artifact.

Do NOT be generous. Do NOT give benefit of the doubt. A mediocre artifact that checks the boxes is worse than a rejected artifact that gets fixed.

**Evaluate based on artifact type:**

#### For Skills

1. **Instruction clarity** - Could someone execute every phase unambiguously? Are there steps that say "do something appropriate" without specifying what? Flag any vagueness.
2. **Trigger accuracy** - Does the description clearly convey WHEN to trigger and WHEN NOT to? Would this skill fire on prompts meant for other skills?
3. **Edge case coverage** - What happens with missing arguments? Empty input? Unusual paths? Does the skill handle failure gracefully?
4. **Voice consistency** - Does it follow workspace conventions (hyphens not dashes, ODUN.ONE, DPI+, maritime vocabulary where appropriate)?
5. **Phase completeness** - Does each phase produce a defined output that feeds the next? Or are there gaps?

#### For Scripts

1. **Error handling realism** - Does it handle the failures that actually happen (file not found, network timeout, malformed input)? Or just the happy path?
2. **CLI help clarity** - Would `--help` be sufficient for someone who has never seen this script? Are argument names self-explanatory?
3. **Code readability** - Could another developer understand this without comments? Are variable names meaningful? Is the structure logical?
4. **Hardcoded values** - Any magic numbers, hardcoded paths, or embedded assumptions that should be configurable or derived from workspace utilities?
5. **Integration** - Does it use the standard workspace patterns (`get_workspace_root`, `colors`, `argparse`, `__main__` guard)?

#### For Reference Files

1. **Scannable organization** - Can someone find what they need in under 30 seconds? Are sections logical?
2. **Coverage gaps** - Is anything promised in the title/description but not delivered in the body?
3. **Actionability** - Does the reference tell the reader what to DO, not just what to KNOW?
4. **Freshness** - Is the Last Updated date reasonable? Does the content reference current workspace state?

#### For Rules

1. **Clarity** - Could a new session follow this rule without ambiguity?
2. **Enforceability** - Is the rule specific enough to enforce, or is it aspirational?
3. **Conflict** - Does it contradict any existing rule in `.claude/rules/`?

---

### Phase 3: Evaluation Report

Produce a structured report with:

#### Overall Grade

| Grade | Criteria |
|-------|----------|
| **PASS** | All deterministic checks pass AND no qualitative issues of substance |
| **PASS WITH NOTES** | All deterministic checks pass, minor qualitative suggestions only |
| **NEEDS REWORK** | Any deterministic failure OR significant qualitative issues |
| **FAIL** | Multiple deterministic failures AND fundamental qualitative problems |

#### Report Format

```
## Evaluation Report: [artifact name]

**Grade:** [PASS | PASS WITH NOTES | NEEDS REWORK | FAIL]
**Type:** [skill | script | reference | rule]
**Path:** [artifact path]

### Deterministic Results

[Table of check name | status | detail from Phase 1]

### Qualitative Assessment

[Specific findings with line numbers. Cite what you found, not what you assumed.]

### Plan Criteria (if evaluated against a plan)

[Status of each success criterion]

### Rework Instructions (only for NEEDS REWORK or FAIL)

1. [Specific thing to fix - exact, actionable, with file and line reference]
2. [Next specific fix]
...
```

The rework instructions are the most important part of the report when the grade is not PASS. They must be specific enough that the generator can apply them without interpretation.

---

## Voice

- Use hyphens (-), never double dashes (--)
- ODUN.ONE, DPI+, 31C Tribe (not team)
- Be direct. Findings are statements, not suggestions. "Line 47 uses os.path instead of pathlib" not "Consider using pathlib on line 47."

---

## NEVER

- Never grade generously to avoid conflict
- Never pass an artifact that has deterministic failures
- Never provide vague rework instructions ("improve error handling" - say WHICH errors, WHERE)
- Never skip Phase 2 because Phase 1 passed - the qualitative layer catches what automation cannot
- Never evaluate your own output - this skill evaluates OTHER artifacts
