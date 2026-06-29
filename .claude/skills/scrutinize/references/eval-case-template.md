# Eval-Case Template

> Consumed by: `.claude/skills/scrutinize/SKILL.md` (Phase 4.5)
> Last Updated: 2026-06-06
>
> Defines the format and promotion workflow for converting `/scrutinize` findings into regression eval cases under `.claude/skills/{skill}/evals/cases/`. Closes P1.6 from the 2026-05-17 workspace deep audit (production-to-eval flywheel).
>
> 2026-05-27 update: added auto-scaffold workflow (R5) and broadened target types to skills, scripts, and rules (R10). Skill regression artefacts stay as JSON in `evals/cases/`; script regressions land in `tests/regression/`; rule regressions land in `.claude/rules/_regression/`. Each target type has a distinct artefact shape per the "Target-type artefact shapes" section below.
>
> 2026-06-06 update (R13): added a fourth artefact - the OUTCOME case in `evals/outcomes/` (binary side-effect grading, distinct from the prose `evals/cases/`), graded by `scripts/eval-outcomes.py`; and `scripts/eval-flag.py`, a lighter CEO-driven capture sibling of Phase 4.5 that stages a draft in `evals/outcomes/_staged/` keyed by the R12 trace ID. See "OUTCOME cases" below. The prose-case shape, `cases/` routing, and the 5 eligibility rules are unchanged.

## What an eval case is

A small JSON file that a future eval run replays against the skill to catch regressions. If `/scrutinize` finds a real defect on a skill, the same defect can recur after a refactor or model upgrade. Locking the finding into an eval case is how the next refactor catches it before it ships.

## File location

```
.claude/skills/{skill}/evals/cases/case-{N}-{slug}.json
```

- `{skill}` - the skill whose finding generated this case (e.g. `crm`, `osint`, `scrutinize`)
- `{N}` - next sequential integer in that skill's `cases/` directory (1-indexed, no padding)
- `{slug}` - lowercase kebab-case description, max 4-5 words

The `cases/` directory must exist. If the target skill doesn't have one, promotion is NOT offered (Phase 4.5 detects missing directory and skips that finding).

## JSON shape

```json
{
  "id": "case-N-slug",
  "description": "One sentence stating what this case proves.",
  "input": "The user prompt to send to the skill.",
  "checks": {
    "must_mention": ["keyword1", "keyword2"],
    "must_not_mention": ["forbidden-string"],
    "min_words": 50,
    "max_words": 800,
    "hidden_chars_clean": true
  }
}
```

### Field semantics

- **`id`** - matches the filename stem. Sequential. Never reused.
- **`description`** - one-sentence English statement of what passing this case proves. No marketing language. State the assertion.
- **`input`** - the literal text sent to the skill in eval mode. Should mirror real user invocations - not a contrived stress test.
- **`checks`** - assertions the eval runner applies to the skill's response:
  - **`must_mention`** - list of substrings that MUST appear in the response (case-insensitive substring match). Use for required terminology, required mode names, required section headers.
  - **`must_not_mention`** - list of substrings that MUST NOT appear. Use for banned vocabulary, sanctions-flagged claims, dropped functionality.
  - **`min_words`** - minimum word count in the response. Use to catch over-truncated outputs.
  - **`max_words`** - maximum word count. Use to catch over-verbose responses.
  - **`hidden_chars_clean`** - if `true`, the response must contain zero invisible Unicode characters per `.claude/rules/hidden-chars.md`. Default `true` on every case.

At least one check field must be present. A case with no checks is invalid.

## Promotion-eligibility rules

A `/scrutinize` finding qualifies for eval-case promotion when ALL of these hold:

1. **Target is a skill.** The finding's location is inside `.claude/skills/{name}/`. Findings against rules, scripts, or other workspace files do NOT promote to eval cases.
2. **Skill has an `evals/cases/` directory.** If absent, do not offer promotion. (Optionally, Phase 4.5 may suggest creating the directory; do not auto-create.)
3. **The finding describes a behaviour, not a typo.** Promote regressions of voice, structure, mode coverage, required content. Do NOT promote: hidden-char contamination (already caught by sanitizer), frontmatter typos (already caught by validator), line-length (already caught by linters).
4. **The finding was approved-and-applied in Phase 4.** Rejected and deferred findings do not promote.
5. **Severity is BLOCKER, HIGH, or MEDIUM.** LOW and NIT are not worth the eval-suite weight.

If a finding qualifies, Phase 4.5 proposes a draft case (filled-in JSON) and asks the CEO to approve, skip, or revise the draft. Auto-write is forbidden - the CEO must explicitly approve each promotion.

## Eligibility quick reference

| Signal | Eligible? |
|---|---|
| Finding on `/crm` SKILL.md describing missing mode in description | Yes - regression candidate |
| Finding on `.claude/rules/voice.md` describing missing terminology | No - rules don't have eval cases |
| Finding on `scripts/dashboard.py` describing logic bug | No - covered by integration tests |
| LOW finding on a skill about a small typo | No - severity floor |
| Hidden-character contamination found by sanitizer | No - sanitizer is the regression test |
| Skill silently drops a documented capability | Yes - canonical regression candidate |
| Skill misroutes to a different sibling skill | Yes - canonical regression candidate |

## Draft-case generation pattern

For each eligible finding, Phase 4.5 builds a draft using this mapping:

| Finding field | Draft case field |
|---|---|
| Finding ID + severity | (used for `slug` derivation only, not stored) |
| Statement | `description` (rephrase to a positive assertion) |
| Evidence quote / required content | seeds `must_mention` |
| Banned terms surfaced in finding | seeds `must_not_mention` |
| Target skill's typical invocation form | seeds `input` |
| Severity-implied output size | seeds `min_words` (HIGH/BLOCKER: 60+; MEDIUM: 30+) |

The draft is a starting point. The CEO can revise any field before approval, or skip the promotion entirely.

## Approval block format (Phase 4.5)

```
## /scrutinize - Eval-Case Promotion

Eligible findings (severity >= MEDIUM, target was a skill, fix applied, evals/cases/ exists):

[B1] Statement: <one-line restatement>
  Skill:        .claude/skills/<name>/
  Next case ID: case-<N>-<slug>
  Draft:
  {
    "id": "case-<N>-<slug>",
    "description": "...",
    "input": "...",
    "checks": { "must_mention": [...], ... }
  }

[H2] ...

### Approval

Reply with one of:
- "promote all"      write all drafts as new eval cases
- "promote <ids>"    e.g. "promote B1, H2" (comma-separated)
- "skip all"         do not promote any
- "revise <id>"      change a draft, re-present, re-ask
```

Silence or "looks good" - WAIT. Promotion writes ARE silent-write-forbidden.

## Auto-scaffold workflow (R5, 2026-05-27)

When Phase 4.5 fires on a skill target that lacks an `evals/cases/` directory, the skill OFFERS to scaffold (it does NOT auto-create). This unlocks the production-to-eval flywheel for the 95% of skills that today have no eval suite.

### Trigger

Phase 4.5 detects:

- Target is a skill (`.claude/skills/{name}/SKILL.md` or its directory)
- At least one applied finding qualifies under the eligibility rules above
- `evals/cases/` does NOT exist for that skill

### Offer block

The skill prints:

```text
## /scrutinize - Eval-Case Promotion (auto-scaffold offer)

The skill `/{skill-name}` has no `evals/cases/` directory. The
following applied findings qualify for promotion if a directory is
created:

[B1] Statement: <one-line restatement>  (severity: BLOCKER)
[H2] Statement: <one-line restatement>  (severity: HIGH)

### Approval

Reply with one of:
- "scaffold and promote all"      create evals/cases/ + write all drafts
- "scaffold and promote <ids>"    e.g. "scaffold and promote B1"
- "scaffold only"                 create empty evals/cases/, skip drafts
- "skip"                          no scaffold, no promotion
```

### Actions

- `scaffold and promote all` / `scaffold and promote <ids>`: `mkdir -p .claude/skills/{name}/evals/cases/`, write the approved drafts. Same sanitize-text + JSON-parse checks per draft as the regular promotion path.

- `scaffold only`: create `.claude/skills/{name}/evals/cases/` with a single placeholder `.gitkeep` file. No drafts written. The next /scrutinize pass that finds qualifying findings can promote normally.

- `skip`: no filesystem change. The findings remain logged in the saved report under `Phase 4.5 outcome: skipped (auto-scaffold declined)`.

### Why auto-scaffold is CEO-gated (not silent)

A skill without an eval suite reflects a deliberate choice (the skill is content-only, has external evals, or was de-prioritised). Silent auto-creation would override that choice. The CEO decides per-skill whether the flywheel applies.

## Target-type artefact shapes (R10, 2026-05-27)

The flywheel now covers three target types. Each lands a different regression artefact in a different location.

| Target type | Location | Artefact shape |
|---|---|---|
| Skill (prose) | `.claude/skills/{name}/evals/cases/case-{N}-{slug}.json` | JSON (existing format above) |
| Skill (outcome) | `.claude/skills/{name}/evals/outcomes/case-{N}-{slug}.json` | JSON with an `outcome` block (R13 - see "OUTCOME cases" below) |
| Script | `tests/regression/scrutinize/test_scrutinize_{slug}.py` | pytest function |
| Rule | `.claude/rules/_regression/{rule-name}-{slug}.yaml` | YAML check pack |

### Script regression artefact

For a finding on a Python script (severity >= MEDIUM, applied, target is `scripts/{name}.py`):

- The artefact is a pytest function file in `tests/regression/scrutinize/`.
- Name pattern: `test_scrutinize_{script-stem}_{slug}.py`.
- Body: imports the script, exercises the function or CLI path where the finding lived, asserts the corrected behaviour.

Draft body template:

```python
"""Regression for /scrutinize finding {finding_id} on scripts/{script}.py.

Original finding ({severity}, conf {confidence}):
  {statement}

Fix applied: {one-line fix description}

This test fails if the regression returns.
"""
import pytest
from scripts import {module}  # adjust as needed


def test_{slug}():
    # Arrange: minimal input that triggered the original finding
    # Act: invoke the corrected code path
    # Assert: behaviour matches the fix
    pytest.skip("Draft - CEO to flesh out arrange/act/assert before promoting")
```

The draft is intentionally a `pytest.skip()` until the CEO fleshes out the assertion - silent passing tests are worse than missing ones. CEO writes the assertion as part of the approval flow; the test enters the suite only after approval.

### Rule regression artefact

For a finding on a rule (severity >= MEDIUM, applied, target is `.claude/rules/{name}.md`):

- The artefact is a YAML check pack in `.claude/rules/_regression/`.
- Name pattern: `{rule-name}-{slug}.yaml`.
- Body: structured assertions about rule presence, wording, or absence of contradicting content.

Draft body template:

```yaml
# Regression for /scrutinize finding {finding_id} on .claude/rules/{rule}.md
# Original finding ({severity}, conf {confidence}):
#   {statement}
# Fix applied: {one-line fix description}

rule_file: .claude/rules/{rule}.md
checks:
  - id: {finding_id}-{slug}
    type: must_contain  # or: must_not_contain, must_match_regex, must_have_section
    value: "{the corrected wording or required phrase}"
    severity_on_fail: HIGH
```

A separate workspace check (`scripts/rule-regression-runner.py`, to be built when the first rule artefact lands) consumes these YAML packs as part of the existing rule-validation suite. Until that runner ships, rule artefacts serve as documentation only - the file is committed but not yet automatically checked.

### Eligibility unchanged

The 5 eligibility rules (target is appropriate type, dir exists or will be scaffolded, finding is behaviour not typo, finding applied, severity >= MEDIUM) apply uniformly across skill / script / rule targets. The artefact shape differs; the gate does not.

## OUTCOME cases (R13, 2026-06-06)

An OUTCOME case grades a **side-effect**, not the model's prose. It lives in a SEPARATE directory - `.claude/skills/{name}/evals/outcomes/` - that the prose harness never globs (`run-skill-eval.py` and the eval-drift daemon read only `evals/cases/*.json`), so an outcome case can never trigger a model call, pollute `benchmark.json`, or be replayed against an empty check set. It is graded by `scripts/eval-outcomes.py` (`--skill NAME` / `--all`, `--render` to opt into a real render, `--no-write` to skip the `evals/benchmark-outcomes.json` sidecar). No model call - binary pass/fail.

A case is a prose case (`evals/cases/`, carries `checks`) OR an outcome case (`evals/outcomes/`, carries `outcome`) - separated by directory, never colliding.

### OUTCOME JSON shape

```json
{
  "id": "case-N-slug",
  "description": "One sentence stating the side-effect this case proves.",
  "outcome": { "type": "crm_log | doctype_render", "...": "per-type fields" }
}
```

Two assertor types ship today:

- **`crm_log`** - the email->CRM finalizer produced the right log. Fields: `conv_id`, `conversations` (the fixture list written to a sandbox `_latest-fetch.json`), `create_contacts` (slugs seeded under the sandbox `crm/contacts/`), `expect_ok`, `expected_slug`, `expected_date`, `expect_error`, `expect_idempotent`. The runner calls the real `log_to_crm` against a throwaway sandbox workspace and asserts the outcome (right slug + right date + no double-log).
- **`doctype_render`** - the doctype data dict has the expected field-presence result. Fields: `doctype` (one of `letter|proposal|partnership|official|xpager`), `data` (the dict), `expect_missing` (`[]` for a positive case, the exact missing-field list for a negative case). Default path is in-process `validate_required_fields` - no subprocess, no browser. On `--render`, a positive case additionally invokes the real renderer (non-PDF format) and asserts a file was produced.

### One-keystroke capture (`scripts/eval-flag.py`)

`eval-flag.py` is a lighter, CEO-driven sibling of Phase 4.5: it turns a flagged bad output into a DRAFT regression case keyed by the R12 trace ID, in one command, console-first and offline-capable.

```
eval-flag.py <id-or-prefix>             # stage a draft from a live Action Queue card (loopback)
eval-flag.py --skill NAME --note "..."  # offline; stage a draft from a note
eval-flag.py --list                     # list staged drafts
```

Drafts land in `.claude/skills/{name}/evals/outcomes/_staged/flag-{ts}-{slug}.json` (atomic write). They are **inert** and **gitignored** (`**/evals/outcomes/_staged/` in `.gitignore`): no runner globs `_staged/` (the loaders glob `evals/cases/*.json` and `evals/outcomes/*.json` at the top level only), and being untracked a staged draft can never be shipped by `publish-corporate.py` (tracked-only) nor trip its untracked-corporate-file block. Promotion is the existing never-auto-write discipline: the CEO edits the draft, then moves it up into `evals/outcomes/` and `git add`s it (a plain `mv` + `git add`, not `git mv`, since the source is gitignored). A `eval-flag.py promote` subcommand is deliberately deferred.

## NEVER

- Never auto-promote a finding to an eval case without an explicit CEO `promote` command for that case
- Never overwrite an existing case file (Phase 4.5 reads the directory first, picks the next free N)
- Never silently auto-create the `evals/cases/` directory; offer it as a scaffold step requiring explicit CEO approval (`scaffold and promote ...`, `scaffold only`, or `skip`)
- Never promote LOW or NIT findings - the eval suite must stay signal-rich
- Never promote findings whose fix was rejected or deferred - only applied findings qualify
- Never run Phase 4.5 in `--relentless` mode - relentless pre-approves fixes but eval-case promotion is a separate CEO decision
- Never run Phase 4.5 for `target=plan` - plans don't have skill files to test
- Never emit a script regression test with a real assertion the CEO has not approved - drafts start as `pytest.skip()` until the assertion is filled in
- Never run the rule regression checks until `scripts/rule-regression-runner.py` exists - until then, rule YAMLs are documentation only
