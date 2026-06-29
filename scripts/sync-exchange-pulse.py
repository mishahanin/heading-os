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

import io
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WORKSPACE = Path(__file__).resolve().parent.parent
RUNTIME_DIR = WORKSPACE / ".sync-exchange"
PID_FILE = RUNTIME_DIR / "daemon.pid"
LOG_FILE = RUNTIME_DIR / "daemon.log"
STARTED_AT_FILE = RUNTIME_DIR / "started_at"



def _daemon_alive() -> tuple[bool, int | None]:
    """Return (alive, pid). Mirrors is_daemon_alive() in sync-exchange-daemon."""
    if not PID_FILE.exists():
        return False, None
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return False, None
    if pid <= 0:
        return False, None
    if sys.platform == "win32":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False, None
        try:
            code = ctypes.c_ulong(0)
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            return code.value == 259, pid
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    else:
        try:
            os.kill(pid, 0)
            return True, pid
        except (ProcessLookupError, PermissionError):
            return False, None


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


def _spawn_detached_daemon() -> int | None:
    """Spawn the sync-exchange daemon in a fully detached process. Returns sentinel.

    Returns -1 on Windows (success — daemon PID lands in .sync-exchange/daemon.pid),
    actual PID on POSIX, None on spawn failure.
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
        return proc.pid
    except Exception:
        return None


def _last_job_ok() -> str | None:
    """Parse daemon.log for the most recent 'job-ok sync-exchange' line.

    Returns a friendly relative time like '12m ago' or None if not found.
    """
    if not LOG_FILE.exists():
        return None
    # The R12 trace-id convention (2026-06-03) inserts an optional "[<hex>] "
    # correlation token between INFO and the message. Match it optionally so
    # both pre- and post-R12 log lines parse.
    pattern = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO (?:\[[0-9a-f]+\] )?job-ok sync-exchange"
    )
    last_ts: datetime | None = None
    try:
        with LOG_FILE.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pattern.match(line)
                if m:
                    try:
                        last_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        pass
    except OSError:
        return None
    if last_ts is None:
        return None
    # last_ts is naive (logs are written in local/local time by default).
    delta = datetime.now() - last_ts
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
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    alive, pid = _daemon_alive()
    if not alive:
        new_pid = _spawn_detached_daemon()
        if new_pid is None:
            print(
                "🔄 Sync-Exchange: ❌ daemon NOT RUNNING and auto-start failed. "
                "Run manually: python scripts/sync-exchange-daemon.py daemon"
            )
            return
        tag = f"pid {new_pid}" if new_pid > 0 else "detached"
        print(f"🔄 Sync-Exchange: daemon was NOT RUNNING — started {tag}")
        return

    last_ok = _last_job_ok()
    if last_ok:
        print(f"🔄 Sync-Exchange: ✅ daemon up pid={pid}, last sync {last_ok}")
    else:
        print(f"🔄 Sync-Exchange: ✅ daemon up pid={pid}, no sync logged yet")


if __name__ == "__main__":
    main()
