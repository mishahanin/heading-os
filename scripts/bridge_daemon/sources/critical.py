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
patterns. JSONL append + atomic write so concurrent writers don't
corrupt the file.
"""
import hashlib
import json
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from scripts.bridge_daemon._atomic import atomic_write_text

CRITICAL_LOG_FILE = "outputs/operations/bridge/critical-items.jsonl"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)
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


def _read_log_lines(workspace_root: Path) -> list[dict]:
    """Return ALL log entries in append order (active + tombstones).

    Caller filters; returning the raw stream keeps this primitive
    small and testable.
    """
    log_path = workspace_root / CRITICAL_LOG_FILE
    if not log_path.exists():
        return []
    try:
        if log_path.stat().st_size > CRITICAL_LOG_MAX_BYTES:
            return []
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _active_entries(workspace_root: Path) -> dict[str, dict]:
    """Return {id: entry} for entries that survive tombstone replay.

    Iteration order: append. Last write per id wins; an `undo: true`
    tombstone removes the id from the active map.
    """
    active: dict[str, dict] = {}
    for entry in _read_log_lines(workspace_root):
        eid = entry.get("id")
        if not isinstance(eid, str) or not eid:
            continue
        if entry.get("undo") is True:
            active.pop(eid, None)
            continue
        active[eid] = entry
    return active


def list_critical(workspace_root: Path) -> dict:
    """Return the active critical-items list, newest first.

    Shape:
        {
            "items": [entry, ...],   # active entries, ts DESC
            "total": int,
            "data_time": ISO 8601 of the newest entry's ts (or None).
        }
    """
    active = _active_entries(workspace_root)
    items = list(active.values())
    items.sort(key=lambda e: e.get("ts", ""), reverse=True)
    data_time = items[0].get("ts") if items else None
    return {
        "items": items,
        "total": len(items),
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
    for entry in _read_log_lines(workspace_root):
        eid = entry.get("id")
        if not isinstance(eid, str) or not eid:
            continue
        if entry.get("undo") is True:
            tombstoned_at[eid] = entry.get("ts", "")
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
        "date": date.today().isoformat(),
    }
    log_path = workspace_root / CRITICAL_LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        existing = ""
        if log_path.exists():
            try:
                existing = log_path.read_text(encoding="utf-8")
            except OSError:
                existing = ""
        new_content = existing
        if existing and not existing.endswith("\n"):
            new_content += "\n"
        new_content += json.dumps(entry) + "\n"
        try:
            atomic_write_text(log_path, new_content, mode=0o644)
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
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        existing = ""
        if log_path.exists():
            try:
                existing = log_path.read_text(encoding="utf-8")
            except OSError:
                existing = ""
        new_content = existing
        if existing and not existing.endswith("\n"):
            new_content += "\n"
        new_content += json.dumps(entry) + "\n"
        try:
            atomic_write_text(log_path, new_content, mode=0o644)
        except OSError as e:
            return {"ok": False, "error": f"write failed: {e}"}
    return {"ok": True, "id": item_id, "ts": entry["ts"]}
