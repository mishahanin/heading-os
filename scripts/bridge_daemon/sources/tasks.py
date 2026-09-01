"""Real-data source for the /tasks endpoint.

Parses outputs/operations/viraid/tasks.md - a markdown checklist with
'## Active' and '## Completed' sections. Returns active items sorted by
priority (P1 first), then by due date ASC (overdue first), then date
captured.

Phase 1.90 adds a dashboard mark-done workflow via append-only JSONL
log (mirrors inbox-dismiss). tasks.md stays canonical and the /viraid
skill remains the official task editor; the bridge simply hides done
items from its own surface. Each task gets a derived stable key
(captured | priority | first chars of description) since tasks.md has
no explicit IDs.
"""
import logging
import re
import threading
from datetime import date, datetime, timezone
from scripts.utils.workspace import get_default_tz
from pathlib import Path

from scripts.bridge_daemon._shapes import entry_ts, is_undo

from scripts.bridge_daemon._jsonl import append_jsonl, read_jsonl_capped
from scripts.utils.paths import get_data_root

logger = logging.getLogger(__name__)

PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}

# Phase 1.90: dashboard-side done log. Filters tasks out of /tasks listing
# without touching tasks.md. The /viraid skill remains canonical.
DONE_LOG_FILE = "outputs/operations/viraid/_done-log.jsonl"  # leak-guard: ok (relative suffix rooted by caller)
DONE_LOG_MAX_BYTES = 1_000_000
DONE_NOTE_MAX_CHARS = 200
TASK_KEY_DESC_PREFIX = 64  # chars of description used in the stable key
_DONE_LOG_LOCK = threading.Lock()


def _task_key(captured: str, priority: str, description: str) -> str:
    """Stable identifier for a task row.

    Format: '{captured}|{priority}|{first N chars of description}'.
    If the CEO edits the description meaningfully, the key changes and
    the row reappears in the listing - that's acceptable.
    """
    desc = (description or "").strip()[:TASK_KEY_DESC_PREFIX]
    return f"{captured}|{priority}|{desc}"


def read_done_log(data_root: Path) -> set[str]:
    """Return the set of task keys marked done via the dashboard.

    Last entry per key wins (tombstones restore the row).
    """
    log_path = data_root / DONE_LOG_FILE
    entries, _truncated = read_jsonl_capped(log_path, DONE_LOG_MAX_BYTES)
    out: dict[str, dict] = {}
    for entry in entries:
        key = entry.get("task_key")
        if not isinstance(key, str) or not key:
            continue
        if is_undo(entry):
            out.pop(key, None)
            continue
        out[key] = entry
    return set(out.keys())


def _write_done_entry(data_root: Path, entry: dict) -> dict:
    """Append a JSONL line under the lock, via the shared O_APPEND primitive."""
    log_path = data_root / DONE_LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with _DONE_LOG_LOCK:
        try:
            append_jsonl(log_path, entry)
        except OSError as e:
            return {"ok": False, "error": f"write failed: {e}"}
    return {"ok": True}


def done_log_recent(data_root: Path, limit: int = 20) -> list[dict]:
    """Return the most-recent active done entries (tombstoned omitted).

    Each entry: {task_key, ts, date, note, description (parsed from key)}.
    Ordered by ts DESC. Used by the /tasks 'Recently done' footer so the
    CEO can restore an accidental mark-done.
    """
    log_path = data_root / DONE_LOG_FILE
    entries, _truncated = read_jsonl_capped(log_path, DONE_LOG_MAX_BYTES)
    # Last record per key wins (matches read_done_log semantics).
    active: dict[str, dict] = {}
    for entry in entries:
        key = entry.get("task_key")
        if not isinstance(key, str) or not key:
            continue
        if is_undo(entry):
            active.pop(key, None)
            continue
        active[key] = entry
    rows = []
    for key, entry in active.items():
        # Surface the description prefix from the key for a readable label.
        parts = key.split("|", 2)
        description = parts[2] if len(parts) == 3 else key
        priority = parts[1] if len(parts) >= 2 else ""
        rows.append({
            "task_key": key,
            "description": description,
            "priority": priority,
            "ts": entry_ts(entry),
            "date": entry.get("date", ""),
            "note": entry.get("note", ""),
        })
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[: max(0, int(limit))]


def mark_done(data_root: Path, task_key: str, note: str = "") -> dict:
    """Append a done entry for `task_key`. Returns {ok, task_key, ts, date}."""
    if not isinstance(task_key, str) or not task_key.strip():
        return {"ok": False, "error": "task_key is required"}
    if len(task_key) > 500:
        return {"ok": False, "error": "task_key too long"}
    safe_note = (note or "").replace("\n", " ").replace("\r", " ").strip()[:DONE_NOTE_MAX_CHARS]
    now = datetime.now(timezone.utc)
    entry = {
        "task_key": task_key,
        "date": datetime.now(get_default_tz()).date().isoformat(),  # local day per Phase 1.80
        "ts": now.isoformat(),
        "note": safe_note,
    }
    result = _write_done_entry(data_root, entry)
    if not result["ok"]:
        return result
    return {"ok": True, "task_key": task_key, "ts": entry["ts"], "date": entry["date"]}


def undo_done(data_root: Path, task_key: str) -> dict:
    """Tombstone a prior done entry for `task_key`. Idempotent."""
    if not isinstance(task_key, str) or not task_key.strip():
        return {"ok": False, "error": "task_key is required"}
    now = datetime.now(timezone.utc)
    entry = {"task_key": task_key, "undo": True, "ts": now.isoformat()}
    result = _write_done_entry(data_root, entry)
    if not result["ok"]:
        return result
    return {"ok": True, "task_key": task_key, "ts": entry["ts"]}

# Match a single active checkbox row. Anchored to start of line, allows
# arbitrary whitespace. The 'date' is the YYYY-MM-DD captured at the start
# of the bold marker. Description is everything between the **date** and
# the next ` | ` pipe. Priority is the P-tag inside backticks (P1, P2,..).
# Due date is optional, parsed separately below.
_ACTIVE_RE = re.compile(
    r"^-\s*\[\s*\]\s*\*\*(?P<captured>\d{4}-\d{2}-\d{2})\*\*\s*\|\s*"
    r"`(?P<priority>P\d)`\s*\|\s*(?P<rest>.+?)$"
)

_DUE_RE = re.compile(r"\bDue:\s*(?P<due>\d{4}-\d{2}-\d{2})\b")
_KIND_RE = re.compile(r"\*(?P<kind>[A-Za-z][A-Za-z \+/]*?)\*")
# A kind cell is a segment that is NOTHING BUT `*Kind*`. `startswith("*")` was
# the old test, and it fires on a body segment that merely OPENS with emphasis.
# MEASURED 2026-08-31: the row `Call Zeek | *urgently* about the SOW | *Task* |
# Due: 2026-05-15` came back with the description "Call Zeek", so the half of
# the sentence that says what to do was cut off the /tasks card with nothing
# logged and nothing else carrying it.
_KIND_CELL_RE = re.compile(r"\*[A-Za-z][A-Za-z \+/]*\*\Z")


def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _strip_metadata_suffix(rest: str) -> tuple[str, str | None, str | None]:
    """Extract the user-visible description from the rest of the row.

    Returns (description, kind, source). The 'rest' looks like:
        TASK BODY | *Task* | Source: ... | Due: YYYY-MM-DD
    We want to drop the trailing | *Kind* | Source: ... | Due: ... segments
    but keep the multi-pipe text inside the body. Strategy: split on '|',
    treat first segment as body, last segments as metadata.
    """
    parts = [p.strip() for p in rest.split("|")]
    # Body is everything until we hit a WHOLE-CELL kind (`*Task*`) or a
    # 'Source:' / 'Due:' prefix. The kind test is a full match, not a prefix:
    # see `_KIND_CELL_RE` for the sentence that half a description used to
    # disappear into.
    body_parts = []
    meta_kind = None
    meta_source = None
    for i, part in enumerate(parts):
        if (_KIND_CELL_RE.match(part) or part.lower().startswith("source:")
                or part.lower().startswith("due:")):
            # Metadata starts here. Stop body accumulation.
            for tail in parts[i:]:
                if tail.startswith("*"):
                    km = _KIND_RE.match(tail)
                    if km:
                        meta_kind = km.group("kind").strip()
                elif tail.lower().startswith("source:"):
                    meta_source = tail.split(":", 1)[1].strip()
            break
        body_parts.append(part)
    body = " | ".join(body_parts).strip()
    return body, meta_kind, meta_source


def list_active_tasks(data_root: "Path | None" = None,
                      today: date | None = None) -> dict:
    """Parse viraid tasks.md and return active items.

    Returns:
        {
            "tasks": [
                {
                    "task_key": str,           # the id mark_done/undo_done take
                    "captured": "YYYY-MM-DD",  # date the task was captured
                    "priority": "P1" | "P2" | ...,
                    "description": str,
                    "kind": str or None,        # *Task*, *CRM Action*, etc.
                    "source": str or None,
                    "due": "YYYY-MM-DD" or None,
                    "days_until_due": int or None,  # negative if overdue
                    "is_overdue": bool,
                },
                ...
            ] sorted by (priority, days_until_due, captured),
            "counts": {"P1": N, "P2": N, "P3": N},
            "overdue_count": int,
            "done_filtered": int,      # rows hidden because they are marked done
            "done_log_count": int,     # active entries in the done log
            "data_time": ISO 8601 UTC of file mtime, else None,
        }

    `data_time` is None when tasks.md is absent AND when it is present but
    unreadable, which the older "None if missing" did not cover. The two are
    told apart in the daemon log, not in the return: an unreadable tasks.md is
    NAMED at warning level, an absent one is silent.

    `task_key`, `done_filtered` and `done_log_count` were all returned and none
    of them documented. `task_key` is the worst of the three: the mark-done
    workflow needs it and a caller could not discover it from this block.

    HEADING OS engine/data split: tasks.md is DATA, so it resolves under
    ``data_root``, which falls back to the ``get_data_root()`` seam when not
    supplied. The done log is DATA too and shares that root; both used to sit
    behind a dead leading ``workspace_root``, which is what made an audit read
    the writer and the reader as pointing at different trees. They never did.
    """
    if data_root is None:
        data_root = get_data_root()
    today = today or datetime.now(get_default_tz()).date()
    tasks_md = data_root / "outputs" / "operations" / "viraid" / "tasks.md"

    def _empty() -> dict:
        # `done_filtered` and `done_log_count` are in the Returns block above,
        # and both early exits used to omit them -- so a consumer indexing
        # either one got a KeyError exactly when tasks.md was missing. The done
        # LOG can be non-empty in that state, which is why the count is read
        # rather than hardcoded to 0: the "Recently done" footer still has
        # rows to show.
        return {"tasks": [], "counts": {}, "overdue_count": 0,
                "done_filtered": 0, "done_log_count": len(read_done_log(data_root)),
                "data_time": None}

    if not tasks_md.exists():
        return _empty()

    try:
        text = tasks_md.read_text(encoding="utf-8")
        mtime = tasks_md.stat().st_mtime
    # `UnicodeDecodeError` is a `ValueError` and not an `OSError`, and it is
    # raised INSIDE `read_text` -- so the handler whose whole job is to turn an
    # unreadable tasks.md into `_empty()` never saw it. tasks.md is hand-edited
    # through the /viraid skill and by the operator directly, which is exactly
    # where a stray non-UTF-8 byte comes from. MEASURED 2026-09-01 with one
    # 0xe9 byte in an Active row: `UnicodeDecodeError: invalid continuation
    # byte` raised out of /tasks instead of the empty listing.
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("tasks file %s is unreadable (%s); /tasks reports ZERO "
                       "active tasks, which is not the same as none being due",
                       tasks_md, exc)
        return _empty()

    # Phase 1.90: pull the done-key set up front so we can filter cheaply.
    # The done log is DATA too; read it from the resolved data_root.
    done_keys = read_done_log(data_root)
    done_filtered = 0

    # Find the "## Active" section and walk lines until the next H2 heading.
    in_active = False
    tasks = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_active = stripped.lower().startswith("## active")
            continue
        if not in_active:
            continue
        m = _ACTIVE_RE.match(line)
        if not m:
            continue
        rest = m.group("rest")
        # Strip trailing Due:/Source: metadata before extracting body.
        due_match = _DUE_RE.search(rest)
        due_str = due_match.group("due") if due_match else None
        body, kind, source = _strip_metadata_suffix(rest)
        captured = m.group("captured")
        priority = m.group("priority")
        task_key = _task_key(captured, priority, body)
        if task_key in done_keys:
            done_filtered += 1
            continue
        due_date = _parse_iso_date(due_str)
        days_until = (due_date - today).days if due_date else None
        is_overdue = days_until is not None and days_until < 0
        tasks.append({
            "captured": captured,
            "priority": priority,
            "description": body,
            "task_key": task_key,
            "kind": kind,
            "source": source,
            "due": due_str,
            "days_until_due": days_until,
            "is_overdue": is_overdue,
        })

    # Sort: priority first (P1 ahead), then days_until_due ASC (None last,
    # overdue first because they sort smaller / more negative), then captured ASC.
    def sort_key(t):
        pri = PRIORITY_ORDER.get(t["priority"], 99)
        du = t["days_until_due"]
        # Push None due dates to the end with a large positive number.
        du_key = 99999 if du is None else du
        return (pri, du_key, t["captured"])
    tasks.sort(key=sort_key)

    counts: dict = {}
    for t in tasks:
        counts[t["priority"]] = counts.get(t["priority"], 0) + 1
    overdue_count = sum(1 for t in tasks if t["is_overdue"])

    data_time = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    return {
        "tasks": tasks,
        "counts": counts,
        "overdue_count": overdue_count,
        "done_filtered": done_filtered,
        # Phase 1.91: total active entries in the done log (whether or not
        # the task still matches a row in tasks.md). Drives the
        # 'Recently done' footer's visibility.
        "done_log_count": len(done_keys),
        "data_time": data_time,
    }
