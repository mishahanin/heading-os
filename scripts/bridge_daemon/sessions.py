"""Reader for the hook-maintained active-sessions registry.

The daemon NEVER globs ~/.claude/projects/ directly. All session lookup
goes through this module which reads the registry written atomically by
.claude/hooks/bridge-hook.py.
"""
import json
from pathlib import Path

from scripts.utils.timeparse import parse_iso

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

    "Newest" is decided on PARSED datetimes, not on the raw strings. ISO-8601
    text only orders correctly as text when every value shares one format and
    one UTC offset, and nothing enforces that here: `started_at` is written by
    `.claude/hooks/bridge-hook.py`, a per-user file shared by every project on
    the machine, and the module records one unannounced registry schema change
    already. `"2026-08-24T10:00:00+02:00"` is 08:00Z, an hour OLDER than
    `"2026-08-24T09:00:00+00:00"`, and sorts above it as a string, so `/launch`
    resolved the dead session and spawned a fresh terminal beside the live one
    - the exact failure this lookup exists to prevent. `parse_iso` is the
    engine's one timestamp reader and was already used for this in
    `action_queue.append_cards`.

    An unparseable or absent `started_at` never displaces a parsed one. Absent
    and older are different facts, and the cost of guessing is attaching to a
    session that is gone.
    """
    newest_sid: str | None = None
    newest_started = None
    for key, entry in read_registry(registry_path_).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("cwd") != cwd:
            continue
        started = parse_iso(entry.get("started_at"))
        # One condition, two reasons to take it: nothing has been chosen yet,
        # or this entry is readable and later than the one that was.
        if newest_sid is None or (started is not None
                                  and (newest_started is None
                                       or started > newest_started)):
            newest_sid = str(entry.get("session_id") or key)
            newest_started = started
    return newest_sid

def active_count(registry_path: Path) -> int:
    return len(read_registry(registry_path))
