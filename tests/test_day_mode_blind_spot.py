"""A source file a test exercises, that day mode cannot select, must be NAMED.

THE DEFECT THIS EXISTS FOR, measured 2026-09-04 on this repository.
`.claude/hooks/_dispatch.py` is the single PreToolUse entry point for twelve
walls and the most security-critical file in the tree. `codegraph affected`
reports ZERO tests affected by it, and the same is true of fifteen of the
sixteen files under `.claude/hooks/`: their tests drive them as a SUBPROCESS,
and a subprocess is not an import edge. A day mode built on an import graph
alone would have run the mandatory core, reported a pass, and let a change to
every wall in this workspace through untested.

WHY THIS IS A DERIVED CHECK AND NOT A LIST. The obvious repair is to write down
the files the graph cannot see and always run their tests. This repository
already records what happens to that shape: a hand-maintained security list
falls behind silently, and the day it matters is the day nobody notices. So the
question is asked of the TREE on every run. For each tracked non-test Python
file: does any test mention its stem, and can day mode select a test for it? A
file that is mentioned and unselectable is blind, and blind is a failure.

THE BASELINE IS THE EXCEPTION LIST, NOT THE MECHANISM, and it only shrinks.
`config/day-mode-blind-baseline.json` holds the files that were already blind
when day mode landed, each with the reason someone read out of the source. A new
file cannot join it without a deliberate edit, which is the whole point: the
guard fails on arrival, not at the next audit. An entry that has STOPPED being
blind also fails, because a registered exception guarding nothing is how the
list starts drifting from the tree.

FLOOR. Every assertion below that loops over a discovered corpus is preceded by
an assertion on the size of that corpus. A blind-spot check over an empty set of
candidates is green and means nothing, which is the exact failure
`.claude/rules/development-standards.md` obligation 7 names.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_BASELINE = _ROOT / "config" / "day-mode-blind-baseline.json"

# Measured 2026-09-04 on this repository: 455 tracked non-test Python files were
# candidates and 1077 test files were searched. The floors sit well under both,
# so they pin against the corpus collapsing without going red on ordinary churn.
_MIN_CANDIDATES = 300
_MIN_TEST_FILES = 800


@pytest.fixture(scope="module")
def index():
    from scripts.utils.day_mode import build_index

    return build_index(_ROOT)


def _baseline() -> dict:
    return json.loads(_BASELINE.read_text(encoding="utf-8"))


@pytest.mark.slow
def test_no_new_file_is_blind_to_day_mode(index):
    """The guard. A file no route can reach, that a test names, fails here.

    When this goes red the answer is almost never to add the file to the
    baseline. It is to look at how its test reaches it and ask whether the
    literal route can be taught that shape, because every shape it learns closes
    the hole for every future file at once.
    """
    from scripts.utils.day_mode import blind_files

    candidates = [
        rel
        for rel in index.tracked
        if rel.endswith(".py") and not rel.startswith("tests/")
    ]
    assert len(candidates) >= _MIN_CANDIDATES, (
        f"only {len(candidates)} candidate source files; the walk collapsed and"
        " this check would pass over nothing"
    )
    assert len(index.test_files) >= _MIN_TEST_FILES, (
        f"only {len(index.test_files)} test files discovered; the corpus this"
        " check searches for mentions collapsed"
    )

    known = set(_baseline()["blind"])
    blind = set(blind_files(index))
    surprises = sorted(blind - known)
    assert not surprises, (
        "these files are exercised by a test that names them, and day mode can"
        " select no test for them. A change to any one would run only the"
        f" mandatory core:\n  " + "\n  ".join(surprises)
    )


@pytest.mark.slow
def test_the_baseline_only_shrinks(index):
    """A registered exception that is no longer blind must be removed.

    Without this the list accumulates entries that guard nothing, and the day
    someone reads it to learn what day mode cannot see, it is describing a tree
    that stopped existing.
    """
    from scripts.utils.day_mode import blind_files

    known = set(_baseline()["blind"])
    assert known, "the baseline is empty; either delete it or record what it exempts"
    blind = set(blind_files(index))
    healed = sorted(known - blind)
    assert not healed, (
        "these files are no longer blind and must be dropped from"
        f" {_BASELINE.relative_to(_ROOT)}:\n  " + "\n  ".join(healed)
    )


def test_every_baseline_entry_carries_a_reason_and_still_exists():
    """An exemption with no reason is a shrug, and one naming a deleted file is
    a claim about a tree that has moved on."""
    data = _baseline()
    assert data["blind"], "empty baseline"
    for rel in data["blind"]:
        reason = data["reasons"].get(rel, "")
        assert len(reason) > 40, f"{rel} has no real reason: {reason!r}"
        assert (_ROOT / rel).exists(), f"{rel} is in the baseline but not in the tree"


def test_the_guard_goes_red_when_a_blind_file_appears(tmp_path):
    """The failing half, and the one that matters.

    A guard nobody has watched refuse is a guard nobody has tested. This builds
    the exact shape the real defect has -- a script reached only through a path
    assembled from an f-string, so neither the import route nor the literal
    route can see it -- and asserts `blind_files` names it. Against a
    `blind_files` that returned an empty list, every other test in this file
    still passes and this one fails.
    """
    from scripts.utils.day_mode import blind_files, build_index

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "invisible.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_drives_it.py").write_text(
        textwrap.dedent(
            """
            import subprocess, sys
            from pathlib import Path
            import pytest
            ROOT = Path(__file__).resolve().parent.parent

            @pytest.mark.parametrize("name", ["invisible"])
            def test_it(name):
                path = ROOT / "scripts" / f"{name}.py"
                assert path.exists()
            """
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True
    )

    index = build_index(tmp_path, use_cache=False)
    assert "scripts/invisible.py" in blind_files(index)


def test_a_file_reachable_by_a_literal_is_not_blind(tmp_path):
    """The passing half. The same tree, with the path spelled out.

    This is what makes the test above a measurement rather than a tautology: the
    only difference between the two trees is the f-string.
    """
    from scripts.utils.day_mode import blind_files, build_index

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "invisible.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_drives_it.py").write_text(
        textwrap.dedent(
            """
            from pathlib import Path
            ROOT = Path(__file__).resolve().parent.parent

            def test_it():
                assert (ROOT / "scripts" / "invisible.py").exists()
            """
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True
    )

    index = build_index(tmp_path, use_cache=False)
    assert blind_files(index) == []


def test_the_cli_exits_non_zero_while_anything_is_blind():
    """`day-mode.py blind` is the operator's surface for this, so it has to
    report through its exit code and not only its stdout."""
    result = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "day-mode.py"), "blind"],
        capture_output=True,
        text=True,
        cwd=_ROOT,
    )
    assert result.returncode in (0, 1), result.stderr
    if result.returncode == 1:
        assert "blind file" in result.stdout
