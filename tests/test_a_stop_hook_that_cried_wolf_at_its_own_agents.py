#!/usr/bin/env python3
"""The turn gate reported a red that was its own reflection.

`scripts/turn-check.py` runs the matched tests under `-n auto` above a
threshold. Each xdist worker collects the suite independently, a moment apart.
When a test FILE lands in `tests/` between gw0's collection and gw9's, their
sets differ and xdist aborts the entire lane with "Different tests were
collected between gw0 and gw9". Nothing failed. The corpus moved under the
collector.

MEASURED 2026-09-01, while a fleet of agents was writing test files:

    serial collection, three runs back to back    20799, 20799, 20799
    parallel collection, minutes apart            20806, then 20808

Sixteen test files were written into `tests/` in the preceding thirty minutes,
one of them 45 seconds before a run. The serial number never moved across three
runs, so the cause is the race and not a conftest that generates tests
nondeterministically. Eleven such errors reached the operator as a Stop-hook
block on a turn in which nothing was broken.

The cost is not the wasted run. The hook's own message says "a failure here is
almost always real", and in this regime that sentence was false. A check that
cries wolf teaches its reader to skip it, which is the one failure mode a Stop
hook cannot survive.

The fix retries ONE string, ONCE, and only under `-n auto`. This file has two
jaws, and the second is the one that matters: a mismatch that repeats must
still be reported. Without it, "retry the lane" and "swallow the error" are
indistinguishable, and the retry would be a way to lose real failures rather
than a way to stop losing real signal.
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TURN_CHECK = ROOT / "scripts" / "turn-check.py"

XDIST_MISMATCH = (
    "ERROR gw9 - Different tests were collected between gw0 and gw9. "
    "The difference is:\n--- gw0\n+++ gw9\n"
)
ORDINARY_FAILURE = (
    "FAILED tests/test_something.py::test_a_real_defect - assert 1 == 2\n"
    "1 failed, 3 passed in 2.10s\n"
)


@pytest.fixture()
def turn_check() -> types.ModuleType:
    """Load the hyphenated script by path; it cannot be imported by name."""
    spec = importlib.util.spec_from_file_location("turn_check_ut", TURN_CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Recorder:
    """Stands in for `subprocess.run` and records how often it was called.

    It RECORDS rather than discards, because a stub that threw its arguments
    away could not tell "retried once" from "retried five times", and an
    unbounded retry over a genuinely broken lane is its own defect.
    """

    def __init__(self, outcomes: list[tuple[int, str]]):
        self._outcomes = list(outcomes)
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        code, body = self._outcomes[min(len(self.calls) - 1,
                                        len(self._outcomes) - 1)]
        return subprocess.CompletedProcess(args, code, stdout=body, stderr="")


def _lane(turn_check, monkeypatch, recorder, *, parallel=True):
    """Drive the real `lane_tests` with only two seams replaced.

    `matching_tests` is stubbed because the lane derives its own parallelism
    from how many test files it matched (`len(targets) >= 20`), and this file
    is about the retry, not about the mapping from changed source to test. The
    count is what selects the lane, so it is the count this helper controls:
    one file below the threshold, thirty above it.

    Everything between those two seams is production code, including the
    decision to retry, which is the thing under test.
    """
    count = 30 if parallel else 1
    assert (count >= turn_check.PARALLEL_FILE_THRESHOLD) is parallel, (
        "the threshold moved and this helper no longer selects the lane it "
        "names, so every case below would be measuring the other one")
    files = [ROOT / "tests" / f"test_p{i}.py" for i in range(count)]
    monkeypatch.setattr(turn_check, "matching_tests", lambda paths: files)
    monkeypatch.setattr(turn_check, "is_contract", lambda t: False)
    monkeypatch.setattr(turn_check.subprocess, "run", recorder)
    return turn_check.lane_tests([ROOT / "scripts" / "whatever.py"], timeout=30)


def test_a_collection_race_is_retried_and_the_turn_is_not_blocked(
        turn_check, monkeypatch):
    """The defect. A mismatch that clears on the retry must not block.

    The second attempt returns exit 0, which is what actually happens: the
    retry collects after the write that broke the first one.
    """
    recorder = _Recorder([(1, XDIST_MISMATCH), (0, "60 passed in 12.00s\n")])
    problems = _lane(turn_check, monkeypatch, recorder)[0]

    assert len(recorder.calls) == 2, (
        f"the lane ran {len(recorder.calls)} time(s); a collection race must "
        f"be retried exactly once")
    assert problems == [], (
        f"a collection race still blocked the turn: {problems!r}. Nothing "
        f"failed; the corpus moved under the collector.")


def test_a_mismatch_that_repeats_is_still_reported(turn_check, monkeypatch):
    """The jaw that keeps the retry honest.

    A conftest generating tests nondeterministically mismatches on BOTH
    attempts. Without this case, retrying and swallowing look identical, and
    the retry becomes a way to lose a real defect.
    """
    recorder = _Recorder([(1, XDIST_MISMATCH), (1, XDIST_MISMATCH)])
    problems = _lane(turn_check, monkeypatch, recorder)[0]

    assert len(recorder.calls) == 2, (
        "a repeating mismatch was retried more than once, so a genuinely "
        "broken lane would be retried without bound")
    assert problems, (
        "a mismatch that repeated on the retry was reported as clean, which "
        "means the retry swallows the error rather than absorbing the race")


def test_an_ordinary_failure_is_never_retried(turn_check, monkeypatch):
    """The anchor against widening. A real red must cost exactly one run.

    This is the case that stops the retry from becoming "run it twice and hope".
    """
    recorder = _Recorder([(1, ORDINARY_FAILURE)])
    problems = _lane(turn_check, monkeypatch, recorder)[0]

    assert len(recorder.calls) == 1, (
        f"an ordinary test failure was retried ({len(recorder.calls)} runs). "
        f"The retry is gated on ONE error string for exactly this reason.")
    assert problems, "a real failing test was not reported"


def test_a_clean_lane_still_runs_once(turn_check, monkeypatch):
    """The clean-path anchor. The common case must pay nothing for the retry."""
    recorder = _Recorder([(0, "60 passed in 12.00s\n")])
    problems = _lane(turn_check, monkeypatch, recorder)[0]

    assert len(recorder.calls) == 1
    assert problems == []


def test_the_serial_lane_does_not_retry(turn_check, monkeypatch):
    """Below the parallel threshold there are no workers to disagree.

    A serial run cannot produce this error, so a retry there would be reacting
    to a cause that cannot occur, and would mask something else instead.
    """
    recorder = _Recorder([(1, XDIST_MISMATCH)])
    _lane(turn_check, monkeypatch, recorder, parallel=False)

    assert len(recorder.calls) == 1, (
        "the serial lane retried an error only the parallel lane can raise")
    assert "-n" not in recorder.calls[0], (
        "this case ran the PARALLEL lane, so it proves nothing about the "
        "serial one")


def test_the_retry_is_gated_on_a_literal_and_not_on_any_failure(turn_check):
    """Asked of the AST, because this is the property a later edit erodes.

    A grep would match the paragraph explaining the gate. This walks the
    condition and asserts the module constant is named in it, so loosening the
    retry to "any non-zero exit" fails here even if every behavioural case
    above were deleted.
    """
    tree = ast.parse(TURN_CHECK.read_text(encoding="utf-8"),
                     filename=str(TURN_CHECK))
    lane = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "lane_tests"),
                None)
    assert lane is not None, "lane_tests is gone; this test measures nothing"

    gated = [
        node for node in ast.walk(lane)
        if isinstance(node, ast.If)
        and any(isinstance(n, ast.Name) and n.id == "_COLLECTION_RACE"
                for n in ast.walk(node.test))
    ]
    assert gated, (
        "no branch in lane_tests tests _COLLECTION_RACE. Either the retry is "
        "gone or it is now gated on something looser, and a retry that fires "
        "on any failure hides real defects instead of a race.")

    names = {n.id for g in gated for n in ast.walk(g.test)
             if isinstance(n, ast.Name)}
    assert "parallel" in names, (
        "the retry is no longer restricted to the parallel lane, so it now "
        "fires for a cause that only xdist can produce")


def test_the_constant_matches_what_xdist_actually_prints(turn_check):
    """Bind the literal to the message, not to itself.

    A constant asserted only against its own definition is untested. This
    checks it against a real xdist error line, so a typo in either one fails.
    """
    assert turn_check._COLLECTION_RACE in XDIST_MISMATCH, (
        f"{turn_check._COLLECTION_RACE!r} does not appear in the error xdist "
        f"emits, so the retry can never fire and the false red is back")
    assert turn_check._COLLECTION_RACE not in ORDINARY_FAILURE, (
        "the constant also matches an ordinary failure, so the retry is not "
        "the narrow gate it claims to be")


# ---------------------------------------------------------------------------
# The second defect in the same function, found by shard 23 and fixed with it.
# ---------------------------------------------------------------------------

def test_no_child_output_is_decoded_without_a_replacement_policy():
    """A hook that raises is a failed TURN, not a failed check.

    Every `subprocess.run(..., text=True)` here decodes the child's stdout and
    stderr strictly unless told otherwise, and `UnicodeDecodeError` is a
    `ValueError` that none of the `except` clauses around these calls names. So
    one non-UTF-8 byte anywhere in pytest's captured output raised out of the
    lane, inside the Stop hook, where the operator sees a broken turn rather
    than a reported failure.

    MEASURED 2026-09-01 against a child printing `b"ok \\xff done\\n"`:

        text=True, no errors=      RAISED UnicodeDecodeError
        text=True, errors=replace  'ok \\ufffd done'

    Reachable, not theoretical. This suite writes files holding lone 0xff bytes
    on purpose, and `git` prints a filename byte-for-byte, so a tracked path
    that is not valid UTF-8 reaches the same decode.

    Four call sites carried it and one earlier fix had touched none of them,
    which is the one-of-N shape. Asked of the AST across EVERY call rather than
    the four known ones, so a fifth added later inherits the requirement.

    The `git` call is deliberately exempt and must stay so: it reads BINARY with
    no `text=True` at all, because `-z` output has to be split on NUL before
    anything decodes it, and universal-newline translation would corrupt a
    filename containing a CR.
    """
    tree = ast.parse(TURN_CHECK.read_text(encoding="utf-8"),
                     filename=str(TURN_CHECK))
    runs = [c for c in ast.walk(tree)
            if isinstance(c, ast.Call)
            and getattr(c.func, "attr", None) == "run"]
    assert len(runs) >= 4, (
        f"only {len(runs)} subprocess.run call(s) found; this test scans by "
        f"shape and measures nothing if the shape changed")

    unguarded = []
    for call in runs:
        kw = {k.arg for k in call.keywords}
        if "text" in kw and "errors" not in kw:
            unguarded.append(call.lineno)

    assert not unguarded, (
        f"subprocess.run at line(s) {unguarded} decodes child output with "
        f"text=True and no errors= policy. One non-UTF-8 byte from pytest or "
        f"git then raises UnicodeDecodeError out of the Stop hook.")
