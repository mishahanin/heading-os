#!/usr/bin/env python3
"""Supervised subprocess runner with progress-based liveness.

Why this exists
---------------
Some steps MUST verifiably complete before the next step may run — e.g. a
``git push`` whose pre-push hook runs a ~2.5-minute regression gate. A fixed
wall-clock timeout is the wrong tool: too short kills a healthy long step (and
looks exactly like a network/VPN hang — see the 2026-06-20 misdiagnosis), too
long wastes minutes on a genuinely stuck one.

This runner blocks until the step *verifiably* finishes. It declares the step
HUNG only on **inactivity** — no new output AND no CPU progress across the whole
process tree for ``stall_window`` seconds — never on total elapsed time. So a
long-but-working process is allowed to run as long as it keeps making progress,
while a deadlocked / network-blocked one (no output, no CPU) is caught quickly
and killed instead of waited on forever.

On natural exit it can verify a ``postcondition`` callable, so an exit code of 0
is never trusted blindly (a bare ``git push`` can report success while leaving
the branch un-advanced).

Public API
----------
    run_supervised(cmd, *, cwd=None, env=None, stall_window=120, poll=3,
                   postcondition=None, status_path=None, label="",
                   hard_cap=None) -> dict

Returns a verdict dict::

    {
      "state": "ok" | "failed" | "hung" | "postcondition_failed",
      "exit_code": int | None,
      "postcondition_ok": bool | None,
      "elapsed_s": float,
      "stalled_s": float,
      "tail": str,            # last lines of combined stdout/stderr
      "reason": str,          # human-readable verdict reason
      "log_path": str,
    }

Linux/WSL only (reads ``/proc`` for the CPU-tree signal). Dependency-free.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional, Sequence


# ============================================================
# Process-tree CPU sampling (the "silent but working" signal)
# ============================================================

def _proc_stat(pid: int) -> Optional[tuple[int, int]]:
    """Return (ppid, utime+stime_ticks) for ``pid``, or None if gone.

    Parses ``/proc/<pid>/stat`` defensively: the ``comm`` field (field 2) may
    itself contain spaces and parentheses, so we split after the final ')'.
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    rparen = data.rfind(b")")
    if rparen == -1:
        return None
    fields = data[rparen + 2:].split()
    # After dropping pid(1) and comm(2), the remaining fields are 0-indexed:
    #   [0]=state [1]=ppid ... [11]=utime [12]=stime  (man proc, 1-based 14/15)
    try:
        ppid = int(fields[1])
        utime = int(fields[11])
        stime = int(fields[12])
    except (IndexError, ValueError):
        return None
    return ppid, utime + stime


def _tree_cpu_ticks(root_pid: int) -> int:
    """Sum utime+stime (clock ticks) for ``root_pid`` and all descendants.

    One pass over ``/proc`` to snapshot every process, then a walk of the
    pid->children map rooted at ``root_pid``. A subtree that is doing real work
    advances this total even when it prints nothing to stdout.
    """
    procs: dict[int, tuple[int, int]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return 0
    for entry in entries:
        if not entry.isdigit():
            continue
        st = _proc_stat(int(entry))
        if st is not None:
            procs[int(entry)] = st
    children: dict[int, list[int]] = {}
    for pid, (ppid, _ticks) in procs.items():
        children.setdefault(ppid, []).append(pid)
    total = 0
    seen: set[int] = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        if pid in procs:
            total += procs[pid][1]
            stack.extend(children.get(pid, []))
    return total


# ============================================================
# Helpers
# ============================================================

def _tail(path: str, max_lines: int = 25, max_bytes: int = 16384) -> str:
    """Return the last ``max_lines`` lines of ``path`` (bounded read)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            chunk = fh.read()
    except OSError:
        return ""
    text = chunk.decode("utf-8", "replace")
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


def _write_status(status_path: Optional[str], payload: dict) -> None:
    """Atomically write the live status JSON (tmp + os.replace)."""
    if not status_path:
        return
    try:
        p = Path(status_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass  # status is observability, never fatal to the supervised run


def _kill_tree(proc: subprocess.Popen) -> None:
    """SIGTERM then SIGKILL the process group started with start_new_session."""
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


# ============================================================
# The supervised runner
# ============================================================

def run_supervised(
    cmd: Sequence[str],
    *,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    stall_window: float = 120.0,
    poll: float = 3.0,
    postcondition: Optional[Callable[[], bool]] = None,
    status_path: Optional[str] = None,
    label: str = "",
    hard_cap: Optional[float] = None,
) -> dict:
    """Run ``cmd`` under a progress watchdog. See module docstring.

    ``stall_window``: seconds of zero progress (no output growth AND no CPU
        advance) before the run is declared hung and its process group killed.
    ``hard_cap``: optional absolute ceiling in seconds (default None — rely on
        the stall window so a long-but-live step is never wrongly killed).
    """
    start = time.monotonic()
    log_fd, log_path = tempfile.mkstemp(prefix="supervise-", suffix=".log")

    proc = subprocess.Popen(
        list(cmd), cwd=cwd, env=env,
        stdout=log_fd, stderr=subprocess.STDOUT,
        start_new_session=True,  # own process group -> killable as a unit
    )
    os.close(log_fd)

    last_size = 0
    last_cpu = 0
    last_progress = start
    forced_state: Optional[str] = None
    forced_reason = ""

    while True:
        ret = proc.poll()
        now = time.monotonic()

        try:
            size = os.path.getsize(log_path)
        except OSError:
            size = last_size
        cpu = _tree_cpu_ticks(proc.pid)

        if size > last_size or cpu > last_cpu:
            last_progress = now
        last_size = max(size, last_size)
        last_cpu = max(cpu, last_cpu)

        elapsed = now - start
        stalled = now - last_progress

        _write_status(status_path, {
            "label": label,
            "state": "running",
            "pid": proc.pid,
            "elapsed_s": round(elapsed, 1),
            "stalled_s": round(stalled, 1),
            "stall_window_s": stall_window,
            "tail": _tail(log_path),
        })

        if ret is not None:
            break
        if stalled >= stall_window:
            forced_state, forced_reason = "hung", (
                f"no output and no CPU progress for {stalled:.0f}s "
                f"(stall_window={stall_window:.0f}s) — process tree appears "
                f"deadlocked or blocked; killed."
            )
            _kill_tree(proc)
            break
        if hard_cap is not None and elapsed >= hard_cap:
            forced_state, forced_reason = "hung", (
                f"hard cap {hard_cap:.0f}s reached; killed."
            )
            _kill_tree(proc)
            break
        time.sleep(poll)

    exit_code = proc.returncode
    elapsed = time.monotonic() - start
    stalled = time.monotonic() - last_progress
    postcondition_ok: Optional[bool] = None

    if forced_state == "hung":
        state, reason = "hung", forced_reason
    elif exit_code not in (0, None):
        state = "failed"
        reason = f"command exited {exit_code}."
    else:
        if postcondition is not None:
            try:
                postcondition_ok = bool(postcondition())
            except Exception as exc:  # noqa: BLE001 - report, never swallow
                postcondition_ok = False
                reason = f"postcondition raised: {exc!r}"
                state = "postcondition_failed"
                verdict = {
                    "state": state, "exit_code": exit_code,
                    "postcondition_ok": postcondition_ok,
                    "elapsed_s": round(elapsed, 1), "stalled_s": round(stalled, 1),
                    "tail": _tail(log_path), "reason": reason, "log_path": log_path,
                    "label": label,
                }
                _write_status(status_path, {**verdict, "state": state})
                return verdict
            if postcondition_ok:
                state, reason = "ok", "exited 0 and postcondition satisfied."
            else:
                state, reason = "postcondition_failed", (
                    "exited 0 but postcondition is FALSE — the step did not "
                    "actually take effect (e.g. push reported success but the "
                    "branch is not advanced)."
                )
        else:
            state, reason = "ok", "exited 0."

    verdict = {
        "state": state,
        "exit_code": exit_code,
        "postcondition_ok": postcondition_ok,
        "elapsed_s": round(elapsed, 1),
        "stalled_s": round(stalled, 1),
        "tail": _tail(log_path),
        "reason": reason,
        "log_path": log_path,
        "label": label,
    }
    _write_status(status_path, verdict)
    return verdict
