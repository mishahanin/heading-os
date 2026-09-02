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

import json
import os
import select
import signal
import stat
import subprocess
import sys
import threading
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
    # The wrapper records what its OWN fd 0 points at before it forks. Reading
    # the forked child's fd 0 instead would prove nothing: POSIX sh assigns
    # /dev/null to the stdin of an asynchronous list, so `sleep 60 &` shows
    # /dev/null whether or not the wrapper inherited the caller's stdin.
    exe.write_text(
        "#!/bin/sh\n"
        "echo 'browser starting'\n"
        f'readlink /proc/$$/fd/0 > "{tmp_path / "stdin.link"}" 2>/dev/null\n'
        "sleep 60 &\n"
        f'echo "$!" > "{pidfile}"\n'
        "exit 0\n"
    )
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe, pidfile


def _patch_launch(monkeypatch, tmp_path: Path, owners=()) -> None:
    """Point launch_comet at the fake browser and a throwaway lock/log."""
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
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: list(owners))
    monkeypatch.setattr(browser, "lock_file", lambda p=tmp_path / "lock.json": p)
    monkeypatch.setattr(browser, "launch_log", lambda p=tmp_path / "launch.log": p)

    calls = {"n": 0}

    def fake_cdp_ready(port, timeout=1.0):
        calls["n"] += 1
        return calls["n"] > 1  # not ready on the entry check, ready on the wait

    monkeypatch.setattr(browser, "_cdp_ready", fake_cdp_ready)


def _launch_with_fd_on_a_pipe(monkeypatch, tmp_path: Path, fd: int) -> int:
    """Run launch_comet with `fd` pointing at a pipe. Returns the read end.

    Parameterised over the descriptor since 2026-09-01. It read fd 1 only, and
    `stderr=log` is a separate argument to the same `Popen`: MEASURED, dropping
    it left all 49 tests across the three files importing this module green,
    while the forked child went on holding the caller's fd 2. The command in this
    module's own docstring is `browser.py launch ... 2>&1 | tail -20`, so the
    descriptor the 12.5-hour hang actually travelled on was the untested one.
    """
    _patch_launch(monkeypatch, tmp_path)

    read_fd, write_fd = os.pipe()
    saved = os.dup(fd)
    try:
        os.dup2(write_fd, fd)
        browser.launch_comet(port=19222, wait_timeout=5.0)
    finally:
        os.dup2(saved, fd)
        os.close(saved)
        os.close(write_fd)
    return read_fd


def _launch_with_stdout_on_a_pipe(monkeypatch, tmp_path: Path) -> int:
    return _launch_with_fd_on_a_pipe(monkeypatch, tmp_path, 1)


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


def _wait_for_log_text(log: Path, needle: str,
                       timeout: float = EOF_TIMEOUT_S) -> bool:
    """Poll `log` for `needle`. A log that is not there YET is a poll, not an error.

    The poll used to call `log.read_text()` unguarded. Today `launch_comet` opens
    the launch log before it spawns the wrapper, so by the time the poll starts
    the file exists and the bare call happens to be safe - it is safe by an
    ordering nothing in this file asserts. Move the open to after the CDP wait
    and the first iteration raises FileNotFoundError, so the test ERRORS instead
    of polling and reports a broken test where the real finding is a launcher
    that logs later than it used to.

    An absent file is the only tolerated failure. A permission error or a
    directory in the log's place is a real fault and still propagates.
    """
    deadline = time.time() + timeout
    while True:
        try:
            if needle in log.read_text(encoding="utf-8", errors="replace"):
                return True
        except FileNotFoundError:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(0.05)


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
    try:
        # The browser writes on its own schedule; launch_comet does not wait
        # for it, so poll rather than read once.
        assert _wait_for_log_text(tmp_path / "launch.log", "browser starting"), (
            "the launch log never received the browser's output, so detaching "
            "the streams threw it away instead of redirecting it"
        )
    finally:
        os.close(read_fd)
        _reap(tmp_path)


def test_the_log_poll_reports_an_absent_log_rather_than_erroring(tmp_path):
    """The tolerance, asked to refuse: no log, no exception, a False verdict."""
    missing = tmp_path / "never-written.log"
    assert not missing.exists(), "fixture broken: the log must be absent"

    assert _wait_for_log_text(missing, "browser starting", timeout=0.15) is False


def test_the_log_poll_still_sees_text_that_arrives_after_it_starts(tmp_path):
    """The anchor. A poll that answered False for everything would pass above.

    The text lands 0.15s in, after the first iteration has already found the
    file absent, so this also pins that the tolerated FileNotFoundError keeps
    the loop going rather than ending it.
    """
    log = tmp_path / "late.log"
    writer = threading.Timer(
        0.15, lambda: log.write_text("browser starting\n", encoding="utf-8"))
    writer.start()
    try:
        assert _wait_for_log_text(log, "browser starting", timeout=3.0) is True
    finally:
        writer.cancel()
        writer.join()


def test_launched_browser_does_not_hold_the_callers_stderr(monkeypatch, tmp_path):
    """`2>&1 | tail -20` is the command that hung, so fd 2 is not optional."""
    read_fd = _launch_with_fd_on_a_pipe(monkeypatch, tmp_path, 2)
    try:
        assert _reaches_eof(read_fd), (
            "the caller's stderr pipe never reached EOF: the launched browser "
            "inherited fd 2, so `2>&1 | tail` hangs until the browser closes "
            "exactly as it did on 2026-07-28"
        )
    finally:
        os.close(read_fd)
        _reap(tmp_path)


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="reads /proc/<pid>/fd")
def test_the_launched_browser_does_not_inherit_the_callers_stdin(monkeypatch,
                                                                 tmp_path):
    """The third stream. "Detach the browser's standard streams" names all three.

    MEASURED 2026-09-01: dropping `stdin=subprocess.DEVNULL` left all 49 tests
    green, because nothing looked at fd 0. Asserted against a NAMED file rather
    than against /dev/null: under pytest the runner's own stdin is usually
    /dev/null already, so a test comparing to that would pass with the argument
    deleted and prove nothing.
    """
    marker = tmp_path / "callers-stdin"
    marker.write_text("the caller's own stdin\n", encoding="utf-8")

    _patch_launch(monkeypatch, tmp_path)
    saved = os.dup(0)
    with marker.open("rb") as fh:
        try:
            os.dup2(fh.fileno(), 0)
            browser.launch_comet(port=19222, wait_timeout=5.0)
        finally:
            os.dup2(saved, 0)
            os.close(saved)

    # The wrapper runs on its own schedule and launch_comet does not wait for
    # it, so poll rather than read once.
    link = tmp_path / "stdin.link"
    deadline = time.time() + EOF_TIMEOUT_S
    while time.time() < deadline and not link.exists():
        time.sleep(0.05)
    assert link.exists(), "the fake browser never reported its stdin"

    target = link.read_text().strip()
    try:
        assert target != str(marker), (
            "the launched browser's child holds the caller's stdin; a terminal "
            "sharing fd 0 with a background browser is the same inheritance "
            "defect as the stdout hang"
        )
    finally:
        _reap(tmp_path)


def test_the_lock_records_the_cdp_owner_not_the_launcher(monkeypatch, tmp_path):
    """On Debian/Ubuntu `Popen`'s PID is the wrapper, which exits at once.

    Recording it is the 2026-07-27 defect `test_browser_stop.py` opens with: the
    tracked PID is dead, its number is free to be recycled, and `stop` then has
    nothing real to signal. MEASURED 2026-09-01: replacing `owners[0] if owners
    else proc.pid` with a bare `proc.pid` left all 49 tests green.
    """
    _patch_launch(monkeypatch, tmp_path, owners=[1894831])

    pid = browser.launch_comet(port=19222, wait_timeout=5.0)
    try:
        assert pid == 1894831, (
            f"launch returned {pid}, the wrapper PID, not the CDP owner"
        )
        state = json.loads((tmp_path / "lock.json").read_text(encoding="utf-8"))
        assert state["pid"] == 1894831
        assert state["port"] == 19222
    finally:
        _reap(tmp_path)


def test_the_launcher_pid_is_still_used_when_nothing_owns_the_port(monkeypatch,
                                                                   tmp_path):
    """The negative case. `owners[0] if owners else proc.pid` has a second half,
    and on Windows `_pids_for_cdp_port` returns [] by design: a launch that
    recorded 0 or None there would leave `stop` with nothing to signal."""
    _patch_launch(monkeypatch, tmp_path, owners=[])

    pid = browser.launch_comet(port=19222, wait_timeout=5.0)
    try:
        assert pid > 0
        state = json.loads((tmp_path / "lock.json").read_text(encoding="utf-8"))
        assert state["pid"] == pid
    finally:
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
