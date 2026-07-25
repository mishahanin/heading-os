<!-- version: 1.0.2 | last-updated: 2026-07-08 -->
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

Editing the documentation site itself (`docs/`) has its own contract: every page is
either Markdown-sourced (regenerated) or hand-authored HTML, and a drift guard fails
the build if the two fall out of sync. See [DOCS-PIPELINE.md](DOCS-PIPELINE.html)
before editing anything under `docs/`.

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
  cases) and an entry in the skill router, so a new skill cannot silently hijack
  another's queries.
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

Keep rules concise and single-purpose. Several existing rules encode security controls
(the send-gate, the engine/data separation, the secret guards); adapt brand and voice
rules freely, but leave the security ones in place.

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
- **CodeQL** runs on the repository for static security analysis; address what it
  flags on a pull request.

### Freezing the test contract

`scripts/canopus.py` locks a set of paths so the tests that prove a change cannot be
edited by whoever is making the change.

```bash
python scripts/canopus.py freeze tests/test_thing.py --label my-slice \
    --anchor ../my-notes-repo/plans/2026-07-25-pre-impl-my-slice.md
python scripts/canopus.py verify
python scripts/canopus.py status
python scripts/canopus.py release --reason "slice shipped"
```

`freeze` writes a per-file digest manifest to a gitignored `.canopus/`, records the event
in an append-only ledger, and prints a **root hash** plus the exact `canopus-anchor:` line
to paste into the anchor artifact and commit. From then on `verify` reads the expected
hash from that artifact by itself. Nobody types a digest and nobody compares one by eye.

Three layers, and the differences matter:

- The PreToolUse deny is a **convenience**. It sees `Write` and `Edit` tool calls only, so
  a shell `sed -i` or an agent with its own toolset walks past it.
- `verify` is the **guarantee**. It recomputes digests from disk and catches a change made
  by any route.
- `scripts/run-tests.py` is what makes the guarantee **fire**. It runs the check before the
  suite, so a build cannot reach green while its contract is moved. A verification that is
  never invoked is worth nothing, however well its expected value is protected.

`verify` reports one of three states:

| State | Meaning |
|---|---|
| `LOCK HELD` | the manifest is intact and matches the hash recorded in the anchor artifact |
| `LOSS OF LOCK` | something changed, or the anchor disagrees or vanished. Exit code 1. |
| `LOCK UNCONFIRMED` | no anchor hash is recorded yet: nothing changed since the last check, which is not the same as "this is the approved contract" |

The anchor must live outside the working tree. An anchor the build can write to is not an
anchor. Point it at a sibling repository with its own history, so a build reaching for the
anchor leaves a commit in a repository it had no reason to touch.

Freezing a directory is recursive. Freezing a file also guards its parent directory's
immediate membership, which catches a `conftest.py` dropped beside a frozen test to
neutralize it.

If the manifest is ever damaged, every write is denied fail-closed. Clear it with
`release --force`, which is logged. Deleting the file by hand also works and deliberately
leaves a gap in an append-only ledger.

A contract that turns out to be wrong is not edited in place. Release it, fix it,
re-freeze it, and get the new root hash re-approved.

---

## 6. Testing discipline

The suite lives in `tests/` (security tests in `tests/security/`). Every behavior you
change needs a test that exercises the real pattern through the public interface, not
an implementation detail. Write one test, make it pass, then the next.

When debugging, build a fast reproduction first: a failing test or a deterministic
harness that makes the bug appear and disappear on demand. Do not hypothesize about a
cause you cannot reproduce. Write the regression test before the fix, watch it fail,
apply the fix, watch it pass.

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

*HEADING OS · Extending the engine · maintained by Misha Hanin · see also
[Architecture](ARCHITECTURE.html) for how the pieces compose and
[Security model](SECURITY-MODEL.html) for the controls your code inherits.*
