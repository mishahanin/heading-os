"""Real-data source for /inbox.

Phase 1.5: adapts to the actual scripts/email-intelligence.py output
schema (conversations dict with topic+last_seen). Phase 1's stub
expected a messages array that doesn't exist.

Phase 1.62 adds a dismiss workflow so the CEO can clear noise from
the dashboard's Inbox surface without touching Outlook. Dismissed
conversation IDs persist to a gitignored jsonl log; reads filter them out.

Phase 1.32 reframes the listing into three priority bands sourced from
the rich _latest-fetch.json analysis (summary + recommended actions per
conversation), replacing the flat now/later zoned list.
"""
import json
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scripts.bridge_daemon._atomic import atomic_write_text
from scripts.utils.paths import get_data_root

# Phase 1.32: priority -> band. P1/P2 need a decision or reply (full cards);
# P3 is analyzed-but-no-action; P4 is low-priority noise (count only).
_PRIORITY_BAND = {"P1": "needs-you", "P2": "needs-you", "P3": "fyi", "P4": "noise"}
PROPOSED_ACTIONS_CAP = 6  # cap recommended actions surfaced per card

# Phase 1.62: dismiss log. Conversations the CEO has explicitly cleared.
DISMISS_LOG_FILE = "outputs/operations/email-intelligence/_dismiss-log.jsonl"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)
DISMISS_LOG_MAX_BYTES = 1_000_000
DISMISS_NOTE_MAX_CHARS = 200
_DISMISS_LOG_LOCK = threading.Lock()

# Phase 1.33: defer log. A conversation deferred to a future date drops
# off the Inbox until that date arrives, then resurfaces in its band.
DEFER_LOG_FILE = "outputs/operations/email-intelligence/_defer-log.jsonl"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)
DEFER_LOG_MAX_BYTES = 1_000_000
_DEFER_LOG_LOCK = threading.Lock()

# Phase 1.33: crm-logged log. Conversations already recorded as a CRM
# interaction - append-only, prevents the dashboard double-logging.
CRM_LOGGED_FILE = "outputs/operations/email-intelligence/_crm-logged.jsonl"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)
CRM_LOGGED_MAX_BYTES = 1_000_000
_CRM_LOGGED_LOCK = threading.Lock()


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def read_dismiss_log(workspace_root: Path) -> set[str]:
    """Return the set of dismissed conversation IDs.

    Last entry per conv_id wins, so a tombstone entry ('undo': True)
    cancels a prior dismiss. Mirrors the mark-sent/undo pattern.
    """
    log_path = workspace_root / DISMISS_LOG_FILE
    if not log_path.exists():
        return set()
    try:
        if log_path.stat().st_size > DISMISS_LOG_MAX_BYTES:
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
        conv_id = entry.get("conv_id")
        if not isinstance(conv_id, str) or not conv_id:
            continue
        if entry.get("undo") is True:
            out.pop(conv_id, None)
            continue
        out[conv_id] = entry
    return set(out.keys())


def dismiss_log_recent(workspace_root: Path, limit: int = 20,
                       data_root: "Path | None" = None) -> list[dict]:
    """Return the most-recent active dismiss entries (tombstoned omitted).

    Each entry: {conv_id, ts, date, note}. Ordered by ts DESC. Used by
    the /inbox 'Recently dismissed' footer so the CEO can restore an
    accidental dismiss.

    Pulls the conversation topic from _latest-fetch.json when present so
    the UI can show a readable label, falling back to conv_id otherwise.

    HEADING OS engine/data split: the dismiss log + the fetch file are DATA,
    so they resolve under ``data_root`` (falls back to ``workspace_root``).
    """
    if data_root is None:
        data_root = get_data_root()
    log_path = data_root / DISMISS_LOG_FILE
    if not log_path.exists():
        return []
    try:
        if log_path.stat().st_size > DISMISS_LOG_MAX_BYTES:
            return []
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return []
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
        conv_id = entry.get("conv_id")
        if not isinstance(conv_id, str) or not conv_id:
            continue
        if entry.get("undo") is True:
            active.pop(conv_id, None)
            continue
        active[conv_id] = entry

    # Look up topics from the latest fetch so the footer can show readable
    # labels. Best-effort; missing or malformed fetch means we fall back
    # to the conv_id string.
    topics: dict = {}
    fetch_path = data_root / "outputs" / "operations" / "email-intelligence" / "_latest-fetch.json"
    if fetch_path.exists():
        try:
            data = json.loads(fetch_path.read_text(encoding="utf-8"))
            for c in data.get("conversations", []) or []:
                if isinstance(c, dict) and c.get("id"):
                    topics[c["id"]] = c.get("topic") or ""
        except (json.JSONDecodeError, OSError):
            pass

    rows = []
    for conv_id, entry in active.items():
        rows.append({
            "conv_id": conv_id,
            "topic": topics.get(conv_id) or conv_id[:80],
            "ts": entry.get("ts", ""),
            "date": entry.get("date", ""),
            "note": entry.get("note", ""),
        })
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[: max(0, int(limit))]


def mark_dismissed(workspace_root: Path, conv_id: str, note: str = "") -> dict:
    """Append a dismiss entry for `conv_id`. Returns {ok, conv_id, ts}."""
    if not isinstance(conv_id, str) or not conv_id.strip():
        return {"ok": False, "error": "conv_id is required"}
    if len(conv_id) > 500:
        return {"ok": False, "error": "conv_id too long"}
    safe_note = (note or "").replace("\n", " ").replace("\r", " ").strip()[:DISMISS_NOTE_MAX_CHARS]
    # Phase 1.80: 'date' is local (CEO calendar day) so today_activity can
    # match it directly; 'ts' stays UTC for ordering.
    now = datetime.now(timezone.utc)
    entry = {
        "conv_id": conv_id,
        "date": date.today().isoformat(),
        "ts": now.isoformat(),
        "note": safe_note,
    }
    log_path = workspace_root / DISMISS_LOG_FILE
    with _DISMISS_LOG_LOCK:
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
    return {"ok": True, "conv_id": conv_id, "ts": entry["ts"]}


def undo_dismissed(workspace_root: Path, conv_id: str) -> dict:
    """Tombstone a prior dismiss for `conv_id`. Idempotent."""
    if not isinstance(conv_id, str) or not conv_id.strip():
        return {"ok": False, "error": "conv_id is required"}
    now = datetime.now(timezone.utc)
    entry = {"conv_id": conv_id, "undo": True, "ts": now.isoformat()}
    log_path = workspace_root / DISMISS_LOG_FILE
    with _DISMISS_LOG_LOCK:
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
    return {"ok": True, "conv_id": conv_id, "ts": entry["ts"]}


# ============================================================
# Phase 1.33: defer + crm-log helpers
# ============================================================

def _parse_date(s: str | None) -> date | None:
    """Parse a YYYY-MM-DD string to a date, or None."""
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _read_jsonl(log_path: Path, max_bytes: int) -> list[dict]:
    """Read a jsonl log into a list of dict entries.

    Tolerant of corrupt lines and oversized files (returns [] if the
    log exceeds max_bytes, matching the dismiss-log guard).
    """
    if not log_path.exists():
        return []
    try:
        if log_path.stat().st_size > max_bytes:
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


def _append_jsonl(log_path: Path, lock: threading.Lock, entry: dict) -> tuple[bool, str | None]:
    """Append one JSON entry as a line to a jsonl log, atomically.

    Returns (True, None) on success or (False, error) on a write
    failure. Mirrors the read-append-atomic-write pattern the dismiss
    log uses.
    """
    with lock:
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
            return False, str(e)
    return True, None


def _fetch_topics(workspace_root: Path) -> dict:
    """Map conv_id -> topic from the latest fetch (best-effort, may be {})."""
    topics: dict = {}
    fetch_path = workspace_root / LATEST_FETCH_FILE
    if not fetch_path.exists():
        return topics
    try:
        data = json.loads(fetch_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return topics
    for c in data.get("conversations", []) or []:
        if isinstance(c, dict) and c.get("id"):
            topics[c["id"]] = c.get("topic") or ""
    return topics


def _active_defers(workspace_root: Path) -> dict:
    """Return {conv_id: latest defer entry}, with undo tombstones applied."""
    active: dict = {}
    for entry in _read_jsonl(workspace_root / DEFER_LOG_FILE, DEFER_LOG_MAX_BYTES):
        conv_id = entry.get("conv_id")
        if not isinstance(conv_id, str) or not conv_id:
            continue
        if entry.get("undo") is True:
            active.pop(conv_id, None)
            continue
        active[conv_id] = entry
    return active


def read_defer_log(workspace_root: Path, today: date | None = None) -> set[str]:
    """Return conv_ids currently deferred (defer_until still in the future).

    A defer whose date has arrived is not returned - the conversation
    resurfaces in its band with no mutation needed.
    """
    today = today or date.today()
    deferred = set()
    for conv_id, entry in _active_defers(workspace_root).items():
        until = _parse_date(entry.get("defer_until"))
        if until is not None and until > today:
            deferred.add(conv_id)
    return deferred


def mark_deferred(workspace_root: Path, conv_id: str, defer_until: str, note: str = "") -> dict:
    """Defer `conv_id` until `defer_until` (YYYY-MM-DD, must be a future date)."""
    if not isinstance(conv_id, str) or not conv_id.strip():
        return {"ok": False, "error": "conv_id is required"}
    if len(conv_id) > 500:
        return {"ok": False, "error": "conv_id too long"}
    until = _parse_date(defer_until)
    if until is None:
        return {"ok": False, "error": "defer_until must be a YYYY-MM-DD date"}
    if until <= date.today():
        return {"ok": False, "error": "defer_until must be a future date"}
    safe_note = (note or "").replace("\n", " ").replace("\r", " ").strip()[:DISMISS_NOTE_MAX_CHARS]
    entry = {
        "conv_id": conv_id,
        "defer_until": until.isoformat(),
        "ts": datetime.now(timezone.utc).isoformat(),
        "note": safe_note,
    }
    ok, err = _append_jsonl(workspace_root / DEFER_LOG_FILE, _DEFER_LOG_LOCK, entry)
    if not ok:
        return {"ok": False, "error": f"write failed: {err}"}
    return {"ok": True, "conv_id": conv_id, "defer_until": entry["defer_until"]}


def undo_deferred(workspace_root: Path, conv_id: str) -> dict:
    """Tombstone a prior defer for `conv_id`. Idempotent."""
    if not isinstance(conv_id, str) or not conv_id.strip():
        return {"ok": False, "error": "conv_id is required"}
    entry = {
        "conv_id": conv_id,
        "undo": True,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    ok, err = _append_jsonl(workspace_root / DEFER_LOG_FILE, _DEFER_LOG_LOCK, entry)
    if not ok:
        return {"ok": False, "error": f"write failed: {err}"}
    return {"ok": True, "conv_id": conv_id}


def defer_log_recent(workspace_root: Path, today: date | None = None, limit: int = 20) -> list[dict]:
    """Return still-deferred conversations, most-recently-set first.

    Each entry: {conv_id, topic, defer_until, ts, note}. Drives the
    'Deferred' footer so the CEO can see and undo a defer.
    """
    today = today or date.today()
    topics = _fetch_topics(workspace_root)
    rows = []
    for conv_id, entry in _active_defers(workspace_root).items():
        until = _parse_date(entry.get("defer_until"))
        if until is None or until <= today:
            continue
        rows.append({
            "conv_id": conv_id,
            "topic": topics.get(conv_id) or conv_id[:80],
            "defer_until": entry.get("defer_until", ""),
            "ts": entry.get("ts", ""),
            "note": entry.get("note", ""),
        })
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[: max(0, int(limit))]


def read_crm_logged(workspace_root: Path) -> set[str]:
    """Return conv_ids already logged as a CRM interaction (append-only set)."""
    out = set()
    for entry in _read_jsonl(workspace_root / CRM_LOGGED_FILE, CRM_LOGGED_MAX_BYTES):
        conv_id = entry.get("conv_id")
        if isinstance(conv_id, str) and conv_id:
            out.add(conv_id)
    return out


def mark_crm_logged(workspace_root: Path, conv_id: str, slug: str = "") -> tuple[bool, str | None]:
    """Record that `conv_id` was logged to CRM. Append-only, no undo."""
    entry = {
        "conv_id": conv_id,
        "slug": slug,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    return _append_jsonl(workspace_root / CRM_LOGGED_FILE, _CRM_LOGGED_LOCK, entry)


def _external_sender(participants: list) -> str:
    """Return the display name of the first 'sender' participant.

    The card byline shows who the conversation is from; falls back to
    the sender email, then an empty string.
    """
    for p in participants:
        if isinstance(p, dict) and p.get("role") == "sender":
            return p.get("name") or p.get("email") or ""
    return ""


def _inbox_row(conv: dict, crm_logged: set[str], now: datetime) -> dict:
    """Project a _latest-fetch.json conversation into a compact Inbox row.

    The row carries everything a banded card renders - subject, the
    analyst summary, recommended actions, CRM/pipeline context - so the
    browser can render a useful card without the drill-down round-trip.
    `crm_logged` is the set of conv_ids already recorded as a CRM
    interaction; the row's `crm_logged` flag disables the dashboard
    button so a conversation cannot be logged twice. `aging` is True
    when the conversation has been unread more than 24h.
    """
    analysis = conv.get("analysis") or {}
    priority = conv.get("priority") or analysis.get("priority") or "P3"
    if priority not in _PRIORITY_BAND:
        priority = "P3"
    crm = conv.get("crm_context") or {}
    pipe = conv.get("pipeline_context") or {}
    actions = analysis.get("proposed_actions")
    actions = actions if isinstance(actions, list) else []
    # Phase 1.34: flag conversations unread more than 24h so nothing the
    # CEO is deliberately holding quietly slips. No age cap - all unread
    # conversations show; aging is a visual mark only.
    ts = _parse_iso(conv.get("latest_datetime"))
    if ts is not None and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    aging = ts is not None and (now - ts) > timedelta(hours=24)
    return {
        "id": conv["id"],
        "aging": aging,
        # Phase 1.32: 'email' is the only source today. Telegram and other
        # channels slot in here without changing the band/card contract.
        "source": "email",
        "subject": conv.get("topic") or "(no subject)",
        "priority": priority,
        "band": _PRIORITY_BAND[priority],
        "category": analysis.get("category", ""),
        "summary": analysis.get("summary", ""),
        "proposed_actions": [str(a) for a in actions][:PROPOSED_ACTIONS_CAP],
        "sender": _external_sender(conv.get("participants") or []),
        "message_count": conv.get("message_count") or 0,
        "latest_datetime": conv.get("latest_datetime") or "",
        # Phase 1.33: contact_slug drives the 'Log to CRM' card action.
        "crm": {
            "name": crm.get("name"),
            "company": crm.get("company"),
            "contact_slug": crm.get("contact_slug"),
        } if crm else None,
        "crm_logged": conv["id"] in crm_logged,
        "pipeline": {"stage": pipe.get("stage"), "est_value": pipe.get("est_value")} if pipe else None,
    }


def read_inbox(workspace_root: Path, now: datetime | None = None,
               data_root: "Path | None" = None) -> dict:
    """Read the analyzed Inbox unread set and return banded conversations.

    Phase 1.34: the source `_latest-fetch.json` is now produced by
    `email-intelligence.py --unread` - it holds exactly the conversations
    unread in Exchange right now, each analyzed. The dashboard therefore
    mirrors the CEO's actual inbox; read or delete a message in Outlook
    and it drops off here on the next refresh.

    Conversations are ranked into three priority bands:

        needs-you  - P1/P2: full cards (summary + recommended actions)
        fyi        - P3: analyzed, no action needed
        noise      - P4: low-priority, count-only in the UI

    dismissed and currently-deferred conversations are filtered out
    (a defer whose date has arrived resurfaces on its own). Each row
    carries an `aging` flag set when the conversation has been unread
    more than 24h.

    Returns empty bands on missing/corrupt fetch (silent degradation;
    the freshness UI surfaces staleness via data_time).

    HEADING OS engine/data split: the fetch file + the dismiss/defer/crm
    logs are DATA, so they resolve under ``data_root`` (falls back to
    ``workspace_root`` when not supplied).
    """
    if data_root is None:
        data_root = get_data_root()
    now = now or datetime.now(timezone.utc)
    today = now.date()
    fetch_file = data_root / "outputs" / "operations" / "email-intelligence" / "_latest-fetch.json"
    dismissed = read_dismiss_log(data_root)
    deferred = read_defer_log(data_root, today)
    crm_logged = read_crm_logged(data_root)

    def _empty(data_time=None):
        return {
            "bands": {"needs-you": [], "fyi": [], "noise": []},
            "counts": {"needs-you": 0, "fyi": 0, "noise": 0},
            "dismissed_count": 0,
            "dismiss_log_count": len(dismissed),
            "deferred_count": 0,
            "defer_log_count": len(deferred),
            "data_time": data_time,
        }

    if not fetch_file.exists():
        return _empty()
    try:
        data = json.loads(fetch_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty()

    conversations = data.get("conversations", [])
    if not isinstance(conversations, list):
        return _empty()
    data_time = (data.get("run_info") or {}).get("timestamp")

    dismissed_count = 0
    deferred_count = 0
    bands: dict = {"needs-you": [], "fyi": [], "noise": []}
    for conv in conversations:
        if not isinstance(conv, dict):
            continue
        conv_id = conv.get("id")
        if not isinstance(conv_id, str) or not conv_id:
            continue
        if conv_id in dismissed:
            dismissed_count += 1
            continue
        if conv_id in deferred:
            deferred_count += 1
            continue
        row = _inbox_row(conv, crm_logged, now)
        bands[row["band"]].append(row)

    # Sort each band most-recent-first; rows with no/garbled ts sort last.
    _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    for band_rows in bands.values():
        band_rows.sort(
            key=lambda r: _parse_iso(r["latest_datetime"]) or _epoch,
            reverse=True,
        )

    return {
        "bands": bands,
        "counts": {k: len(v) for k, v in bands.items()},
        "dismissed_count": dismissed_count,
        # Phase 1.92: total active dismiss entries (incl. conversations not
        # in current fetch). Drives the 'Recently dismissed' footer visibility.
        "dismiss_log_count": len(dismissed),
        # Phase 1.33: deferred_count is convs filtered out of THIS fetch;
        # defer_log_count is every still-deferred conv. Drives the
        # 'Deferred' footer visibility.
        "deferred_count": deferred_count,
        "defer_log_count": len(deferred),
        "data_time": data_time,
    }


# ============================================================
# Phase 1.34: per-conversation drill-down
# ============================================================
# _latest-fetch.json carries the rich payload (priority, summary,
# proposed_actions, commitments, participants) from the most recent
# email-intelligence run. Conversations older than that fetch window
# (default 168h / 7d) are not present here, and the drill-down falls
# back to a "stale - older than last fetch" message.
LATEST_FETCH_FILE = "outputs/operations/email-intelligence/_latest-fetch.json"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)
RAW_EMAIL_SNIPPET_BYTES = 1200  # cap any single raw email body excerpt
MAX_RAW_EMAILS_RETURNED = 5     # cap chain length to avoid huge payloads


def _read_state_conversation(workspace_root: Path, conv_id: str,
                             data_root: "Path | None" = None) -> dict | None:
    """Phase 1.100: fall-back lookup for conversations that aren't in the
    most recent fetch file. The triage state.json keeps a wider rolling
    window than _latest-fetch.json's rich payload, so older conversations
    in the inbox listing have no analysis/CRM context to surface. Return
    the basic info that IS available so the drill-down isn't a dead-end.

    HEADING OS engine/data split: state.json is DATA (resolved under
    ``data_root``; falls back to ``workspace_root`` when not supplied).
    """
    if data_root is None:
        data_root = get_data_root()
    state_file = data_root / "outputs" / "operations" / "email-intelligence" / "state.json"
    if not state_file.exists():
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    convs = data.get("conversations", {})
    if not isinstance(convs, dict):
        return None
    entry = convs.get(conv_id)
    if not isinstance(entry, dict):
        return None
    return {
        "id": conv_id,
        "topic": entry.get("topic", "(no subject)"),
        "direction": entry.get("direction", ""),
        "priority": "",
        "message_count": entry.get("message_count") or 0,
        "latest_datetime": entry.get("last_seen", ""),
        "participants": [],
        "is_internal": False,
        "crm_context": None,
        "pipeline_context": None,
        "analysis": {
            "category": "",
            "summary": "",
            "proposed_actions": [],
            "commitments": [],
            "relationship_signal": "",
        },
        "raw_emails": [],
        "raw_emails_truncated": False,
        # Honest UI hint: this conversation predates the rich fetch window.
        "degraded": True,
        "degraded_reason": "older than last /email-intel fetch (only basic info available)",
    }


def read_conversation(workspace_root: Path, conv_id: str,
                      data_root: "Path | None" = None) -> dict:
    """Look up a single conversation - rich data from _latest-fetch.json
    if present, else degraded fallback from state.json.

    Returns:
        {"ok": True, "conversation": {...trimmed for browser...}}
        {"ok": False, "error": "..."}    (file missing, id missing, etc.)

    The fallback path returns ok=True with a degraded=True flag on the
    conversation so the UI can show the basic info without pretending
    a rich analysis exists.

    HEADING OS engine/data split: the fetch file + state.json are DATA
    (resolved under ``data_root``; falls back to ``workspace_root``).
    """
    if data_root is None:
        data_root = get_data_root()
    if not conv_id or not isinstance(conv_id, str):
        return {"ok": False, "error": "missing conversation id"}
    fetch_path = data_root / LATEST_FETCH_FILE
    if not fetch_path.exists():
        # Fetch missing entirely - try state.json fallback before giving up.
        fallback = _read_state_conversation(data_root, conv_id)
        if fallback:
            return {"ok": True, "conversation": fallback}
        return {"ok": False, "error": "no latest fetch on disk (run /email-intel first)"}
    try:
        data = json.loads(fetch_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"ok": False, "error": f"fetch file unreadable: {e}"}
    conversations = data.get("conversations", [])
    if not isinstance(conversations, list):
        return {"ok": False, "error": "unexpected fetch schema"}
    match = next((c for c in conversations if c.get("id") == conv_id), None)
    if match is None:
        # Phase 1.100: don't error out - try state.json so the UI can show
        # at least the topic + last_seen instead of a blank drill-down.
        fallback = _read_state_conversation(data_root, conv_id)
        if fallback:
            return {"ok": True, "conversation": fallback}
        return {"ok": False, "error": "conversation older than last fetch window"}

    # Trim raw_emails: cap count + truncate body snippets to bound payload.
    raw_emails_in = match.get("raw_emails") or []
    raw_emails_out = []
    for em in raw_emails_in[:MAX_RAW_EMAILS_RETURNED]:
        if not isinstance(em, dict):
            continue
        body = em.get("body") or em.get("snippet") or ""
        if isinstance(body, str) and len(body) > RAW_EMAIL_SNIPPET_BYTES:
            body = body[:RAW_EMAIL_SNIPPET_BYTES] + "..."
        raw_emails_out.append({
            "from": em.get("from", ""),
            "to": em.get("to", []),
            "cc": em.get("cc", []),
            "subject": em.get("subject", ""),
            "datetime": em.get("datetime", ""),
            "body": body,
        })

    analysis = match.get("analysis") or {}
    crm_ctx = match.get("crm_context") or {}
    pipe_ctx = match.get("pipeline_context") or {}
    return {
        "ok": True,
        "conversation": {
            "id": match.get("id"),
            "topic": match.get("topic", ""),
            "direction": match.get("direction", ""),
            "priority": match.get("priority", ""),
            "message_count": match.get("message_count", 0),
            "latest_datetime": match.get("latest_datetime"),
            "participants": match.get("participants") or [],
            "is_internal": match.get("is_internal", False),
            "crm_context": {
                "contact_slug": crm_ctx.get("contact_slug"),
                "name": crm_ctx.get("name"),
                "company": crm_ctx.get("company"),
                "type": crm_ctx.get("type"),
                "last_touch": crm_ctx.get("last_touch"),
                "days_since": crm_ctx.get("days_since"),
                "cadence": crm_ctx.get("cadence"),
            } if crm_ctx else None,
            "pipeline_context": {
                "company": pipe_ctx.get("company"),
                "stage": pipe_ctx.get("stage"),
                "est_value": pipe_ctx.get("est_value"),
            } if pipe_ctx else None,
            "analysis": {
                "category": analysis.get("category", ""),
                "summary": analysis.get("summary", ""),
                "proposed_actions": analysis.get("proposed_actions") or [],
                "commitments": analysis.get("commitments") or [],
                "relationship_signal": analysis.get("relationship_signal", ""),
            },
            "raw_emails": raw_emails_out,
            "raw_emails_truncated": len(raw_emails_in) > MAX_RAW_EMAILS_RETURNED,
        },
    }
