#!/usr/bin/env python3
"""Bridge hook router.

Subcommands:
  session-start   write registry entry on SessionStart event
  session-end     remove registry entry on SessionEnd event
  stop            origin-gated "stay or browser?" prompt on Stop event (Task 20)

Reads hook payload from stdin. NEVER writes to stdout (SessionStart stdout
gets injected into Claude's context). All diagnostics go to stderr.
"""
import contextlib
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

# `checkpoint_paths.file_lock`, when this clone has it. Located by walking for
# `scripts/utils/`, the same way `checkpoint-inject.py` does, so a hook shipped
# inside a plugin bundle finds its own copy rather than counting parents.
#
# OPTIONAL on purpose. A hook that cannot import a helper must still register the
# session; failing to import is not a reason to lose the entry. So `_CP` stays
# None, `_registry_lock` degrades to a no-op, and one line goes to stderr saying
# the registry write is unserialised. Loud degradation, never a silent one.
_CP = None
for _candidate in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
    if (_candidate / "scripts" / "utils" / "checkpoint_paths.py").is_file():
        sys.path.insert(0, str(_candidate))
        try:
            from scripts.utils import checkpoint_paths as _CP  # noqa: E402
        except Exception as _exc:  # noqa: BLE001 - reported, never fatal
            print(f"bridge-hook: checkpoint_paths unavailable ({_exc}); "
                  f"registry writes are not serialised", file=sys.stderr)
        break


@contextlib.contextmanager
def _registry_lock():
    """Serialise the registry read-modify-write across concurrent sessions.

    `_atomic_write` makes each WRITE indivisible, which is a DIFFERENT guarantee
    from making a read and its following write indivisible. Both `session_start`
    and `session_end` load the whole registry, mutate their own key, and write
    the whole thing back; two sessions overlapping in that span lose one of the
    two mutations, and `os.replace` gives no error when it happens.

    `session_end` is the worse half: it writes back a copy loaded before a
    concurrent `session_start` landed, deleting the entry of a session that is
    still alive. That is the same defect the 2026-08-23 audit fixed once already
    by rekeying from cwd to session_id, arriving through a different door.

    The registry lives at `~/.claude/state/active-sessions.json`, a PER-USER path
    shared by every project on the machine, so the racing writers need not be in
    this workspace. `bridge-hook.py` is its only writer, so every racer is
    another copy of this hook.

    Found by the 2026-08-31 audit of the hooks family, its first ever. The
    primitive is `scripts/utils/checkpoint_paths.file_lock`, which is bounded and
    degrades to unlocked with a stderr line rather than blocking: a hook that
    waits forever is worse than a hook that races, and the Stop hook has a
    90-second budget.
    """
    if _CP is None:
        # ABSENT was the silent case, and it is the ordinary one. The comment
        # above `_CP` promises "one line goes to stderr saying the registry write
        # is unserialised. Loud degradation, never a silent one", and the only
        # branch that printed was the one where the file WAS found and its import
        # raised. A public engine clone carries no `scripts/utils/`, so the walk
        # finds nothing, no exception is raised, and nothing was said. MEASURED
        # 2026-08-31 by copying this hook to a directory with no `scripts/utils`
        # above it: `session-start` exited 0, wrote the entry, and printed 0
        # bytes of stderr. Said here rather than at import, so it names a
        # read-modify-write that is actually happening: `stop` touches no
        # registry and has nothing to warn about.
        print("bridge-hook: checkpoint_paths not available; this registry "
              "read-modify-write is not serialised", file=sys.stderr)
        yield False
        return
    with _CP.file_lock(REGISTRY.with_suffix(".lock"), label="bridge-hook") as held:
        yield held


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
    except BaseException:
        # BaseException, not Exception. `KeyboardInterrupt` and `SystemExit` do
        # not derive from `Exception`, so a Ctrl-C or an interpreter shutdown
        # landing between the mkstemp and the replace walked past this clause
        # and left the scratch file beside the target, named `tmpXXXXXXXX` and
        # owned by nothing. This hook writes the session registry at session
        # start and session end, so an interrupt is not a remote possibility.
        #
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
        loaded = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # OSError was uncaught until 2026-08-23, so an unreadable or locked
        # registry crashed session-start with a traceback while this docstring
        # promised recovery. A permission or lock error is exactly the case the
        # promise was written for.
        #
        # `ValueError`, not `json.JSONDecodeError`, since 2026-09-01. The decode
        # happens INSIDE `read_text(encoding="utf-8")`, before `json.loads` is
        # reached at all, and `UnicodeDecodeError` is a SIBLING of
        # `JSONDecodeError` under `ValueError` rather than a subclass of it. So
        # the narrower tuple walked straight past one byte that is not UTF-8.
        # MEASURED 2026-09-01: a registry holding a single 0xff made both
        # `session-start` and `session-end` exit 1 with a traceback, and because
        # the file is only ever rewritten AFTER this read, it could never heal -
        # every session on the machine stayed unregistered until a human deleted
        # it. The daemon-side reader of this same file,
        # `scripts/bridge_daemon/sessions.read_registry`, had already been fixed
        # for exactly this; its writer had not.
        print(f"bridge-hook: registry unreadable ({exc}); starting empty",
              file=sys.stderr)
        return {}
    if not isinstance(loaded, dict):
        print(f"bridge-hook: registry held {type(loaded).__name__}, not an object; "
              "starting empty", file=sys.stderr)
        return {}
    return loaded


def session_start(payload: dict) -> int:
    """Write a registry entry keyed by session_id. Returns 1 on missing fields.

    Keyed by cwd until 2026-08-23, which this workspace breaks by design: it
    runs several sessions on one tree, as `checkpoint-statusline.py` states in
    its own docstring. Two sessions launched from the same directory produced
    one entry, the second silently overwriting the first, and whichever ended
    first deleted it, deregistering a session that was still alive. Found by the
    2026-08-23 audit.

    session_id is unique per session and is already required here, so it is the
    natural key; cwd stays as a field for anything that groups by directory.
    """
    sid = payload.get("session_id")
    cwd = payload.get("cwd")
    if not sid or not cwd:
        print("bridge-hook: missing session_id or cwd in session-start payload", file=sys.stderr)
        return 1
    with _registry_lock():
        reg = _load_registry()
        reg[sid] = {
            "session_id": sid,
            "cwd": cwd,
            "transcript_path": payload.get("transcript_path"),
            "pid": os.getppid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "source": payload.get("source", "unknown"),
        }
        _atomic_write(REGISTRY, json.dumps(reg, indent=2))
    return 0


def session_end(payload: dict) -> int:
    """Remove this session's entry. Idempotent - no-op if absent.

    Deletes by session_id. Deleting by cwd removed whichever session happened to
    hold that key, which with two sessions in one directory was the one still
    running. A cwd-keyed entry written before 2026-08-23 is swept too, so an old
    registry drains instead of accumulating forever.
    """
    sid = payload.get("session_id")
    cwd = payload.get("cwd")
    with _registry_lock():
        reg = _load_registry()
        changed = False
        if sid and sid in reg:
            del reg[sid]
            changed = True
        # Legacy cwd-keyed entry from before the rekey, and only if it is OURS.
        if cwd and cwd in reg and isinstance(reg[cwd], dict) \
                and reg[cwd].get("session_id") == sid:
            del reg[cwd]
            changed = True
        if changed:
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
            # `monotonic`, not `time`. This branch was left on wall-clock time
            # when the POSIX branch below was rewritten to be bounded, so the
            # sentence that rewrite added twenty lines down, "every wait is
            # bounded, so the worst case is the timeout the caller asked for",
            # was false on Windows. An NTP correction or a manual clock change
            # that steps the system clock BACKWARD by N seconds extended this
            # loop by N, blocking the Stop hook and the session exit with it.
            # `monotonic` cannot step backward, so the bound holds. Found by the
            # 2026-08-31 audit of the hooks family, its first ever.
            deadline = _t.monotonic() + timeout
            buf = ""
            while _t.monotonic() < deadline:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\r", "\n"):
                        break
                    buf += ch
                _t.sleep(0.05)
            return buf.strip().lower()
        else:
            import select
            import time as _t
            # `select` fires on ONE readable byte, and Claude Code leaves the
            # terminal in raw/cbreak mode, so a stray keypress made the old
            # `tty.readline()` wait for a newline that might never arrive. That
            # blocked the Stop hook, and with it the session exit, forever,
            # under a docstring promising a 5 s timeout. Found by the 2026-08-23
            # audit. Read byte by byte against a deadline instead: every wait is
            # bounded, so the worst case is the timeout the caller asked for.
            deadline = _t.monotonic() + timeout
            buf = ""
            with open("/dev/tty", "rb", buffering=0) as tty:
                while True:
                    remaining = deadline - _t.monotonic()
                    if remaining <= 0:
                        return buf.strip().lower()
                    ready, _, _ = select.select([tty], [], [], remaining)
                    if not ready:
                        return buf.strip().lower()
                    chunk = tty.read(1)
                    if not chunk:            # EOF
                        return buf.strip().lower()
                    ch = chunk.decode("utf-8", errors="replace")
                    if ch in ("\r", "\n"):
                        return buf.strip().lower()
                    buf += ch
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
    # `[]`, `"x"` and `3` are valid JSON and are not objects; the first `.get`
    # in session_start then raises an uncaught AttributeError. Reproduced
    # 2026-08-23: `echo '[]' | bridge-hook.py session-start` exited 1 with a
    # traceback. Same shape checkpoint-inject.py fixed on 2026-08-20.
    if not isinstance(payload, dict):
        print(f"bridge-hook: payload was {type(payload).__name__}, not an object",
              file=sys.stderr)
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
