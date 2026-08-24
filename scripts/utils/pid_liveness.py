#!/usr/bin/env python3
"""One cross-platform "is this PID alive?", used by every caller that asks.

Two copies of this existed -- `_pid_is_running` in `sync-exchange-daemon.py` and
`_daemon_alive` in `sync-exchange-pulse.py`, the second annotated "Mirrors
is_daemon_alive()". Both made the same mistake and both would have needed the
same fix twice.

The mistake: on POSIX, `os.kill(pid, 0)` raising `PermissionError` means the
process EXISTS and belongs to another user. Both copies returned False for it,
so a daemon started under sudo or a service account read as dead -- `stop`
became a no-op, and the pulse script, which spawns when it sees "dead", started
a SECOND daemon beside the first. Two APScheduler instances then ran the same
two-hour Exchange sync, because `max_instances=1` only dedupes within one
scheduler.
"""

from __future__ import annotations

import os


def pid_is_running(pid: int) -> bool:
    """True when a process with this PID exists, whoever owns it."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong(0)
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists; we simply may not signal it.
        return True
    return True
