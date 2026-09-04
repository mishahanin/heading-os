#!/usr/bin/env python3
"""`run_supervised` promises a verdict dict and raised instead.

The module docstring's public API says the function returns a verdict dict with
a `"state"`. The `Popen` call was unguarded, so a command whose binary does not
exist raised `FileNotFoundError` straight past every caller written against
`verdict["state"]`, `os.close(log_fd)` never ran (a leaked descriptor), and the
mkstemp'd log file was stranded in the temp directory with no `log_path` ever
returned to find it by.

Measured 2026-08-30: `run_supervised(["/nonexistent-binary-jamesbond"])` raised
FileNotFoundError; `run_supervised([])` raised IndexError from
`executable = args[0]` inside subprocess, which an OSError-only clause would
also have missed. `scripts/utils/schedule._run` carries the same pair for the
same reason.
"""
import gc
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.supervise import run_supervised  # noqa: E402


def _open_fd_count():
    return len(os.listdir("/proc/self/fd"))


# Every call below passes `log_dir=str(tmp_path)`. `run_supervised` hands
# `verdict["log_path"]` back for a human to open AFTER the run, so it does not
# remove the log and must not -- in production that is the point. Under pytest
# nobody opens it and nothing removed it: one `/tmp/supervise-*.log` survived
# every test here that starts a child. MEASURED 2026-09-04, the day /tmp on
# this machine was counted at 50,225 top-level entries.

def test_a_missing_binary_returns_a_failed_verdict_not_an_exception():
    verdict = run_supervised(["/nonexistent-binary-jamesbond"], label="probe")
    assert verdict["state"] == "failed"
    assert verdict["exit_code"] is None
    assert verdict["label"] == "probe"
    assert "/nonexistent-binary-jamesbond" in verdict["reason"]


def test_an_empty_command_returns_a_failed_verdict_not_an_indexerror():
    verdict = run_supervised([], label="empty")
    assert verdict["state"] == "failed"
    assert "empty command" in verdict["reason"]


def test_the_verdict_carries_every_documented_key():
    """A caller reading any documented field must not hit a KeyError."""
    verdict = run_supervised(["/nonexistent-binary-jamesbond"])
    for key in ("state", "exit_code", "postcondition_ok", "elapsed_s",
                "stalled_s", "tail", "reason", "log_path"):
        assert key in verdict, f"verdict is missing {key!r}: {verdict!r}"


def test_a_failed_spawn_leaks_neither_a_descriptor_nor_a_temp_file(
        tmp_path, monkeypatch):
    """(b) and (c) of the defect: the fd and the stranded log.

    The temp directory is REDIRECTED to `tmp_path` rather than measured where
    the strandings originally were. `run_supervised` calls `tempfile.mkstemp`
    with no `dir=`, so it lands wherever `tempfile.gettempdir()` points, and
    that resolves `tempfile.tempdir` first.

    Reading the real platform temp directory made this a test of what else was
    running. MEASURED 2026-08-30: it failed with
    `a failed spawn stranded a log file: [PosixPath('/tmp/supervise-44gms5bp.log')]`
    while a concurrent pytest run was calling `run_supervised` in another
    process. Nothing in this test produced that file and nothing in the code
    under test was wrong. Under `-n auto` the same collision is reachable
    between two xdist workers of one run, so this was not merely an
    agents-in-the-tree artefact.

    A before/after diff over a directory other writers can reach is a
    coin toss, not a measurement. The redirect gives this test a directory
    only it writes to, which is what makes "nothing was stranded" a claim
    about `run_supervised` rather than about the machine.
    """
    logdir = tmp_path / "supervise-tmp"
    logdir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(logdir))
    assert Path(tempfile.gettempdir()) == logdir, (
        "the redirect did not take, so the assertions below would read a "
        "directory other processes can write to")

    gc.collect()
    before_fds = _open_fd_count()
    before_logs = set(logdir.glob("supervise-*.log"))
    assert before_logs == set(), "the private temp directory started dirty"

    for _ in range(5):
        run_supervised(["/nonexistent-binary-jamesbond"])

    gc.collect()
    assert _open_fd_count() == before_fds, "a failed spawn leaked a descriptor"
    after_logs = set(logdir.glob("supervise-*.log"))
    assert after_logs - before_logs == set(), (
        f"a failed spawn stranded a log file: {sorted(after_logs - before_logs)}")


def test_the_log_the_supervisor_makes_lands_where_this_test_can_see_it(
        tmp_path, monkeypatch):
    """The negative control for the redirect above, and it is load-bearing.

    If `run_supervised` ever stopped writing into `tempfile.gettempdir()` --
    an explicit `dir=`, a hardcoded `/tmp`, an env var read at import -- the
    previous test would glob an empty private directory and pass over a
    stranding it could no longer see. A guard is only a guard while it is
    still pointed at the thing.

    A SUCCESSFUL run keeps its log and returns the path, so this proves the
    redirect reaches the real mkstemp call.

    THE ONE CALL IN THIS FILE THAT DELIBERATELY OMITS `log_dir`, added
    2026-09-04. The parameter exists so callers who do not want the log to
    survive can say so, and every other real-child call here passes a
    `tmp_path`. This one must not: what it pins is the DEFAULT, that with no
    `log_dir` the supervisor still writes into `tempfile.gettempdir()`. Passing
    one here would make the assertion below trivially true against `tmp_path`
    and blind the stranding check in the test above, which is the exact failure
    this test's own docstring describes. It leaks nothing, because `tempdir` is
    redirected under `tmp_path` two lines down.
    """
    logdir = tmp_path / "supervise-tmp"
    logdir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(logdir))

    verdict = run_supervised(["/bin/true"], label="control")

    assert verdict["state"] != "failed", f"the control command did not run: {verdict!r}"
    assert verdict["log_path"], "a successful run returned no log path"
    assert Path(verdict["log_path"]).parent == logdir, (
        f"the supervisor logged to {verdict['log_path']!r}, outside the "
        f"redirected temp directory, so the stranding check above is blind")


def test_the_status_file_records_the_failed_spawn(tmp_path):
    """A supervised step that never started must still say so on the status surface."""
    import json

    status = tmp_path / "status.json"
    run_supervised(["/nonexistent-binary-jamesbond"], status_path=str(status),
                   label="probe")
    assert status.exists()
    assert json.loads(status.read_text(encoding="utf-8"))["state"] == "failed"


def test_a_command_that_does_start_is_still_supervised_normally(tmp_path):
    """The control: the guard must not swallow a real run."""
    verdict = run_supervised([sys.executable, "-c", "print('ok')"],
                             stall_window=10.0, poll=0.3,
                             log_dir=str(tmp_path))
    assert verdict["state"] == "ok"
    assert verdict["exit_code"] == 0
    assert verdict["log_path"], "a successful run must still name its log"


def test_a_command_that_exits_non_zero_is_still_failed_with_an_exit_code(tmp_path):
    """The negative case: not every failure became a spawn failure."""
    verdict = run_supervised([sys.executable, "-c", "raise SystemExit(3)"],
                             stall_window=10.0, poll=0.3,
                             log_dir=str(tmp_path))
    assert verdict["state"] == "failed"
    assert verdict["exit_code"] == 3
