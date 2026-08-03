"""The mutation harness must not be able to report a verdict it cannot justify.

Written after two invalid mutations in one slice produced a confident SURVIVED
each, and both were caught by re-reading the mutation rather than by the
harness. A harness whose worst outcome is a plausible lie is worse than none:
the lie is acted on, and a guard that was working gets weakened.
"""

import sys
from pathlib import Path

import pytest

from scripts.utils.mutation_probe import (
    INVALID,
    KILLED,
    SURVIVED,
    Mutation,
    Result,
    render,
    run_mutations,
)


def _tree(tmp_path: Path) -> Path:
    """A scratch project: one source file and one test that pins its behaviour."""
    (tmp_path / "src.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    (tmp_path / "check.py").write_text(
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "from src import answer\n"
        "raise SystemExit(0 if answer() == 42 else 1)\n",
        encoding="utf-8")
    return tmp_path


_COMMAND = lambda: [sys.executable, "check.py"]  # noqa: E731 - one-liner fixture


def _always_valid(_sources):
    return None


def test_a_mutation_the_test_catches_is_killed(tmp_path):
    root = _tree(tmp_path)
    results = run_mutations(
        [Mutation("break the answer", (("src.py", "return 42", "return 43"),), _always_valid)],
        _COMMAND(), root)

    assert [r.verdict for r in results] == [KILLED]


def test_a_mutation_the_test_misses_is_survived(tmp_path):
    root = _tree(tmp_path)
    results = run_mutations(
        [Mutation("rename nothing that matters",
                  (("src.py", "def answer():", "def answer():  # noqa"),), _always_valid)],
        _COMMAND(), root)

    assert [r.verdict for r in results] == [SURVIVED]


def test_a_missing_anchor_is_invalid_and_never_survived(tmp_path):
    """The four-space-indent failure: the author describes code that is not
    there, the file is untouched, and the suite passes. Reported as SURVIVED
    that reads as 'the contract is weak here', which is the opposite of true."""
    root = _tree(tmp_path)
    results = run_mutations(
        [Mutation("mutate a line that does not exist",
                  (("src.py", "return 999", "return 1000"),), _always_valid)],
        _COMMAND(), root)

    assert results[0].verdict == INVALID
    assert "anchor" in results[0].detail
    assert results[0].trustworthy is False


def test_a_failing_control_is_invalid_even_when_the_suite_passes(tmp_path):
    """The broken-chain failure: the edit lands, the suite passes, and the
    mutation still proves nothing because the thing it was meant to exercise is
    unreachable. Only the control can tell those apart."""
    root = _tree(tmp_path)

    def _requires_a_call(sources):
        # A CALL, not a definition. Checked as an indented statement, because
        # `"helper()" in src` is also true of `def helper():` -- the same
        # substring trap that made this test's first draft pass while proving
        # nothing, which is the very failure the module exists to prevent.
        called = any(line.strip() == "helper()"
                     for line in sources["src.py"].splitlines())
        return None if called else "src.py never calls helper"

    results = run_mutations(
        [Mutation("add a helper nothing calls",
                  (("src.py", "def answer():", "def helper():\n    pass\n\n\ndef answer():"),),
                  _requires_a_call)],
        _COMMAND(), root)

    assert results[0].verdict == INVALID
    assert "never calls" in results[0].detail


def test_a_passing_control_lets_the_verdict_through(tmp_path):
    root = _tree(tmp_path)

    def _requires_the_helper(sources):
        return None if "def helper" in sources["src.py"] else "helper missing"

    results = run_mutations(
        [Mutation("add a helper and break the answer",
                  (("src.py", "    return 42", "    return 43\n\n\ndef helper():\n    pass"),),
                  _requires_the_helper)],
        _COMMAND(), root)

    assert results[0].verdict == KILLED


def test_every_file_is_restored_byte_for_byte(tmp_path):
    root = _tree(tmp_path)
    before = (root / "src.py").read_bytes()

    run_mutations(
        [Mutation("break it", (("src.py", "return 42", "return 43"),), _always_valid),
         Mutation("bad anchor", (("src.py", "nope", "nah"),), _always_valid)],
        _COMMAND(), root)

    assert (root / "src.py").read_bytes() == before


def test_a_multi_file_mutation_restores_the_files_it_edited_before_failing(tmp_path):
    """A mutation whose SECOND anchor is missing must not leave the first edit
    behind: every later verdict in the run would then describe a tree nobody
    intended."""
    root = _tree(tmp_path)
    (root / "other.py").write_text("VALUE = 1\n", encoding="utf-8")
    before = (root / "src.py").read_bytes()

    results = run_mutations(
        [Mutation("edit two files, second anchor missing",
                  (("src.py", "return 42", "return 43"),
                   ("other.py", "VALUE = 99", "VALUE = 100")),
                  _always_valid)],
        _COMMAND(), root)

    assert results[0].verdict == INVALID
    assert (root / "src.py").read_bytes() == before
    assert (root / "other.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_two_edits_to_one_file_restore_it_completely(tmp_path):
    """The residue bug, found by `git status` rather than by this suite.

    A mutation with two edits to the SAME file snapshotted it twice, so the
    second snapshot captured the text with the first edit already applied. The
    restore then wrote that partially-mutated text back and the digest check
    agreed with it, because it was comparing against the wrong baseline. The
    tree kept the first edit and the harness reported success -- and the next
    mutation's verdict described a file nobody meant to change.
    """
    root = _tree(tmp_path)
    before = (root / "src.py").read_bytes()

    results = run_mutations(
        [Mutation("two edits, one file",
                  (("src.py", "def answer():", "def helper():\n    pass\n\n\ndef answer():"),
                   ("src.py", "return 42", "return 43")),
                  _always_valid)],
        _COMMAND(), root)

    assert results[0].verdict == KILLED          # the second edit really landed
    assert (root / "src.py").read_bytes() == before


def test_the_rendered_table_shouts_an_invalid_verdict(tmp_path):
    text = render([Result("a", KILLED), Result("b", SURVIVED),
                   Result("c", INVALID, "anchor not found in x.py")])

    assert "!! survived" in text
    assert "!! invalid" in text
    assert "anchor not found in x.py" in text


@pytest.mark.parametrize("verdict,expected", [
    (KILLED, True), (SURVIVED, True), (INVALID, False),
])
def test_only_a_real_verdict_is_trustworthy(verdict, expected):
    assert Result("x", verdict).trustworthy is expected
