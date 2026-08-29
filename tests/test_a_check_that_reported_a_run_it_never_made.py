#!/usr/bin/env python3
"""A turn check killed by the clock reported a failure and a test count.

`scripts/turn-check.py` bounds its test lane with a wall clock. When the clock
runs out, pytest is killed and NOTHING has been judged. Until 2026-08-29 that
outcome was indistinguishable from a real test failure, and it carried a number
for work that never happened.

MEASURED 2026-08-29 by forcing `subprocess.TimeoutExpired` on the pytest call
and reading the result dict:

    before   {"status": "fail", "lane": "tests", "tests_run": 2, ...}
    after    {"status": "fail", "lane": "tests", "tests_run": 0,
              "unmeasured": 2, ...}

The Stop hook then rendered the before-case with its ordinary template, whose
second sentence reads "This is the fast check, not the full suite, so a failure
here is almost always real." For a timeout that sentence is false twice over:
there was no failure, and nothing was real because nothing ran. The operator
reads that one message and no other, so this is the half that mattered.

`.claude/rules/scope-claims.md` names the shape: a tool says only what its
method established. The method killed a process. The sentence asserted a
verdict, and the count asserted a measurement.

Two paths reach non-completion and both were wrong the same way: the wall-clock
timeout, and an `OSError` where pytest could not start at all. Both now report
`tests_run=0` and count their files as unmeasured.
"""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import typing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(relpath: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tc():
    return _load("scripts/turn-check.py", "unmeasured_probe_tc")


@pytest.fixture(scope="module")
def hook():
    return _load(".claude/hooks/turn-check.py", "unmeasured_probe_hook")


@pytest.fixture()
def one_green_file(tmp_path, monkeypatch, tc):
    """One matched test file that passes, so the only variable is the outcome
    of the pytest call itself."""
    probe = tmp_path / "test_probe_green.py"
    probe.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    monkeypatch.setattr(tc, "matching_tests", lambda paths: [probe])
    return probe


def _break_pytest(monkeypatch, tc, exc):
    real = subprocess.run

    def fake(args, **kwargs):
        if "pytest" in args:
            raise exc
        return real(args, **kwargs)

    monkeypatch.setattr(tc.subprocess, "run", fake)


# ============================================================
# The lane stops counting work it did not do
# ============================================================

def test_a_timed_out_lane_reports_no_tests_run(tc, monkeypatch, one_green_file):
    _break_pytest(monkeypatch, tc, subprocess.TimeoutExpired("pytest", 120))

    failures, ran, _skipped, _dropped, _empty, unmeasured = tc.lane_tests(
        [one_green_file], timeout=120)

    assert failures, "a lane that never finished must still say so"
    assert ran == 0, "a killed pytest run judged nothing, so it ran nothing"
    assert unmeasured == 1


def test_a_pytest_that_could_not_start_reports_no_tests_run(tc, monkeypatch,
                                                            one_green_file):
    """The other non-completion path. It had the same wrong count."""
    _break_pytest(monkeypatch, tc, OSError("no such interpreter"))

    failures, ran, _skipped, _dropped, _empty, unmeasured = tc.lane_tests(
        [one_green_file], timeout=120)

    assert failures
    assert ran == 0
    assert unmeasured == 1


def test_a_lane_that_finished_reports_what_it_ran_and_nothing_unmeasured(
        tc, one_green_file):
    """The mirror. Marking every run unmeasured would make the note meaningless
    and would zero a count the operator relies on."""
    failures, ran, _skipped, _dropped, _empty, unmeasured = tc.lane_tests(
        [one_green_file], timeout=120)

    assert failures == [], failures
    assert ran == 1
    assert unmeasured == 0


def test_the_return_arity_matches_its_own_annotation(tc):
    """The annotation said four while the body returned five, for three days.
    Neither was checked against the other."""
    declared = typing.get_args(typing.get_type_hints(tc.lane_tests)["return"])
    assert len(declared) == 6
    assert len(tc.lane_tests([], 60)) == len(declared)


# ============================================================
# The count reaches the result dict, and the renderer
# ============================================================

def _run_with_broken_pytest(tc, monkeypatch, tmp_path, exc):
    probe = tmp_path / "test_probe_for_run.py"
    probe.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    monkeypatch.setattr(tc, "changed_python_files", lambda: [probe])
    monkeypatch.setattr(tc, "deleted_python_files", list)
    monkeypatch.setattr(tc, "narrow", lambda paths, transcript: (paths, 0))
    monkeypatch.setattr(tc, "matching_tests", lambda paths: [probe])
    monkeypatch.setattr(tc, "lane_compile", lambda paths: [])
    monkeypatch.setattr(tc, "lane_import", lambda paths: [])
    _break_pytest(monkeypatch, tc, exc)
    return tc.run(timeout=120, use_cache=False)


def test_the_result_dict_carries_the_unmeasured_count(tc, monkeypatch, tmp_path):
    """The note and the hook branch are both unreachable unless `run` threads
    the value through."""
    result = _run_with_broken_pytest(tc, monkeypatch, tmp_path,
                                     subprocess.TimeoutExpired("pytest", 120))

    assert result["status"] == "fail"
    assert result["unmeasured"] == 1
    assert result["tests_run"] == 0


def test_the_renderer_does_not_call_an_unfinished_lane_failed(tc, monkeypatch,
                                                              tmp_path):
    """"Failed" is a verdict about the code. A lane that never finished reached
    no verdict, so it does not get that word."""
    result = _run_with_broken_pytest(tc, monkeypatch, tmp_path,
                                     subprocess.TimeoutExpired("pytest", 120))
    text = tc.render(result)

    assert "did not finish" in text
    assert "lane failed" not in text
    assert "left unmeasured" in text


def test_the_renderer_still_calls_a_real_failure_failed(tc, monkeypatch, tmp_path):
    """The mirror. A genuine red test must keep the blunt word."""
    probe = tmp_path / "test_probe_red.py"
    probe.write_text("def test_no():\n    assert False\n", encoding="utf-8")
    monkeypatch.setattr(tc, "changed_python_files", lambda: [probe])
    monkeypatch.setattr(tc, "deleted_python_files", list)
    monkeypatch.setattr(tc, "narrow", lambda paths, transcript: (paths, 0))
    monkeypatch.setattr(tc, "matching_tests", lambda paths: [probe])
    monkeypatch.setattr(tc, "lane_compile", lambda paths: [])
    monkeypatch.setattr(tc, "lane_import", lambda paths: [])

    result = tc.run(timeout=120, use_cache=False)
    text = tc.render(result)

    assert result["status"] == "fail"
    assert result["unmeasured"] == 0
    assert "lane failed" in text
    assert "did not finish" not in text
    assert "left unmeasured" not in text


# ============================================================
# What the operator actually reads
# ============================================================

def _hook_reason(hook, monkeypatch, capsys, result):
    """Drive the REAL hook over a canned result and return what it decided.

    The first version of these tests rebuilt the template choice here, in the
    test, and asserted against its own copy. Mutation caught it: flipping the
    hook's real selection to a single template survived, because nothing in
    this file ever executed that line. A rule can be correct and unreached.

    The hook shells out to `scripts/turn-check.py --json` and parses the last
    stdout line, so stubbing that one call is enough to drive every branch
    below it.
    """
    class _Proc:
        returncode = 1
        stdout = json.dumps(result)
        stderr = ""

    monkeypatch.setattr(hook.subprocess, "run", lambda *a, **k: _Proc())
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO("{}"))
    hook.main()
    printed = capsys.readouterr().out.strip().splitlines()[-1]
    return json.loads(printed)["reason"]


def test_the_hook_does_not_tell_the_operator_a_timeout_is_almost_always_real(
        hook, monkeypatch, capsys):
    result = {"status": "fail", "lane": "tests", "unmeasured": 2,
              "tests_run": 0,
              "failures": ["the matched tests did not finish in 120s"]}

    reason = _hook_reason(hook, monkeypatch, capsys, result)

    assert "almost always real" not in reason, (
        "an unmeasured lane was reported to the operator as a real failure")
    assert "did not finish" in reason
    assert "nothing was measured" in reason
    assert "2 matched file(s) left unmeasured" in reason, (
        "the exclusion line did not name the files nobody judged")


def test_the_hook_still_says_almost_always_real_for_a_genuine_failure(
        hook, monkeypatch, capsys):
    """The mirror. That sentence earns its place on a real red test: it stops
    the reader dismissing the fast lane as noise."""
    result = {"status": "fail", "lane": "tests", "unmeasured": 0,
              "tests_run": 3, "failures": ["assert False"]}

    reason = _hook_reason(hook, monkeypatch, capsys, result)

    assert "almost always real" in reason
    assert "nothing was measured" not in reason


def test_the_two_hook_templates_are_not_the_same_text(hook):
    """A rewrite that collapsed one into the other would leave every test above
    green while restoring the defect."""
    assert hook.UNMEASURED_REASON != hook.REASON
    assert "almost always real" in hook.REASON
    assert "almost always real" not in hook.UNMEASURED_REASON
