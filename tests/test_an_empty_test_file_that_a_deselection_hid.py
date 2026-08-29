"""A matched file that ran nothing, made invisible by a slow test beside it.

Covers the k3 audit shard `scripts-15-p3`, finding 2, for
`scripts/turn-check.py`.

Pytest exits 5 for two different reasons, and `lane_tests` treated them as
mutually exclusive: `(0 if dropped else len(targets))`. They co-occur the moment
the matched set holds one all-`slow` file and one file with no test in it. The
run exits 5, `dropped` is at least 1, and `collected_nothing` was reported as 0,
so the file that ran nothing was never named. MEASURED 2026-08-30 over exactly
that pair: the lane returned `collected_nothing=0` while zero tests ran in
either file.

That is the "a silent exclusion reads as coverage" failure the `_empty_note`
machinery and the comment block above the line exist to prevent.

The fix resolves the split instead of guessing at it, which is obligation 1 of
`.claude/rules/scope-claims.md`: on exit 5 the lane collects the same files
again WITHOUT the marker filter and counts the ones that yield no node id, so
the all-slow file is reported as deselected and the helper-only file as empty.
Widening to every target would have satisfied the finding, and it would also
have regressed `test_an_all_slow_file_is_deselected_not_empty`, which pins the
case the old guard did get right.

The fixture files have to sit in the real `tests/` tree, because
`matching_tests` only picks up a changed test file whose path is under `tests/`.
They carry names nothing else uses and are removed in a `finally`, with
`missing_ok=True` so cleanup can never be the thing that fails a run.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SLOW_ONLY = '''"""Fixture: one slow test, deselected by the lane's marker filter."""
import pytest


@pytest.mark.slow
def test_sleeps_for_real():
    assert True
'''

NO_TESTS_AT_ALL = '''"""Fixture: a helper and no test, the TDD-placeholder shape."""


def helper():
    return 1
'''

SLOW_FIXTURE = ROOT / "tests" / "test_turn_check_empty_masked_slow_fixture.py"
EMPTY_FIXTURE = ROOT / "tests" / "test_turn_check_empty_masked_empty_fixture.py"


@pytest.fixture(scope="module")
def tc():
    spec = importlib.util.spec_from_file_location(
        "turn_check_empty_masked_mod", str(ROOT / "scripts" / "turn-check.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["turn_check_empty_masked_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def both_fixtures():
    SLOW_FIXTURE.write_text(SLOW_ONLY, encoding="utf-8")
    EMPTY_FIXTURE.write_text(NO_TESTS_AT_ALL, encoding="utf-8")
    try:
        yield [SLOW_FIXTURE, EMPTY_FIXTURE]
    finally:
        SLOW_FIXTURE.unlink(missing_ok=True)
        EMPTY_FIXTURE.unlink(missing_ok=True)


def test_no_other_test_owns_these_fixture_names():
    """The 2026-08-23 xdist race: two files writing one probe path."""
    others = [p for p in (ROOT / "tests").rglob("test_*.py")
              if p.name != Path(__file__).name]
    assert others, "empty corpus proves nothing"
    for name in (SLOW_FIXTURE.name, EMPTY_FIXTURE.name):
        clashes = [p.name for p in others
                   if name in p.read_text(encoding="utf-8", errors="replace")]
        assert not clashes, clashes


def test_a_deselection_no_longer_hides_a_file_that_ran_nothing(tc,
                                                               both_fixtures):
    """The measured defect, end to end through the real pytest subprocess."""
    (failures, ran, _skipped, dropped,
     collected_nothing, unmeasured) = tc.lane_tests(both_fixtures, timeout=120)

    assert failures == [], failures
    assert ran == 2
    assert unmeasured == 0
    # Both causes are present at once. This is the co-occurrence the old
    # expression treated as impossible.
    assert dropped == 1, "the slow-marked test was not deselected"
    assert collected_nothing == 1, (
        "the helper-only file holds no test and must be named; the all-slow "
        "file holds one and belongs to the deselection count, not this one")


def test_the_render_names_the_file_that_ran_nothing(tc, both_fixtures):
    """The count only matters if it reaches the operator's line."""
    (_failures, ran, skipped, dropped,
     collected_nothing, unmeasured) = tc.lane_tests(both_fixtures, timeout=120)

    text = tc.render({"status": "pass", "files": 2, "tests_run": ran,
                      "skipped_foreign": 0, "skipped_contract": skipped,
                      "deselected_slow": dropped,
                      "collected_nothing": collected_nothing,
                      "unmeasured": unmeasured})

    assert "1 matched file(s) collected no tests" in text
    # The deselection is still named beside it, so the operator can tell the two
    # exclusions apart rather than reading one as the whole story.
    assert "1 slow test(s) not run here" in text


def test_an_all_slow_file_alone_is_still_not_called_empty(tc):
    """The case the old guard got right, pinned here too.

    Widening `collected_nothing` to every target on exit 5 would have satisfied
    the finding and broken this. The file holds a test; the lane declined to run
    it. That is a deselection, and `_slow_note` is where it belongs.
    """
    SLOW_FIXTURE.write_text(SLOW_ONLY, encoding="utf-8")
    try:
        (failures, _ran, _skipped, dropped,
         collected_nothing, _unmeasured) = tc.lane_tests([SLOW_FIXTURE],
                                                         timeout=120)
    finally:
        SLOW_FIXTURE.unlink(missing_ok=True)

    assert failures == []
    assert dropped == 1
    assert collected_nothing == 0


def test_a_collection_probe_that_could_not_run_widens_to_every_file(tc,
                                                                    monkeypatch):
    """Obligation 3: no answer means "all of them", never a confident zero."""
    def _refuse(*_args, **_kwargs):
        raise OSError("no interpreter")

    monkeypatch.setattr(tc.subprocess, "run", _refuse)
    targets = [Path("tests/a.py"), Path("tests/b.py"), Path("tests/c.py")]

    assert tc._files_holding_no_test(targets, 60) == 3


def test_a_lane_that_actually_ran_a_test_reports_no_empties(tc):
    """The negative direction: the fix must not report every clean run as empty.

    Without this, `collected_nothing = len(targets)` unconditionally, outside the
    exit-5 branch, would satisfy the tests above.
    """
    real = ROOT / "tests" / "test_turn_check_empty_masked_real_fixture.py"
    real.write_text('def test_green():\n    assert True\n', encoding="utf-8")
    try:
        (failures, ran, _skipped, dropped,
         collected_nothing, unmeasured) = tc.lane_tests([real], timeout=120)
    finally:
        real.unlink(missing_ok=True)

    assert failures == []
    assert ran == 1
    assert dropped == 0
    assert unmeasured == 0
    assert collected_nothing == 0
    assert tc._empty_note({"collected_nothing": 0}) == ""
