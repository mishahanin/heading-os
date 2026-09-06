"""A test wrote `tests/test_*.py` into the live tree, and a collector found it.

THE DEFECT. `tests/test_turn_check.py::test_the_test_lane_deselects_slow_marked_
tests` has to put a real file under `tests/` -- `matching_tests` in
`scripts/turn-check.py` only picks up a changed test whose path is under that
directory, so a `tmp_path` fixture would exercise nothing. Until 2026-09-06 it
named that file `tests/test_turn_check_slow_fixture.py`, which is inside every
`test_*.py` glob on the machine, pytest's OWN collector included.

MEASURED 2026-09-06, HELM, one full `-n auto` run in three:

    ERROR tests/test_turn_check_slow_fixture.py

an ImportError carrying no text and no line, because a collector resolved the
path and the `unlink` in that test landed before the import did.

THE THIRD RACE AROUND ONE FILE. The first two (2026-08-22 and 2026-08-23, both
written up in `tests/test_venv_relaunch_guard.py`) were paid for by adding
tolerance to the scanners in this repository that walk `tests/`. That worked
because those scanners are ours. This one is pytest's collector, which is not,
so the same remedy does not reach it.

THE FIX IS THE ONE THIS REPOSITORY ALREADY MADE ONCE. A rename out of the
`test_*.py` glob would have satisfied pytest's collector and left the file in
the tree for every other walker. What `matching_tests` actually requires is a
path that is relative to the module global `tc.ROOT` and starts with `tests/`,
so moving that global moves the requirement: the fixture now lives under
`tmp_path/tests/` and the live directory is never touched. That is exactly what
`tests/test_an_empty_test_file_that_a_deselection_hid.py` did for its own two
fixtures on 2026-08-30 after the same failure, and its docstring states the
principle this one had not yet adopted -- a test that plants a file in the live
tree is the defect, and the walkers are entitled to assume `tests/` holds only
tests.

MEASURED 2026-09-06 after the move: `ran == 1` and `deselected == 1` unchanged,
and `matching_tests` still picks the fixture (now asserted in that test, which
it never was while the claim was only a comment).

THE THIRD PLANTED FILE, fixed the same day by a different remedy.
`tests/test_venv_relaunch_guard.py` used to write an untracked `test_`-prefixed
probe into the live directory. It cannot move under `tmp_path`: the function it
drives scans the REAL tests tree through a module global and asserts a floor of
380 modules against it. What that test needs is only that the scanned `rglob`
YIELD an untracked, unreadable path, and yielding is not creating -- `rglob` is
now patched for the one call and no file is ever written. The ownership scan it
carried, a second walk of the live tests directory, went with it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT))

import tests.test_turn_check as lane_test  # noqa: E402


def _under_live_tests(paths) -> list[str]:
    """Which of these paths sit directly in the repository's own `tests/`."""
    return [str(p) for p in paths if Path(p).parent == TESTS]


def test_the_lane_fixture_never_lands_in_the_live_tests_directory(
        tmp_path, monkeypatch):
    """The failing half, observed at the write.

    Not read out of the source: the claim is about the file that actually
    appears beside the running suite, and a path assembled at runtime would be
    invisible to a source scan. Against the version before 2026-09-06 this
    records `<repo>/tests/test_turn_check_slow_fixture.py` and fails.

    The real lane test is DRIVEN, not imitated, so its own `ran == 1` and
    `deselected == 1` assertions run here too: had the move cost the lane its
    collection, this test would fail on that instead, which is the outcome the
    fix must not have.
    """
    written: list[Path] = []
    real_write = Path.write_text

    def _record(self, *a, **kw):
        written.append(Path(self))
        return real_write(self, *a, **kw)

    # The fixtures it asks for, whatever they are. A hardcoded call signature
    # would make this test fail against the previous version with a TypeError,
    # which proves only that a parameter list changed; the claim being made is
    # about the path that lands on disk, and it has to be what goes red.
    import inspect
    available = {"tmp_path": tmp_path, "monkeypatch": monkeypatch}
    target = lane_test.test_the_test_lane_deselects_slow_marked_tests
    wanted = {name: available[name]
              for name in inspect.signature(target).parameters
              if name in available}

    monkeypatch.setattr(Path, "write_text", _record)
    target(**wanted)
    monkeypatch.undo()

    # A floor outside the filter: an interception that recorded nothing would
    # satisfy the assertion below while measuring no file at all.
    assert len(written) >= 1, "no write was intercepted; the probe measured nothing"
    live = _under_live_tests(written)
    assert live == [], (
        f"{live} is written into the LIVE tests directory while the rest of the "
        "suite walks it. pytest's own collector resolves such a path and then "
        "fails the run with a bare ImportError when the unlink wins the race"
    )


def test_the_venv_guards_vanish_probe_writes_no_file_either(monkeypatch):
    """The third planted file, measured the same way as the first.

    Drives the real test and watches every `write_text`. Against the version
    before 2026-09-06 this records an untracked `test_`-prefixed probe in the
    live tests directory; after it, the probe exists only as a path handed to
    the scan.
    """
    import tests.test_venv_relaunch_guard as venv_test

    written: list[Path] = []
    real_write = Path.write_text

    def _record(self, *a, **kw):
        written.append(Path(self))
        return real_write(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", _record)
    venv_test.test_a_vanished_untracked_file_is_dropped_but_a_tracked_one_is_not()
    monkeypatch.undo()

    assert _under_live_tests(written) == [], (
        f"{_under_live_tests(written)} is planted in the live tests directory"
    )


def test_a_live_tree_write_is_what_the_probe_above_looks_for(tmp_path):
    """The control for that probe, so its silence means something.

    The test above passes when nothing was written under the live `tests/`
    directory. That is also what it would report if the filter were simply
    blind, so the filter is exercised here against a path that IS under it. No
    file is created: the classification is what is being measured, and writing
    into the live tree to prove a point about not writing into the live tree
    would be its own defect.
    """
    live = TESTS / "test_something_transient.py"
    elsewhere = tmp_path / "tests" / "test_something_transient.py"
    assert _under_live_tests([live, elsewhere]) == [str(live)]
    assert _under_live_tests([elsewhere]) == []


if __name__ == "__main__":                      # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
