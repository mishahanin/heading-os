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


# Win32 constants, named so the branch below reads without a lookup.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5


def _windows_pid_is_running(pid: int, kernel32, get_last_error, c_ulong, byref) -> bool:
    """The Windows branch, with its ctypes surface passed IN.

    Extracted so it can be exercised on this workspace's actual platform. WSL2
    is Linux, `os.name` is never "nt" here, and the whole branch was therefore
    unreachable by any test - which is how it kept a defect that the module
    docstring, forty lines above, describes and fixes for POSIX.

    The defect: a NULL handle from `OpenProcess` is not "no such process".
    It fails with ERROR_ACCESS_DENIED when the PID belongs to ANOTHER USER or
    to a more-privileged process, exactly the case `PermissionError` covers on
    POSIX. Returning False there meant a daemon under a service account read as
    dead, `stop` became a no-op, and the pulse script started a SECOND daemon
    beside the first - the same two-schedulers outcome the docstring records.

    ERROR_INVALID_PARAMETER (87) is the genuine "no such PID" and anything else
    is an unknown failure; both stay False. Only access-denied flips, because
    only that one is evidence the process EXISTS.
    """
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = c_ulong(0)
        kernel32.GetExitCodeProcess(handle, byref(code))
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def pid_is_running(pid: int) -> bool:
    """True when a process with this PID exists, whoever owns it."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        # `use_last_error=True` is required: `ctypes.windll` does not populate
        # the ctypes-private error slot, so `get_last_error()` on it returns a
        # stale zero and the access-denied case above would silently read as
        # "unknown failure" - the defect this branch exists to fix.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        return _windows_pid_is_running(
            pid, kernel32, ctypes.get_last_error, ctypes.c_ulong, ctypes.byref)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists; we simply may not signal it.
        return True
    return True
