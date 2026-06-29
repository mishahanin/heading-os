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
