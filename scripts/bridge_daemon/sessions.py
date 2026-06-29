"""Reader for the hook-maintained active-sessions registry.

The daemon NEVER globs ~/.claude/projects/ directly. All session lookup
goes through this module which reads the registry written atomically by
.claude/hooks/bridge-hook.py.
"""
import json
from pathlib import Path

def read_registry(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}

def session_for_cwd(registry_path: Path, cwd: str) -> str | None:
    entry = read_registry(registry_path).get(cwd)
    return entry.get("session_id") if entry else None

def active_count(registry_path: Path) -> int:
    return len(read_registry(registry_path))
