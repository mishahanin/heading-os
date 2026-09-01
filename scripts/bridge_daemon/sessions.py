"""Reader for the hook-maintained active-sessions registry.

The daemon NEVER globs ~/.claude/projects/ directly. All session lookup
goes through this module which reads the registry written atomically by
.claude/hooks/bridge-hook.py.
"""
import json
from pathlib import Path

def read_registry(path: Path) -> dict:
    """The hook-maintained session registry, or {} when it is not usable.

    Catches OSError as well as JSONDecodeError, and checks the SHAPE. The
    parsed value used to be returned unchecked, so a registry holding a JSON
    array reached `session_for_cwd`, which called `.get` on a list and 500'd
    `/launch`. `heartbeat._active_session_count` already reads the same file
    with both guards; this was the copy that drifted.

    `UnicodeDecodeError` is in the tuple because it is a `ValueError`, not an
    `OSError`, and `json.JSONDecodeError` never covers it: the decode happens
    in `read_text` BEFORE `json.loads` is reached. A registry left holding a
    torn half-written multi-byte character therefore raised straight out of
    this reader and 500'd `/launch`, past a docstring promising `{}` for
    anything not usable.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}

def registry_path() -> Path:
    """The one registry `.claude/hooks/bridge-hook.py` actually writes.

    Named here so a second reader cannot invent its own path, which is what
    `heartbeat._active_session_count` did: it read
    `<workspace>/.daemon-state/active-sessions.json`, a file nothing in this
    repository writes, and reported `active_sessions: 0` for a daemon serving
    live sessions - while its docstring credited bridge-hook.py as the writer.
    """
    return Path.home() / ".claude" / "state" / "active-sessions.json"


def session_for_cwd(registry_path_: Path, cwd: str) -> str | None:
    """The session id registered for `cwd`, or None.

    Scans the VALUES. The registry was rekeyed from cwd to session_id on
    2026-08-23, and this lookup kept indexing by KEY, so against any registry
    the current hook produces it could never hit: `/launch` with a cwd and no
    session_id - the fallback that justifies the registry existing - always
    resolved None and spawned a fresh terminal beside the live session. The
    regression stayed invisible because the endpoint test seeded the registry by
    hand in the OLD cwd-keyed shape, which the hook can no longer write.

    `isinstance`, not truthiness: `read_registry` guarantees the registry is a
    dict and says nothing about its values, so a bare session-id string from an
    older hook, or a hand edit, must not reach `.get`.
    """
    newest_sid: str | None = None
    newest_started = ""
    for key, entry in read_registry(registry_path_).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("cwd") != cwd:
            continue
        started = str(entry.get("started_at") or "")
        if newest_sid is None or started > newest_started:
            newest_sid = str(entry.get("session_id") or key)
            newest_started = started
    return newest_sid

def active_count(registry_path: Path) -> int:
    return len(read_registry(registry_path))
