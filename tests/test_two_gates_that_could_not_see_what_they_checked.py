"""Two gates that ran, reported clean, and could not see the thing they check.

Both are the same shape: a check exists, is correct as far as it goes, and stops
one step short of the property it is named after.

1. **`scripts/skill-metadata-check.py` validated `shared_state`'s CONTAINER, not
   its contents.** `.claude/rules/skill-orchestrator.md` § Conflict Detection
   step 4 decides whether two skills may run concurrently by intersecting these
   lists by substring. The checker asserted only `isinstance(value, list)`.
   MEASURED 2026-08-31 on a fixture skill holding `Write, Edit` and declaring
   `parallel_safe: true` with `shared_state: []`: status WARN, `invalid_values`
   empty. Change the same field to the string `outputs/liar/` and it FAILs. The
   gate had an opinion about the type and none about the value.

   The emptiness half of that is not fixable in the checker, and this file does
   not try: frontmatter cannot say whether a skill writes. `allowed-tools` is a
   grant rather than a limit, and most writing here happens inside a script
   reached through `Bash(python3:*)`. That half lives in
   `test_two_skill_contracts_that_were_declared_and_never_measured.py`, which
   can carry a per-skill reason. What IS decidable from frontmatter, and is
   fixed here, is the entry level: a `None`, a mapping or a blank string is a
   list element that passes the container check and can never intersect a
   sibling's path.

2. **`scripts/artifact-evaluator.py`'s "Consumed by:" check was a substring
   scan** (`"consumed by" not in ref_content.lower()`), and `warn=True` on top.
   MEASURED 2026-08-31 on a reference file whose only occurrence was the prose
   "This data is consumed by downstream tooling somewhere": the evaluator
   stamped it `pass`, and the run exited 0 even with a sibling file that had no
   pointer at all. A gate that cannot fail is bad; one that reports OK on a file
   the real gate rejects is worse, because the OK is read as coverage.

   The evaluator stays advisory and stays `warn=True` - see the module comment
   on `has_consumed_by_pointer`. What changed is that its verdict now matches
   the corpus gate's. `test_the_evaluator_agrees_with_the_corpus_gate` pins them
   together on the live corpus so they cannot drift apart again.

Every walk here asserts a floor first. A loop over an empty corpus passes every
assertion inside it.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_two_skill_contracts_that_were_declared_and_never_measured import (
    _consumed_by_line,
    _reference_files,
)

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / ".claude" / "skills"

# Well under the live counts (94 SKILL.md / 83 reference files on 2026-08-31).
MIN_SKILLS = 80
MIN_REFERENCE_FILES = 70


def _load(stem: str, filename: str):
    spec = importlib.util.spec_from_file_location(stem, str(ROOT / "scripts" / filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[stem] = mod
    spec.loader.exec_module(mod)
    return mod


metadata_check = _load("skill_metadata_check_under_test", "skill-metadata-check.py")
evaluator = _load("artifact_evaluator_under_test", "artifact-evaluator.py")


_FIXTURE_SKILL = """---
name: {name}
description: Fixture skill for the shared_state entry check.
argument-hint: "[x]"
allowed-tools: "Read, Write, Edit"
metadata:
  author: Jane Placeholder
  email: jane@example.invalid
  version: "1.0"
x-heading-orchestration:
  parallel_safe: true
  shared_state: {shared_state}
  triggers: []
x-heading-routing:
  category: Operations
  triggers: []
  exclusions: ["N/A"]
  compound: 'No'
  router: manual
---
# Fixture
"""


def _check_fixture(tmp_path: Path, monkeypatch, shared_state: str) -> dict:
    """Run the real `check_skill` over a fixture SKILL.md.

    `check_skill` reports a repo-relative path, so the workspace root is pointed
    at the fixture tree for the call. The checker itself is unpatched: this is
    the shipped function reading a real file, not a reimplementation of it.
    """
    skill_dir = tmp_path / "fixture-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        _FIXTURE_SKILL.format(name="fixture-skill", shared_state=shared_state),
        encoding="utf-8")
    monkeypatch.setattr(metadata_check, "get_workspace_root", lambda: tmp_path)
    return metadata_check.check_skill(skill_dir)


def _shared_state_complaints(result: dict) -> list[str]:
    return [v for v in result["invalid_values"] if "shared_state" in v]


# ---------------------------------------------------------------------------
# Gate 1 - shared_state entries, not just the list around them
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "declared,why",
    [
        ("[null]", "a None entry matches no sibling path"),
        ("[{}]", "a mapping entry matches no sibling path"),
        ('[""]', "an empty string entry matches no sibling path"),
        ('["   "]', "a whitespace-only entry matches no sibling path"),
        ('["outputs/design/", null]', "one bad entry among good ones still hides a path"),
    ],
)
def test_an_unusable_shared_state_entry_is_rejected(tmp_path, monkeypatch, declared, why):
    result = _check_fixture(tmp_path, monkeypatch, declared)
    assert _shared_state_complaints(result), (
        f"shared_state={declared} passed the checker, but {why}"
    )
    assert result["status"] == "FAIL", (
        f"shared_state={declared} produced status={result['status']!r}; an "
        f"invalid value must fail, not warn"
    )


@pytest.mark.parametrize(
    "declared",
    ['["outputs/design/", "outputs/content/images/"]',
     '["/tmp/docparse_parsed.json"]',
     '["../heading-os-corporate/"]',
     "[]"],
)
def test_a_usable_shared_state_declaration_is_accepted(tmp_path, monkeypatch, declared):
    """The negative cases above must not be an accident of failing everything.

    `[]` is here deliberately. It is NOT rejected by the checker, and that is
    the argued boundary, not an oversight: whether a skill that declares nothing
    writes nothing is a judgement the frontmatter cannot answer, so it is the
    sibling test's job. Absolute paths and parent-relative paths are here
    because /docparse and /publish-corporate really declare them.
    """
    result = _check_fixture(tmp_path, monkeypatch, declared)
    assert not _shared_state_complaints(result), (
        f"shared_state={declared} is a legitimate declaration and was rejected: "
        f"{result['invalid_values']}"
    )


def test_the_live_skill_corpus_declares_no_unusable_entry():
    """The rule must not fire spuriously on the tree it just landed in."""
    skill_dirs = sorted(d for d in SKILLS_DIR.iterdir()
                        if (d / "SKILL.md").is_file())
    assert len(skill_dirs) >= MIN_SKILLS, (
        f"only {len(skill_dirs)} skills found under {SKILLS_DIR}; the walk is "
        f"broken and the assertion below is vacuous"
    )
    offenders = {}
    for d in skill_dirs:
        complaints = _shared_state_complaints(metadata_check.check_skill(d))
        if complaints:
            offenders[d.name] = complaints
    assert not offenders, f"live skills rejected by the new entry rule: {offenders}"


# ---------------------------------------------------------------------------
# Gate 2 - the evaluator's "Consumed by:" verdict
# ---------------------------------------------------------------------------

_POINTER_SHAPES = [
    "Consumed by: `.claude/skills/design/SKILL.md`",
    "**Consumed by:** /design",
    "> Consumed by: /design",
    "- Consumed by: /design",
    "_Consumed by_: /design",
]


@pytest.mark.parametrize("line", _POINTER_SHAPES)
def test_the_evaluator_accepts_a_real_pointer(line):
    text = f"# Title\n\nLast Updated: 2026-08-31\n\n{line}\n\nBody.\n"
    assert evaluator.has_consumed_by_pointer(text), f"rejected a real pointer: {line!r}"


@pytest.mark.parametrize(
    "text,why",
    [
        ("# Title\n\nLast Updated: 2026-08-31\n\nBody with no pointer.\n",
         "no pointer at all"),
        ("# Title\n\nThis data is consumed by downstream tooling somewhere.\n",
         "a mid-sentence prose mention is not a labelled pointer"),
        ("# Title\n\nEverything here is consumed by: nobody in particular, said Bob.\n",
         "the label must open the line, not sit at the end of a sentence"),
        ("---\nconsumed by: /design\n---\n\n# Title\n\nBody.\n",
         "a frontmatter key is not the body pointer the rule asks for"),
    ],
)
def test_the_evaluator_rejects_what_is_not_a_pointer(text, why):
    assert not evaluator.has_consumed_by_pointer(text), why


def test_the_evaluator_agrees_with_the_corpus_gate():
    """One rule, two readers, and they must not diverge.

    `_consumed_by_line` in the sibling test file is the corpus gate that runs in
    CI over all 83 reference files; `has_consumed_by_pointer` is the
    single-artifact advisory reader. Before 2026-08-31 the second was a
    substring scan and could report OK where the first reports missing.
    """
    refs = _reference_files()
    assert len(refs) >= MIN_REFERENCE_FILES, (
        f"only {len(refs)} reference files found; the glob is broken and the "
        f"comparison below is vacuous"
    )
    disagree = []
    for ref in refs:
        gate = _consumed_by_line(ref) is not None
        advisory = evaluator.has_consumed_by_pointer(ref.read_text(encoding="utf-8"))
        if gate != advisory:
            disagree.append(f"{ref.relative_to(ROOT)}: gate={gate} evaluator={advisory}")
    assert not disagree, (
        "the corpus gate and the evaluator disagree about which reference files "
        "carry a pointer: " + "; ".join(disagree)
    )


# ---------------------------------------------------------------------------
# Gate 3 - the CI bandit scan reaches every tree that ships Python
# ---------------------------------------------------------------------------

def test_the_ci_bandit_scan_covers_every_tree_that_ships_tracked_python():
    """The recursive roots were `scripts .claude/hooks` until 2026-08-31.

    41 tracked Python files ship inside `.claude/skills/` and sat outside both,
    so the CI scan opened 395 files and none of them. The pre-commit bandit hook
    did reach them, which left "the developer ran `pre-commit install`" as the
    only protection on code CI exists to backstop.

    Derived from `git ls-files`, not from a hard-coded list, so a new tree that
    starts shipping Python fails this instead of quietly going unscanned. The
    step is located by parsing the workflow and reading the resolved command
    string of the step named `bandit`.
    """
    import shlex

    import yaml

    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = [s for job in workflow["jobs"].values()
             for s in job.get("steps", []) if s.get("name") == "bandit"]
    assert len(steps) == 1, f"expected exactly one CI step named 'bandit', found {len(steps)}"

    argv = shlex.split(steps[0]["run"])
    roots = {a for a in argv[argv.index("-r") + 1:] if not a.startswith("-")}
    assert roots, "the bandit step passes -r with no roots"

    # `-z`, and split on NUL: git C-quotes any path holding a non-ASCII byte, so
    # a plain read drops those files from the coverage set without a word, and
    # `.split()` additionally splits a path that holds a space into two. Either
    # way an unscanned tree could hide behind a name this reader cannot spell.
    # MEASURED 2026-08-31: 1329 tracked .py, identical under both spellings
    # today, which is the point -- the defect is silent until the first such
    # filename lands.
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.py"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    ).stdout
    tracked = [rel for rel in out.split("\0") if rel]
    assert len(tracked) >= 400, (
        f"only {len(tracked)} tracked .py found; git ls-files is not answering "
        f"and the coverage assertion below is vacuous"
    )

    def covered(rel: str) -> bool:
        return any(rel == r or rel.startswith(r.rstrip("/") + "/") for r in roots)

    # tests/ is deliberately out of scope for bandit here, matching the pre-existing
    # roots; the assertion is about SHIPPED code.
    unscanned = sorted({p.split("/")[0] + ("/" + p.split("/")[1] if p.startswith(".claude/") else "")
                        for p in tracked
                        if not covered(p) and not p.startswith("tests/")})
    assert not unscanned, (
        f"trees shipping tracked Python that the CI bandit scan never opens: {unscanned}"
    )


_REF_FIXTURE_SKILL = """---
name: fixture-ref-skill
description: Fixture skill whose reference file carries no real pointer.
metadata:
  author: Jane Placeholder
  version: "1.0"
---
# Fixture

## NEVER
- never
"""


@pytest.mark.parametrize(
    "ref_body,expect_warn",
    [
        ("# Title\n\nLast Updated: 2026-08-31\n\nConsumed by: /design\n",
         False),
        ("# Title\n\nLast Updated: 2026-08-31\n\nThis is consumed by downstream tooling.\n",
         True),
        ("# Title\n\nLast Updated: 2026-08-31\n\nNo pointer here.\n",
         True),
    ],
)
def test_the_skill_evaluator_call_site_uses_the_label_reader(tmp_path, ref_body, expect_warn):
    """Binds `evaluate_skill`, not just the helper it calls.

    A mutation that reverted ONLY the call site back to
    `"consumed by" not in ref_content.lower()` - leaving the corrected helper
    in place and unused - survived every other test in this file. A fix that
    lands in one of two places is the defect this case exists to catch.
    """
    skill_dir = tmp_path / "fixture-ref-skill"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_REF_FIXTURE_SKILL, encoding="utf-8")
    (skill_dir / "references" / "notes.md").write_text(ref_body, encoding="utf-8")

    results = evaluator.evaluate_skill(skill_dir)
    ref_checks = [c for c in results if c["name"] == "ref_notes.md"]
    assert len(ref_checks) == 1, f"expected one ref check, got {ref_checks}"
    detail = ref_checks[0]["detail"]
    warned = "missing 'Consumed by' pointer" in detail
    assert warned is expect_warn, (
        f"evaluate_skill reported {detail!r} for a reference file that "
        f"{'lacks' if expect_warn else 'carries'} a labelled pointer"
    )
