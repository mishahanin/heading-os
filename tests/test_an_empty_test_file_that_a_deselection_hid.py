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

# Names only. These used to be paths into the LIVE `tests/` directory, and the
# real-fixture case below records why that shape is a defect. The morning of
# 2026-08-30 moved that one file and left these two, so the full suite failed
# again the same day: `1 engine-routed file(s) could not be scanned`, with the
# two paths named in a vanished-mid-walk warning. One planted file is enough to
# break a repository-wide walk, so a partial move fixes nothing.
SLOW_FIXTURE_NAME = "test_turn_check_empty_masked_slow_fixture.py"
EMPTY_FIXTURE_NAME = "test_turn_check_empty_masked_empty_fixture.py"


@pytest.fixture(scope="module")
def tc():
    spec = importlib.util.spec_from_file_location(
        "turn_check_empty_masked_mod", str(ROOT / "scripts" / "turn-check.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["turn_check_empty_masked_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def both_fixtures(tc, tmp_path, monkeypatch):
    """Both probe files live under `tmp_path`, never in the real tree.

    `tc.ROOT` moves with them for the reason spelled out in
    `test_a_lane_that_actually_ran_a_test_reports_no_empties`: `_rel` measures
    against the module global, so a `tmp_path` file under the real ROOT would
    fall through to stem matching and run some unrelated file.

    There is no `finally` unlink any more, and that is the point. The old shape
    could not be made safe by cleaning up faster, because the window it opened
    was the whole pytest subprocess. `tmp_path` closes the window instead.
    """
    monkeypatch.setattr(tc, "ROOT", tmp_path)
    (tmp_path / "tests").mkdir()
    slow = tmp_path / "tests" / SLOW_FIXTURE_NAME
    empty = tmp_path / "tests" / EMPTY_FIXTURE_NAME
    slow.write_text(SLOW_ONLY, encoding="utf-8")
    empty.write_text(NO_TESTS_AT_ALL, encoding="utf-8")
    # Set comparison: `matching_tests` sorts, and the order it returns is not
    # what this assertion is about.
    assert set(tc.matching_tests([slow, empty])) == {slow, empty}, (
        "the lane did not pick the fixtures, so the counts would be about "
        "some other file")
    return [slow, empty]


def test_no_other_test_owns_these_fixture_names():
    """The 2026-08-23 xdist race: two files writing one probe path."""
    others = [p for p in (ROOT / "tests").rglob("test_*.py")
              if p.name != Path(__file__).name]
    assert others, "empty corpus proves nothing"
    for name in (SLOW_FIXTURE_NAME, EMPTY_FIXTURE_NAME):
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


def test_an_all_slow_file_alone_is_still_not_called_empty(tc, tmp_path,
                                                          monkeypatch):
    """The case the old guard got right, pinned here too.

    Widening `collected_nothing` to every target on exit 5 would have satisfied
    the finding and broken this. The file holds a test; the lane declined to run
    it. That is a deselection, and `_slow_note` is where it belongs.
    """
    monkeypatch.setattr(tc, "ROOT", tmp_path)
    (tmp_path / "tests").mkdir()
    slow = tmp_path / "tests" / SLOW_FIXTURE_NAME
    slow.write_text(SLOW_ONLY, encoding="utf-8")
    assert tc.matching_tests([slow]) == [slow]

    (failures, _ran, _skipped, dropped,
     collected_nothing, _unmeasured) = tc.lane_tests([slow], timeout=120)

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


def test_a_lane_that_actually_ran_a_test_reports_no_empties(tc, tmp_path,
                                                            monkeypatch):
    """The negative direction: the fix must not report every clean run as empty.

    Without this, `collected_nothing = len(targets)` unconditionally, outside the
    exit-5 branch, would satisfy the tests above.

    The fixture file lives under `tmp_path`, and `tc.ROOT` is moved to point at
    it. It used to be written into the LIVE `tests/` directory as
    `test_turn_check_empty_masked_real_fixture.py` and unlinked in a `finally`,
    which made it a real file in the real tree for the length of one pytest
    subprocess. MEASURED 2026-08-30: two separate full-suite runs failed naming
    that exact path, once as
    `FileNotFoundError: .../tests/test_turn_check_empty_masked_real_fixture.py`
    out of another test's repository-wide walk, and once out of the overlay
    snapshot in `tests/conftest.py`. Neither had anything to do with the code
    under test. Under `-n auto` another worker lists `tests/` while this one is
    mid-`finally`, so the file exists at listing time and not at read time.

    Moving the file is the fix rather than teaching each walker to tolerate a
    vanishing path: a test that plants a file in the live tree is the defect,
    and the walkers are entitled to assume `tests/` holds only tests.

    `tc.ROOT` has to move with it. `matching_tests` picks a path directly only
    when `_rel(p)` starts with `tests/`, `_rel` measures against the module
    global `ROOT`, and `lane_tests` runs pytest with `cwd=str(ROOT)`. A
    `tmp_path` file under the real ROOT would fall through to stem matching and
    run some unrelated file instead, which is the silent-wrong-coverage shape
    this whole module exists to refuse.
    """
    monkeypatch.setattr(tc, "ROOT", tmp_path)
    (tmp_path / "tests").mkdir()
    real = tmp_path / "tests" / "test_a_lane_can_see_a_green_file.py"
    real.write_text('def test_green():\n    assert True\n', encoding="utf-8")

    assert tc.matching_tests([real]) == [real], (
        "the lane did not pick the fixture, so the counts below would be "
        "about some other file")

    (failures, ran, _skipped, dropped,
     collected_nothing, unmeasured) = tc.lane_tests([real], timeout=120)

    assert failures == []
    assert ran == 1
    assert dropped == 0
    assert unmeasured == 0
    assert collected_nothing == 0
    assert tc._empty_note({"collected_nothing": 0}) == ""
