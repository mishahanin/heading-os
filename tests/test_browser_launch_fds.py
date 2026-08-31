#!/usr/bin/env python3
"""The launched browser must not inherit the caller's standard streams.

Regression test for a defect measured on 2026-07-28. A session ran

    timeout 90 python scripts/browser.py launch --url ... 2>&1 | tail -20

and the shell was still alive 12.5 hours later. `timeout 90` had killed python
on schedule, but the pipeline could not finish: `tail` waits for EOF on its
stdin, and the write end of that pipe was still held by two `cat` helpers
forked by `/usr/bin/brave-browser` (a shell wrapper, not a binary), which had
inherited it from the launcher. The pipe only closed when the browser closed.

The test reproduces the shape rather than the browser: a fake executable that
forks a child outliving the wrapper, exactly like the Debian wrapper does.
With the browser's streams detached, the caller's pipe reaches EOF immediately.
"""
from __future__ import annotations

import os
import select
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import browser  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX fd-inheritance semantics"
)

EOF_TIMEOUT_S = 5.0


def _fake_browser(tmp_path: Path) -> tuple[Path, Path]:
    """A wrapper that forks a long-lived child and exits, like brave-browser."""
    pidfile = tmp_path / "child.pid"
    exe = tmp_path / "fake-browser"
    exe.write_text(
        "#!/bin/sh\n"
        "echo 'browser starting'\n"
        "sleep 60 &\n"
        f'echo "$!" > "{pidfile}"\n'
        "exit 0\n"
    )
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe, pidfile


def _launch_with_stdout_on_a_pipe(monkeypatch, tmp_path: Path) -> int:
    """Run launch_comet with fd 1 pointing at a pipe. Returns the read end."""
    exe, _ = _fake_browser(tmp_path)

    monkeypatch.setattr(
        browser,
        "_browser_paths",
        lambda b=browser.DEFAULT_BROWSER: {
            "exe": exe,
            "user_data": tmp_path / "user-data",
            "process_name": "fake-browser",
        },
    )
    monkeypatch.setattr(browser, "is_running", lambda b=browser.DEFAULT_BROWSER: False)
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [])
    monkeypatch.setattr(browser, "lock_file", lambda p=tmp_path / "lock.json": p)
    monkeypatch.setattr(browser, "launch_log", lambda p=tmp_path / "launch.log": p)

    calls = {"n": 0}

    def fake_cdp_ready(port, timeout=1.0):
        calls["n"] += 1
        return calls["n"] > 1  # not ready on the entry check, ready on the wait

    monkeypatch.setattr(browser, "_cdp_ready", fake_cdp_ready)

    read_fd, write_fd = os.pipe()
    saved_stdout = os.dup(1)
    try:
        os.dup2(write_fd, 1)
        browser.launch_comet(port=19222, wait_timeout=5.0)
    finally:
        os.dup2(saved_stdout, 1)
        os.close(saved_stdout)
        os.close(write_fd)
    return read_fd


def _reaches_eof(read_fd: int, timeout: float = EOF_TIMEOUT_S) -> bool:
    """Drain the pipe. True if it closes, False if a writer still holds it."""
    deadline_reads = 0
    while True:
        ready, _, _ = select.select([read_fd], [], [], timeout)
        if not ready:
            return False  # nobody wrote, nobody closed: a writer is still alive
        if os.read(read_fd, 4096) == b"":
            return True
        deadline_reads += 1
        if deadline_reads > 100:
            return False


def _reap(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    if not pidfile.exists():
        return
    try:
        os.kill(int(pidfile.read_text().strip()), signal.SIGKILL)
    except ProcessLookupError:
        return  # already exited: the normal end state
    except (ValueError, PermissionError) as exc:
        print(f"teardown: could not reap the fake browser child: {exc}", file=sys.stderr)


def test_launched_browser_does_not_hold_the_callers_stdout(monkeypatch, tmp_path):
    """The bug: the browser's forked child kept the caller's pipe open."""
    read_fd = _launch_with_stdout_on_a_pipe(monkeypatch, tmp_path)
    try:
        assert _reaches_eof(read_fd), (
            "the caller's stdout pipe never reached EOF: the launched browser "
            "inherited it, so a `| tail` pipeline would hang until the browser "
            "closes (the 2026-07-28 12.5-hour hang)"
        )
    finally:
        os.close(read_fd)
        _reap(tmp_path)


def test_browser_output_goes_to_the_launch_log(monkeypatch, tmp_path):
    """Detaching the streams must not throw the output away."""
    read_fd = _launch_with_stdout_on_a_pipe(monkeypatch, tmp_path)
    log = tmp_path / "launch.log"
    try:
        # The browser writes on its own schedule; launch_comet does not wait
        # for it, so poll rather than read once.
        deadline = time.time() + EOF_TIMEOUT_S
        while time.time() < deadline and "browser starting" not in log.read_text():
            time.sleep(0.05)
        assert "browser starting" in log.read_text()
    finally:
        os.close(read_fd)
        _reap(tmp_path)


def test_the_fake_browser_really_does_outlive_its_wrapper(tmp_path):
    """Guard the test's own premise: without the fix this shape DOES hang.

    If the fake wrapper stopped forking a surviving child, the two tests above
    would pass against a reverted fix and prove nothing.
    """
    exe, pidfile = _fake_browser(tmp_path)
    read_fd, write_fd = os.pipe()
    try:
        subprocess.run([str(exe)], stdout=write_fd, stderr=subprocess.DEVNULL, check=True)
        os.close(write_fd)
        write_fd = -1
        assert not _reaches_eof(read_fd, timeout=2.0), (
            "the fake wrapper's child did not survive, so these tests would "
            "pass even with the fix reverted"
        )
    finally:
        if write_fd != -1:
            os.close(write_fd)
        os.close(read_fd)
        _reap(tmp_path)
