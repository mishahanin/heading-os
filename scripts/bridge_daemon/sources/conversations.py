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
"""
import json
from datetime import datetime, timezone
from pathlib import Path

LATEST_FETCH_FILE = "outputs/operations/email-intelligence/_latest-fetch.json"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)
CONVERSATIONS_ROW_CAP = 100  # safety cap, but typical fetch is ~30
PARTICIPANT_CAP = 3          # show first N participants then "+ N more"

CONVERSATION_PRIORITIES = ["urgent", "high", "medium", "low"]
PRIORITY_ORDER = {p: i for i, p in enumerate(CONVERSATION_PRIORITIES)}


def _trim_participants(parts: list) -> tuple[list, int]:
    """Return the first PARTICIPANT_CAP names + the count of remaining."""
    if not isinstance(parts, list):
        return [], 0
    trimmed = []
    for p in parts[:PARTICIPANT_CAP]:
        # Each participant can be a dict {name, email} or a bare string.
        if isinstance(p, dict):
            trimmed.append(p.get("name") or p.get("email") or "")
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
                    "priority": "urgent" | "high" | "medium" | "low" | "",
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
            "data_time": ISO 8601 UTC of fetch file mtime (None if missing),
        }
    """
    fetch_path = workspace_root / LATEST_FETCH_FILE
    if not fetch_path.exists():
        return {
            "conversations": [], "counts": {"by_priority": {}, "by_category": {}, "by_direction": {}},
            "total": 0, "data_time": None,
        }
    try:
        text = fetch_path.read_text(encoding="utf-8")
        mtime = fetch_path.stat().st_mtime
    except OSError:
        return {
            "conversations": [], "counts": {"by_priority": {}, "by_category": {}, "by_direction": {}},
            "total": 0, "data_time": None,
        }
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {
            "conversations": [], "counts": {"by_priority": {}, "by_category": {}, "by_direction": {}},
            "total": 0, "data_time": None,
        }
    raw = data.get("conversations", [])
    if not isinstance(raw, list):
        raw = []

    by_priority: dict = {}
    by_category: dict = {}
    by_direction: dict = {}
    out: list[dict] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        analysis = c.get("analysis") or {}
        crm = c.get("crm_context") or {}
        parts, extra = _trim_participants(c.get("participants") or [])
        priority = (c.get("priority") or analysis.get("priority") or "").lower().strip()
        category = (analysis.get("category") or "").strip()
        direction = (c.get("direction") or "").lower().strip()
        summary = analysis.get("summary") or ""
        if isinstance(summary, str) and len(summary) > 200:
            summary = summary[:200].rstrip() + "..."
        out.append({
            "id": c.get("id") or "",
            "topic": c.get("topic") or "(no subject)",
            "direction": direction,
            "priority": priority,
            "category": category,
            "message_count": int(c.get("message_count") or 0),
            "latest_datetime": c.get("latest_datetime") or "",
            "participants": parts,
            "participants_extra": extra,
            "summary": summary,
            "contact_name": crm.get("name") or None,
            "contact_company": crm.get("company") or None,
            "is_internal": bool(c.get("is_internal")),
        })
        if priority:
            by_priority[priority] = by_priority.get(priority, 0) + 1
        if category:
            by_category[category] = by_category.get(category, 0) + 1
        if direction:
            by_direction[direction] = by_direction.get(direction, 0) + 1

    # Sort by latest_datetime DESC (empty/None to end).
    def sort_key(c):
        ts = c["latest_datetime"]
        return (0 if ts else 1, -1 * _parse_ts(ts))
    out.sort(key=sort_key)
    out = out[:CONVERSATIONS_ROW_CAP]

    data_time = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    return {
        "conversations": out,
        "counts": {
            "by_priority": by_priority,
            "by_category": by_category,
            "by_direction": by_direction,
        },
        "total": len(raw),
        "data_time": data_time,
    }


def _parse_ts(s: str) -> float:
    """Return a float sortable timestamp from an ISO string, or 0.0 on failure."""
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
