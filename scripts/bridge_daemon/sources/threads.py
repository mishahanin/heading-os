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
import logging
from datetime import date, datetime, timezone
from scripts.utils.workspace import get_default_tz
from pathlib import Path

# Re-use the pulse.py constants + parser so we have one source of truth.
from .pulse import (
    THREADS_BUSINESS_DIR,
    THREADS_ACTIVE_STATUSES,
    _parse_thread_frontmatter,
)
from scripts.bridge_daemon._safepath import contains_symlink, normalize_rel_path

logger = logging.getLogger(__name__)

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


def list_active_threads(data_root: Path) -> dict:
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
            "truncated": bool (total > the number of rows returned),
            "row_cap": int (THREADS_ROW_CAP, the cap that produced it),
            "data_time": ISO 8601 UTC of most-recent file mtime,
        }

    `truncated` and `row_cap` were emitted by both return statements and
    documented by neither, while this block opens and closes the dict literal
    and so reads as the exhaustive shape. A consumer of the truncation
    behaviour had to read the code to learn the keys existed. Same defect
    `tasks.py` records fixing for `task_key`, `done_filtered` and
    `done_log_count`.
    """
    biz_dir = data_root / THREADS_BUSINESS_DIR
    if not biz_dir.is_dir():
        return {
            # The SAME shape the parsed payload returns; see the note on the
            # matching early return in approvals.py.
            "threads": [], "counts": {}, "bucket_order": [],
            "total": 0, "truncated": False, "row_cap": THREADS_ROW_CAP,
            "data_time": None,
        }
    today = datetime.now(get_default_tz()).date()
    raw_threads: list[dict] = []
    most_recent_mtime: float = 0.0
    for p in biz_dir.glob("*.md"):
        if not p.is_file():
            continue
        try:
            # `read_thread` below refuses a symlinked thread and refuses a body
            # over THREAD_MAX_BYTES; this walker refused neither, so the same
            # files were served by one reader and rejected by the other.
            #
            # `p.is_file()` follows symlinks, so a link planted in
            # threads/business/ was read and its title and frontmatter were
            # published by the listing while the detail view answered "symlinks
            # not allowed" for the very same row. And the cap on
            # THREAD_MAX_BYTES says it bounds "any thread body read" while this
            # loop pulled every body in whole on every poll. studio.py's
            # `_artifact_md_is_readable` names this bug class from its own
            # incident: a guard on one of two readers of the same files is a
            # guard on neither.
            if contains_symlink(biz_dir, p):
                logger.warning(
                    "threads: skipping %s from the /threads listing; it is a "
                    "symlink, and read_thread refuses to open it.", p)
                continue
            stat_result = p.stat()
            if stat_result.st_size > THREAD_MAX_BYTES:
                logger.warning(
                    "threads: skipping %s from the /threads listing; it is %d "
                    "bytes, over the %d-byte cap read_thread applies.",
                    p, stat_result.st_size, THREAD_MAX_BYTES)
                continue
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # UnicodeDecodeError is a ValueError, NOT an OSError, so ONE thread
            # file saved in anything but UTF-8 used to abort this walk with a
            # bare traceback: /threads 500'd, and so did /pulse, whose
            # `threads_state_preview` runs the same read unguarded and is called
            # without a wrapper in `pulse_data`. `library.py` caught both after
            # exactly this measurement on knowledge/, and `read_thread` below
            # already catches both -- the fix landed in the reader and not in
            # the walker. MEASURED 2026-08-31: two thread files, one of them
            # holding a Latin-1 title, and `list_active_threads` raised
            # `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9` instead
            # of returning the one good thread.
            logger.warning(
                "threads: skipping %s from the /threads listing; it is not "
                "readable as UTF-8 text. Re-save it as UTF-8.", p,
                exc_info=True)
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
            "path": str(p.relative_to(data_root)).replace("\\", "/"),
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

    # The per-bucket counts are measured BEFORE the cap, for the same reason
    # `total` above is: they are counts of the active set, not lengths of this
    # page. Counting the sliced list made `sum(counts.values())` disagree with
    # `total` the moment there were more than THREADS_ROW_CAP active threads,
    # and the bucket chips under-reported with nothing saying why. Half of this
    # function measured before the slice and half after; now both do.
    counts: dict = {}
    for t in raw_threads:
        b = t["bucket"]
        counts[b] = counts.get(b, 0) + 1
    bucket_order = [b for b in THREADS_BUCKET_ORDER if counts.get(b, 0) > 0]

    raw_threads = raw_threads[:THREADS_ROW_CAP]

    data_time = (
        datetime.fromtimestamp(most_recent_mtime, tz=timezone.utc).isoformat()
        if most_recent_mtime else None
    )
    return {
        "threads": raw_threads,
        "counts": counts,
        "bucket_order": bucket_order,
        "total": total,
        "truncated": total > len(raw_threads),
        "row_cap": THREADS_ROW_CAP,
        "data_time": data_time,
    }


def read_thread(data_root: Path, rel_path: str) -> dict:
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
    # No prefix is stripped: a leading dot or slash means the caller did not
    # name a served file, and the check below is where that dies. See
    # `normalize_rel_path`.
    rel_path = normalize_rel_path(rel_path)
    if not rel_path.startswith(THREADS_BUSINESS_DIR + "/"):
        return {"ok": False, "error": "path must be under threads/business/"}
    parts = [p for p in rel_path.split("/") if p]
    if any(p == ".." or p.startswith(".") for p in parts):
        return {"ok": False, "error": "invalid path segment"}
    target_raw = data_root / rel_path
    target = target_raw.resolve()
    threads_root = (data_root / THREADS_BUSINESS_DIR).resolve()
    try:
        target.relative_to(threads_root)
    except ValueError:
        return {"ok": False, "error": "path escapes threads dir"}
    if not target.exists():
        return {"ok": False, "error": "not found"}
    try:
        if contains_symlink(data_root / THREADS_BUSINESS_DIR, target_raw):
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
