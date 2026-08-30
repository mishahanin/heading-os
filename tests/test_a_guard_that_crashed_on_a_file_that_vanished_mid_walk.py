#!/usr/bin/env python3
"""A tree sweep gathers its paths, then reads them. Files move in between.

Seven sweeps under `tests/` walked the repository, collected a path list, and
read each entry with a bare `path.read_text(...)`. In a workspace where several
agents work against one checkout, a file can be created and deleted inside that
window.

MEASURED 2026-08-30: an agent's scratch file,
`tests/test_turn_check_empty_masked_real_fixture.py`, existed when
`tests/test_subprocess_interpreter_guard.py` gathered its corpus and was gone
when it read it. The suite failed with

    FAILED tests/test_subprocess_interpreter_guard.py::
        test_no_bare_python_interpreter_in_spawned_commands
    FileNotFoundError: .../tests/test_turn_check_empty_masked_real_fixture.py

A crash inside the guard, presented as though the guard had caught something.
Nothing was violated.

Silently swallowing the miss is the other half of the same defect: the sweep
would then hold its verdict over a corpus that shrank underneath it and say
nothing, which `.claude/rules/scope-claims.md` forbids. So `read_sources` skips
the vanished path, WARNS naming it, and hands the caller a list to put in its
own message.

The direction that matters just as much is the second one: a file that is
genuinely there and genuinely violating must still fail the guard. A fix that
turns a crash into a pass is worse than the crash.

Run:
    .venv/bin/python -m pytest \\
        tests/test_a_guard_that_crashed_on_a_file_that_vanished_mid_walk.py \\
        -q --no-header -p no:randomly
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.repo_files import read_sources  # noqa: E402

SPAWNER = "import subprocess\nsubprocess.run(['python3', 'x.py'])\n"
CLEAN = "import subprocess\nimport sys\nsubprocess.run([sys.executable, 'x.py'])\n"


def _corpus(tmp_path: Path, **files: str) -> list[Path]:
    """Write a corpus and return the gathered path list, as a walk would."""
    made = []
    for name, body in files.items():
        p = tmp_path / f"{name}.py"
        p.write_text(body, encoding="utf-8")
        made.append(p)
    return sorted(made)


# ============================================================
# The measured failure
# ============================================================


def test_a_path_that_vanishes_between_the_walk_and_the_read_does_not_crash(tmp_path):
    """The exact race: gathered while present, read after deletion."""
    a, b, c = _corpus(tmp_path, a=CLEAN, b=CLEAN, c=CLEAN)
    walked = [a, b, c]

    b.unlink()  # a parallel agent removes its scratch file

    with pytest.warns(UserWarning):
        got = [p for p, _ in read_sources(walked)]

    assert got == [a, c], "the survivors must still be read"


def test_the_vanished_path_is_reported_not_silently_dropped(tmp_path):
    """A sweep whose corpus shrank in silence claims coverage it does not have."""
    a, b = _corpus(tmp_path, a=CLEAN, b=CLEAN)
    b.unlink()

    vanished: list[Path] = []
    with pytest.warns(UserWarning, match="vanished between the walk and the read"):
        list(read_sources([a, b], vanished))

    assert vanished == [b], "the caller must be able to name what it did not read"


def test_the_warning_names_the_file(tmp_path):
    """"One path was skipped" is not a report. The operator needs the name."""
    a, b = _corpus(tmp_path, a=CLEAN, b=CLEAN)
    b.unlink()

    with pytest.warns(UserWarning) as caught:
        list(read_sources([a, b]))

    assert str(b) in str(caught[0].message)


def test_a_complete_walk_warns_about_nothing(tmp_path):
    """The ordinary case must stay silent, or the warning becomes noise nobody
    reads - and a warning nobody reads is the silence this test forbids."""
    walked = _corpus(tmp_path, a=CLEAN, b=CLEAN)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        got = [p for p, _ in read_sources(walked)]

    assert got == walked


def test_every_surviving_file_is_read_in_full(tmp_path):
    """Skipping must not truncate: the text handed over is the file's own."""
    a, b = _corpus(tmp_path, a=SPAWNER, b=CLEAN)
    ghost = tmp_path / "ghost.py"
    ghost.write_text(CLEAN, encoding="utf-8")
    walked = sorted([a, b, ghost])
    ghost.unlink()

    with pytest.warns(UserWarning):
        pairs = dict(read_sources(walked))

    assert pairs[a] == SPAWNER
    assert pairs[b] == CLEAN


# ============================================================
# The other direction: the guard must still catch a real violation
# ============================================================


def test_a_present_violating_file_is_still_caught(tmp_path):
    """A fix that turns a crash into a pass is worse than the crash.

    Same composition the guard runs: read the corpus, parse it, detect. The
    violating file is present the whole time and must be reported.
    """
    guard = _load_guard()
    a, b = _corpus(tmp_path, a=SPAWNER, b=CLEAN)

    violations = []
    for path, source in read_sources([a, b]):
        for lineno, name in guard._bare_interpreter_calls(ast.parse(source)):
            violations.append((path.name, lineno, name))

    assert violations == [("a.py", 2, "python3")]


def test_a_violation_survives_a_vanished_neighbour(tmp_path):
    """The skip must not swallow the finding sitting next to it."""
    guard = _load_guard()
    a, b = _corpus(tmp_path, a=SPAWNER, b=CLEAN)
    b.unlink()

    violations = []
    with pytest.warns(UserWarning):
        for path, source in read_sources([a, b]):
            for lineno, name in guard._bare_interpreter_calls(ast.parse(source)):
                violations.append((path.name, lineno, name))

    assert violations == [("a.py", 2, "python3")]


def _load_guard():
    """The real guard module, imported by name."""
    import importlib

    return importlib.import_module("tests.test_subprocess_interpreter_guard")


# ============================================================
# Not over-caught: a real fault about a file that IS there still raises
# ============================================================


def test_a_directory_handed_in_where_a_file_was_expected_still_raises(tmp_path):
    """`except FileNotFoundError`, never `except OSError`. A path that exists and
    cannot be read is a genuine fault and must not be filed under "vanished"."""
    directory = tmp_path / "adir"
    directory.mkdir()

    with pytest.raises(IsADirectoryError):
        list(read_sources([directory]))


def test_a_decoding_failure_still_raises(tmp_path):
    """Strict by default: a file whose bytes are not UTF-8 is a real finding
    about a file that is present, not a file that went away."""
    bad = tmp_path / "bad.py"
    bad.write_bytes(b"\xff\xfe\x00 not utf-8 \xc3\x28\n")

    with pytest.raises(UnicodeDecodeError):
        list(read_sources([bad]))


def test_errors_replace_is_available_for_the_sweep_that_wants_it(tmp_path):
    """One sweep reads SKILL.md with `errors="replace"`; the helper must keep
    that behaviour rather than silently tighten it."""
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"head \xc3\x28 tail\n")

    pairs = dict(read_sources([bad], errors="replace"))
    assert "head" in pairs[bad] and "tail" in pairs[bad]


# ============================================================
# The sweeps that had the hole
# ============================================================

# Found by parsing every module under `tests/` and reporting each `read_text`
# call made on a variable bound by a loop over a REPOSITORY-wide walk
# (`tracked_paths`, `tracked_python_files`, or `.rglob`/`.glob` rooted at ROOT),
# outside any `try`. Seven matched on 2026-08-30. A `tmp_path` walk is excluded:
# the test that built that corpus owns it, and nothing else can delete it
# underneath.
_FIXED_SWEEPS = (
    "tests/test_a_catch_all_rule_the_report_could_not_see.py",
    "tests/test_a_rule_that_reached_one_of_six_generators.py",
    "tests/test_a_shed_that_dropped_the_newer_turn.py",
    "tests/test_a_state_redirect_that_covered_some_of_a_modules_tests.py",
    "tests/test_checkpoint_session_scope.py",
    "tests/test_subprocess_interpreter_guard.py",
    "tests/test_ten_regexes_that_spelled_the_fence_themselves.py",
)


@pytest.mark.parametrize("rel", _FIXED_SWEEPS)
def test_the_sweep_reads_through_the_shared_helper(rel):
    """Each of the seven routes its read through one policy.

    Named individually rather than counted, so a sweep that quietly reverts to a
    bare `read_text` on its walked path is a named failure and not a number that
    drifted.
    """
    source = (ROOT / rel).read_text(encoding="utf-8")
    assert "read_sources(" in source, f"{rel} no longer reads through read_sources"


def test_the_helper_lives_in_one_place():
    """`tests/repo_files` is a re-export. A second implementation under `tests/`
    is the copy that stops being fixed, which is why the walker moved out."""
    import tests.repo_files as shim

    assert shim.read_sources.__module__ == "scripts.utils.repo_files"
