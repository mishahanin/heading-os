#!/usr/bin/env python3
"""Fireside Pulse — diff-based status check for /loop.

Reads current bot state, compares against a checkpoint file, and prints ONLY
the changes since the last run. Designed to be invoked every ~10 min by
`/loop` so the operator only sees output when something meaningful has happened.

Usage:
    python scripts/fireside-pulse.py

Output policy (the lines this actually prints; the block below used to describe
an older format, and a wrapper matching it got nothing):
    - Every run first prints one daemon-status line, "Fireside: ..." -- the
      first run is NOT silent
    - First run (no checkpoint): plus "pulse: baseline set | started <N>/<tribe>
      | last poll <age> | sessions <n> | errors <n>"
    - No changes: plus "ok | started <N>/<tribe> | last poll <age> | no news"
    - Changes: plus a header line and a bulleted list of new events
    - Polling stale (>15 min): a WARN bullet
    - Non-transient error burst (>=3 new): a WARN bullet

`<age>` is "<N> min ago" or the words "no tick recorded"; it is never the
string "None".

Tests: tests/test_a_spawn_that_reported_a_daemon_it_never_confirmed.py
"""
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
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


def state_dir() -> Path:
    """Resolved at call time, never at import.

    `get_datastore_dir()` reads `HEADING_OS_DATA` on every call, so it follows
    the environment for a caller that asks after the environment moved. As a
    module-level constant it asked once, during its own import, and stored the
    answer, so a test that imported this module and then repointed the root
    still read the operator's real overlay.
    """
    return get_datastore_dir() / "operations" / "tribe" / "fireside-state"


def checkpoint() -> Path:
    return get_outputs_dir() / "operations" / "tribe-fireside" / "pulse-checkpoint.json"


def _svc() -> dict:
    """The managed service-host VM's private instance topology.

    The engine ships scripts/service-host.example.json with a generic default;
    `resolve_config_with_example` reaches the data root, so this is read at call
    time for the same reason the two paths above are.
    """
    return json.loads(
        resolve_config_with_example(
            "service-host.json", Path(__file__).resolve().parent / "service-host.example.json"
        ).read_text(encoding="utf-8")
    )


def _fireside_unit() -> str:
    return _svc().get("fireside_unit", "fireside.service")

def _force_utf8_stdout() -> None:
    """Re-wrap stdout as UTF-8, but only when it needs it and can take it.

    This ran unconditionally at import and REPLACED the process's stdout, so
    any importer got its own stream swapped out as a side effect of an import.
    Under pytest that breaks capture with "I/O operation on closed file"; the
    same shape would break any caller that had installed its own stream.

    The Windows console is the reason it exists (cp1252 cannot print the emoji
    in this script's output), so it still fires there. It skips when the stream
    is already UTF-8, and when there is no `.buffer` to wrap.
    """
    stream = sys.stdout
    if getattr(stream, "encoding", "").lower().replace("-", "") == "utf8":
        return
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        return
    with contextlib.suppress(AttributeError, ValueError, OSError):
        sys.stdout = io.TextIOWrapper(buffer, encoding="utf-8")


_force_utf8_stdout()

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
def _probe() -> str:
    return _PROBE_TEMPLATE.replace("__FIRESIDE_UNIT__", _fireside_unit())


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
            input=_probe(),
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


def _webhook_listening(host: str, port: int, timeout: float = 6.0) -> bool | None:
    """Is the daemon's Telegram webhook port accepting TCP connections?

    Returns True/False, or None when the SSH alias cannot be resolved to an
    address (in which case nothing about the daemon has been established).
    Second, independent evidence path for `_print_remote_status`: the SSH probe
    failing says only that OUR ssh call failed - an outbound :22 block on the
    operator's current network produces exactly the same failure as a dead VM.
    """
    try:
        proc = subprocess.run(
            ["ssh", "-G", host], capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    addr = next(
        (ln.split(None, 1)[1].strip()
         for ln in proc.stdout.splitlines() if ln.startswith("hostname ")),
        "",
    )
    if not addr:
        return None
    try:
        with socket.create_connection((addr, port), timeout=timeout):
            return True
    except OSError:
        return False


def _print_remote_status(host: str) -> None:
    """Print a /prime-friendly one-line status for the service-host daemon."""
    data = _query_service_host(host)
    if not data:
        # The SSH probe failing establishes that the probe failed, not that the
        # daemon is down (see .claude/rules/scope-claims.md). Ask the webhook
        # port before saying anything about the daemon.
        # Guarded: this line sits on the path that reports "daemon state
        # UNKNOWN" after an SSH probe failed. A bad `webhook_port` in the
        # private config turned that careful report into a stack trace, which
        # is a worse answer than the unknown it was about to give.
        raw_port = _svc().get("webhook_port", 8443)
        try:
            port = int(raw_port)
        except (TypeError, ValueError):
            print(f"pulse: webhook_port is {raw_port!r}, not a port number; "
                  f"using 8443 for the hint below", file=sys.stderr)
            port = 8443
        listening = _webhook_listening(host, port)
        if listening:
            print(f"🔥 Fireside (service-host {host}): SSH probe failed, unit state UNKNOWN "
                  f"- but webhook port {port} is accepting connections, so the daemon is listening")
            print("  - check whether outbound :22 is blocked on this network before treating it as a VM fault")
        elif listening is False:
            print(f"🔥 Fireside (service-host {host}): SSH probe failed AND webhook port {port} "
                  f"is not accepting connections - daemon state UNKNOWN, host may be down")
        else:
            print(f"🔥 Fireside (service-host {host}): SSH probe failed and the host address "
                  f"could not be resolved - daemon state UNKNOWN")
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


def _remote_host_from(text: str) -> str:
    """The first real SSH target in `.fireside/remote-host`, ignoring comments.

    Only line 1 used to be examined, and a `#` on it made the whole pointer
    invisible. The `startswith("#")` test proves comments were anticipated, so a
    documented pointer file --

        # fireside lives on the service-host VM
        fireside-vm

    -- read as "no remote host", fell through to the LOCAL path, and that path
    calls `_spawn_detached_daemon`. The operator who commented their own config
    got a second bot spawned beside the remote one, on one Telegram token: the
    exact disaster the PermissionError branch of `_daemon_alive` was written to
    avoid. Every line is scanned now, so a comment can never silence the pointer.
    """
    for line in (text or "").splitlines():
        candidate = line.strip()
        if candidate and not candidate.startswith("#"):
            return candidate
    return ""


def _windows_alive(open_ok: bool, get_exit_ok: bool, exit_code: int) -> bool:
    """Is the Windows process alive, given the three things the API tells us?

    Split out because it is the DECISION, and the decision is what was wrong;
    the ctypes calls around it cannot be exercised from this workspace, so
    leaving the logic inline left it untestable and therefore untested.

    Two of the three shapes below re-created a hazard the POSIX branch had
    already been fixed for -- reading "I could not tell" as "dead", after which
    `main()` spawns a SECOND daemon beside a live one:

    * `OpenProcess` failing (elevated or protected process) was read as dead.
      POSIX answers ALIVE for the equivalent `PermissionError`, and says why.
    * `GetExitCodeProcess`'s BOOL return was never checked, so a failed call
      left `exit_code` at its initialised 0 and a live daemon read as dead.

    The third shape is the opposite and is accepted: a process that genuinely
    exited with 259 (`STILL_ACTIVE`) reads as alive. Telling those apart needs
    `WaitForSingleObject`, and it fails SAFE -- it suppresses an auto-start
    rather than duplicating a bot.
    """
    if not open_ok:
        return True          # could not tell; never answer "dead" on a guess
    if not get_exit_ok:
        return True          # ditto: an unchecked 0 used to mean "dead"
    return exit_code == 259  # STILL_ACTIVE


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
            # NOT `False`. See `_windows_alive`: "could not open" is not "dead",
            # and answering dead here spawns a second bot on one token.
            return _windows_alive(False, False, 0), pid
        try:
            code = ctypes.c_ulong(0)
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            return _windows_alive(True, bool(ok), code.value), pid
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False, None
        except PermissionError:
            # The process EXISTS and belongs to another user. Reading that as
            # "dead" made pulse spawn a second daemon beside a running one --
            # two bots on one Telegram token. Alive is the safe answer, and it
            # is also the true one: signal 0 only reaches a live pid.
            return True, pid
        return True, pid


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
            # The pid file is the proof, not the spawn. `start` is a cmd.exe
            # builtin: our Popen returns cmd.exe's pid, cmd exits in
            # milliseconds, and it exits 0 whether or not the target launched.
            # Returning the sentinel unconditionally made pulse print "started
            # detached" when the daemon script was missing or the interpreter
            # failed. Wait briefly for the daemon to write its real pid.
            for _ in range(20):                     # up to ~4s
                time.sleep(0.2)
                alive, real_pid = _daemon_alive()
                if alive and real_pid:
                    return real_pid
            return None
        subprocess.Popen(
            [str(venv_py), str(daemon), "daemon"],
            cwd=str(WORKSPACE),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        # The pid file is the proof, not the spawn -- the same rule the Windows
        # branch above already follows, and for the same reason it gives: a
        # `Popen` that returns a pid proves the INTERPRETER launched, not that
        # the daemon did. A missing `fireside-bot-daemon.py`, an import that
        # raises, or an immediate exit all leave `proc.pid` naming a process that
        # is already gone, and `main()` then printed "daemon was NOT RUNNING -
        # started pid N" while the daemon stayed down. That fix was applied to
        # Windows and not here, on the platform this workspace actually runs.
        for _ in range(20):                     # up to ~4s, matching Windows
            time.sleep(0.2)
            alive, real_pid = _daemon_alive()
            if alive and real_pid:
                return real_pid
        return None
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
    sessions = load_jsonl(state_dir() / "sessions.jsonl")
    dm_log = load_jsonl(state_dir() / "dm-log.jsonl")

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
        # Both tick types, matching `_PROBE_TEMPLATE`. In webhook mode the
        # daemon skips the poll job BY DESIGN and writes heartbeat-tick instead,
        # so accepting only poll-tick left `last_poll_ts` None on a perfectly
        # healthy daemon and fired the 15-minute stale-poll WARN forever. The
        # remote probe already knew this; the local path did not.
        if e.get("dm_type") in ("poll-tick", "heartbeat-tick"):
            last_poll_ts = e.get("ts")
            break

    # Non-transient errors (exclude ConnectionResetError, NameResolutionError, WinError 10013 noise)
    errors_path = state_dir() / "errors.log"
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
    """The saved baseline, or None when there is not a usable one.

    None means "re-baseline", which is what an absent file already meant. A
    truncated or hand-edited `pulse-checkpoint.json` used to raise out of every
    run, so the operator got no status at all -- from the tool whose entire job
    is to report status -- until they deleted the file by hand.
    """
    path = checkpoint()
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"pulse: checkpoint at {path} is unreadable ({exc}); "
              f"re-baselining", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        print(f"pulse: checkpoint at {path} is a {type(data).__name__}, "
              f"not an object; re-baselining", file=sys.stderr)
        return None
    return data


def save_checkpoint(state):
    """Write the checkpoint atomically, on a scratch path nobody else can hold.

    `os.replace` is atomic; a SHARED scratch name is not. This wrote every run's
    state to the one fixed path `pulse-checkpoint.tmp`, and this script is what
    `/loop` fires every ten minutes -- so a manual run beside the loop, or one
    run overrunning into the next, had both writing that same file and one
    `replace` moved the other's half-written bytes into place as the baseline.
    `mkstemp` gives each writer its own name in the same directory, which is all
    `os.replace` needs to stay atomic.
    """
    path = checkpoint()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                    prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def load_roster_names():
    """Return dict of {user_id: name} for friendly output, {} when unreadable.

    Guarded the way `load_checkpoint` above it already is, and for the same
    reason: a truncated or hand-edited `tribe-roster.json` raised straight out of
    `main()`, so the tool whose only job is to report status reported nothing at
    all. The roster is decoration here -- names instead of raw ids, and the
    denominator -- so its absence degrades the line rather than the run.
    """
    path = state_dir() / "tribe-roster.json"
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            roster = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"pulse: roster at {path} is unreadable ({exc}); "
              f"names and the tribe total are unavailable", file=sys.stderr)
        return {}
    if not isinstance(roster, dict):
        print(f"pulse: roster at {path} is a {type(roster).__name__}, not an "
              f"object; names and the tribe total are unavailable", file=sys.stderr)
        return {}
    return {m.get("telegram_user_id"): m.get("name", k)
            for k, m in roster.items() if isinstance(m, dict)}


def poll_age_minutes(last_poll_ts):
    """Minutes since `last_poll_ts`, or None when that cannot be established.

    None means UNKNOWN, never zero and never "fine" -- `poll_label` below turns
    it into a word rather than letting it render as the string "None".
    """
    if not last_poll_ts:
        return None
    try:
        t = datetime.fromisoformat(last_poll_ts)
        if t.tzinfo is None:
            t = t.replace(tzinfo=get_default_tz())
        now = datetime.now(t.tzinfo)
        return int((now - t).total_seconds() / 60)
    except (TypeError, ValueError):
        # Narrow, not bare. An unparseable stamp is the one failure this can
        # have, and a catch-all here would hide a real bug as "unknown age".
        print(f"pulse: liveness stamp {last_poll_ts!r} is unreadable; "
              f"the tick age is UNKNOWN", file=sys.stderr)
        return None


def poll_label(age):
    """The tick age as words. None is a fact to state, not a value to print.

    Every status line interpolated `poll_age` straight into "last poll {} min
    ago", so a bot that had never ticked -- or whose newest stamp would not
    parse -- reported "last poll None min ago" on a line the operator reads to
    decide whether the daemon is healthy. `_print_remote_status` already said
    "no tick recorded" for the same state; only the LOCAL path printed the word
    None, exactly as it did for the poll-tick/heartbeat-tick split above it.
    """
    return f"{age} min ago" if age is not None else "no tick recorded"


def main():
    # Per-host remote pointer. If .fireside/remote-host exists and names an SSH
    # target, query that host's daemon read-only and report its state instead of
    # the local one. Used on the laptop when fireside lives on the service-host VM.
    remote_host_file = WORKSPACE / ".fireside" / "remote-host"
    if remote_host_file.exists():
        host = _remote_host_from(remote_host_file.read_text(encoding="utf-8"))
        if host:
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
    # No invented denominator. `or 55` printed "started 12/55" when the roster
    # was absent, unreadable or empty -- a ratio against a number nothing on
    # this machine had measured.
    tribe_size = len(names)
    # "?" rather than a number the roster never supplied. A denominator of 0
    # renders as "12/0", which reads as a bug; "12/?" reads as what it is.
    tribe_label = str(tribe_size) if tribe_size else "?"
    poll_age = poll_age_minutes(state["last_poll_ts"])
    poll_text = poll_label(poll_age)

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
              f"started {started_count}/{tribe_label}, "
              f"last poll {poll_text}")

    # First run: initialise silently, print baseline only
    if prior is None:
        save_checkpoint(state)
        print(f"pulse: baseline set | started {started_count}/{tribe_label} | last poll {poll_text} | sessions {state['session_count']} | errors {state['non_transient_errors']}")
        return

    deltas = []

    # New /start events
    # `.get`, like every other read in this block. `load_checkpoint` deliberately
    # returns any well-formed dict, and its docstring promises a hand-edited
    # checkpoint will not take the tool down -- but this one line indexed
    # directly, so a checkpoint merely MISSING the key (an older schema, a hand
    # edit, a partial write repaired by hand) raised KeyError and killed the run
    # the guard above exists to keep alive.
    new_started_uids = set(state["started_uids"]) - set(prior.get("started_uids", []))
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
        print(f"fireside pulse @ {state['ts'][:19]} | started {started_count}/{tribe_label} | last poll {poll_text}")
        for d in deltas:
            print(f"  - {d}")
    else:
        print(f"ok | started {started_count}/{tribe_label} | last poll {poll_text} | no news")

    save_checkpoint(state)


if __name__ == "__main__":
    main()
