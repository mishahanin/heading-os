"""Two tests swept every tracked Markdown file and were not in the mandatory core.

Day mode's core is the set of tests whose input is the whole repository tree.
It is derived, not listed: a test is in it when it calls a sweep on a
root-anchored path or one of the shared "files in this repository" helpers in
`REPO_HELPERS`. `scripts/check-path-references.tracked_markdown` is such a
helper - `git ls-files '*.md'`, the entire Markdown corpus - and it was not in
the set, so the two tests that call it sat outside the core. A new or renamed
`.md` file changes what they read, and nothing selected them.

MEASURED 2026-09-05, both directions, on the real index:

    core without `tracked_markdown`   158 test files
    core with it                      160
    joining:  tests/test_path_references.py
              tests/test_an_acceptance_gate_that_grades_the_wrong_world.py

WHAT THIS IS NOT. The note that sent me here said this covered "15 of 38
no-route instances" in the pre-push gate. It covers none, and the reason is
structural rather than a matter of degree: a sweep puts a test in the CORE, and
the core is added to every selection without being attributed to any one changed
file, so it never moves a file out of `undecided` and never changes a push-gate
decision. Measured to be sure rather than argued: `docs/ARCHITECTURE.md`,
`README.md` and `.claude/rules/voice.md` each report `undecided == []` with the
helper and without it. This test therefore asserts the core membership, which is
the real effect, and not a gate verdict, which is not.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.day_mode import (  # noqa: E402
    REPO_HELPERS,
    build_index,
    extract,
    select,
)

SWEEPING = [
    "tests/test_path_references.py",
    "tests/test_an_acceptance_gate_that_grades_the_wrong_world.py",
]


def test_the_helper_is_registered():
    assert "tracked_markdown" in REPO_HELPERS


def test_a_call_through_the_module_is_recorded_as_a_sweep():
    source = (
        "import scripts\n"
        "def test_x():\n"
        "    for rel in cpr.tracked_markdown(ROOT):\n"
        "        assert rel\n"
    )
    assert "tracked_markdown" in extract("tests/test_x.py", source).sweeps


def test_a_bare_call_is_recorded_too():
    source = (
        "from scripts import tracked_markdown\n"
        "def test_x():\n"
        "    assert tracked_markdown(ROOT)\n"
    )
    assert "tracked_markdown" in extract("tests/test_y.py", source).sweeps


def test_the_two_sweeping_tests_are_in_the_mandatory_core():
    """The real index. Both files call the helper and both must be in the core."""
    index = build_index(ROOT, use_cache=False)
    for rel in SWEEPING:
        assert (ROOT / rel).exists(), f"{rel} has moved; re-derive this list"
        assert rel in index.core, f"{rel} sweeps every tracked .md but is not in the core"
        assert "tracked_markdown" in index.core[rel]


def test_the_core_did_not_swallow_the_suite():
    """The direction that keeps day mode worth running.

    A helper name added to `REPO_HELPERS` too loosely puts most of the suite in
    the core, and the core runs on every invocation. MEASURED 2026-09-05: 160 of
    1095 test files. The ceiling is generous but far below the point at which
    selection stops meaning anything.
    """
    index = build_index(ROOT, use_cache=False)
    total = len(index.test_files)
    assert total >= 1000, f"corpus floor: only {total} test files found"
    assert len(index.core) < total * 0.30, (
        f"the mandatory core is {len(index.core)} of {total} test files"
    )


def test_this_changes_no_push_gate_verdict():
    """Stated as a test because the note that prompted the change claimed otherwise.

    A markdown change is decided by the literal route or not at all. The core is
    not a route.
    """
    index = build_index(ROOT, use_cache=False)
    for rel in ["docs/ARCHITECTURE.md", "README.md", ".claude/rules/voice.md"]:
        assert select(index, [rel]).undecided == [], rel
    assert isinstance(ast.parse("x = 1"), ast.Module)
