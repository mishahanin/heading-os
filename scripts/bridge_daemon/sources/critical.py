"""Critical-items source.

Append-only JSONL log of items the CEO has explicitly flagged as
"critical" from any page (pipeline, tasks, approvals, inbox, etc).
The log is tombstone-compatible: an entry with {"undo": true, "id":
...} removes a prior mark.

Schema for an active entry:
    {
        "id":          unique identifier (hash of kind+ref+ts),
        "kind":        "deal" | "task" | "draft" | "conversation" | "other",
        "ref":         pointer to source (path, slug, key, etc.),
        "label":       human-readable single-line description,
        "source_page": route hash to click through to ("#/pipeline", etc.),
        "note":        optional CEO note (up to NOTE_MAX_CHARS),
        "ts":          ISO 8601 UTC timestamp,
        "date":        local date,
    }

Schema for a tombstone:
    {"id": str, "undo": true, "ts": ISO 8601 UTC}

Mirrors the approval-sent-log / inbox-dismiss-log / task-done-log
patterns. Writes go through ``_jsonl.append_jsonl`` (a real ``O_APPEND``
line), so a writer in another PROCESS cannot silently overwrite an entry;
the module-level ``threading.Lock`` only ever covered this one. Reads go
through ``_jsonl.read_jsonl_capped``, which keeps the newest entries when
the log outgrows its cap rather than rendering the page empty.
"""
import hashlib
import threading
from datetime import date, datetime, timezone
from scripts.utils.workspace import get_default_tz
from pathlib import Path

from scripts.bridge_daemon._jsonl import append_jsonl, read_jsonl_capped
from scripts.bridge_daemon._shapes import entry_ts, is_undo

CRITICAL_LOG_FILE = "outputs/operations/bridge/critical-items.jsonl"  # leak-guard: ok (relative suffix rooted by caller)
CRITICAL_LOG_MAX_BYTES = 1_000_000  # 1 MB safety cap
NOTE_MAX_CHARS = 280
ALLOWED_KINDS = {"deal", "task", "draft", "conversation", "other"}
_LOCK = threading.Lock()


def _make_id(kind: str, ref: str, ts: str) -> str:
    """Stable short id from kind+ref+ts. 12-char hex slice is enough
    for an append-only log keyed off the (rare) mark-critical action.
    Uses sha256 (not sha1) - ruff S324. No security claim either way;
    we just need a deterministic id from the tuple."""
    h = hashlib.sha256(f"{kind}|{ref}|{ts}".encode("utf-8")).hexdigest()
    return h[:12]


def _read_log_lines(workspace_root: Path) -> tuple[list[dict], bool]:
    """Return ``(entries, truncated)`` in append order (active + tombstones).

    Caller filters; returning the raw stream keeps this primitive
    small and testable. Over CRITICAL_LOG_MAX_BYTES the reader keeps the
    newest entries and says so, instead of returning an empty list that a
    page renders as "you have flagged nothing".
    """
    return read_jsonl_capped(workspace_root / CRITICAL_LOG_FILE,
                             CRITICAL_LOG_MAX_BYTES)


def _active_entries(workspace_root: Path) -> tuple[dict[str, dict], bool]:
    """Return ``({id: entry}, truncated)`` for entries surviving tombstone replay.

    Iteration order: append. Last write per id wins; an `undo` tombstone
    removes the id from the active map. Truthiness, not ``is True``: a
    tombstone hand-edited to ``"undo": 1`` used to be read as an ACTIVE entry,
    resurrecting an item the operator had unmarked.
    """
    entries, truncated = _read_log_lines(workspace_root)
    active: dict[str, dict] = {}
    for entry in entries:
        eid = entry.get("id")
        if not isinstance(eid, str) or not eid:
            continue
        if is_undo(entry):
            active.pop(eid, None)
            continue
        active[eid] = entry
    return active, truncated


def list_critical(workspace_root: Path) -> dict:
    """Return the active critical-items list, newest first.

    Shape:
        {
            "items": [entry, ...],   # active entries, ts DESC
            "total": int,
            "truncated": bool,       # the log outgrew its cap; older marks
                                     # are on disk but not in this list
            "data_time": ISO 8601 of the newest entry's ts (or None).
        }
    """
    active, truncated = _active_entries(workspace_root)
    items = list(active.values())
    items.sort(key=entry_ts, reverse=True)
    data_time = entry_ts(items[0]) or None if items else None
    return {
        "items": items,
        "total": len(items),
        "truncated": truncated,
        "data_time": data_time,
    }


def recent_unmarked(workspace_root: Path, limit: int = 10) -> list[dict]:
    """Return the most-recent tombstoned entries so the CEO can restore.

    Each row: {id, label, kind, ref, ts, source_page}. Ordered by
    tombstone ts DESC. Mirrors approvals.sent_log_recent / tasks.done_log_recent.
    """
    # Replay the log capturing both active state and the LAST tombstone ts
    # per id. An entry that's currently tombstoned has its last-known
    # active payload elsewhere in the log; we keep that payload as the
    # 'what was it?' context.
    last_active: dict[str, dict] = {}
    tombstoned_at: dict[str, str] = {}
    entries, _truncated = _read_log_lines(workspace_root)
    for entry in entries:
        eid = entry.get("id")
        if not isinstance(eid, str) or not eid:
            continue
        if is_undo(entry):
            tombstoned_at[eid] = entry_ts(entry)
        else:
            last_active[eid] = entry
            tombstoned_at.pop(eid, None)  # active again, drop tombstone
    rows: list[dict] = []
    for eid, ts in tombstoned_at.items():
        payload = last_active.get(eid)
        if not payload:
            continue
        rows.append({
            "id": eid,
            "kind": payload.get("kind", "other"),
            "ref": payload.get("ref", ""),
            "label": payload.get("label", ""),
            "source_page": payload.get("source_page", ""),
            "ts": ts,
        })
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[: max(0, int(limit))]


def mark_critical(
    workspace_root: Path,
    kind: str,
    ref: str,
    label: str,
    source_page: str = "",
    note: str = "",
) -> dict:
    """Append a critical-mark entry. Returns {ok, id, ts} or {ok: False, error}.

    Validations:
    - kind must be one of ALLOWED_KINDS
    - ref + label must be non-empty strings
    - note clipped to NOTE_MAX_CHARS, newlines stripped
    - source_page (when given) must start with '#/'
    """
    if not isinstance(kind, str) or kind not in ALLOWED_KINDS:
        return {"ok": False, "error": f"kind must be one of {sorted(ALLOWED_KINDS)}"}
    if not isinstance(ref, str) or not ref.strip():
        return {"ok": False, "error": "ref is required"}
    if not isinstance(label, str) or not label.strip():
        return {"ok": False, "error": "label is required"}
    if source_page and not (isinstance(source_page, str) and source_page.startswith("#/")):
        return {"ok": False, "error": "source_page must start with '#/'"}
    ref = ref.strip()
    label = label.strip()
    # `note` is the one field that had no isinstance guard, so a non-string
    # from a direct Python caller raised AttributeError instead of the
    # validation error its three siblings return. (The HTTP path is safe:
    # pydantic rejects a non-string `note` before this function sees it.)
    if note is not None and not isinstance(note, str):
        return {"ok": False, "error": "note must be a string"}
    safe_note = (note or "").replace("\n", " ").replace("\r", " ").strip()[:NOTE_MAX_CHARS]
    now = datetime.now(timezone.utc)
    ts = now.isoformat()
    eid = _make_id(kind, ref, ts)
    entry = {
        "id": eid,
        "kind": kind,
        "ref": ref,
        "label": label,
        "source_page": source_page or "",
        "note": safe_note,
        "ts": ts,
        "date": datetime.now(get_default_tz()).date().isoformat(),
    }
    log_path = workspace_root / CRITICAL_LOG_FILE
    with _LOCK:
        try:
            append_jsonl(log_path, entry)
        except OSError as e:
            return {"ok": False, "error": f"write failed: {e}"}
    return {"ok": True, "id": eid, "ts": ts, "date": entry["date"]}


def unmark_critical(workspace_root: Path, item_id: str) -> dict:
    """Append a tombstone for `item_id`. Idempotent (re-tombstone is harmless)."""
    if not isinstance(item_id, str) or not item_id.strip():
        return {"ok": False, "error": "id is required"}
    item_id = item_id.strip()
    now = datetime.now(timezone.utc)
    entry = {"id": item_id, "undo": True, "ts": now.isoformat()}
    log_path = workspace_root / CRITICAL_LOG_FILE
    with _LOCK:
        try:
            append_jsonl(log_path, entry)
        except OSError as e:
            return {"ok": False, "error": f"write failed: {e}"}
    return {"ok": True, "id": item_id, "ts": entry["ts"]}
