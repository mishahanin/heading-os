#!/usr/bin/env python3
"""Bridge hook router.

Subcommands:
  session-start   write registry entry on SessionStart event
  session-end     remove registry entry on SessionEnd event
  stop            origin-gated "stay or browser?" prompt on Stop event (Task 20)

Reads hook payload from stdin. NEVER writes to stdout (SessionStart stdout
gets injected into Claude's context). All diagnostics go to stderr.
"""
import http.client
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REGISTRY = (
    Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or "~").expanduser()
    / ".claude" / "state" / "active-sessions.json"
)


def _atomic_write(path: Path, content: str) -> None:
    """Atomically write content to path via write-to-tmp + os.replace.

    Required by the workspace global CLAUDE.md security rule for state files.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        # Cleanup the orphan tmp file. Swallow only the cleanup OSError -
        # the original exception is re-raised below so the caller sees it.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_registry() -> dict:
    """Read the registry. Returns empty dict on missing file or corrupt JSON
    (auto-recover - the next session-start will rewrite a clean file)."""
    if not REGISTRY.exists():
        return {}
    try:
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def session_start(payload: dict) -> int:
    """Write a registry entry keyed by cwd. Returns 1 on missing required fields."""
    sid = payload.get("session_id")
    cwd = payload.get("cwd")
    if not sid or not cwd:
        print("bridge-hook: missing session_id or cwd in session-start payload", file=sys.stderr)
        return 1
    reg = _load_registry()
    reg[cwd] = {
        "session_id": sid,
        "transcript_path": payload.get("transcript_path"),
        "pid": os.getppid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source": payload.get("source", "unknown"),
    }
    _atomic_write(REGISTRY, json.dumps(reg, indent=2))
    return 0


def session_end(payload: dict) -> int:
    """Remove the registry entry for the given cwd. Idempotent - no-op if absent."""
    cwd = payload.get("cwd")
    reg = _load_registry()
    if cwd in reg:
        del reg[cwd]
        _atomic_write(REGISTRY, json.dumps(reg, indent=2))
    return 0


def _read_user_choice(timeout: int) -> str:
    """Read a single line from the controlling terminal, not stdin.

    The hook payload is delivered on stdin and consumed in main(), so stdin
    is at EOF here. We read from the user's real keyboard: /dev/tty on POSIX
    or the Win32 console via msvcrt on Windows. If no tty is available
    (headless `claude -p`, CI, background daemon), return empty string and
    let the caller default to stay.
    """
    try:
        if sys.platform == "win32":
            import msvcrt
            import time as _t
            deadline = _t.time() + timeout
            buf = ""
            while _t.time() < deadline:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\r", "\n"):
                        break
                    buf += ch
                _t.sleep(0.05)
            return buf.strip().lower()
        else:
            import select
            with open("/dev/tty", "r") as tty:
                ready, _, _ = select.select([tty], [], [], timeout)
                if ready:
                    return tty.readline().strip().lower()
                return ""
    except (OSError, FileNotFoundError, ImportError):
        # No controlling tty (headless / CI / background). Caller defaults to stay.
        return ""


def _find_daemon_state(start: Path) -> Path | None:
    """Walk up from `start` looking for .daemon-state/port. The hook may
    run from a subdirectory of the workspace (the user cd-ed into a subdir
    before launching Claude), so the daemon state lives at an ancestor.
    """
    try:
        p = start.resolve()
    except OSError:
        return None
    for ancestor in [p, *p.parents]:
        if (ancestor / ".daemon-state" / "port").exists():
            return ancestor / ".daemon-state"
    return None


def _trigger_return(session_id: str, session_cwd: str | None) -> None:
    """POST to the daemon's /return endpoint to focus the browser tab.

    Resolves the daemon state directory by walking up from candidate roots:
    (1) the session cwd from the hook payload, (2) PWD env var, (3) os.getcwd().
    First .daemon-state/ found wins. Silently logs and returns on any failure -
    a failed return-to-browser must not block the user's terminal session exit.
    """
    import urllib.error
    import urllib.request
    import json as _json
    candidates = []
    if session_cwd:
        candidates.append(Path(session_cwd))
    if os.environ.get("PWD"):
        candidates.append(Path(os.environ["PWD"]))
    candidates.append(Path(os.getcwd()))

    state_dir = None
    for c in candidates:
        state_dir = _find_daemon_state(c)
        if state_dir:
            break
    if not state_dir:
        print("bridge: could not locate .daemon-state/ from session cwd, PWD, or getcwd",
              file=sys.stderr)
        return
    try:
        port = (state_dir / "port").read_text(encoding="utf-8").strip()
        token = (state_dir / "token").read_text(encoding="utf-8").strip()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/return",
            data=_json.dumps({"session_id": session_id, "target_page": "pulse"}).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
    except (urllib.error.URLError, OSError, ValueError, http.client.InvalidURL) as e:
        print(f"bridge: /return failed: {e}", file=sys.stderr)


def stop(payload: dict) -> int:
    """Origin-gated Stop prompt.

    Fires only if BRIDGE_ORIGIN=browser is set (the daemon's terminal launcher
    injects this env var into spawned sessions). Background daemons (Sentinel,
    fireside-bot, `claude -p` batches) never see it.

    Prompt timeout is 5s default (locked 2026-05-17). Default on timeout or
    when no controlling tty is available: stay.
    """
    if os.environ.get("BRIDGE_ORIGIN") != "browser":
        return 0

    try:
        timeout = int(os.environ.get("BRIDGE_STOP_TIMEOUT", "5"))
    except ValueError:
        timeout = 5

    print(f"\nbridge: [stay (Enter) / browser (b)] - {timeout}s to stay: ",
          file=sys.stderr, end="", flush=True)

    choice = _read_user_choice(timeout)

    if choice == "b":
        print("bridge: returning to browser...", file=sys.stderr)
        _trigger_return(payload.get("session_id", ""), payload.get("cwd"))
    else:
        print("bridge: stay.", file=sys.stderr)
    return 0


def main() -> int:
    """Dispatch on argv[1]. Reads payload JSON from stdin."""
    if len(sys.argv) < 2:
        print("usage: bridge-hook.py {session-start|session-end|stop}", file=sys.stderr)
        return 1
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    cmd = sys.argv[1]
    if cmd == "session-start":
        return session_start(payload)
    if cmd == "session-end":
        return session_end(payload)
    if cmd == "stop":
        return stop(payload)
    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
