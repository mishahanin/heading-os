"""Tests for the progress-based supervised runner (scripts/utils/supervise.py).

Verifies the four verdicts deterministically and fast:
  - a process that keeps printing is allowed to finish -> ok;
  - a silent, CPU-idle process is declared hung within the stall window and
    killed (NOT waited on forever);
  - a non-zero exit -> failed;
  - exit 0 with a false postcondition -> postcondition_failed (exit code is
    never trusted blindly).

Run: python3 -m pytest tests/test_supervise.py
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.supervise import run_supervised

PY = sys.executable


def test_long_but_progressing_run_is_ok():
    # Prints for ~2.4s; gaps (0.3s) stay under the 2s stall window -> never hung.
    cmd = [PY, "-c",
           "import time,sys\n"
           "for i in range(8):\n"
           "    print('tick', i); sys.stdout.flush(); time.sleep(0.3)\n"]
    v = run_supervised(cmd, stall_window=2.0, poll=0.4)
    assert v["state"] == "ok", v
    assert v["exit_code"] == 0
    assert v["elapsed_s"] >= 2.0  # it really did run a while, not killed early


def test_silent_idle_process_is_declared_hung():
    # Sleeps silently: no output, no CPU. Must be caught by the stall window
    # (~2s), killed, and reported hung -- not waited on for the full 30s.
    cmd = [PY, "-c", "import time; time.sleep(30)"]
    started = time.monotonic()
    v = run_supervised(cmd, stall_window=2.0, poll=0.4, hard_cap=20)
    waited = time.monotonic() - started
    assert v["state"] == "hung", v
    assert waited < 10, f"watchdog waited too long ({waited:.1f}s)"


def test_nonzero_exit_is_failed():
    cmd = [PY, "-c", "import sys; print('boom'); sys.exit(3)"]
    v = run_supervised(cmd, stall_window=5.0, poll=0.3)
    assert v["state"] == "failed", v
    assert v["exit_code"] == 3
    assert "boom" in v["tail"]


def test_exit_zero_but_false_postcondition_is_not_trusted():
    cmd = [PY, "-c", "print('done ok')"]
    v = run_supervised(cmd, stall_window=5.0, poll=0.3,
                       postcondition=lambda: False)
    assert v["state"] == "postcondition_failed", v
    assert v["exit_code"] == 0
    assert v["postcondition_ok"] is False


def test_exit_zero_with_true_postcondition_is_ok():
    cmd = [PY, "-c", "print('done ok')"]
    v = run_supervised(cmd, stall_window=5.0, poll=0.3,
                       postcondition=lambda: True)
    assert v["state"] == "ok", v
    assert v["postcondition_ok"] is True


def test_status_file_is_written(tmp_path):
    status = tmp_path / "run.status.json"
    cmd = [PY, "-c", "print('hi')"]
    run_supervised(cmd, stall_window=5.0, poll=0.3, status_path=str(status))
    assert status.exists()
    import json
    data = json.loads(status.read_text())
    assert data["state"] == "ok"
    assert "elapsed_s" in data


# ============================================================
# The spawn never happened
# ============================================================
#
# `run_supervised` promises a VERDICT DICT, and `Popen` raises before any
# verdict exists. Both branches below were written on 2026-08-30 against a
# measurement recorded in the module, and neither had a test: MEASURED
# 2026-09-01 by narrowing `except (OSError, IndexError)` to one arm at a time,
# this file stayed green both times while a missing binary and an empty command
# each escaped past every caller written against `verdict["state"]`.

def test_a_missing_binary_is_a_verdict_not_an_exception():
    """A step whose executable is absent is an ordinary failure of that step."""
    verdict = run_supervised(["/nonexistent-binary-for-the-supervise-contract"],
                             stall_window=5.0, poll=0.3)
    assert verdict["state"] == "failed", verdict
    assert verdict["exit_code"] is None
    assert "/nonexistent-binary-for-the-supervise-contract" in verdict["reason"]
    # No log was opened, so none was stranded and none is offered to read.
    assert verdict["log_path"] == ""


def test_an_empty_command_is_a_verdict_not_an_exception():
    """`executable = args[0]` inside subprocess raises IndexError, not OSError.

    The distinct arm matters: an OSError-only clause let this one through.
    """
    verdict = run_supervised([], stall_window=5.0, poll=0.3)
    assert verdict["state"] == "failed", verdict
    assert verdict["exit_code"] is None
    assert "empty command" in verdict["reason"]


def test_a_failed_spawn_still_writes_its_status_file(tmp_path):
    """The observability half. A caller watching the status file must see the
    failure rather than a file that never appears."""
    import json
    status = tmp_path / "spawn.status.json"
    run_supervised(["/nonexistent-binary-for-the-supervise-contract"],
                   stall_window=5.0, poll=0.3, status_path=str(status))
    assert json.loads(status.read_text())["state"] == "failed"


# ============================================================
# The two ceilings, and the postcondition that raises
# ============================================================

def test_the_hard_cap_kills_a_process_that_keeps_printing():
    """`hard_cap` is the only bound on a run that is loud and never finishes.

    The stall window cannot see it: output keeps arriving, so the tree looks
    alive forever. Deleting the hard-cap branch left this file green until
    2026-09-01, because the one test that passed `hard_cap` also stalled and was
    killed by the stall window two seconds in.
    """
    cmd = [PY, "-c",
           "import time,sys\n"
           "while True:\n"
           "    print('tick'); sys.stdout.flush(); time.sleep(0.2)\n"]
    started = time.monotonic()
    v = run_supervised(cmd, stall_window=60.0, poll=0.3, hard_cap=3)
    waited = time.monotonic() - started
    assert v["state"] == "hung", v
    assert "hard cap" in v["reason"], v["reason"]
    assert waited < 20, f"the cap did not fire ({waited:.1f}s)"


def test_a_postcondition_that_raises_is_not_read_as_satisfied():
    """A predicate that blew up settled nothing, so the step is not trusted."""
    def explode():
        raise RuntimeError("the postcondition itself is broken")

    v = run_supervised([PY, "-c", "print('done ok')"], stall_window=5.0, poll=0.3,
                       postcondition=explode)
    assert v["state"] == "postcondition_failed", v
    assert v["postcondition_ok"] is False
    assert "postcondition raised" in v["reason"]
    assert "RuntimeError" in v["reason"]


@pytest.mark.slow
def test_a_child_that_exits_does_not_freeze_the_progress_signal():
    """The 2026-08-26 regression, reproduced.

    `_tree_cpu_ticks` sums the processes alive RIGHT NOW, so the total DROPS
    when a child exits. Comparing against a running maximum (`cpu > last_cpu`)
    turned that drop into a high-water mark, and the survivor had to re-earn the
    departed child's whole lifetime before it counted as progress again. A
    process saturating a core was killed as "deadlocked".

    The fixture is that shape: a subprocess burns CPU and exits, then the parent
    busy-loops in silence. Under the fixed comparison every sample changes and
    the run finishes; under the high-water one the parent must out-burn the
    child's ticks before the stall window expires, and it cannot.
    """
    burner = (
        "import subprocess,sys,time\n"
        "subprocess.run([sys.executable,'-c',"
        "'t=__import__(\"time\").monotonic()\\nwhile __import__(\"time\")"
        ".monotonic()-t<5.0: pass'])\n"
        "t=time.monotonic()\n"
        "while time.monotonic()-t<6.0: pass\n"
    )
    v = run_supervised([PY, "-c", burner], stall_window=2.5, poll=0.4, hard_cap=40)
    assert v["state"] == "ok", (
        f"a busy parent was declared {v['state']}: {v['reason']}"
    )
