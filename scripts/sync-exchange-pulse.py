#!/usr/bin/env python3
"""Sync-Exchange Pulse — liveness check + auto-spawn for the sync-exchange daemon.

Called by /prime's parallel health-check helper to ensure the daemon is running.
If the daemon is dead, spawns it detached so it survives this shell exiting.

Output policy:
    - Daemon alive: single OK line with pid + last sync timing
    - Daemon dead + auto-spawn ok: line announcing detached start
    - Daemon dead + auto-spawn failed: error line with manual command

Usage:
    python scripts/sync-exchange-pulse.py
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))

from scripts.utils.pid_liveness import pid_is_running  # noqa: E402
from scripts.utils.clone_guard import require_main_clone

RUNTIME_DIR = WORKSPACE / ".sync-exchange"
PID_FILE = RUNTIME_DIR / "daemon.pid"
LOG_FILE = RUNTIME_DIR / "daemon.log"
STARTED_AT_FILE = RUNTIME_DIR / "started_at"



def _daemon_alive() -> tuple[bool, int | None]:
    """Return (alive, pid), using the SHARED liveness helper.

    The body here was a verbatim copy of the daemon's, carrying the same defect:
    PermissionError read as dead, so a daemon owned by another user looked
    stopped and this script spawned a duplicate beside it.
    """
    if not PID_FILE.exists():
        return False, None
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return False, None
    return (pid_is_running(pid), pid if pid > 0 else None)


def _resolve_pythonw() -> Path | None:
    """Locate pythonw.exe (Windows) or python (POSIX) for detached spawn.

    Strategy: take sys.executable, swap python.exe -> pythonw.exe on Windows so
    the spawned daemon has no console window. On POSIX just use sys.executable.
    """
    exe = Path(sys.executable)
    if sys.platform == "win32":
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.exists():
            return pythonw
        # Fallback to python.exe; CREATE_NO_WINDOW + cmd start /B already suppresses console.
        return exe if exe.exists() else None
    return exe if exe.exists() else None


# How long to give a freshly spawned daemon to fall over before calling it
# started. Only paid on the path where the daemon was down, so a healthy pulse
# never waits.
STARTUP_SETTLE_SECONDS = 2.0


def _spawn_detached_daemon() -> int | None:
    """Spawn the sync-exchange daemon in a fully detached process. Returns sentinel.

    Returns -1 on Windows (success — daemon PID lands in .sync-exchange/daemon.pid),
    actual PID on POSIX, None on spawn failure OR on a POSIX child that exited
    within `STARTUP_SETTLE_SECONDS`.

    The survival check covers POSIX only. On Windows the Popen is the `cmd`
    shell, not the daemon, so its exit says nothing about the daemon; that lane
    still reports on the spawn alone.
    """
    py = _resolve_pythonw()
    daemon = WORKSPACE / "scripts" / "sync-exchange-daemon.py"
    if py is None or not daemon.exists():
        return None
    try:
        if sys.platform == "win32":
            # Use `cmd /c start /B ""` so the daemon survives parent shell exit.
            # Same subprocess pattern as the other pulse probes — proven reliable under Git Bash.
            cmd = [
                "cmd.exe", "/c", "start", "/B", "",
                str(py), str(daemon), "daemon",
            ]
            subprocess.Popen(
                cmd,
                cwd=str(WORKSPACE),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )
            return -1
        proc = subprocess.Popen(
            [str(py), str(daemon), "daemon"],
            cwd=str(WORKSPACE),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        # Popen hands back a pid the instant the fork lands, which says nothing
        # about survival. A daemon that dies on startup (apscheduler missing
        # from the interpreter it was spawned with, an import error, or its own
        # "another instance is starting" exit 1 when two health checks race)
        # still yielded a pid, so the pulse printed "started pid N" and returned
        # 0 forever. Wait a moment: if `wait` RETURNS, the child is already
        # dead, and only a timeout means it is still running.
        try:
            proc.wait(timeout=STARTUP_SETTLE_SECONDS)
        except subprocess.TimeoutExpired:
            return proc.pid
        return None
    except Exception:
        return None


def _last_job_ok() -> str | None:
    """Parse daemon.log for the most recent 'job-ok sync-exchange' line.

    Returns a friendly relative time like '12m ago' or None if not found.

    The rotated backups are read too, newest first. The daemon logs through
    `RotatingFileHandler(maxBytes=1_000_000, backupCount=3)`, which moves all
    history into `daemon.log.1`-`.3` and starts a fresh EMPTY `daemon.log`.
    Reading only the active file therefore reported "no sync logged yet" for a
    daemon that had synced minutes earlier, for up to the whole two-hour gap
    until the next job: MEASURED 2026-08-29 with a 7-minute-old job-ok line in
    `daemon.log.1` and an empty `daemon.log`, where this returned None. The
    liveness signal silently reset on every rotation.
    """
    # The R12 trace-id convention (2026-06-03) inserts an optional "[<hex>] "
    # correlation token between INFO and the message. Match it optionally so
    # both pre- and post-R12 log lines parse.
    pattern = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO (?:\[[0-9a-f]+\] )?job-ok sync-exchange"
    )
    last_ts: datetime | None = None
    # Newest first, and stop at the first file that yields a match: a rotated
    # backup can only hold lines OLDER than the file that displaced it.
    candidates = [LOG_FILE] + [
        LOG_FILE.with_name(f"{LOG_FILE.name}.{n}") for n in (1, 2, 3)
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = pattern.match(line)
                    if m:
                        # A line the regex accepted but strptime rejects is a
                        # truncated write, which a rotating log produces at its
                        # tail. Skipping it keeps the previous match; raising
                        # here would turn a torn byte into "no sync logged yet",
                        # which is the exact wrong answer this function was just
                        # fixed to stop giving.
                        with contextlib.suppress(ValueError):
                            last_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.now().astimezone().tzinfo)
        except OSError:
            continue
        if last_ts is not None:
            break
    if last_ts is None:
        return None
    # `last_ts` is AWARE: the `.replace(tzinfo=...)` above attaches the local
    # zone, because the daemon writes `%(asctime)s` in local time. The comment
    # here read "last_ts is naive" until 2026-08-29 and described the state that
    # line deliberately eliminates. A reader who trusted it and dropped the
    # `.replace` got `TypeError: can't subtract offset-naive and offset-aware
    # datetimes`, verified on that date.
    delta = datetime.now().astimezone() - last_ts
    mins = int(delta.total_seconds() / 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins}m ago"
    h, m = divmod(mins, 60)
    if h < 24:
        return f"{h}h{m}m ago"
    d = h // 24
    return f"{d}d ago"


def main():
    # Force UTF-8 on stdout so emoji + non-ASCII log lines don't crash on
    # Windows. Done here (not at import time) so importing this module is a
    # pure, side-effect-free operation — tests load it by path.
    require_main_clone(__file__)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    alive, pid = _daemon_alive()
    if not alive:
        new_pid = _spawn_detached_daemon()
        if new_pid is None:
            print(
                "🔄 Sync-Exchange: ❌ daemon NOT RUNNING and auto-start failed "
                "(it did not spawn, or it exited straight after starting). "
                "Run manually: python scripts/sync-exchange-daemon.py daemon"
            )
            # Non-zero. This is the liveness check /prime's health helper calls,
            # and exiting 0 here reported a healthy sync pipeline while the
            # daemon was down -- indefinitely, because nothing else looks.
            return 1
        tag = f"pid {new_pid}" if new_pid > 0 else "detached"
        print(f"🔄 Sync-Exchange: daemon was NOT RUNNING — started {tag}")
        return 0

    last_ok = _last_job_ok()
    if last_ok:
        print(f"🔄 Sync-Exchange: ✅ daemon up pid={pid}, last sync {last_ok}")
    else:
        print(f"🔄 Sync-Exchange: ✅ daemon up pid={pid}, no sync logged yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
