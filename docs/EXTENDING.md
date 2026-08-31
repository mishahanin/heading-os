<!-- version: 2.0.1 | last-updated: 2026-08-22 -->
# Extending the engine

How to build on HEADING OS: add a skill, a rule, or a script, and clear the gates
before declaring it done. This is the developer how-to. The contribution policy (open
an issue first; pull requests are by invitation) lives in
[CONTRIBUTING.md](https://github.com/mishahanin/heading-os/blob/main/CONTRIBUTING.md);
read it before sending code.

---

## 0. Dev setup

```bash
uv sync --all-extras --group dev   # core + all integration extras + dev tools (pytest, ruff, pre-commit)
pre-commit install                 # arm the commit-time gates (once per clone)
uv run python scripts/run-tests.py
```

A green test run on a fresh clone means your environment is sound.

---

## 1. The shape of the engine

Four kinds of artifact, each with its own home and conventions:

| Artifact | Lives in | Is |
|---|---|---|
| Skill | `.claude/skills/{name}/SKILL.md` | a slash-command workflow, routed from natural language |
| Rule | `.claude/rules/*.md` | always-on or path-scoped behavior the agent follows |
| Script | `scripts/*.py` | a CLI tool or daemon; shared code in `scripts/utils/` |
| Hook | wired in `.claude/settings*.json` | a `PreToolUse` / `PostToolUse` / `SessionStart` guard |

Before building anything: search for an existing pattern and reuse it. The standards
below are summarized from the engine's own development rules.

The documentation site itself (`docs/`) has its own contract. Every page is either
Markdown-sourced (regenerated) or hand-authored HTML. A drift guard fails the build if
the two fall out of sync. Read [DOCS-PIPELINE.md](DOCS-PIPELINE.html) before you edit
anything under `docs/`.

---

## 2. Writing a skill

A skill is a folder with a `SKILL.md`. The frontmatter is a contract:

```yaml
---
name: example-skill                    # kebab-case
description: >                          # what it does, when to use, AND when NOT to
  One paragraph. Name the alternative skill for the cases this one should not handle.
argument-hint: "[target]"
allowed-tools: "Read, Bash(python3:*)"  # least privilege
metadata:
  author: Your Name
  email: you@example.com
  version: "1.0"
x-heading-orchestration:                    # how the orchestrator may dispatch it
  parallel_safe: false                  # true | partial | false
  shared_state: []                      # paths it writes to
  triggers: ["example phrase"]          # natural-language triggers, or []
---
```

Rules of the road:

- **Body under 500 lines.** Overflow goes in a `references/` subdirectory.
- **Phased structure.** Phase 0 loads context, Phase 1 executes, Phase 2 synthesizes,
  Phase 3 outputs. Include a `NEVER` section listing prohibitions.
- **Routing-sensitive skills ship `triggers.json`** (6 to 10 positive and negative
  cases) and an entry in the skill router. That way a new skill cannot silently
  hijack another's queries.
- **Invocation control.** Add `disable-model-invocation: true` for high-blast-radius
  skills that should fire only on an explicit slash command.

The `/skill-creator` skill scaffolds and evaluates a new skill against these
standards.

---

## 3. Writing a rule

Rules in `.claude/rules/` load automatically. A rule with no frontmatter is always
active; a rule with a `paths:` list loads only when work touches those paths:

```yaml
---
paths:
  - "scripts/**"
---
```

Keep rules concise and single-purpose. Several existing rules encode security controls:
the send-gate, the engine/data separation, and the secret guards. Adapt brand and voice
rules freely. Leave the security ones in place.

---

## 4. Writing a script

```python
#!/usr/bin/env python3
"""One-line purpose. Usage examples in the docstring."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root   # reuse, don't reinvent
# ... argparse CLI ...
if __name__ == "__main__":
    main()
```

- **Naming.** `kebab-case.py` for CLI scripts you invoke directly; `snake_case.py` for
  anything in `scripts/utils/` or imported as a module (hyphens are illegal in Python
  module names).
- **Reuse `scripts/utils/`** for workspace paths, colors, API-key loading, and `.env`
  reads. Never hardcode paths; use `pathlib.Path` and the workspace helpers.
- **Data goes through the seam.** Write artifacts via the data-root helpers
  (`get_data_root()` / `get_*_dir()`), never into the engine tree.
- **Resolve a data path when you call, never at import.** `get_data_root()` reads
  `HEADING_OS_DATA` on every call. A module-level constant asks once, during its own
  import, and keeps that answer. A test that imports your module and then repoints
  the root still reads the operator's real data. Write a function instead:
  `def out_dir() -> Path: return get_outputs_dir() / "reports"`. The gate is
  `tests/test_a_tracked_dir_list_frozen_before_any_test_could_move_it.py`.
- **Catch `HTTPError` before `URLError`** (the former is a subclass).

---

## 5. The gates before "done"

Run these on anything you add or change:

```bash
uv run python scripts/sanitize-text.py <file> --scan   # zero hidden characters
uv run python -m py_compile <script>                    # Python syntax
uv run python scripts/run-tests.py                      # the suite
```

`ruff` (the linter) and the secret scan run automatically through `pre-commit`. Then:

- **The pre-push gate.** `push-all.py` runs the full regression suite (parallelized
  with `pytest-xdist`) before a push is allowed, plus the unbypassable secret content
  scan. Budget time for it; do not work around it.
- **The data overlay's own gate.** A data overlay that carries a `tests/` directory
  gets its own pre-push hook, which runs those tests and then hands off to git-lfs.
  Arm both repositories with `uv run python scripts/install-git-hooks.py`; verify
  with `--check`. An overlay with no `tests/` passes straight through, which is the
  normal case on a managed workspace.
- **CodeQL** runs on the repository for static security analysis; address what it
  flags on a pull request.
- **The prose path audit.** `check-path-references.py --check` runs as the pre-commit
  hook `path-references` and as a CI step. It fails when tracked Markdown gains a NEW
  reference to an engine path that does not exist. That is the rot you get when
  someone renames a script and leaves the docs that name it. Rename a script and this
  catches the prose in the same commit. Paths that route to the private overlay are
  skipped: the overlay is absent on a public clone, so its absence proves nothing.
  Placeholders, regex fragments and correct prose about deleted things are frozen
  in the scanner's `BASELINE`, each with the reason it should not exist. To add
  one, state the reason; to clean one up, delete the line.
- **The coverage report.** `check-path-references.py --coverage` lists the engine
  Python files that no prose describes. Run it after you add a script, to see
  whether your documentation landed. It is advisory and gates nothing.

  It excludes archive trees from the verdict: `outputs/`, `plans/archive/`,
  `chronicle/`, `docs/superpowers/` and `threads/`. A handoff summary that quotes a
  filename does not document that file. It also prints which prose sources it read.
  On a clone with no private overlay, a file documented only there reads as
  undocumented.

### The slice standard

Non-trivial work runs on Canopus, the build standard. It has seven numbered steps. Two
of them are the operator's own approval moments, and three instruments measure whether
the approval meant anything. **[Canopus, the build standard](CANOPUS.html)** is the full
page. It carries the steps, the four `check` clauses, what `probe` reads, and the two
places the standard reports rather than blocks.

One criterion is worth carrying here rather than following a link for, because
it is the one a builder acts on. `probe` runs the contract twice against
null-stubbed modules that carry different values. A test that never FAILS
under either run is vacuous. A pass, a skip and an error all leave a test
unproved; only a failure shows it read the value.

The three commands you will actually type while contributing:

```bash
python scripts/canopus.py probe tests/contract/<date>-<slug>/   # before approval
python scripts/canopus.py check --range origin/main..HEAD       # after the build
python scripts/canopus.py note <slug> ...                       # when it ships
```

Two facts worth carrying here rather than looking up. The approval is a COMMIT,
not a lock file. `git show <sha>:<path>` reads the frozen bytes. `git diff`
answers whether the contract moved. `git merge-base --is-ancestor` answers
whether the implementation descends from the approval. Nothing on this machine
holds those bytes down, and the CI clause that reads them REPORTS a break rather
than blocking one. Do not describe either as prevention.

The steps themselves are defined once, as data, in
`scripts/utils/canopus_steps.py`. The `/canopus` skill and the page above both
summarise that module; neither may renumber it.

---

## 6. Testing discipline

The suite lives in `tests/` (security tests in `tests/security/`). Every behavior you
change needs a test that exercises the real pattern through the public interface, not
an implementation detail. Write one test, make it pass, then the next.

When debugging, build a fast reproduction first: a failing test or a deterministic
harness that makes the bug appear and disappear on demand. Do not hypothesize about a
cause you cannot reproduce. Write the regression test before the fix, watch it fail,
apply the fix, watch it pass.

### Name the tests your module cannot match by name

`scripts/turn-check.py` runs at the end of a turn. It maps a changed module to its
tests by name. `wizard-verify-key.py` maps to `test_wizard_verify_key.py`. A module
whose tests carry behaviour names matches nothing. The lane then runs no test and
still prints `clean`.

Declare the fast contract in the module docstring. Start the line at column 0:

<!-- ste-skip-start -->
```
Tests: tests/test_turn_check.py, tests/test_session_scope.py
```
<!-- ste-skip-end -->

Repeat the line when the paths do not fit. Name tests that are cheap and that pin this
module. Leave out tests that sleep, because `scripts/run-tests.py` still runs those. An
indented line is an example, not a declaration. A declared path with no file behind it
fails `tests/test_turn_check.py`.

---

## 7. Restraint

- **Simplicity.** The minimum artifact that solves the problem. No speculative
  features, flags, or abstractions for single-use code.
- **Surgical changes.** Touch only what the task requires. Do not refactor adjacent
  code, and match the style of the file you are editing.
- **No new dependency** without raising it in the issue first; pin exact versions.
- **Security and review findings override restraint.** Fix an open finding in a file
  you touch before the requested change, and say so if it widens the diff.

---

## 8. Reference

| File | Role |
|---|---|
| [`CONTRIBUTING.md`](https://github.com/mishahanin/heading-os/blob/main/CONTRIBUTING.md) | Contribution policy (issues, PR by invitation) |
| `scripts/run-tests.py` | The test runner |
| `tests/`, `tests/security/` | The regression suite |
| `.claude/skills/skill-creator/` | Scaffolds and evaluates a new skill |
| `scripts/utils/` | Shared modules to reuse |
| `pyproject.toml` | Pinned dependencies, ruff / pytest config |

---

## 9. Trigger regression tests

Moved here from `.claude/rules/skill-router.md` on 2026-08-20 — it is authoring
guidance, never routing, and that rule loads on every session.

The router is a markdown rule the model interprets, so a new skill's triggers can
silently hijack another skill's queries. `scripts/skill-trigger-test.py` is an LLM-judge
harness that regression-tests this. It feeds the router rules plus a target skill's
description to a judge model. It then checks whether each query in
`.claude/skills/{name}/triggers.json` routes as expected (`should_trigger`).

Run `python scripts/skill-trigger-test.py --all`. Use `--skill NAME` for one skill. Use
`--changed [--base REF]` to test only the skills whose `SKILL.md` or `triggers.json`
changed since the base, which defaults to `origin/main`. A `skill-router.md` change
widens the scope to all skills. The harness is **advisory** by default, because the judge
is not deterministic. It gates only under `--strict --threshold`.

`/push-updates` Phase 0 runs `--changed --strict --threshold 0.85` as a **soft gate**. It
surfaces routing regressions on changed skills, and the CEO confirms to override. It is
not a hard block yet, per audit #63-2.

70 routing-sensitive skills carry `triggers.json`, and they hold 730 cases between them.
`scripts/dev/check-readme-numbers.py` derives both figures from the tree, and it fails
when this page disagrees. The `readme-numbers` pre-commit hook runs that guard whenever a
`triggers.json` file or this page changes. Until 2026-08-29 someone typed the two numbers
by hand and dated them to the day of the last count. Both had drifted. When you add or
re-scope a skill, update its `triggers.json` and re-run the harness.

---

## 10. Archived skills

`.claude/skills/archive/{date-slug}/SKILL.md` is the workspace convention for retired
skills. The parent `archive/` directory has no SKILL.md of its own and is intentionally
inert. Claude Code's skill discovery is single-level and does not auto-load nested
skills. Archived skills do not appear in the skill router registry. They are never
invoked unless you retrieve one explicitly, with `git mv` back into
`.claude/skills/{name}/`. Do NOT create a stub SKILL.md inside `archive/` itself; that
would shadow the convention and risk false routing.

---

*HEADING OS · Extending the engine · maintained by Misha Hanin.* See also:

- [Architecture](ARCHITECTURE.html): how the pieces compose.
- [Security model](SECURITY-MODEL.html): the controls your code inherits.
