"""The overlay sentinel accused the run of writes it could not attribute.

Its method is a whole-session before/after walk of the operator's LIVE overlay.
Its message said `N file(s) rewrote in the operator's live overlay ... during
the run`. The verb and the phrase "during the run" assign authorship; a diff of
two instants sees that a tree moved and sees nothing whatever about who moved
it. The code knew: the next sentence listed 200 child processes, "any of which
could be the writer".

MEASURED 2026-09-03, from HELM, three independent witnesses:

    systemctl --user  ->  sync-exchange-daemon.service   active (running)
    journal           ->  Sep 03 15:53:08 job-start sync-exchange
                          Sep 03 15:53:29 job-ok    sync-exchange (exit=0)
    mtimes            ->  .sync/logs/crm-autolog-2026-09-03.jsonl  15:53:29.141838523
                          outputs/_sync/emails/inbox-latest.md      15:53:29.142528951

Both accused files land at the very end of that window, 0.0007 s apart, and the
same sweep rewrote eight more the sentinel did not name, including a week of
calendar. A test run cannot write next week's calendar.

It was the SECOND time. CHANGELOG already carries the 2026-09-02 case, where a
collect-only child was blamed for a compaction hook's write. A claim that is
wrong twice by the same mechanism is not bad luck; the method cannot make it.

THE REPAIR, per `.claude/rules/scope-claims.md` obligation 1: resolve the claim
rather than narrow it. The invariant this method CAN establish, with no race
against any daemon, is a different and more useful one -- how many of the run's
OWN children had the operator's live data root reachable. `_CHILD_SPAWN_COUNT`
measures exactly that. The refusal moves there, ratcheted against a frozen
number because zero is not today's tree; the diff stays, as an observation that
never fails anybody, beside an explicit statement of what was not established.

MEASURED the same day: the whole suite reaches the live root 16 times, not the
"200" the old report printed -- 200 was `_CHILD_SPAWN_CAP`, a ceiling shown as
a count.

Deliberately NOT a list of filenames daemons touch. Such a list goes quiet the
first time a new daemon writes something, which is exactly when it should shout.

THE FLAKE, and it was not what it looked like. The end-to-end test below failed
intermittently, apparently under load, so it read as a wall-clock ceiling sized
on an idle machine. It is not. MEASURED 2026-09-04 on an IDLE machine: 5 of 10
runs failed, always at the same assertion, always with the inner run finishing
in under a second. The sentinel compares file SIZES, and the foreign writer
wrote `'x' * (10 + i % 7)` -- seven sizes on a 350 ms cycle. Whenever the inner
session's two snapshots landed in the same phase of that cycle the sizes matched
and the sentinel correctly reported no change to a file rewritten hundreds of
times. Load shifts the phase, which is the whole of its apparent
load-dependence. In one harness, same machine, same minute: cycling sizes 3 of 8
green, strictly growing sizes 8 of 8. So no timeout allowance, general or local,
would have fixed this one, and raising a ceiling would have hidden it further.
The repair removes the timing dependence instead of widening a constant: the
writer's size grows by one byte per write, so any two distinct instants differ;
it writes until it is terminated rather than for a fixed 400 iterations; the
setup waits for the first write rather than sleeping 200 ms; and the test now
asserts the file actually grew across the run, so a silent sentinel over a
motionless file can no longer read as a pass.

Run: python3 -m pytest tests/test_a_sentinel_that_blamed_the_run_for_a_daemon_write.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import overlay_write_guard as guard  # noqa: E402
from tests import conftest as root_conftest  # noqa: E402

BASELINE_FILE = ROOT / "config" / "overlay-reachability-baseline.json"


# ============================================================
# The message states an observation, never an author
# ============================================================

def _snap(**roots):
    return {label: (ROOT, files) for label, files in roots.items()}


def test_a_changed_file_is_reported_without_a_verb_of_authorship():
    before = _snap(overlay={"a.md": 100})
    after = _snap(overlay={"a.md": 20})
    (complaint,) = guard.watch_complaints(before, after)
    assert "changed" in complaint
    assert "between the start and the end of the run" in complaint
    for accusing in ("rewrote", "during the run"):
        assert accusing not in complaint, (
            f"{accusing!r} assigns authorship the diff cannot establish")


def test_the_detection_itself_is_unchanged():
    """The wording moved; what it SEES must not have.

    A truncation in place adds no file and removes none, which is the 2026-08-27
    destruction this watch was built for.
    """
    before = _snap(overlay={"MEMORY.md": 20828})
    after = _snap(overlay={"MEMORY.md": 20})
    (complaint,) = guard.watch_complaints(before, after)
    assert "MEMORY.md" in complaint and "1 file(s)" in complaint

    appeared = guard.watch_complaints(_snap(overlay={}), _snap(overlay={"n.md": 1}))
    vanished = guard.watch_complaints(_snap(overlay={"n.md": 1}), _snap(overlay={}))
    assert appeared and "appeared" in appeared[0]
    assert vanished and "vanished" in vanished[0]


# ============================================================
# The count is a count, not a ceiling
# ============================================================

def test_the_reachable_count_is_separate_from_the_example_cap():
    assert guard._CHILD_SPAWN_COUNT is not guard._CHILD_SPAWN_CAP
    src = (ROOT / "scripts" / "utils" / "overlay_write_guard.py").read_text(
        encoding="utf-8")
    # The count must be incremented BEFORE the cap returns, or the cap is the
    # count again and the report is a ceiling wearing a number's clothes.
    body = src[src.index("def _record_spawn("):]
    body = body[:body.index("\n    # ONLY `Popen` is wrapped")]
    assert body.index("_CHILD_SPAWN_COUNT += 1") < body.index("_CHILD_SPAWN_CAP")


# ============================================================
# The frozen budget
# ============================================================

def test_the_baseline_is_a_committed_measured_number():
    data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    assert isinstance(data["reachable_children"], int)
    assert data["reachable_children"] >= 0
    assert data["measured"] == "2026-09-03"


def test_a_missing_or_corrupt_baseline_fails_strict(tmp_path, monkeypatch):
    """Deleting the file must not disable the guard.

    That is the exact shape this whole repair is about: a control whose absent
    state is indistinguishable from a healthy one.
    """
    monkeypatch.setattr(root_conftest, "_REACHABILITY_BASELINE",
                        tmp_path / "gone.json")
    assert root_conftest._overlay_reachability_baseline() == 0

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(root_conftest, "_REACHABILITY_BASELINE", corrupt)
    assert root_conftest._overlay_reachability_baseline() == 0

    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"something_else": 5}', encoding="utf-8")
    monkeypatch.setattr(root_conftest, "_REACHABILITY_BASELINE", wrong)
    assert root_conftest._overlay_reachability_baseline() == 0


# ============================================================
# Both directions, end to end, against a real concurrent writer
# ============================================================

class _ForeignWriter:
    """Another process touching the watched tree while the run is in flight.

    A THREAD would not do. The point is a writer the run does not control and
    cannot see, and an in-process thread is caught by the guard's own wrappers.

    Two properties, and both were absent in the first version. The sentinel
    compares file SIZES between two instants, so the churn is only VISIBLE if
    the size differs at whichever two instants the inner session happens to
    snapshot at.

      * STRICTLY GROWING, never a cycle. The first writer wrote
        `'x' * (10 + i % 7)`, seven sizes on a 350 ms loop, so the size at the
        end of the run matched the size at the start whenever the two landed in
        the same phase, and the sentinel correctly reported no change to a file
        that had been rewritten hundreds of times. MEASURED 2026-09-04 on an
        IDLE machine: 5 of 10 runs failed, and in the same harness a strictly
        growing size passed 8 of 8. Load shifts the phase, which is why this
        looked like a load-dependent timeout; it is not one, and no timeout
        would have fixed it. One byte more per iteration makes ANY two distinct
        instants distinguishable, so the assertion no longer depends on when
        the snapshots land.
      * BOUNDED BY THE RUN, not by a count. 400 iterations is 20 seconds of
        writing; a run slower than that finished with no writer beside it, which
        is a wall-clock ceiling wearing a loop counter. It now writes until it
        is terminated, with a deadline only as a safety net for a parent that
        dies without running `__exit__`.

    `__enter__` waits for the first write rather than sleeping a fixed 200 ms,
    for the same reason: a condition that is checked cannot be outrun by a
    loaded machine, and a duration that is assumed can.
    """

    # A safety net for an orphan, never a bound on the scenario: the writer is
    # terminated by `__exit__` long before this. Well past the inner run's own
    # 600 s ceiling, so it can never be what ends the writing.
    _ORPHAN_DEADLINE = 900

    def __init__(self, target: Path):
        self.target = target
        self.count = 0
        self._stop = threading.Event()
        self._proc = None

    def __enter__(self):
        script = (
            "import sys, time, pathlib\n"
            "p = pathlib.Path(sys.argv[1])\n"
            "deadline = time.monotonic() + float(sys.argv[2])\n"
            "i = 0\n"
            "while time.monotonic() < deadline:\n"
            "    p.write_text('x' * (10 + i))\n"
            "    i += 1\n"
            "    time.sleep(0.05)\n"
        )
        self._proc = subprocess.Popen(
            [sys.executable, "-c", script, str(self.target),
             str(self._ORPHAN_DEADLINE)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._await_first_write()
        return self

    def _await_first_write(self, ceiling: float = 120.0) -> None:
        """Block until the writer has actually written, or say why it did not.

        The ceiling is a diagnosis budget, not a timing assumption: it is only
        reached when the child never starts at all, and it is generous enough
        that a loaded machine reaches the condition first.
        """
        deadline = time.monotonic() + ceiling
        while time.monotonic() < deadline:
            if self.target.exists():
                return
            if self._proc.poll() is not None:
                raise AssertionError(
                    f"the foreign writer exited {self._proc.returncode} before "
                    f"writing {self.target}")
            time.sleep(0.02)
        raise AssertionError(
            f"the foreign writer never wrote {self.target} within {ceiling}s")

    def __exit__(self, *exc):
        if self._proc is not None:
            self._proc.terminate()
            self._proc.wait(timeout=30)


def _scratch_overlay(tmp_path: Path) -> Path:
    overlay = tmp_path / ".heading-os-data"
    (overlay / "outputs").mkdir(parents=True)
    (overlay / "outputs" / "settled.md").write_text("settled", encoding="utf-8")
    return overlay


@pytest.mark.slow
def test_a_run_survives_a_foreign_process_writing_the_watched_tree(tmp_path):
    """The direction the daemon broke. Reproduced, not argued.

    A separate process rewrites a watched file every 50 ms for the whole run.
    The session must REPORT the churn and must NOT fail: nothing in the run
    wrote it, and the sentinel cannot see who did.
    """
    overlay = _scratch_overlay(tmp_path)
    target = overlay / "outputs" / "written-by-somebody-else.md"
    env = dict(os.environ, HEADING_OS_DATA=str(overlay))
    env.pop("WS_OVERLAY_WATCH_OWNER", None)

    with _ForeignWriter(target):
        opened_at = target.stat().st_size
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", "--color=no",
             "-p", "no:cacheprovider", "-p", "no:xdist",
             "tests/test_data_root.py"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=600, env=env)
        closed_at = target.stat().st_size

    assert target.exists(), "the foreign writer never ran; nothing was measured"
    # The scenario itself, asserted rather than assumed. A writer that stopped
    # before the run started leaves a file that exists and never moved, and the
    # sentinel would be right to report nothing -- a green that measures the
    # absence of the very thing under test.
    assert closed_at > opened_at, (
        f"the watched file did not change while the run was in flight "
        f"({opened_at} -> {closed_at} bytes), so a silent sentinel proves "
        f"nothing here")
    assert "under the operator's" in proc.stdout, (
        f"the churn was not reported at all\n{proc.stdout[-2000:]}")
    assert "NOT established" in proc.stdout
    assert proc.returncode == 0, (
        "a foreign process wrote the watched tree and the run was failed for "
        f"it\n{proc.stdout[-3000:]}")


def test_the_in_process_half_still_refuses_a_real_write(tmp_path, monkeypatch):
    """The other direction, and the one that must never be lost.

    The diff was never the half that could name a culprit. This one is: it
    refuses at the moment of the write, so the traceback carries the test. A
    repair that quietened the diff and left this weakened would have removed
    the protection while looking like an improvement.
    """
    overlay = _scratch_overlay(tmp_path)
    probe = (
        "import os, sys, pathlib\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from scripts.utils import overlay_write_guard as g\n"
        f"g.arm(g.MODE_REFUSE, snapshot=False)\n"
        f"g._OVERLAY_PREFIXES = (str({str(overlay)!r}),)\n"
        f"open(os.path.join({str(overlay)!r}, 'outputs', 'x.md'), 'w')\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode != 0, (
        "an in-process write into a watched overlay was permitted; the half "
        "that CAN name a culprit is gone")
    assert "Refused" in proc.stderr or "Refused" in proc.stdout, proc.stderr[-2000:]
