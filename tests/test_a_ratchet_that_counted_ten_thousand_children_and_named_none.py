"""The reachability ratchet enforced a number nobody could act on.

`pytest_sessionfinish` in tests/conftest.py fails the session when more child
processes of the run had the operator's live data root reachable than the
frozen `config/overlay-reachability-baseline.json`. On 2026-09-04, in HELM on
main at b5d3a30, a full `-n auto` run reported:

    24652 passed, 2 skipped, 0 failed  ->  exit 1
    10069 child process(es) ... reachable (frozen baseline 9659)

The pre-push gate runs that suite, so it refused every push while no test was
failing. And the report could not be acted on. `_CHILD_SPAWN_COUNT` and
`_CHILD_SPAWNS` are PER PROCESS: under `-n auto` the controller holds only its
own, and the controller spawns nothing but the execnet bootstrap of the
workers. So all sixteen printed examples read

    <unknown test> -> .venv/bin/python -u -c import sys;exec(eval(...))

while the other ~10053 arrived through `workeroutput["overlay_reachable"]` as a
BARE INTEGER: no nodeid, no command, no attribution for 99.8% of the number the
gate enforced. The message then said to "pass HEADING_OS_DATA pointing at a
tmp_path to whatever this run added" and named nothing it could be passed to.

THE FIRST HALF OF THE REPAIR: the workers report attribution upward, not just a
count, and the controller aggregates it into one ranking printed with the ERROR
line.

MEASURED 2026-09-04 in this worktree, full `-n auto -m "not acceptance"`, with
the frozen baseline forced to 0 so the report always printed:

    9173 reachable, 1460 distinct tests named, 1286 unattributed
    644  tests/test_a_wall_that_read_the_present_and_shipped_the_past.py
    578  tests/test_git_push.py
    461  tests/test_push_all_gate.py
    351  tests/test_canopus_check.py
    ...  6420 of the 7887 attributed spawns were raw `git`

THE SECOND HALF: `git` has never read HEADING_OS_DATA, so a test that only
shells out to `git` in a scratch tree can pin that variable away from the
operator's overlay without changing anything it measures. Sixteen files take
the `scratch_data_root` fixture on that basis.

RUN-TO-RUN VARIANCE, which the baseline file recorded as NOT ESTABLISHED and
which decides whether the growth is real: two full runs of the SAME shape on
the SAME tree, 2026-09-04, gave 9119 and 9173. A spread of 54, 0.6%. The HELM
sequence 9659 -> 9815 -> 9861 -> 10069 is +410 against a 0.6% noise floor, so
it is growth, not variance.

THE COUNT IS CLONE-DEPENDENT, which is why the baseline is not simply refrozen
at what this worktree measures. 50 tests skip in a YARD (`MAIN_CLONE_SKIP`, "no
data overlay on this clone"), so the same commit measures about 900 lower here
than in HELM, and HELM is where the pre-push gate runs.

WHAT THE PIN DOES NOT BUY, measured because it was worth knowing rather than
assumed. Same tree, same shape, the pin the only difference:

    pin off   8904 children   820 s
    pin on    4855 children   803 s

Halving the children does not halve the suite, and barely moves it. The
reduction is worth having because it removes a real reachability and unblocks a
gate; it is not a performance change and must not be sold as one.

The suite's own runtime went 458 s -> 813 s across this work and is
UNEXPLAINED. Two candidates are eliminated, named here so the next person does
not spend two full runs re-deriving them. It is not the pin: the A/B above
holds everything else fixed and the pin-off half is 820 s, not 458 s. It is not
a competing session: the other pytest processes on the machine were traced by
ancestry to this suite's OWN nested pytest children. That nesting is real --
27 of 1083 test files spawn a pytest child directly, at 57 call sites, and ten
scripts spawn one themselves, so a test that runs one of those nests with no
`pytest` literal anywhere in it -- but its CONTRIBUTION to the step is NOT
established, and a first count of "226 files" was a grep matching the word in
docstring prose rather than in argv.

Run: python3 -m pytest \\
     tests/test_a_ratchet_that_counted_ten_thousand_children_and_named_none.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import overlay_write_guard as guard  # noqa: E402
from scripts.utils.repo_files import read_sources  # noqa: E402
from tests import conftest as root_conftest  # noqa: E402


# ============================================================
# Doubles for the pytest objects the hook reads
# ============================================================

class _Reporter:
    def __init__(self):
        self.lines = []

    def write_line(self, line, **_kwargs):
        self.lines.append(line)


class _Config:
    def __init__(self, reporter, options, worker=False):
        self._options = options
        self._reporter = reporter
        self.pluginmanager = self
        if worker:
            self.workerinput = {"workerid": "gw0"}
            self.workeroutput = {}

    def getoption(self, name, default=None):
        return self._options.get(name, default)

    def get_plugin(self, name):
        return self._reporter if name == "terminalreporter" else None


class _Session:
    def __init__(self, reporter, worker=False, **options):
        self.config = _Config(reporter, options, worker=worker)
        self.exitstatus = 0


class _Node:
    def __init__(self, output):
        self.workeroutput = output


@pytest.fixture
def isolated_counters(monkeypatch):
    """Every test here writes the guard's module globals; none may leak.

    The suite's OWN reachability total is computed from these names, so a test
    that left one raised would corrupt the number the gate enforces at the end
    of the very run that contains it.
    """
    monkeypatch.setattr(guard, "_CHILD_SPAWN_COUNT", 0, raising=True)
    monkeypatch.setattr(guard, "_CHILD_SPAWNS", [], raising=True)
    # `raising=False` on the three names this change INTRODUCED, deliberately.
    # With `raising=True` these tests fail against the previous version with an
    # AttributeError out of the fixture, which proves only that a name is new.
    # Falling through lets each one fail on its own assertion instead, which is
    # the claim actually being made.
    monkeypatch.setattr(guard, "_CHILD_SPAWNS_BY_TEST", {}, raising=False)
    monkeypatch.setattr(guard, "_CHILD_SPAWN_UNATTRIBUTED", 0, raising=False)
    monkeypatch.setattr(root_conftest, "_WORKER_REACHABLE_TOTAL", [0],
                        raising=True)
    monkeypatch.setattr(root_conftest, "_WORKER_SPAWN_ATTRIBUTION", {},
                        raising=False)
    monkeypatch.setattr(guard, "_WATCH_BEFORE", {"live": (ROOT, {})},
                        raising=True)
    monkeypatch.setattr(guard, "_watch_snapshot", lambda: {"live": (ROOT, {})})
    monkeypatch.setattr(guard, "watch_complaints", lambda before, after: [])
    monkeypatch.setattr(root_conftest, "_OWNS_OVERLAY_WATCH", True,
                        raising=True)


# ============================================================
# A worker reports WHO, not only HOW MANY
# ============================================================

def test_a_worker_sends_attribution_up_beside_the_count(isolated_counters):
    """The failing half. Before this change `workeroutput` carried exactly one
    key, `overlay_reachable`, and a bare integer is the whole reason 99.8% of
    the enforced number had no nodeid.
    """
    guard._CHILD_SPAWNS_BY_TEST.update({
        "tests/test_x.py::test_a": [30, "git init -q"],
        "tests/test_x.py::test_b": [4, "git clone -q src dst"],
    })
    guard._CHILD_SPAWN_COUNT = 34

    session = _Session(_Reporter(), worker=True, collectonly=False)
    root_conftest.pytest_sessionfinish(session, 0)

    rows = session.config.workeroutput["overlay_reachable_by_test"]
    assert ("tests/test_x.py::test_a", "git init -q", 30) in rows
    assert ("tests/test_x.py::test_b", "git clone -q src dst", 4) in rows
    assert session.config.workeroutput["overlay_reachable"] == 34
    assert session.config.workeroutput["overlay_reachable_unattributed"] == 0
    assert session.exitstatus == 0, "a shard still judges nothing"


def test_what_the_wire_slice_leaves_behind_is_reported_as_a_number(
        isolated_counters):
    """The other direction of the same claim, and the honesty obligation.

    `workeroutput` is pickled through execnet, so a worker sends its TOP slice
    rather than its whole map. A partial list presented as the run is the
    defect this gate's own history is made of, so whatever the slice drops is
    stated in the same message.
    """
    for i in range(root_conftest._WORKER_ATTRIBUTION_ROWS + 25):
        guard._CHILD_SPAWNS_BY_TEST[f"tests/test_x.py::t{i:03d}"] = [1, "git init"]
    guard._CHILD_SPAWN_COUNT = len(guard._CHILD_SPAWNS_BY_TEST)

    session = _Session(_Reporter(), worker=True, collectonly=False)
    root_conftest.pytest_sessionfinish(session, 0)

    out = session.config.workeroutput
    assert len(out["overlay_reachable_by_test"]) == \
        root_conftest._WORKER_ATTRIBUTION_ROWS
    assert out["overlay_reachable_unattributed"] == 25


# ============================================================
# The controller aggregates the shards into one ranking
# ============================================================

def test_the_controller_sums_the_same_test_across_workers(isolated_counters):
    """A test that ran on two workers must appear once, with both counts.

    Before this change the controller summed integers and kept no map at all,
    so this could not be asked.
    """
    root_conftest.pytest_testnodedown(_Node({
        "overlay_reachable": 12,
        "overlay_reachable_by_test": [("tests/a.py::t", "git init", 12)],
    }), None)
    root_conftest.pytest_testnodedown(_Node({
        "overlay_reachable": 5,
        "overlay_reachable_by_test": [("tests/a.py::t", "git init", 3),
                                      ("tests/b.py::u", "git clone", 2)],
    }), None)

    assert root_conftest._WORKER_REACHABLE_TOTAL[0] == 17
    assert root_conftest._WORKER_SPAWN_ATTRIBUTION["tests/a.py::t"][0] == 15
    assert root_conftest._WORKER_SPAWN_ATTRIBUTION["tests/b.py::u"][0] == 2


def test_the_error_line_names_the_top_offender_with_its_command(
        isolated_counters):
    """The refusal, and the sentence that makes it actionable.

    The pre-change ERROR line carried a total and a baseline and told the
    reader to pin HEADING_OS_DATA on "whatever this run added", naming nothing.
    """
    monkeypatch_baseline(1)
    root_conftest.pytest_testnodedown(_Node({
        "overlay_reachable": 40,
        "overlay_reachable_by_test": [
            ("tests/heavy.py::test_spawns_a_lot", "git init -q", 37),
            ("tests/light.py::test_spawns_one", "git clone -q a b", 3)],
    }), None)

    reporter = _Reporter()
    session = _Session(reporter, collectonly=False, numprocesses=8)
    root_conftest.pytest_sessionfinish(session, 0)

    body = "\n".join(reporter.lines)
    assert session.exitstatus == 1
    assert "top spawners:" in body
    assert "37x tests/heavy.py::test_spawns_a_lot -> git init -q" in body
    # The ranking is a ranking: the heavier one comes first.
    assert body.index("test_spawns_a_lot") < body.index("test_spawns_one")
    # And the advice names a test instead of "whatever this run added".
    assert "Start with tests/heavy.py::test_spawns_a_lot" in body


def test_a_run_inside_the_budget_still_says_nothing(isolated_counters):
    """The other direction. A report that always prints is not a gate, and a
    ranking is not a reason to start printing on a green run.
    """
    monkeypatch_baseline(100)
    root_conftest.pytest_testnodedown(_Node({
        "overlay_reachable": 40,
        "overlay_reachable_by_test": [("tests/heavy.py::t", "git init", 40)],
    }), None)

    reporter = _Reporter()
    session = _Session(reporter, collectonly=False, numprocesses=8)
    root_conftest.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0
    assert reporter.lines == []


def monkeypatch_baseline(value):
    """Set the frozen baseline for one test.

    A plain rebind rather than a fixture because `isolated_counters` already
    owns the undo for everything else here, and pytest's monkeypatch is not
    reachable from a bare helper. Restored by `_restore_baseline` below.
    """
    root_conftest._overlay_reachability_baseline = lambda: value


@pytest.fixture(autouse=True)
def _restore_baseline():
    original = root_conftest._overlay_reachability_baseline
    yield
    root_conftest._overlay_reachability_baseline = original


# ============================================================
# The pin: a child that cannot resolve the operator's data root
# ============================================================

def test_a_child_with_the_data_root_inside_the_overlay_is_counted(
        isolated_counters, monkeypatch, tmp_path):
    """The mechanism the ratchet actually measures, driven for real.

    `_OVERLAY_PREFIXES` is faked so this holds on a public clone with no
    overlay on disk, where the real prefix set is empty and every child would
    read as safe.
    """
    fake_overlay = tmp_path / "overlay"
    monkeypatch.setattr(guard, "_OVERLAY_PREFIXES",
                        (str(fake_overlay) + "/",), raising=True)
    monkeypatch.setenv("HEADING_OS_DATA", str(fake_overlay / "data"))

    subprocess.run([sys.executable, "-c", "pass"], check=True)

    assert guard._CHILD_SPAWN_COUNT == 1, (
        "a child that inherits a data root inside the overlay is exactly what "
        "the ratchet counts; if this is 0 the guard is not armed and every "
        "other claim in this file is measuring nothing")


def test_a_child_under_the_scratch_root_is_not_counted(
        isolated_counters, monkeypatch, tmp_path, scratch_data_root):
    """The fix, and the failing half against the previous version: before the
    `scratch_data_root` fixture existed this errored with `fixture not found`.
    """
    fake_overlay = tmp_path / "overlay"
    monkeypatch.setattr(guard, "_OVERLAY_PREFIXES",
                        (str(fake_overlay) + "/",), raising=True)

    subprocess.run([sys.executable, "-c", "pass"], check=True)

    assert guard._CHILD_SPAWN_COUNT == 0


def test_the_opt_in_is_actually_taken_by_the_files_it_was_measured_on():
    """A floor under the corpus.

    The fixture on its own removes nothing. Sixteen files took it on
    2026-09-04, which is what turned 9173 into the number in the baseline file;
    a silent removal of the opt-ins would restore the count with nothing red.
    """
    # `read_sources`, not `read_text`, because this is a walk-then-read over a
    # live checkout: a file a parallel agent creates and deletes inside that
    # window would otherwise raise FileNotFoundError out of a floor check.
    vanished: list = []
    marks = [path for path, text in read_sources(
        sorted((ROOT / "tests").rglob("test_*.py")), vanished, errors="replace")
        if 'usefixtures("scratch_data_root")' in text]
    assert len(marks) >= 16, (
        f"only {len(marks)} file(s) opt into scratch_data_root; 16 did when "
        f"the baseline was frozen on 2026-09-04")
