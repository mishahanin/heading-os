"""Real-data source for the /threads endpoint.

Walks threads/business/ for active threads. Returns the full active set
sorted by last_touched DESC, sectioned into recency buckets (today /
this week / older) for the dashboard's sectioned-list pattern.

Phase 1.76 is browse + drill-down. The /thread skill remains the
canonical way to open, log to, hold, close, and reopen threads; the
dashboard surface is read-only.

The CEO-only thread subtree (threads outside threads/business/) is
intentionally NOT walked even though the daemon runs on the CEO's
machine - this keeps the bridge sources portable to any future
per-exec workspace.
"""
from datetime import date, datetime, timezone
from pathlib import Path

# Re-use the pulse.py constants + parser so we have one source of truth.
from .pulse import (
    THREADS_BUSINESS_DIR,
    THREADS_ACTIVE_STATUSES,
    _parse_thread_frontmatter,
)

THREADS_ROW_CAP = 50
THREAD_MAX_BYTES = 200_000  # 200 KB upper bound on any thread body read

# Recency buckets. days_since == 0 -> today, 1..7 -> this_week, else older.
THREADS_BUCKET_ORDER = ["today", "this_week", "older"]
THREADS_BUCKET_LABEL = {
    "today": "Today",
    "this_week": "This week",
    "older": "Older",
}


def _recency_bucket(days_since: int | None) -> str:
    if days_since is None:
        return "older"
    if days_since == 0:
        return "today"
    if days_since <= 7:
        return "this_week"
    return "older"


def list_active_threads(workspace_root: Path) -> dict:
    """Return all active threads with recency sectioning.

    Returns:
        {
            "threads": [
                {
                    "id": str,
                    "title": str,
                    "path": "threads/business/{slug}.md",  # leak-guard: ok (docstring return-shape example, not a filesystem path)
                    "status": str,
                    "type": str,
                    "last_touched": "YYYY-MM-DD",
                    "opened": "YYYY-MM-DD",
                    "days_since": int | None,
                    "bucket": "today" | "this_week" | "older",
                },
                ...
            ] sorted by days_since ASC (None last), capped at THREADS_ROW_CAP,
            "counts": {"today": N, "this_week": N, "older": N},
            "bucket_order": list[str] (only buckets with entries),
            "total": int (pre-cap),
            "data_time": ISO 8601 UTC of most-recent file mtime,
        }
    """
    biz_dir = workspace_root / THREADS_BUSINESS_DIR
    if not biz_dir.is_dir():
        return {
            "threads": [], "counts": {}, "bucket_order": [],
            "total": 0, "data_time": None,
        }
    today = date.today()
    raw_threads: list[dict] = []
    most_recent_mtime: float = 0.0
    for p in biz_dir.glob("*.md"):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
            stat_result = p.stat()
        except OSError:
            continue
        fm = _parse_thread_frontmatter(text)
        if not fm:
            continue
        status = (fm.get("status") or "").lower()
        if status not in THREADS_ACTIVE_STATUSES:
            continue
        last_touched_raw = fm.get("last_touched") or fm.get("opened") or ""
        opened_raw = fm.get("opened") or ""
        last_touched_date = None
        if last_touched_raw:
            try:
                last_touched_date = date.fromisoformat(last_touched_raw[:10])
            except ValueError:
                last_touched_date = None
        days_since = (today - last_touched_date).days if last_touched_date else None
        bucket = _recency_bucket(days_since)
        raw_threads.append({
            "id": fm.get("id", p.stem),
            "title": fm.get("title") or p.stem,
            "path": str(p.relative_to(workspace_root)).replace("\\", "/"),
            "status": status,
            "type": fm.get("type", ""),
            "last_touched": last_touched_raw,
            "opened": opened_raw,
            "days_since": days_since,
            "bucket": bucket,
        })
        if stat_result.st_mtime > most_recent_mtime:
            most_recent_mtime = stat_result.st_mtime

    total = len(raw_threads)

    # Sort by days_since ASC; None entries to the end.
    def key(t):
        d = t["days_since"]
        return (d is None, d if d is not None else 999_999, t["title"])
    raw_threads.sort(key=key)
    raw_threads = raw_threads[:THREADS_ROW_CAP]

    counts: dict = {}
    for t in raw_threads:
        b = t["bucket"]
        counts[b] = counts.get(b, 0) + 1
    bucket_order = [b for b in THREADS_BUCKET_ORDER if counts.get(b, 0) > 0]

    data_time = (
        datetime.fromtimestamp(most_recent_mtime, tz=timezone.utc).isoformat()
        if most_recent_mtime else None
    )
    return {
        "threads": raw_threads,
        "counts": counts,
        "bucket_order": bucket_order,
        "total": total,
        "data_time": data_time,
    }


def read_thread(workspace_root: Path, rel_path: str) -> dict:
    """Read a thread .md file safely.

    Path validation: must start with threads/business/, must resolve
    inside that dir, must be a .md file, no symlinks, under THREAD_MAX_BYTES.

    Returns:
        {"ok": True, "path": rel_path, "content": str, "size": int}
        OR
        {"ok": False, "error": str}
    """
    if not rel_path or not isinstance(rel_path, str):
        return {"ok": False, "error": "missing path"}
    rel_path = rel_path.replace("\\", "/").lstrip("./")
    if not rel_path.startswith(THREADS_BUSINESS_DIR + "/"):
        return {"ok": False, "error": "path must be under threads/business/"}
    parts = [p for p in rel_path.split("/") if p]
    if any(p == ".." or p.startswith(".") for p in parts):
        return {"ok": False, "error": "invalid path segment"}
    target = (workspace_root / rel_path).resolve()
    threads_root = (workspace_root / THREADS_BUSINESS_DIR).resolve()
    try:
        target.relative_to(threads_root)
    except ValueError:
        return {"ok": False, "error": "path escapes threads dir"}
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
    if size > THREAD_MAX_BYTES:
        return {"ok": False, "error": f"file too large ({size} bytes, max {THREAD_MAX_BYTES})"}
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"read failed: {e}"}
    return {"ok": True, "path": rel_path, "content": content, "size": size}
