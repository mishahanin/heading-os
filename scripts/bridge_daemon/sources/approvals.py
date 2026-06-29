"""Real-data source for the /approvals endpoint.

Walks outputs/communications/email/ for outbound email drafts pending
CEO go/no-go. Each .md file is parsed for its To/Cc/Subject header
block. Phase 1.56 is read-only; sending stays on scripts/send-email.py
to keep the high-blast-radius send path off the dashboard.

Phase 1.71 adds a mark-sent workflow so the CEO can clear a draft from
the queue after sending via scripts/send-email.py. The draft file stays
on disk; the dashboard simply filters it out of the queue. JSONL log
with tombstone undo, mirroring the inbox-dismiss pattern.

A future phase may extend coverage to other draft surfaces (LinkedIn
posts in outputs/content/linkedin/, fundraising first-touches, etc.).
"""
import json
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from scripts.bridge_daemon._atomic import atomic_write_text

EMAIL_DRAFTS_DIR = "outputs/communications/email"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)
APPROVALS_ROW_CAP = 20  # safety cap; CEO unlikely to have more pending
DRAFT_MAX_BYTES = 200_000  # 200 KB upper bound on any single draft body read

# Phase 1.71: sent log for the mark-sent workflow.
SENT_LOG_FILE = "outputs/operations/bridge/approval-sent-log.jsonl"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)
SENT_LOG_MAX_BYTES = 1_000_000
SENT_NOTE_MAX_CHARS = 200
_SENT_LOG_LOCK = threading.Lock()

# Header lines in each draft file. Look for '**Key:** value' until the
# first separator '---' line. Skip the opening H1.
_HDR_RE = re.compile(r"^\*\*([A-Za-z]+):\*\*\s*(.+?)\s*$")


def _normalize_rel_path(rel_path: str) -> str:
    """Lowercase + forward-slash so log entries are comparable across OSes."""
    return rel_path.replace("\\", "/").strip()


def read_sent_log(workspace_root: Path) -> set[str]:
    """Return the set of draft paths the CEO has marked sent.

    Last entry per path wins; an undo tombstone removes the path from
    the set so the draft surfaces again. Mirrors the inbox-dismiss log.
    """
    log_path = workspace_root / SENT_LOG_FILE
    if not log_path.exists():
        return set()
    try:
        if log_path.stat().st_size > SENT_LOG_MAX_BYTES:
            return set()
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return set()
    out: dict[str, dict] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            continue
        path = _normalize_rel_path(path)
        if entry.get("undo") is True:
            out.pop(path, None)
            continue
        out[path] = entry
    return set(out.keys())


def sent_log_recent(workspace_root: Path, limit: int = 20) -> list[dict]:
    """Return the most-recent active sent entries (tombstoned ones omitted).

    Each entry: {path, ts, date, note, filename}. Ordered by ts DESC.
    Used by the /approvals page's "Recently sent" footer so the CEO can
    restore an accidental mark-sent.
    """
    log_path = workspace_root / SENT_LOG_FILE
    if not log_path.exists():
        return []
    try:
        if log_path.stat().st_size > SENT_LOG_MAX_BYTES:
            return []
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return []
    # Last record per path wins (matches read_sent_log semantics).
    active: dict[str, dict] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            continue
        path = _normalize_rel_path(path)
        if entry.get("undo") is True:
            active.pop(path, None)
            continue
        active[path] = entry
    rows = []
    for path, entry in active.items():
        rows.append({
            "path": path,
            "filename": path.rsplit("/", 1)[-1],
            "ts": entry.get("ts", ""),
            "date": entry.get("date", ""),
            "note": entry.get("note", ""),
        })
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[: max(0, int(limit))]


def mark_sent(workspace_root: Path, rel_path: str, note: str = "") -> dict:
    """Append a sent entry for `rel_path`. Returns {ok, path, ts}.

    Path must be under EMAIL_DRAFTS_DIR; otherwise rejected. Note is
    capped at SENT_NOTE_MAX_CHARS and stripped of newlines.
    """
    if not isinstance(rel_path, str) or not rel_path.strip():
        return {"ok": False, "error": "path is required"}
    rel_path = _normalize_rel_path(rel_path)
    if not rel_path.startswith(EMAIL_DRAFTS_DIR + "/"):
        return {"ok": False, "error": "path must be under email drafts dir"}
    safe_note = (note or "").replace("\n", " ").replace("\r", " ").strip()[:SENT_NOTE_MAX_CHARS]
    # Phase 1.80: 'date' is local (CEO calendar day), 'ts' stays UTC.
    now = datetime.now(timezone.utc)
    entry = {
        "path": rel_path,
        "ts": now.isoformat(),
        "date": date.today().isoformat(),
        "note": safe_note,
    }
    log_path = workspace_root / SENT_LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with _SENT_LOG_LOCK:
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
    return {"ok": True, "path": rel_path, "ts": entry["ts"], "date": entry["date"]}


def undo_sent(workspace_root: Path, rel_path: str) -> dict:
    """Tombstone a prior mark-sent for `rel_path`. Idempotent."""
    if not isinstance(rel_path, str) or not rel_path.strip():
        return {"ok": False, "error": "path is required"}
    rel_path = _normalize_rel_path(rel_path)
    now = datetime.now(timezone.utc)
    entry = {"path": rel_path, "undo": True, "ts": now.isoformat()}
    log_path = workspace_root / SENT_LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with _SENT_LOG_LOCK:
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
    return {"ok": True, "path": rel_path, "ts": entry["ts"]}


def _parse_headers(text: str) -> dict:
    """Return {to, cc, subject, body_offset} from the draft header block.

    body_offset is the character index where the body content begins
    (after the first standalone '---' separator).
    """
    headers: dict = {}
    body_offset = 0
    pos = 0
    in_header_block = True
    for raw in text.splitlines(keepends=True):
        line = raw.rstrip()
        pos += len(raw)
        if line == "---":
            body_offset = pos
            break
        if not in_header_block:
            continue
        m = _HDR_RE.match(line)
        if m:
            key = m.group(1).strip().lower()
            val = m.group(2).strip()
            headers[key] = val
    headers["_body_offset"] = body_offset
    return headers


def list_approvals(workspace_root: Path) -> dict:
    """Return pending email drafts as approval items.

    Sort: most-recently-modified first. Cap at APPROVALS_ROW_CAP.

    Returns:
        {
            "items": [
                {
                    "kind": "email-draft",
                    "path": "outputs/communications/email/foo.md",  # leak-guard: ok (docstring return-shape example, not a filesystem path)
                    "filename": str,
                    "title": str,        # H1 from file or filename
                    "to": str,
                    "cc": str,
                    "subject": str,
                    "mtime": ISO 8601 UTC,
                },
                ...
            ],
            "total": int,
            "data_time": ISO 8601 UTC of most-recent mtime or None,
        }
    """
    drafts_dir = workspace_root / EMAIL_DRAFTS_DIR
    if not drafts_dir.is_dir():
        return {"items": [], "total": 0, "sent_count": 0, "data_time": None}
    # Phase 1.71: filter out drafts the CEO has marked sent.
    sent_paths = read_sent_log(workspace_root)
    sent_count = 0
    items: list[dict] = []
    most_recent: float = 0.0
    for p in drafts_dir.glob("*.md"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(workspace_root)).replace("\\", "/")
        if rel in sent_paths:
            sent_count += 1
            continue
        try:
            stat = p.stat()
            # Read just enough to grab headers + H1 (cap bytes for safety).
            with p.open("r", encoding="utf-8") as f:
                head = f.read(4096)
        except OSError:
            continue
        headers = _parse_headers(head)
        # First H1 if present, else filename stem.
        title = ""
        for line in head.splitlines():
            s = line.strip()
            if s.startswith("# ") and not s.startswith("## "):
                title = s[2:].strip()
                break
        if not title:
            title = p.stem.replace("_", " ").replace("-", " ")
        items.append({
            "kind": "email-draft",
            "path": rel,
            "filename": p.name,
            "title": title,
            "to": headers.get("to", ""),
            "cc": headers.get("cc", ""),
            "subject": headers.get("subject", ""),
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "_mtime_ts": stat.st_mtime,
        })
        if stat.st_mtime > most_recent:
            most_recent = stat.st_mtime
    items.sort(key=lambda x: x.pop("_mtime_ts"), reverse=True)
    items = items[:APPROVALS_ROW_CAP]
    data_time = (
        datetime.fromtimestamp(most_recent, tz=timezone.utc).isoformat()
        if most_recent else None
    )
    return {
        "items": items,
        "total": len(items),
        "sent_count": sent_count,
        "data_time": data_time,
    }


def read_draft(workspace_root: Path, rel_path: str) -> dict:
    """Read a single draft file safely.

    Path validation: must start with EMAIL_DRAFTS_DIR, must resolve inside
    that directory, must be a .md file, must not be a symlink, must be
    under DRAFT_MAX_BYTES.

    Returns:
        {"ok": True, "path": rel_path, "content": str, "size": int}
        OR
        {"ok": False, "error": str}
    """
    if not rel_path or not isinstance(rel_path, str):
        return {"ok": False, "error": "missing path"}
    rel_path = rel_path.replace("\\", "/").lstrip("./")
    if not rel_path.startswith(EMAIL_DRAFTS_DIR + "/"):
        return {"ok": False, "error": "path must be under email drafts dir"}
    parts = [p for p in rel_path.split("/") if p]
    if any(p == ".." or p.startswith(".") for p in parts):
        return {"ok": False, "error": "invalid path segment"}
    target = (workspace_root / rel_path).resolve()
    drafts_root = (workspace_root / EMAIL_DRAFTS_DIR).resolve()
    try:
        target.relative_to(drafts_root)
    except ValueError:
        return {"ok": False, "error": "path escapes drafts dir"}
    if not target.exists():
        return {"ok": False, "error": "not found"}
    try:
        if target.is_symlink():
            return {"ok": False, "error": "symlinks not allowed"}
    except OSError:
        return {"ok": False, "error": "stat failed"}
    if not target.is_file():
        return {"ok": False, "error": "not a file"}
    if target.suffix.lower() != ".md":
        return {"ok": False, "error": "only .md files allowed"}
    try:
        size = target.stat().st_size
    except OSError:
        return {"ok": False, "error": "stat failed"}
    if size > DRAFT_MAX_BYTES:
        return {"ok": False, "error": f"file too large ({size} bytes, max {DRAFT_MAX_BYTES})"}
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"read failed: {e}"}
    return {"ok": True, "path": rel_path, "content": content, "size": size}
