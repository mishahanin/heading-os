#!/usr/bin/env python3
"""The suite leaked ~18,000 directories a week into /tmp and nothing said so.

MEASURED 2026-09-04 on the operator's laptop, counted by name family in the
shared temp directory: 10,751 `odin-cad*`, 5,143 `pytest-wall-rate-*`, 1,431
`marp-cli-*`, 967 `skill_report_example-skill_*`, ~878 default-prefixed `tmp*`
and thousands of `supervise-*.log`, spanning seven days, in a tree that had
reached 50,225 top-level entries. Separately `/tmp/pytest-of-administrator` held
216 session directories, 4,940,079 files and 53 GB.

Every one of those families was created DIRECTLY in the shared temp directory --
`tempfile.mkdtemp()` with no `dir=`, or a child process making its own scratch.
That is outside pytest's basetemp, so `make_numbered_dir_with_cleanup(keep=3)`
never saw them, `--basetemp` never moved them and no cleanup reclaimed them. A
test that writes outside `tmp_path` has opted out of the pytest lifecycle, and
until this file nothing in the suite could observe that it had.

WHAT IS UNDER TEST HERE is the guard, `tests/tmp_leak_guard.py`, wired into
`tests/conftest.py`. Both directions for each claim, because a leak detector
that flags everything is the same defect as one that flags nothing: it gets
switched off. So every positive below has the managed-tree twin beside it.

The last test in this file is the one that matters most and is the easiest to
leave out: it asserts the guard is ACTUALLY ARMED in the session running it. A
guard whose wrappers were never installed reports zero survivors forever, which
reads exactly like a clean tree.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import tmp_leak_guard as guard  # noqa: E402


@pytest.fixture
def isolated_guard(tmp_path, monkeypatch):
    """A guard armed over a private "shared temp" and a private "managed tree".

    The real module globals are swapped out, so planting a leak here never
    reaches the count the session enforces against, and a failure in this file
    cannot fail the run twice.
    """
    shared = tmp_path / "shared-temp"
    managed = shared / "pytest-of-nobody"
    managed.mkdir(parents=True)

    monkeypatch.setattr(guard, "_UNMANAGED", {})
    monkeypatch.setattr(guard, "_MANAGED_PREFIX", os.path.realpath(managed))
    monkeypatch.setattr(guard, "CURRENT_TEST", "the-test-under-observation")
    return shared, managed


# ---------------------------------------------------------------------------
# The attributable half: what this interpreter created and did not remove
# ---------------------------------------------------------------------------


def test_a_directory_made_outside_the_managed_tree_is_a_survivor(isolated_guard):
    shared, _managed = isolated_guard
    leaked = Path(tempfile.mkdtemp(prefix="planted-", dir=shared))

    found = guard.survivors()
    assert [p for p, _who in found] == [os.path.realpath(leaked)], found
    assert found[0][1] == "the-test-under-observation", (
        "the survivor arrived without the test that made it; the report would "
        "name nobody")


def test_a_directory_made_inside_the_managed_tree_is_not_a_survivor(isolated_guard):
    """The other direction, and the reason the guard is usable at all.

    Roughly every test in this suite allocates under `tmp_path`. If those
    counted, the number would be five figures on a clean tree and the ratchet
    would be noise.
    """
    _shared, managed = isolated_guard
    tempfile.mkdtemp(prefix="ordinary-", dir=managed)
    assert guard.survivors() == []


def test_a_nested_path_inside_the_managed_tree_is_not_a_survivor(isolated_guard):
    """`tmp_path` is several levels below the retention root, not at it.

    A prefix check that compared for equality would flag every real test.
    """
    _shared, managed = isolated_guard
    deep = managed / "pytest-3" / "popen-gw0" / "test_thing0"
    deep.mkdir(parents=True)
    tempfile.mkdtemp(prefix="ordinary-", dir=deep)
    assert guard.survivors() == []


def test_a_sibling_whose_name_merely_starts_with_the_managed_prefix_is_a_survivor(
        isolated_guard):
    """`/tmp/pytest-of-nobody-EVIL` is not inside `/tmp/pytest-of-nobody`.

    A bare `startswith` on the prefix string says it is. The separator in the
    comparison is what makes that false, and nothing else in the suite would
    have noticed: the name never occurs naturally.
    """
    _shared, managed = isolated_guard
    sibling = Path(str(managed) + "-EVIL")
    sibling.mkdir()
    leaked = Path(tempfile.mkdtemp(prefix="planted-", dir=sibling))
    assert [p for p, _ in guard.survivors()] == [os.path.realpath(leaked)]


def test_scratch_that_was_created_and_removed_is_not_a_survivor(isolated_guard):
    """Judged at session end, not at creation.

    Making a scratch file in the shared temp directory and removing it is
    correct code. Counting the creation would push authors toward `dir=` in
    places where the cleanup already existed, for no gain.
    """
    shared, _managed = isolated_guard
    made = tempfile.mkdtemp(prefix="transient-", dir=shared)
    shutil.rmtree(made)
    assert guard.survivors() == []


def test_mkstemp_outside_the_managed_tree_is_a_survivor(isolated_guard):
    """The second of the three wrappers. `supervise-*.log` came through here."""
    shared, _managed = isolated_guard
    fd, name = tempfile.mkstemp(prefix="planted-", suffix=".log", dir=shared)
    os.close(fd)
    assert [p for p, _ in guard.survivors()] == [os.path.realpath(name)]


def test_a_named_temporary_file_that_deletes_itself_is_not_a_survivor(isolated_guard):
    """`delete=True` is noted at creation and gone by the time it is judged.

    Both halves in one test on purpose: the `delete=False` sibling below is the
    leak, and these two differ by one keyword.
    """
    shared, _managed = isolated_guard
    with tempfile.NamedTemporaryFile(prefix="planted-", dir=shared):
        assert guard.survivors(), "the wrapper never saw the file at all"
    assert guard.survivors() == []


def test_a_named_temporary_file_that_does_not_delete_itself_is_a_survivor(
        isolated_guard):
    """`31c-marp-*.css` came through here: `delete=False`, never unlinked."""
    shared, _managed = isolated_guard
    # `with`, even though the point is that the file OUTLIVES the block. The
    # context manager closes the handle; `delete=False` is what stops it also
    # unlinking, and that pair is exactly the shape being tested.
    with tempfile.NamedTemporaryFile(prefix="planted-", suffix=".css",
                                     delete=False, dir=shared) as handle:
        name = handle.name
    assert [p for p, _ in guard.survivors()] == [os.path.realpath(name)]


def test_a_temporary_directory_context_manager_is_covered_through_mkdtemp(
        isolated_guard):
    """`TemporaryDirectory` is not wrapped; it calls `mkdtemp` and that is.

    Pinned because it is the assumption that lets three wrappers cover the
    whole stdlib. If a future Python inlined the call, the guard would go quiet
    over every `TemporaryDirectory` in the tree and nothing else would say so.
    """
    shared, _managed = isolated_guard
    with tempfile.TemporaryDirectory(prefix="planted-", dir=shared):
        assert guard.survivors(), (
            "TemporaryDirectory no longer routes through tempfile.mkdtemp; the "
            "guard needs its own wrapper for it")
    assert guard.survivors() == []


def test_survivors_are_grouped_by_the_test_that_made_them(isolated_guard):
    shared, _managed = isolated_guard
    for _ in range(3):
        tempfile.mkdtemp(prefix="planted-", dir=shared)
    guard.CURRENT_TEST = "a-second-test"
    tempfile.mkdtemp(prefix="planted-", dir=shared)

    rows = guard.survivors_by_test(10)
    assert [(who, count) for who, count, _example in rows] == [
        ("the-test-under-observation", 3), ("a-second-test", 1)], rows


def test_the_attribution_row_cap_is_honoured(isolated_guard):
    """These rows are pickled back through execnet from each xdist worker.

    An uncapped map of a leaking run is tens of thousands of rows on the wire.
    """
    shared, _managed = isolated_guard
    for i in range(5):
        guard.CURRENT_TEST = f"test-{i}"
        tempfile.mkdtemp(prefix="planted-", dir=shared)
    assert len(guard.survivors_by_test(2)) == 2


def test_worker_rows_fold_into_the_controller_map():
    """Without this the controller enforces against its own count, which is 0.

    Under `-n auto` the controller runs no test bodies. The overlay ratchet
    next door hit exactly this and reported 0 for a whole sharded run.
    """
    # The example paths are deliberately NOT under the shared temp directory.
    # `merge_rows` never opens them -- they are opaque display strings carried
    # through to the report -- so a real-looking /tmp literal here would buy
    # nothing and read, to the next person and to the linter, as a test that
    # touches /tmp.
    into: dict = {}
    guard.merge_rows([("test_a", 2, "example/x"), ("test_b", 1, "example/y")],
                     into=into)
    guard.merge_rows([("test_a", 3, "example/z")], into=into)
    assert into["test_a"][0] == 5
    assert into["test_b"][0] == 1


# ---------------------------------------------------------------------------
# The unattributable half: the before/after walk
# ---------------------------------------------------------------------------


def test_the_diff_reports_only_what_appeared_and_is_still_there(tmp_path,
                                                                monkeypatch):
    """Three cases in one, because the interesting ones are the exclusions.

    An entry that was already there is not this run's. An entry that appeared
    and was removed is not a leak. Only "appeared AND survived" is reported --
    and even that is reported, never enforced, because other processes on this
    machine write here too.
    """
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(shared))

    (shared / "was-here-before").mkdir()
    before = guard.top_level_snapshot()
    assert before == {"was-here-before"}

    (shared / "appeared-and-stayed").mkdir()
    (shared / "appeared-and-went").mkdir()
    after = guard.top_level_snapshot()
    (shared / "appeared-and-went").rmdir()

    assert guard.appeared_and_survived(before, after) == ["appeared-and-stayed"]


def test_an_unreadable_temp_directory_degrades_to_no_observation(monkeypatch):
    """This half fails nothing, so it must not take the session down either.

    The opposite default would turn an unreadable /tmp into a red run with a
    traceback about the guard rather than about the suite.
    """
    monkeypatch.setattr(tempfile, "tempdir", "/nonexistent-temp-jamesbond")
    assert guard.top_level_snapshot() == set()


@pytest.mark.parametrize("name,expected", [
    ("odin-cad-clu-7f3a", "odin-cad-clu-*"),
    ("skill_report_example-skill_9x", "skill_report_example-*"),
    ("31c-marp-01ctpvj6.css", "31c-marp-*"),
    ("systemd-private", "systemd-*"),
    ("bare", "bare"),
])
def test_names_collapse_to_a_readable_family(name, expected):
    """10,751 `odin-cad*` entries are one finding, not 10,751 lines of report."""
    assert guard.family(name) == expected


def test_the_summary_orders_families_by_size():
    names = ["a-1", "a-2", "a-3", "b-1"]
    assert guard.summarise(names) == ["a-* x3", "b-* x1"]


# ---------------------------------------------------------------------------
# The wiring, and whether it is actually live
# ---------------------------------------------------------------------------


def test_the_managed_root_is_the_retention_level_not_this_session(tmp_path,
                                                                  monkeypatch):
    """Found from either depth, because a worker's basetemp is one deeper.

    `pytest-of-user/pytest-3` in the controller, `pytest-of-user/pytest-3/
    popen-gw0` in an xdist worker. Hardcoding either `.parent` or
    `.parent.parent` is right in one process and wrong in the other, and the
    wrong one exempts the whole of /tmp or none of the managed tree.
    """
    from tests.conftest import _managed_temp_root

    shared = tmp_path / "shared"
    retention = shared / "pytest-of-user"
    monkeypatch.setattr(tempfile, "tempdir", str(shared))

    for depth in (retention / "pytest-3", retention / "pytest-3" / "popen-gw0"):
        depth.mkdir(parents=True)

        # `_d=depth` binds this iteration's value. A bare closure over the loop
        # variable reads the LAST one whenever it is finally called, which
        # happens to be harmless here only because the call is in the same
        # iteration -- and that is the accident B023 exists to stop anyone
        # relying on. Both depths must be checked, so a lambda that answered
        # `popen-gw0` twice would assert nothing about the controller's depth.
        class _Config:
            _tmp_path_factory = type(
                "F", (), {"getbasetemp": lambda self, _d=depth: _d})()

        assert _managed_temp_root(_Config()) == retention.resolve(), depth


def test_an_explicit_basetemp_outside_the_shared_directory_is_its_own_root(
        tmp_path, monkeypatch):
    """`--basetemp=/some/where/else`. The caller named it and owns it."""
    from tests.conftest import _managed_temp_root

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "elsewhere"))
    (tmp_path / "elsewhere").mkdir()
    base = tmp_path / "explicit"
    base.mkdir()

    class _Config:
        _tmp_path_factory = type("F", (), {"getbasetemp": lambda self: base})()

    assert _managed_temp_root(_Config()) == base.resolve()


def test_a_missing_or_corrupt_baseline_fails_strict(tmp_path, monkeypatch):
    """A deleted baseline must go red, not quiet.

    The same contract `_overlay_reachability_baseline` states next door: the
    opposite default turns `rm config/tmp-leak-baseline.json` into a silently
    disabled gate, which is the shape this whole repair is about.
    """
    import tests.conftest as ct

    for content in (None, "not json at all", '{"wrong_key": 3}'):
        target = tmp_path / "baseline.json"
        if content is not None:
            target.write_text(content, encoding="utf-8")
        monkeypatch.setattr(ct, "_TMP_LEAK_BASELINE", target)
        assert ct._tmp_leak_baseline() == 0, content

    good = tmp_path / "good.json"
    good.write_text(json.dumps({"surviving_unmanaged_entries": 7}), encoding="utf-8")
    monkeypatch.setattr(ct, "_TMP_LEAK_BASELINE", good)
    assert ct._tmp_leak_baseline() == 7


def test_the_committed_baseline_is_readable_and_is_a_floor_of_zero():
    """The number this branch froze, asserted so a later edit is a visible diff."""
    import tests.conftest as ct

    assert ct._TMP_LEAK_BASELINE.is_file(), ct._TMP_LEAK_BASELINE
    assert ct._tmp_leak_baseline() == 0, (
        "the temp-leak baseline is no longer 0; it is shrink-only, so raising "
        "it needs the leaking test named in the run's report to be fixed instead")


def test_a_clean_run_says_nothing_even_when_the_shared_directory_grew(monkeypatch):
    """A report that always prints is not a gate.

    The diff half cannot speak on its own. MEASURED 2026-09-04 over a full run:
    24 top-level entries appeared in /tmp, 18 of them written by other processes
    on this machine. Keying the report on that number printed a line every
    single run, and it broke two existing tests that pin exactly this contract
    for the overlay watch next door
    (`test_a_run_inside_the_budget_still_says_nothing`,
    `test_a_quiet_overlay_leaves_a_real_session_alone`).

    So: nothing attributable, nothing said, however busy the shared directory
    was.
    """
    import tests.conftest as ct

    class _Reporter:
        def __init__(self):
            self.lines = []

        def write_line(self, text, **_kw):
            self.lines.append(text)

    class _PM:
        def get_plugin(self, _name):
            return reporter

    class _Config:
        workerinput = None  # absent below; see the delattr

        def getoption(self, name, default=None):
            return {"collectonly": False, "numprocesses": None}.get(name, default)
        pluginmanager = _PM()

    class _Session:
        exitstatus = 0
        config = _Config()

    reporter = _Reporter()
    del _Config.workerinput

    # Adopt this synthetic session as the real one for the duration, or the
    # identity check added after the 2026-09-04 measurement returns early and
    # this test asserts an empty list against a function that did nothing.
    monkeypatch.setattr(ct, "_REAL_SESSION_CONFIG", _Session.config)
    # This run allocated nothing outside the managed tree...
    monkeypatch.setattr(guard, "_UNMANAGED", {})
    monkeypatch.setattr(ct, "_WORKER_TMP_TOTAL", [0])
    # ...but the shared directory gained a great deal from elsewhere.
    monkeypatch.setattr(ct, "_TMP_BEFORE", set())
    monkeypatch.setattr(guard, "top_level_snapshot",
                        lambda: {f"somebody-elses-{i}" for i in range(50)})

    session = _Session()
    ct._tmp_leak_sessionfinish(session)

    assert reporter.lines == [], reporter.lines
    assert session.exitstatus == 0


def test_a_synthetic_session_is_not_this_run_and_is_left_alone(monkeypatch):
    """The other direction of the identity check, and it is not hypothetical.

    Three tests next door drive `pytest_sessionfinish` with a session they built
    themselves, to exercise the overlay ratchet's report, and assert on the
    exact lines it writes. MEASURED 2026-09-04 against a deliberately leaking
    tree, all three went red: this guard wrote the REAL run's 116 leaks into
    their fake reporter and set their fake session's exitstatus. On a clean tree
    the counters are empty and the collision is invisible, which is why it has
    to be pinned rather than left to be noticed.
    """
    import tests.conftest as ct

    class _Reporter:
        def __init__(self):
            self.lines = []

        def write_line(self, text, **_kw):
            self.lines.append(text)

    reporter = _Reporter()

    class _Config:
        def getoption(self, name, default=None):
            return {"collectonly": False, "numprocesses": None}.get(name, default)
        pluginmanager = type("PM", (), {"get_plugin": lambda self, _n: reporter})()

    class _Session:
        exitstatus = 0
        config = _Config()

    # A real run that leaked plenty, and a fake session that knows nothing of
    # it. The path is a stand-in that nothing opens; see the note in
    # `test_worker_rows_fold_into_the_controller_map` for why it is not spelled
    # under the shared temp directory.
    leaked = "example/leaked-by-the-real-run"
    monkeypatch.setattr(guard, "_UNMANAGED", {leaked: "tests/real.py::t"})
    monkeypatch.setattr(guard, "survivors",
                        lambda: [(leaked, "tests/real.py::t")])
    monkeypatch.setattr(ct, "_REAL_SESSION_CONFIG", object())

    session = _Session()
    ct._tmp_leak_sessionfinish(session)

    assert reporter.lines == [], reporter.lines
    assert session.exitstatus == 0, (
        "a synthetic session's exit status was set from the real run's leaks")


def test_the_guard_is_armed_in_this_very_session():
    """THE TEST THAT MATTERS. An unarmed guard reports a clean tree forever.

    Asked of the live `tempfile` module rather than of `conftest`'s source,
    because what is under test is whether `arm()` ran, not whether the call is
    written down. All three wrappers, because two of three armed is a guard
    with a documented hole.
    """
    for name in ("mkdtemp", "mkstemp", "NamedTemporaryFile"):
        fn = getattr(tempfile, name)
        assert getattr(fn, "__module__", "") == guard.__name__, (
            f"tempfile.{name} is not wrapped, so anything it creates outside "
            f"pytest's managed tree is invisible to the leak guard")
