"""The overlay gate counted child PROCESSES, and processes move with the load.

THE MEASUREMENT. Nine full runs of this suite in HELM on 2026-09-06, over ONE
unchanged tree, reported these reachable-child counts:

    6010, 6115, 6174, 6209, 6265, 6276, 6361, 6370

and a ninth that printed nothing because it came in under the threshold, which
is the only case the gate stays silent for. Spread 360 on a mean of 6196, about
+-3%, with no edit between any two of them. The last three (6209, 6010, 6370)
were taken under flock with exactly one pytest controller on the machine and a
load average of 8.7 to 11.8 at start, each run 4:11 to 4:22, so this is not
contention with a neighbouring session. Against a frozen 6100 -- the second
percentile of the gate's own distribution -- the suite is red about five runs in
six, and the push gate runs the full suite.

WHY. A count of child processes is a count of how far each test got and how the
xdist workers were packed, both of which the scheduler decides. The SET of tests
that spawn a child able to reach the operator's live data root does not depend
on the scheduler: a test either spawns such a child or it does not.

WHAT THIS ADDS. A second measure, `distinct test(s) spawned at least one such
child`, reported beside the child count. It is not `len(attribution)`, the
number already printed next to the top-spawner list: attribution is a REPORT,
each worker sends only its top `_WORKER_ATTRIBUTION_ROWS` rows, and the rest
arrives as a bare integer -- so that length answers "how many tests survived the
slice". The measure here is the union of per-process digests of every distinct
nodeid in the map, which is unsliced, costs 8 bytes per test on the wire, and
counts a nodeid seen on two workers (or `UNKNOWN_TEST`, which every worker
produces) exactly once.

WHAT IT DOES NOT DO, stated because the opposite would be easy to assume:
nothing here changes `reachable_children`, and the distinct count judges nothing
until the operator writes a `reachable_tests` key into
`config/overlay-reachability-baseline.json`.

THE FLAP WAS NEVER THE METRIC, AND IT IS NOW FIXED. Everything in the two
paragraphs below was measured BEFORE `_wire_safe` existed, and it is kept
because it is how the real defect was found, not because it still describes the
gate. With the wire repaired (`tests/test_a_worker_that_died_of_a_byte_in_a_
filename.py`), three consecutive full runs on 2026-09-06 at machine loads of
0.68, 11.10 and 14.00 reported 6809 children and 2679 distinct tests: the SAME
two numbers, digit for digit, spread ZERO, with every one of the 18 workers
delivering in each run (sum sent 2694 == sum received 2694, no node down). The
distinct count matches the true union measured independently, 2679.

WHAT THAT MEANS FOR THE THRESHOLDS, and it is the operator's call, not this
file's: the frozen `reachable_children` of 6100 was derived from runs that were
each silently missing one to three workers, so it sits BELOW what an honest run
reports. Until it is re-measured in HELM and re-frozen, the ratchet fails on a
correct run.

AND THE MEASUREMENT SAID THE NEW NUMBER FLAPPED TOO. Ten full runs of this suite
in this YARD on 2026-09-06, same tree, `-n auto -m "not acceptance"`, each 4:17
to 4:52, machine load 0.6 to 13.9 at start with one foreign pytest present for
runs 2 onward. Reported distinct tests: 2156, 2318, 2454, 2342, 2388, 2248,
2360, 2344, 2437, 2389. Spread 298 on a mean of 2344, about +-6%, against the
child count's 5537 to 6110, about +-5% over the same runs. Relative to its own
mean the distinct count is no steadier than the number it was meant to replace,
so the ratchet is NOT ready to move onto it and this file does not propose that
it should.

WHY, and this is the finding worth keeping. Four of those runs also dumped every
process's raw `_CHILD_SPAWNS_BY_TEST` keys from a scratch plugin. The TRUE union
across all 19 processes was 2677, 2678, 2678, 2679 -- a spread of TWO, 0.07%,
while what the gate printed for the same four runs ranged over 189. So the
invariant really is near-constant, exactly as the reachable-set argument
predicts, and the +-6% was introduced by the REPORTING PATH, not by the
scheduler. Instrumenting both ends settled it: no key ever enters the map after
the gate reads it (zero, in every process of a full run), every worker sends its
whole map, and the loss is two or three workers per run whose report never
arrives at all -- they die inside xdist's `workerfinished` send on an
unencodable byte. That defect, its measurement and its repair are in
`tests/test_a_worker_that_died_of_a_byte_in_a_filename.py`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import overlay_write_guard as guard  # noqa: E402
from tests import conftest as root_conftest  # noqa: E402


# ============================================================
# Doubles for the pytest objects the hook reads
# ============================================================

class _Reporter:
    def __init__(self):
        self.lines = []

    def write_line(self, line, **_kwargs):
        self.lines.append(line)

    def text(self):
        return "\n".join(self.lines)


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
    """Every test here writes module globals the REAL run also reads.

    The suite's own gate is computed from these names at the end of the very
    session that contains these tests, so one left raised would corrupt the
    number that session enforces.

    `raising=False` on the names this change INTRODUCED: with `raising=True`
    each test would fail against the previous version with an AttributeError
    out of the fixture, which proves only that a name is new rather than the
    claim the test is making.
    """
    monkeypatch.setattr(guard, "_CHILD_SPAWN_COUNT", 0, raising=True)
    monkeypatch.setattr(guard, "_CHILD_SPAWNS", [], raising=True)
    monkeypatch.setattr(guard, "_CHILD_SPAWNS_BY_TEST", {}, raising=True)
    monkeypatch.setattr(guard, "_CHILD_SPAWN_UNATTRIBUTED", 0, raising=True)
    monkeypatch.setattr(root_conftest, "_WORKER_REACHABLE_TOTAL", [0], raising=True)
    monkeypatch.setattr(root_conftest, "_WORKER_SPAWN_ATTRIBUTION", {}, raising=True)
    monkeypatch.setattr(root_conftest, "_WORKER_SPAWNER_DIGESTS", set(),
                        raising=False)
    monkeypatch.setattr(root_conftest, "_WORKER_MAP_CAPPED", [0], raising=False)
    monkeypatch.setattr(guard, "_WATCH_BEFORE", {"live": (ROOT, {})}, raising=True)
    monkeypatch.setattr(guard, "_watch_snapshot", lambda: {"live": (ROOT, {})})
    monkeypatch.setattr(guard, "watch_complaints", lambda before, after: [])
    monkeypatch.setattr(root_conftest, "_OWNS_OVERLAY_WATCH", True, raising=True)
    monkeypatch.delenv("HEADING_OS_OVERLAY_REPORT", raising=False)


@pytest.fixture
def frozen_baselines(monkeypatch, tmp_path):
    """Point the gate at a baseline file this test owns.

    A factory rather than a value: several tests need the same file with and
    without the `reachable_tests` key, and reading the committed one would make
    them depend on a number the operator is expected to change.
    """
    path = tmp_path / "overlay-reachability-baseline.json"
    monkeypatch.setattr(root_conftest, "_REACHABILITY_BASELINE", path)

    def _write(**keys):
        path.write_text(json.dumps(keys), encoding="utf-8")
        return path
    return _write


def _spawns(count_by_nodeid):
    guard._CHILD_SPAWNS_BY_TEST.update(
        {nodeid: [count, "git init -q"] for nodeid, count in count_by_nodeid.items()})
    guard._CHILD_SPAWN_COUNT = sum(count_by_nodeid.values())


# ============================================================
# A worker reports its WHOLE map, not the slice it prints
# ============================================================

def test_a_worker_sends_a_digest_for_every_distinct_test_it_saw(isolated_counters):
    """The failing half. `workeroutput` carried the top 100 attribution rows and
    a count of what they dropped, so the only distinct-test number the
    controller could build was "distinct tests that survived the slice".

    The arrangement makes the two answers differ on purpose: 150 distinct
    tests, a 100-row wire slice.
    """
    _spawns({f"tests/test_x.py::t{i:03d}": 1 for i in range(150)})
    session = _Session(_Reporter(), worker=True, collectonly=False)

    root_conftest.pytest_sessionfinish(session, 0)

    out = session.config.workeroutput
    assert len(out["overlay_reachable_by_test"]) == \
        root_conftest._WORKER_ATTRIBUTION_ROWS, "the report slice moved"
    assert len(out["overlay_reachable_test_digests"]) == 150, (
        "the measure was sliced like the report; a baseline frozen from that "
        "is a baseline about the slice"
    )
    assert out["overlay_reachable_map_capped"] == 0
    assert session.exitstatus == 0, "a shard still judges nothing"


def test_a_worker_says_so_when_its_attribution_map_hit_the_cap(isolated_counters):
    """Past `_CHILD_SPAWN_BY_TEST_CAP` a new nodeid is dropped from the map, so
    the digest union becomes a LOWER BOUND. A lower bound presented as a count
    is the defect the child ratchet's own history is made of."""
    cap = guard._CHILD_SPAWN_BY_TEST_CAP
    _spawns({f"tests/test_x.py::t{i:05d}": 1 for i in range(cap)})
    session = _Session(_Reporter(), worker=True, collectonly=False)

    root_conftest.pytest_sessionfinish(session, 0)

    assert session.config.workeroutput["overlay_reachable_map_capped"] == 1


# ============================================================
# The controller takes a UNION, never a sum
# ============================================================

def test_a_test_seen_on_two_workers_counts_once(isolated_counters,
                                                frozen_baselines):
    """Why digests travel instead of counts.

    `UNKNOWN_TEST` is produced by EVERY worker for its own session-level
    spawns, so adding per-worker distinct counts would inflate the total by one
    per worker before any real test is double-counted. Here two workers each
    report it, plus one shared nodeid and one of their own: the honest answer
    is 4, the sum is 6.
    """
    # Under the child baseline the report is silent by design, so the number
    # this test reads would not be printed at all; the arrangement puts the run
    # over the CHILD ratchet to make the line appear.
    frozen_baselines(reachable_children=1)
    digests = guard.spawner_digests
    a = sorted(digests({guard.UNKNOWN_TEST: 0, "tests/a.py::t": 0,
                        "tests/only_a.py::t": 0}))
    b = sorted(digests({guard.UNKNOWN_TEST: 0, "tests/a.py::t": 0,
                        "tests/only_b.py::t": 0}))
    assert len(a) == 3 and len(b) == 3, "the arrangement lost a nodeid"

    for rows in (a, b):
        root_conftest.pytest_testnodedown(
            _Node({"overlay_reachable": 5,
                   "overlay_reachable_test_digests": rows}), None)

    reporter = _Reporter()
    session = _Session(reporter, collectonly=False, numprocesses=2)
    root_conftest.pytest_sessionfinish(session, 0)

    assert "4 distinct test(s) spawned at least one such child" in reporter.text(), \
        reporter.text()
    assert "6 distinct" not in reporter.text(), "the counts were summed"


def test_the_controller_counts_its_own_spawns_too(isolated_counters,
                                                  frozen_baselines):
    """The controller's own map holds the execnet bootstrap of each worker.
    Those spawns are inside the child count it enforces, so leaving them out of
    the distinct count would make the two numbers describe different runs."""
    frozen_baselines(reachable_children=10)
    _spawns({guard.UNKNOWN_TEST: 8})
    root_conftest.pytest_testnodedown(
        _Node({"overlay_reachable": 5,
               "overlay_reachable_test_digests":
                   sorted(guard.spawner_digests({"tests/a.py::t": 0}))}), None)

    reporter = _Reporter()
    session = _Session(reporter, collectonly=False, numprocesses=2)
    root_conftest.pytest_sessionfinish(session, 0)

    assert "2 distinct test(s)" in reporter.text(), reporter.text()


# ============================================================
# What judges, and what only reports
# ============================================================

def test_without_the_operators_key_the_new_count_judges_nothing(
        isolated_counters, frozen_baselines):
    """The distinct count is measured and printed; the child ratchet decides.

    Arranged so the two would disagree if the new number were enforced against
    anything: 400 distinct tests, 8 children, a child baseline of 10 and no
    `reachable_tests` key at all.
    """
    frozen_baselines(reachable_children=10)
    _spawns({f"tests/test_x.py::t{i:03d}": 0 for i in range(400)})
    guard._CHILD_SPAWN_COUNT = 8

    reporter = _Reporter()
    session = _Session(reporter, collectonly=False)
    root_conftest.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0, "an unset key judged the run"
    assert reporter.lines == [], "a run inside every budget must stay silent"


def test_the_key_the_operator_sets_is_what_arms_the_new_gate(
        isolated_counters, frozen_baselines):
    """Both directions of the new threshold, one arrangement apart.

    At the baseline the run passes; one distinct test above it the run fails,
    names the number, and says what to do. The child count is held constant and
    well under its own baseline in both, so nothing here can pass or fail for
    the old reason.
    """
    _spawns({f"tests/test_x.py::t{i:03d}": 0 for i in range(40)})
    guard._CHILD_SPAWN_COUNT = 3

    frozen_baselines(reachable_children=10, reachable_tests=40)
    at_budget = _Session(_Reporter(), collectonly=False)
    root_conftest.pytest_sessionfinish(at_budget, 0)
    assert at_budget.exitstatus == 0, "the run failed AT its baseline"

    frozen_baselines(reachable_children=10, reachable_tests=39)
    reporter = _Reporter()
    over = _Session(reporter, collectonly=False)
    root_conftest.pytest_sessionfinish(over, 0)

    assert over.exitstatus == 1, "one distinct test over the baseline passed"
    assert "40 distinct test(s) spawned at least one such child (frozen " \
        "baseline 39)" in reporter.text(), reporter.text()
    assert "More distinct tests spawned a child" in reporter.text()
    assert "HEADING_OS_DATA" in reporter.text(), "the message names no remedy"


def test_a_corrupt_baseline_file_still_fails_the_child_ratchet(
        isolated_counters, frozen_baselines, tmp_path):
    """The strictness already paid for, unchanged.

    An unreadable file makes `reachable_children` read as 0, so any reachable
    child is over budget. The new key returns None from the same wreckage
    rather than 0: two red gates over one broken file would say the same thing
    twice and hide which key was actually wrong.
    """
    path = tmp_path / "overlay-reachability-baseline.json"
    path.write_text("{not json", encoding="utf-8")
    _spawns({"tests/test_x.py::t": 1})

    reporter = _Reporter()
    session = _Session(reporter, collectonly=False)
    root_conftest.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 1
    assert "frozen baseline 0" in reporter.text(), reporter.text()
    assert "no `reachable_tests` key" in reporter.text(), reporter.text()


def test_the_committed_baseline_still_carries_the_untouched_child_ratchet():
    """The brief's first boundary, asserted rather than promised: the file that
    ships still enforces 6100 children, and the distinct measure is not armed
    behind the operator's back."""
    data = json.loads((ROOT / "config" / "overlay-reachability-baseline.json")
                      .read_text(encoding="utf-8"))
    assert data["reachable_children"] == 6100
    assert "reachable_tests" not in data, (
        "a distinct-test threshold appeared in the committed baseline; that "
        "number is the operator's to choose"
    )


# ============================================================
# Reading the number on a green run
# ============================================================

def test_the_forced_report_prints_and_never_changes_a_verdict(
        isolated_counters, frozen_baselines, monkeypatch):
    """`HEADING_OS_OVERLAY_REPORT=1` exists because the count cannot be READ on
    a green run, and a threshold has to be chosen from measurements.

    It adds a line and nothing else: same exit status, and the variable is
    honoured only for the session pytest actually started, so the synthetic
    sessions the tests next door drive stay silent while a measurement is
    running.
    """
    frozen_baselines(reachable_children=10)
    _spawns({"tests/test_x.py::t": 1})
    monkeypatch.setenv("HEADING_OS_OVERLAY_REPORT", "1")

    synthetic = _Session(_Reporter(), collectonly=False)
    root_conftest.pytest_sessionfinish(synthetic, 0)
    assert synthetic.config._reporter.lines == [], (
        "the variable reached a session pytest did not start"
    )

    reporter = _Reporter()
    real = _Session(reporter, collectonly=False)
    monkeypatch.setattr(root_conftest, "_REAL_SESSION_CONFIG", real.config)
    root_conftest.pytest_sessionfinish(real, 0)

    assert real.exitstatus == 0, "a report changed a verdict"
    assert "NOTE: overlay watch" in reporter.text()
    assert "1 distinct test(s) spawned at least one such child" in reporter.text()


# ============================================================
# The digest itself
# ============================================================

def test_the_digest_is_stable_per_nodeid_and_distinct_across_them():
    """The two properties the union rests on. Without the first a test counts
    once per worker; without the second two tests count as one."""
    once = guard.spawner_digests({"tests/a.py::t": [1, "x"]})
    again = guard.spawner_digests({"tests/a.py::t": [9, "y"]})
    assert once == again, "the digest moved with the count or the command"
    many = guard.spawner_digests(
        {f"tests/a.py::t{i}": [1, "x"] for i in range(500)})
    assert len(many) == 500, "digests collided across 500 distinct nodeids"


if __name__ == "__main__":                      # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
