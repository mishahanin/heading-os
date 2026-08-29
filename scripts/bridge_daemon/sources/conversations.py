"""Real-data source for the /conversations endpoint.

A flat, historical view of email conversations from the most recent
email-intelligence fetch. Different from /inbox (which is a Now/Later
triage of the last 24h): Conversations lists ALL conversations in the
current fetch window sorted by latest activity, with category and
priority surfaced for visual scanning.

Reads outputs/operations/email-intelligence/_latest-fetch.json (same
file the /inbox/conversation drill-down already uses). The drill-down
view on the page reuses the existing /inbox/conversation endpoint to
avoid duplicating the per-conversation reader.

Phase 1.88 is read-only. Future phases may add the v8 right-column
context panel (Pipeline / CRM / Outputs / Audit) once the dashboard
has a stable join between conversation_id and pipeline + outputs.

Tests: tests/bridge/test_two_layers_that_disagreed_about_the_same_file.py
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from scripts.bridge_daemon._shapes import as_mapping

logger = logging.getLogger(__name__)

LATEST_FETCH_FILE = "outputs/operations/email-intelligence/_latest-fetch.json"  # leak-guard: ok (relative suffix rooted by caller)
CONVERSATIONS_ROW_CAP = 100  # safety cap, but typical fetch is ~30
PARTICIPANT_CAP = 3          # show first N participants then "+ N more"

# The priorities the email-intelligence pipeline is expected to emit. Kept as
# documentation of the expected vocabulary, NOT as a filter: an unrecognised
# priority is passed through and counted, because silently dropping one would
# hide a pipeline change instead of showing it. (Until 2026-08-24 a
# `PRIORITY_ORDER` map was derived from this list and never read by anything,
# while the docstring below promised a closed set the code never enforced.)
CONVERSATION_PRIORITIES = ["urgent", "high", "medium", "low"]


def _as_text(value) -> str:
    """A JSON value as a string, for fields the fetch file is free to get wrong.

    The fetch is written by a separate pipeline and is hand-editable, so a
    field typed as a string in the schema can arrive as a number, a list or
    null. `(c.get("priority") or "").lower()` raises AttributeError on the
    integer 5, and one such record used to take down the whole /conversations
    page. Non-strings become "" rather than `str(value)`: showing `5` in a
    priority chip invents a value the pipeline never meant.
    """
    return value if isinstance(value, str) else ""


def _as_count(value) -> int:
    """A JSON value as a non-negative int, 0 when it is not one.

    `int("three")` raises ValueError and `int([])` raises TypeError; both
    escaped the JSON guards, which only checked that the FILE parsed.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str):
        try:
            return max(0, int(value.strip()))
        except ValueError:
            return 0
    return 0


def _as_mapping(value) -> dict:
    """A JSON value as a dict, {} when it is not one.

    `c.get("analysis") or {}` returns the STRING when analysis is a string,
    and the next `.get()` raises AttributeError.
    """
    return value if isinstance(value, dict) else {}


def _trim_participants(parts: list) -> tuple[list, int]:
    """Return the first PARTICIPANT_CAP names + the count of remaining."""
    if not isinstance(parts, list):
        return [], 0
    trimmed = []
    for p in parts[:PARTICIPANT_CAP]:
        # Each participant can be a dict {name, email} or a bare string.
        if isinstance(p, dict):
            # Through `_as_text`, like every other field this module takes from
            # the fetch file. The dict's CONTENTS are exactly as hand-editable
            # as the dict itself, so `{"name": 5}` used to append the integer 5,
            # survive the truthiness filter below, and put a non-string into
            # `participants` on the wire -- the one value class `_as_text` was
            # written to neutralise everywhere else in this file.
            trimmed.append(_as_text(p.get("name")) or _as_text(p.get("email")))
        elif isinstance(p, str):
            trimmed.append(p)
    trimmed = [t for t in trimmed if t]
    extra = max(0, len(parts) - PARTICIPANT_CAP)
    return trimmed, extra


def list_conversations(workspace_root: Path) -> dict:
    """Return all conversations from the latest email-intelligence fetch.

    Returns:
        {
            "conversations": [
                {
                    "id": str,
                    "topic": str,
                    "direction": "inbound" | "outbound" | "mixed",
                    "priority": lower-cased, as the fetch supplied it.
                                CONVERSATION_PRIORITIES is the expected
                                vocabulary, not an enforced one -- an
                                unrecognised value passes through and is
                                counted, so a pipeline change shows up here
                                instead of disappearing,
                    "category": str,
                    "message_count": int,
                    "latest_datetime": ISO,
                    "participants": list[str] (capped),
                    "participants_extra": int (overflow),
                    "summary": str (truncated),
                    "contact_name": str | None,
                    "contact_company": str | None,
                    "is_internal": bool,
                },
                ...
            ] sorted by latest_datetime DESC, capped at CONVERSATIONS_ROW_CAP,
            "counts": {
                "by_priority": {priority: N},
                "by_category": {category: N},
                "by_direction": {direction: N},
            },
            "total": int,
            "truncated": bool,
            "data_time": ISO 8601 UTC of fetch file mtime (None if missing),
        }

    ``total`` and the three count maps describe the RETURNED rows, so they
    always agree with ``len(conversations)`` and with each other. They used to
    be measured over the raw fetch instead: ``total`` was ``len(raw)``, which
    counted non-dict entries the loop skips AND everything past
    ``CONVERSATIONS_ROW_CAP``, and the counts were accumulated before the cap.
    A dashboard rendering ``total`` beside the rows therefore showed a number
    the rows could never reach, and the sibling endpoint ``contacts.py``
    defines ``total`` the other way round, so the two disagreed on the word.
    ``truncated`` (and a warning line) is what says rows were dropped; the cap
    is no longer silent.
    """
    fetch_path = workspace_root / LATEST_FETCH_FILE
    if not fetch_path.exists():
        return {
            "conversations": [], "counts": {"by_priority": {}, "by_category": {}, "by_direction": {}},
            "total": 0, "truncated": False, "data_time": None,
        }
    try:
        text = fetch_path.read_text(encoding="utf-8")
        mtime = fetch_path.stat().st_mtime
    except OSError:
        return {
            "conversations": [], "counts": {"by_priority": {}, "by_category": {}, "by_direction": {}},
            "total": 0, "truncated": False, "data_time": None,
        }
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {
            "conversations": [], "counts": {"by_priority": {}, "by_category": {}, "by_direction": {}},
            "total": 0, "truncated": False, "data_time": None,
        }
    raw = as_mapping(data).get("conversations", [])
    if not isinstance(raw, list):
        raw = []

    out: list[dict] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        analysis = _as_mapping(c.get("analysis"))
        crm = _as_mapping(c.get("crm_context"))
        parts, extra = _trim_participants(c.get("participants") or [])
        priority = (_as_text(c.get("priority"))
                    or _as_text(analysis.get("priority"))).lower().strip()
        category = _as_text(analysis.get("category")).strip()
        direction = _as_text(c.get("direction")).lower().strip()
        summary = _as_text(analysis.get("summary"))
        if len(summary) > 200:
            summary = summary[:200].rstrip() + "..."
        out.append({
            "id": _as_text(c.get("id")),
            "topic": _as_text(c.get("topic")) or "(no subject)",
            "direction": direction,
            "priority": priority,
            "category": category,
            "message_count": _as_count(c.get("message_count")),
            "latest_datetime": _as_text(c.get("latest_datetime")),
            "participants": parts,
            "participants_extra": extra,
            "summary": summary,
            "contact_name": _as_text(crm.get("name")) or None,
            "contact_company": _as_text(crm.get("company")) or None,
            "is_internal": bool(c.get("is_internal")),
        })

    # Sort by latest_datetime DESC (empty/None to end).
    def sort_key(c):
        ts = c["latest_datetime"]
        return (0 if ts else 1, -1 * _parse_ts(ts))
    out.sort(key=sort_key)
    truncated = len(out) > CONVERSATIONS_ROW_CAP
    if truncated:
        logger.warning("conversations: %d rows over the cap of %d were dropped "
                       "from this response", len(out) - CONVERSATIONS_ROW_CAP,
                       CONVERSATIONS_ROW_CAP)
    out = out[:CONVERSATIONS_ROW_CAP]

    # Counted AFTER the cap, over exactly the rows being returned.
    by_priority: dict = {}
    by_category: dict = {}
    by_direction: dict = {}
    for row in out:
        if row["priority"]:
            by_priority[row["priority"]] = by_priority.get(row["priority"], 0) + 1
        if row["category"]:
            by_category[row["category"]] = by_category.get(row["category"], 0) + 1
        if row["direction"]:
            by_direction[row["direction"]] = by_direction.get(row["direction"], 0) + 1

    data_time = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    return {
        "conversations": out,
        "counts": {
            "by_priority": by_priority,
            "by_category": by_category,
            "by_direction": by_direction,
        },
        "total": len(out),
        "truncated": truncated,
        "data_time": data_time,
    }


def _parse_ts(s: str) -> float:
    """Return a float sortable timestamp from an ISO string, or 0.0 on failure.

    A stamp with no offset is read as UTC, not as this machine's local time.
    `datetime.fromisoformat("2026-06-01T10:00:00").timestamp()` applies the
    local zone, so on a +04:00 host a naive stamp sorted four hours earlier
    than the identical instant written with a `Z`. Email sources mix the two
    forms, and the result was a silently mis-ordered list with no error.
    """
    if not isinstance(s, str) or not s:
        return 0.0
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()
