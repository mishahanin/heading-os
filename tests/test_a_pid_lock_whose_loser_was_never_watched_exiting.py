#!/usr/bin/env python3
"""Nothing ever started `Sentinel.start` beside a held lock and watched it exit.

The second-instance guard in `scripts/sentinel.py` is three things, and only two
of them were measured. `tests/security/test_SEC_016_sentinel_pid_lock.py` proves
the KERNEL side: it lifts the flag expression out of the production call and
shows a real second PROCESS is refused those flags on a held file, with a
positive control on the other side of the release. `tests/test_a_pid_file_
emptied_before_the_lock.py` proves the ORDER, by reading source positions, and
the mode, by reading the AST.

The third thing is what the daemon DOES when the kernel refuses it, and no test
drove it. `Sentinel.start` catches `(IOError, OSError)` around the flock and
then closes the handle, logs, and `sys.exit(1)`. Delete the close and the exit,
leaving the log line, and the losing instance walks on into the code below the
handler: `seek(0)`, `truncate()`, and its own pid written over the live daemon's
pid file. That is the SAME outcome as the `open(PID_FILE, "w")` defect the file
above is named for, reached by a different route, and the daemon then goes on to
connect Telegram and Exchange beside the running one.

MEASURED 2026-09-01. Mutation: drop `self._pid_file_handle.close()` and
`sys.exit(1)` from the flock handler in `scripts/sentinel.py`.

    scope                                                 before      after
    the 8 sentinel test files (test_a_pid_file_..., SEC_016,
    test_a_cleanup_path_..., test_a_shutdown_..., integration/
    test_sentinel_hardening, test_sentinel_telegram_cursor,
    test_sentinel_notifier, test_sentinel_calendar_policy)  157 passed  157 passed

Nothing went red. The same mutation with only `sys.exit(1)` removed also
survived all 157.

This file drives the real `Sentinel.start`. Both branches are exercised on a
real file in `tmp_path`, with `PID_FILE` and `RUNTIME_DIR` redirected, no daemon
started, no network reached and no real process signalled. The lock is held by a
second open file description in this same process, which is a genuine conflict:
`flock` locks the open file description, so two `open()` calls conflict even
inside one process. Everything after the PID block is cut off by making
`_heartbeat_loop` raise a marker, which is also the positive control: reaching
the marker means the block ran to the end.
"""
from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import sentinel as sen  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="scripts/sentinel.py takes this lock under `if sys.platform != "
           "'win32'`; the Windows gap is named in the source",
)

LIVE_PID = "4242"


class _PastThePidBlock(Exception):
    """Raised by the stub heartbeat: `start` got all the way through the block."""


@pytest.fixture
def pid_file(tmp_path, monkeypatch):
    path = tmp_path / "sentinel.pid"
    path.write_text(LIVE_PID, encoding="utf-8")
    monkeypatch.setattr(sen, "PID_FILE", path)
    monkeypatch.setattr(sen, "RUNTIME_DIR", tmp_path)
    return path


def _instance():
    """A Sentinel with just enough state for `start` to reach the PID block.

    `object.__new__` on purpose: `__init__` builds config, state and clients.
    Everything below the PID block is cut off by `_heartbeat_loop`, which is
    CALLED before `asyncio.create_task` receives it, so raising there stops the
    coroutine at exactly the line after the block.
    """
    sentinel = object.__new__(sen.Sentinel)
    sentinel.logger = logging.getLogger("test-sentinel-pid-lock")
    sentinel.config = object.__new__(sen.SentinelConfig)
    sentinel.config.check_interval = 900
    sentinel.config.urgency_threshold = 7
    sentinel.dry_run = False
    sentinel._pid_file_handle = None
    sentinel.install_signal_handlers = lambda: None

    def _stop_here():
        raise _PastThePidBlock

    sentinel._heartbeat_loop = _stop_here
    return sentinel


def _run(sentinel):
    try:
        asyncio.run(sentinel.start())
    finally:
        handle = getattr(sentinel, "_pid_file_handle", None)
        if handle is not None and not handle.closed:
            handle.close()


# ============================================================
# The branch nothing drove
# ============================================================

def test_a_losing_instance_exits_instead_of_carrying_on(pid_file):
    """The whole finding. A refused lock must END the start, not log and walk on.

    `_PastThePidBlock` here would mean the daemon reached the heartbeat with
    another instance already holding the lock: two daemons polling the same
    mailbox, double-notifying, racing on state.json and the Telethon session.
    """
    with open(pid_file, "a+") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

        with pytest.raises(SystemExit) as exc:
            _run(_instance())

    assert exc.value.code == 1, (
        f"a losing instance left `start` with code {exc.value.code!r}; the guard "
        "has to exit non-zero so the unit reports the refusal"
    )


def test_a_losing_instance_leaves_the_live_pid_byte_for_byte(pid_file):
    """The consequence the exit prevents, asserted on the file itself.

    Without the exit the code below the handler runs: seek, truncate, write. The
    live daemon's pid is then gone, `--status` prints UNKNOWN, and `--stop`
    deletes an empty file without signalling anything.
    """
    with open(pid_file, "a+") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

        with pytest.raises(SystemExit):
            _run(_instance())

    assert pid_file.read_text(encoding="utf-8") == LIVE_PID


def test_a_losing_instance_says_why_on_the_log(pid_file, caplog):
    """An exit with no line is an operator staring at a unit that will not start."""
    with open(pid_file, "a+") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

        with caplog.at_level(logging.ERROR, logger="test-sentinel-pid-lock"), \
                pytest.raises(SystemExit):
            _run(_instance())

    assert "already running" in caplog.text


def test_a_losing_instance_leaves_no_open_descriptor_on_the_pid_file(pid_file):
    """The handler closes the handle before exiting. A leaked descriptor keeps a
    lock of its own alive for as long as the interpreter does, which in a
    `--test` run beside the daemon is the rest of the session."""
    sentinel = _instance()
    with open(pid_file, "a+") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

        with pytest.raises(SystemExit):
            asyncio.run(sentinel.start())

    handle = sentinel._pid_file_handle
    assert handle is not None and handle.closed, (
        "the losing instance kept the PID file open on its way out"
    )


# ============================================================
# The other direction, without which the four above are green over nothing
# ============================================================

def test_the_winner_gets_through_the_block_and_owns_the_file(pid_file):
    """The positive control. With no lock held, the same `start` must reach the
    marker and leave ONLY its own pid in the file.

    Without this, a `start` that raised SystemExit unconditionally would satisfy
    every test above.
    """
    sentinel = _instance()
    try:
        with pytest.raises(_PastThePidBlock):
            asyncio.run(sentinel.start())

        assert pid_file.read_text(encoding="utf-8") == str(os.getpid()), (
            "the winner did not replace the previous pid; `a+` appends by "
            "default, so the seek and the truncate are load-bearing"
        )
    finally:
        handle = getattr(sentinel, "_pid_file_handle", None)
        if handle is not None and not handle.closed:
            handle.close()


def test_the_winner_holds_the_lock_it_took(pid_file):
    """The winner's handle must still hold the flock after `start` returns
    through the marker. A lock dropped at the end of the block would let the
    next instance in a second later."""
    sentinel = _instance()
    try:
        with pytest.raises(_PastThePidBlock):
            asyncio.run(sentinel.start())

        with open(pid_file, "a+") as second, pytest.raises(OSError):
            fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        handle = getattr(sentinel, "_pid_file_handle", None)
        if handle is not None and not handle.closed:
            handle.close()


def test_a_clean_boot_with_no_pid_file_still_starts(tmp_path, monkeypatch):
    """`a+` has to CREATE the file, not only refuse to truncate it."""
    path = tmp_path / "runtime" / "sentinel.pid"
    monkeypatch.setattr(sen, "PID_FILE", path)
    monkeypatch.setattr(sen, "RUNTIME_DIR", path.parent)

    sentinel = _instance()
    try:
        with pytest.raises(_PastThePidBlock):
            asyncio.run(sentinel.start())

        assert path.read_text(encoding="utf-8") == str(os.getpid())
    finally:
        handle = getattr(sentinel, "_pid_file_handle", None)
        if handle is not None and not handle.closed:
            handle.close()
