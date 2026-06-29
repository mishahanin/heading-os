#!/usr/bin/env python3
"""Fireside Pulse — diff-based status check for /loop.

Reads current bot state, compares against a checkpoint file, and prints ONLY
the changes since the last run. Designed to be invoked every ~10 min by
`/loop` so the operator only sees output when something meaningful has happened.

Usage:
    python scripts/fireside-pulse.py

Output policy:
    - First run (no checkpoint): initialise silently, print baseline summary
    - No changes: single line "ok: <last_poll_age>, started <N>/<tribe>"
    - Changes: bulleted list of new events
    - Polling stale (>15 min): WARN
    - Non-transient error burst: WARN
"""
import json
import os
import subprocess
import sys
import io
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import (  # noqa: E402
    get_default_tz, get_default_tz_name, get_outputs_dir, get_datastore_dir,
    resolve_config_with_example,
)

WORKSPACE = Path(__file__).resolve().parent.parent
STATE_DIR = get_datastore_dir() / "operations" / "tribe" / "fireside-state"
CHECKPOINT = get_outputs_dir() / "operations" / "tribe-fireside" / "pulse-checkpoint.json"

# The managed service-host VM's fireside unit name is private instance topology
# (engine ships scripts/service-host.example.json with a generic default).
_SVC = json.loads(
    resolve_config_with_example(
        "service-host.json", Path(__file__).resolve().parent / "service-host.example.json"
    ).read_text(encoding="utf-8")
)
_FIRESIDE_UNIT = _SVC.get("fireside_unit", "fireside.service")

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Probe script executed on the managed service-host VM when .fireside/remote-host
# is set. Stdlib only - no external dependencies on the remote end. The unit name
# is substituted from the private service-host config (placeholder below).
_PROBE_TEMPLATE = r'''
import json, os, subprocess
from pathlib import Path
# Resolve the remote workspace root without embedding a username. Honour an
# explicit override the SSH session may export, otherwise fall back to the
# remote user's home (the service-host workspace lives at that user's $HOME).
_root = os.environ.get("WORKSPACE_ROOT") or os.environ.get("FIRESIDE_WORKSPACE_ROOT")
_root = Path(_root) if _root else Path.home()
STATE = _root / "datastore" / "operations" / "tribe" / "fireside-state"

def _load_jsonl(p):
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out

try:
    active = subprocess.run(
        ["systemctl", "is-active", "__FIRESIDE_UNIT__"],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip() or "unknown"
except Exception:
    active = "unknown"

sessions = _load_jsonl(STATE / "sessions.jsonl")
dm_log   = _load_jsonl(STATE / "dm-log.jsonl")

started = set()
for e in sessions:
    if e.get("event_type") in ("start_received", "swap_requested") and e.get("user_id"):
        started.add(e["user_id"])
for e in dm_log:
    if e.get("delivered") is True and e.get("user_id"):
        started.add(e["user_id"])

# Liveness = most recent poll-tick OR heartbeat-tick. In webhook mode the
# daemon skips the poll job by design, so poll-tick never updates; heartbeat-tick
# fires every minute in both modes. Mirror the daemon's own liveness rule.
last_tick = None
for e in reversed(dm_log):
    if e.get("dm_type") in ("poll-tick", "heartbeat-tick"):
        last_tick = e.get("ts")
        break

try:
    roster = json.load((STATE / "tribe-roster.json").open(encoding="utf-8"))
    tribe_size = len(roster)
except Exception:
    tribe_size = 0

print(json.dumps({
    "active": active,
    "started": len(started),
    "tribe_size": tribe_size,
    "last_tick_ts": last_tick,
}))
'''.strip()

# Substitute the instance's real unit name (from private config) into the probe.
_PROBE = _PROBE_TEMPLATE.replace("__FIRESIDE_UNIT__", _FIRESIDE_UNIT)


def _query_service_host(host: str, ssh_timeout: int = 5, run_timeout: int = 12) -> dict | None:
    """Run a read-only status probe on the managed service-host VM via SSH.

    Returns parsed JSON on success; None on any failure (network, auth, parse).
    """
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o", "BatchMode=yes",
                "-o", f"ConnectTimeout={ssh_timeout}",
                host,
                "python3 -",
            ],
            input=_PROBE,
            capture_output=True,
            text=True,
            timeout=run_timeout,
            encoding="utf-8",
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        return None


def _print_remote_status(host: str) -> None:
    """Print a /prime-friendly one-line status for the service-host daemon."""
    data = _query_service_host(host)
    if not data:
        print(f"🔥 Fireside (service-host {host}): UNREACHABLE - SSH probe failed")
        return
    active = data.get("active", "unknown")
    started = data.get("started", 0)
    tribe = data.get("tribe_size", 0)
    last_tick = data.get("last_tick_ts")
    tick_age = poll_age_minutes(last_tick)
    tag = "✅" if active == "active" else "❌"
    tick_str = f"last tick {tick_age} min ago" if tick_age is not None else "no tick recorded"
    print(f"🔥 Fireside (service-host): {tag} {active}, started {started}/{tribe}, {tick_str}")
    if tick_age is not None and tick_age > 15:
        print(f"  - WARN: daemon has not ticked (poll/heartbeat) in {tick_age} min")


def _daemon_alive() -> tuple[bool, int | None]:
    """Return (alive, pid). Mirror of is_daemon_alive() in fireside-bot-daemon."""
    pid_file = WORKSPACE / ".fireside" / "daemon.pid"
    if not pid_file.exists():
        return False, None
    try:
        pid = int(pid_file.read_text().strip())
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


def _spawn_detached_daemon() -> int | None:
    """Spawn the fireside daemon in a fully detached process. Returns PID or None.

    Windows is hostile to "spawn and forget" from a Git-Bash session:
      * subprocess.Popen with DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP
        does NOT detach when parent is mintty/bash — the child stays in the
        parent's job and dies when bash exits.
      * CREATE_BREAKAWAY_FROM_JOB silently fails if the parent's job lacks
        JOB_OBJECT_LIMIT_BREAKAWAY_OK (it does, under Git Bash).
      * Reliable approach: spawn `cmd /c start /B "" pythonw.exe ...`. The
      `start` command launches the target as a CHILD OF CMD, then CMD exits
      immediately. The grandchild is reparented to System and survives parent
      exit. pythonw.exe (not python.exe) avoids any console allocation.

    POSIX: standard `start_new_session=True` works fine.
    """
    # venv layout: 'Scripts/' on Windows, 'bin/' on POSIX (per PEP 405)
    venv_subdir = "Scripts" if sys.platform == "win32" else "bin"
    venv_dir = WORKSPACE / "scripts" / ".venv-fireside" / venv_subdir
    if sys.platform == "win32":
        venv_py = venv_dir / "pythonw.exe"
        if not venv_py.exists():
            venv_py = venv_dir / "python.exe"  # fallback
    else:
        # POSIX: venv interpreter is just 'python' (no .exe), no pythonw equivalent
        venv_py = venv_dir / "python"
        if not venv_py.exists():
            venv_py = venv_dir / "python3"  # some venvs ship python3 symlink only
    daemon = WORKSPACE / "scripts" / "fireside-bot-daemon.py"
    if not venv_py.exists():
        return None
    try:
        if sys.platform == "win32":
            cmd = [
                "cmd.exe", "/c", "start", "/B", "",
                str(venv_py), str(daemon), "daemon",
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
            # We can't return the daemon's actual PID here — `start` is a
            # cmd.exe builtin and our subprocess.Popen returns the cmd.exe PID
            # which exits within milliseconds. The daemon writes its real PID
            # to .fireside/daemon.pid; callers should read that.
            return -1  # success sentinel; caller already prints generic message
        proc = subprocess.Popen(
            [str(venv_py), str(daemon), "daemon"],
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


def load_jsonl(path: Path):
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def derive_state():
    """Compute the current state snapshot from the live files."""
    sessions = load_jsonl(STATE_DIR / "sessions.jsonl")
    dm_log = load_jsonl(STATE_DIR / "dm-log.jsonl")

    # Started: union of start_received uids + delivered DMs
    started_uids = set()
    swap_uids = []   # list of (ts, username) for change detection
    tribe_joins = []
    sessions_logged = []
    no_shows = []
    for e in sessions:
        et = e.get("event_type")
        uid = e.get("user_id")
        if et == "start_received" and uid:
            started_uids.add(uid)
        elif et == "swap_requested" and uid:
            started_uids.add(uid)
            swap_uids.append((e.get("ts", "?"), e.get("username", "?")))
        elif et == "tribe_join" and uid:
            tribe_joins.append((e.get("ts", "?"), e.get("username", "?")))
        elif et == "session_logged":
            sessions_logged.append(e)
        elif et == "no_show":
            no_shows.append(e)
    for e in dm_log:
        if e.get("delivered") is True and e.get("user_id"):
            started_uids.add(e["user_id"])

    last_poll_ts = None
    for e in reversed(dm_log):
        if e.get("dm_type") == "poll-tick":
            last_poll_ts = e.get("ts")
            break

    # Non-transient errors (exclude ConnectionResetError, NameResolutionError, WinError 10013 noise)
    errors_path = STATE_DIR / "errors.log"
    non_transient = 0
    if errors_path.exists():
        with errors_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if "ERROR" not in line:
                    continue
                low = line.lower()
                if "connectionreset" in low or "nameresolution" in low or "winerror 10013" in low:
                    continue
                if "transport failure" in low:
                    continue
                non_transient += 1

    return {
        "ts": datetime.now(get_default_tz()).isoformat(timespec="seconds"),
        "started_uids": sorted(started_uids),
        "swap_events": [list(s) for s in swap_uids],
        "tribe_joins": [list(t) for t in tribe_joins],
        "session_count": len(sessions_logged),
        "no_show_count": len(no_shows),
        "non_transient_errors": non_transient,
        "last_poll_ts": last_poll_ts,
    }


def load_checkpoint():
    if CHECKPOINT.exists():
        with CHECKPOINT.open(encoding="utf-8") as f:
            return json.load(f)
    return None


def save_checkpoint(state):
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINT.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    tmp.replace(CHECKPOINT)


def load_roster_names():
    """Return dict of {user_id: name} for friendly output."""
    path = STATE_DIR / "tribe-roster.json"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        roster = json.load(f)
    return {m.get("telegram_user_id"): m.get("name", k) for k, m in roster.items()}


def poll_age_minutes(last_poll_ts):
    if not last_poll_ts:
        return None
    try:
        t = datetime.fromisoformat(last_poll_ts)
        now = datetime.now(t.tzinfo) if t.tzinfo else datetime.now()
        return int((now - t).total_seconds() / 60)
    except Exception:
        return None


def main():
    # Per-host remote pointer. If .fireside/remote-host exists and names an SSH
    # target, query that host's daemon read-only and report its state instead of
    # the local one. Used on the laptop when fireside lives on the service-host VM.
    remote_host_file = WORKSPACE / ".fireside" / "remote-host"
    if remote_host_file.exists():
        host = remote_host_file.read_text(encoding="utf-8").strip().splitlines()[0:1]
        host = host[0].strip() if host else ""
        if host and not host.startswith("#"):
            _print_remote_status(host)
            return

    # Per-host opt-out. Touch .fireside/disabled to suppress auto-spawn on this
    # machine when the daemon is managed elsewhere with no remote-host pointer.
    if (WORKSPACE / ".fireside" / "disabled").exists():
        print("🔥 Fireside: DISABLED on this host (managed elsewhere)")
        return

    state = derive_state()
    prior = load_checkpoint()
    names = load_roster_names()
    started_count = len(state["started_uids"])
    tribe_size = len(names) or 55
    poll_age = poll_age_minutes(state["last_poll_ts"])

    # Fireside daemon liveness check + auto-spawn
    alive, pid = _daemon_alive()
    if not alive:
        new_pid = _spawn_detached_daemon()
        if new_pid:
            tag = f"pid {new_pid}" if new_pid > 0 else "detached"
            print(f"🔥 Fireside: daemon was NOT RUNNING — started {tag}")
        else:
            venv_hint = (
                "scripts/.venv-fireside/Scripts/python.exe"
                if sys.platform == "win32"
                else "scripts/.venv-fireside/bin/python"
            )
            print("🔥 Fireside: ❌ daemon NOT RUNNING and auto-start failed. "
                  "Check scripts/.venv-fireside/ exists and run "
                  f"{venv_hint} scripts/fireside-bot-daemon.py daemon manually.")
    else:
        print(f"🔥 Fireside: ✅ daemon up pid={pid}, "
              f"started {started_count}/{tribe_size}, "
              f"last poll {poll_age} min ago")

    # First run: initialise silently, print baseline only
    if prior is None:
        save_checkpoint(state)
        print(f"pulse: baseline set | started {started_count}/{tribe_size} | last poll {poll_age} min ago | sessions {state['session_count']} | errors {state['non_transient_errors']}")
        return

    deltas = []

    # New /start events
    new_started_uids = set(state["started_uids"]) - set(prior["started_uids"])
    if new_started_uids:
        new_names = sorted(names.get(uid, f"uid={uid}") for uid in new_started_uids)
        deltas.append(f"new /start ({len(new_names)}): " + ", ".join(new_names))

    # New swaps
    prior_swap_keys = {(s[0], s[1]) for s in prior.get("swap_events", [])}
    new_swaps = [s for s in state["swap_events"] if tuple(s) not in prior_swap_keys]
    if new_swaps:
        for ts, u in new_swaps:
            deltas.append(f"new /swap from @{u} at {ts[:19]}")

    # New tribe_join
    prior_join_keys = {(t[0], t[1]) for t in prior.get("tribe_joins", [])}
    new_joins = [t for t in state["tribe_joins"] if tuple(t) not in prior_join_keys]
    if new_joins:
        for ts, u in new_joins:
            deltas.append(f"new tribe member joined: @{u} at {ts[:19]}")

    # New session logged
    if state["session_count"] > prior.get("session_count", 0):
        deltas.append(f"new session logged (total now {state['session_count']})")

    # New no-show
    if state["no_show_count"] > prior.get("no_show_count", 0):
        deltas.append(f"NO-SHOW recorded (total {state['no_show_count']})")

    # Non-transient error burst (>= 3 new since last check)
    err_delta = state["non_transient_errors"] - prior.get("non_transient_errors", 0)
    if err_delta >= 3:
        deltas.append(f"WARN: {err_delta} new non-transient errors")

    # Polling stale
    if poll_age is not None and poll_age > 15:
        deltas.append(f"WARN: bot has not polled in {poll_age} min")

    # Output
    if deltas:
        print(f"fireside pulse @ {state['ts'][:19]} | started {started_count}/{tribe_size} | last poll {poll_age} min")
        for d in deltas:
            print(f"  - {d}")
    else:
        print(f"ok | started {started_count}/{tribe_size} | last poll {poll_age} min | no news")

    save_checkpoint(state)


if __name__ == "__main__":
    main()
