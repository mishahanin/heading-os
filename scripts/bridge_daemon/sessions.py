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
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}

def session_for_cwd(registry_path: Path, cwd: str) -> str | None:
    # `isinstance`, not truthiness. `read_registry` guarantees the REGISTRY is
    # a dict and says nothing about its values, so a truthy non-dict entry --
    # a bare session-id string from an older hook, or a hand edit -- reached
    # `.get` and 500'd /launch. That is the same shape this module's docstring
    # records fixing one level up; the fix stopped at the top level.
    entry = read_registry(registry_path).get(cwd)
    return entry.get("session_id") if isinstance(entry, dict) else None

def active_count(registry_path: Path) -> int:
    return len(read_registry(registry_path))
