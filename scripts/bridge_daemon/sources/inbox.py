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

Tests: tests/bridge/test_two_layers_that_disagreed_about_the_same_file.py
"""
import json
import logging
import threading
from datetime import date, datetime, timedelta, timezone
from scripts.utils.workspace import get_default_tz
from pathlib import Path

from scripts.bridge_daemon._jsonl import append_jsonl, read_jsonl_capped
from scripts.bridge_daemon._shapes import as_mapping, entry_ts, is_undo
from scripts.utils.timeparse import parse_iso

logger = logging.getLogger(__name__)

# Phase 1.32: priority -> band. P1/P2 need a decision or reply (full cards);
# P3 is analyzed-but-no-action; P4 is low-priority noise (count only).
_PRIORITY_BAND = {"P1": "needs-you", "P2": "needs-you", "P3": "fyi", "P4": "noise"}
PROPOSED_ACTIONS_CAP = 6  # cap recommended actions surfaced per card

# Phase 1.62: dismiss log. Conversations the CEO has explicitly cleared.
DISMISS_LOG_FILE = "outputs/operations/email-intelligence/_dismiss-log.jsonl"  # leak-guard: ok (relative suffix rooted by caller)
DISMISS_LOG_MAX_BYTES = 1_000_000
DISMISS_NOTE_MAX_CHARS = 200
_DISMISS_LOG_LOCK = threading.Lock()

# Phase 1.33: defer log. A conversation deferred to a future date drops
# off the Inbox until that date arrives, then resurfaces in its band.
DEFER_LOG_FILE = "outputs/operations/email-intelligence/_defer-log.jsonl"  # leak-guard: ok (relative suffix rooted by caller)
DEFER_LOG_MAX_BYTES = 1_000_000
_DEFER_LOG_LOCK = threading.Lock()

# Phase 1.33: crm-logged log. Conversations already recorded as a CRM
# interaction - append-only, prevents the dashboard double-logging.
CRM_LOGGED_FILE = "outputs/operations/email-intelligence/_crm-logged.jsonl"  # leak-guard: ok (relative suffix rooted by caller)
CRM_LOGGED_MAX_BYTES = 1_000_000
_CRM_LOGGED_LOCK = threading.Lock()


def read_dismiss_log(data_root: Path) -> set[str]:
    """Return the set of dismissed conversation IDs.

    Last entry per conv_id wins, so a tombstone entry ('undo': True)
    cancels a prior dismiss. Mirrors the mark-sent/undo pattern.
    """
    log_path = data_root / DISMISS_LOG_FILE
    entries, _truncated = read_jsonl_capped(log_path, DISMISS_LOG_MAX_BYTES)
    out: dict[str, dict] = {}
    for entry in entries:
        conv_id = entry.get("conv_id")
        if not isinstance(conv_id, str) or not conv_id:
            continue
        if is_undo(entry):
            out.pop(conv_id, None)
            continue
        out[conv_id] = entry
    return set(out.keys())


def dismiss_log_recent(data_root: Path, limit: int = 20) -> list[dict]:
    """Return the most-recent active dismiss entries (tombstoned omitted).

    Each entry: {conv_id, ts, date, note}. Ordered by ts DESC. Used by
    the /inbox 'Recently dismissed' footer so the CEO can restore an
    accidental dismiss.

    Pulls the conversation topic from _latest-fetch.json when present so
    the UI can show a readable label, falling back to conv_id otherwise.

    HEADING OS engine/data split: the dismiss log + the fetch file are DATA,
    so the single root this takes IS the data root (it used to take a ``workspace_root`` too, which the body never read).
    """
    log_path = data_root / DISMISS_LOG_FILE
    entries, _truncated = read_jsonl_capped(log_path, DISMISS_LOG_MAX_BYTES)
    active: dict[str, dict] = {}
    for entry in entries:
        conv_id = entry.get("conv_id")
        if not isinstance(conv_id, str) or not conv_id:
            continue
        if is_undo(entry):
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
            data = as_mapping(json.loads(fetch_path.read_text(encoding="utf-8")))
            for c in data.get("conversations", []) or []:
                if isinstance(c, dict) and c.get("id"):
                    topics[c["id"]] = c.get("topic") or ""
        # `UnicodeDecodeError` is a `ValueError`, not a `json.JSONDecodeError`
        # and not an `OSError`; it fires inside `read_text`. Same file, same
        # gap as `read_conversation` and `read_inbox` below.
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            logger.warning("fetch file %s is unreadable (%s); dismiss-log rows "
                           "fall back to raw conversation ids", fetch_path, exc)

    rows = []
    for conv_id, entry in active.items():
        rows.append({
            "conv_id": conv_id,
            "topic": topics.get(conv_id) or conv_id[:80],
            "ts": entry_ts(entry),
            "date": entry.get("date", ""),
            "note": entry.get("note", ""),
        })
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[: max(0, int(limit))]


def mark_dismissed(data_root: Path, conv_id: str, note: str = "") -> dict:
    """Append a dismiss entry for `conv_id`. Returns {ok, conv_id, ts}."""
    if not isinstance(conv_id, str) or not conv_id.strip():
        return {"ok": False, "error": "conv_id is required"}
    # Stored stripped, because that is what the guard above validated. The raw
    # value used to be written verbatim, so a conv_id arriving with a trailing
    # space passed the check and then matched nothing: the reads compare against
    # the untrimmed `id` in _latest-fetch.json, so the card came straight back
    # and the CEO's dismiss looked like it had done nothing.
    conv_id = conv_id.strip()
    if len(conv_id) > 500:
        return {"ok": False, "error": "conv_id too long"}
    safe_note = (note or "").replace("\n", " ").replace("\r", " ").strip()[:DISMISS_NOTE_MAX_CHARS]
    # Phase 1.80: 'date' is local (CEO calendar day) so today_activity can
    # match it directly; 'ts' stays UTC for ordering.
    now = datetime.now(timezone.utc)
    entry = {
        "conv_id": conv_id,
        "date": datetime.now(get_default_tz()).date().isoformat(),
        "ts": now.isoformat(),
        "note": safe_note,
    }
    log_path = data_root / DISMISS_LOG_FILE
    with _DISMISS_LOG_LOCK:
        try:
            append_jsonl(log_path, entry)
        except OSError as e:
            return {"ok": False, "error": f"write failed: {e}"}
    return {"ok": True, "conv_id": conv_id, "ts": entry["ts"]}


def undo_dismissed(data_root: Path, conv_id: str) -> dict:
    """Tombstone a prior dismiss for `conv_id`. Idempotent."""
    if not isinstance(conv_id, str) or not conv_id.strip():
        return {"ok": False, "error": "conv_id is required"}
    conv_id = conv_id.strip()  # see mark_dismissed: the guard trims, so the write must too
    now = datetime.now(timezone.utc)
    entry = {"conv_id": conv_id, "undo": True, "ts": now.isoformat()}
    log_path = data_root / DISMISS_LOG_FILE
    with _DISMISS_LOG_LOCK:
        try:
            append_jsonl(log_path, entry)
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

    Tolerant of corrupt lines. Over ``max_bytes`` it keeps the NEWEST entries
    rather than returning [] -- an empty result meant every deferred and
    CRM-logged conversation was instantly forgotten while writes kept
    succeeding, with nothing surfaced anywhere.
    """
    entries, _truncated = read_jsonl_capped(log_path, max_bytes)
    out: list[dict] = []
    out.extend(entries)
    return out


def _append_jsonl(log_path: Path, lock: threading.Lock, entry: dict) -> tuple[bool, str | None]:
    """Append one JSON entry as a line to a jsonl log, under `lock`.

    Returns (True, None) on success or (False, error) on a write failure.
    The write itself is `_jsonl.append_jsonl`, a real O_APPEND line; `lock`
    only orders the writers inside this process.
    """
    with lock:
        try:
            append_jsonl(log_path, entry)
        except OSError as e:
            return False, str(e)
    return True, None


def _fetch_topics(data_root: Path) -> dict:
    """Map conv_id -> topic from the latest fetch (best-effort, may be {})."""
    topics: dict = {}
    fetch_path = data_root / LATEST_FETCH_FILE
    if not fetch_path.exists():
        return topics
    try:
        data = as_mapping(json.loads(fetch_path.read_text(encoding="utf-8")))
    # See `read_conversation`: `UnicodeDecodeError` is neither of the two names
    # that were here, and it is raised by `read_text` before `json.loads` runs.
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("fetch file %s is unreadable (%s); topic labels are "
                       "unavailable for this listing", fetch_path, exc)
        return topics
    for c in data.get("conversations", []) or []:
        if isinstance(c, dict) and c.get("id"):
            topics[c["id"]] = c.get("topic") or ""
    return topics


def _active_defers(data_root: Path) -> dict:
    """Return {conv_id: latest defer entry}, with undo tombstones applied."""
    active: dict = {}
    for entry in _read_jsonl(data_root / DEFER_LOG_FILE, DEFER_LOG_MAX_BYTES):
        conv_id = entry.get("conv_id")
        if not isinstance(conv_id, str) or not conv_id:
            continue
        if is_undo(entry):
            active.pop(conv_id, None)
            continue
        active[conv_id] = entry
    return active


def read_defer_log(data_root: Path, today: date | None = None) -> set[str]:
    """Return conv_ids currently deferred (defer_until still in the future).

    A defer whose date has arrived is not returned - the conversation
    resurfaces in its band with no mutation needed.
    """
    today = today or datetime.now(get_default_tz()).date()
    deferred = set()
    for conv_id, entry in _active_defers(data_root).items():
        until = _parse_date(entry.get("defer_until"))
        if until is not None and until > today:
            deferred.add(conv_id)
    return deferred


def mark_deferred(data_root: Path, conv_id: str, defer_until: str, note: str = "") -> dict:
    """Defer `conv_id` until `defer_until` (YYYY-MM-DD, must be a future date)."""
    if not isinstance(conv_id, str) or not conv_id.strip():
        return {"ok": False, "error": "conv_id is required"}
    conv_id = conv_id.strip()  # see mark_dismissed: the guard trims, so the write must too
    if len(conv_id) > 500:
        return {"ok": False, "error": "conv_id too long"}
    until = _parse_date(defer_until)
    if until is None:
        return {"ok": False, "error": "defer_until must be a YYYY-MM-DD date"}
    if until <= datetime.now(get_default_tz()).date():
        return {"ok": False, "error": "defer_until must be a future date"}
    safe_note = (note or "").replace("\n", " ").replace("\r", " ").strip()[:DISMISS_NOTE_MAX_CHARS]
    entry = {
        "conv_id": conv_id,
        "defer_until": until.isoformat(),
        "ts": datetime.now(timezone.utc).isoformat(),
        "note": safe_note,
    }
    ok, err = _append_jsonl(data_root / DEFER_LOG_FILE, _DEFER_LOG_LOCK, entry)
    if not ok:
        return {"ok": False, "error": f"write failed: {err}"}
    return {"ok": True, "conv_id": conv_id, "defer_until": entry["defer_until"]}


def undo_deferred(data_root: Path, conv_id: str) -> dict:
    """Tombstone a prior defer for `conv_id`. Idempotent."""
    if not isinstance(conv_id, str) or not conv_id.strip():
        return {"ok": False, "error": "conv_id is required"}
    conv_id = conv_id.strip()  # see mark_dismissed: the guard trims, so the write must too
    entry = {
        "conv_id": conv_id,
        "undo": True,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    ok, err = _append_jsonl(data_root / DEFER_LOG_FILE, _DEFER_LOG_LOCK, entry)
    if not ok:
        return {"ok": False, "error": f"write failed: {err}"}
    return {"ok": True, "conv_id": conv_id}


def defer_log_recent(data_root: Path, today: date | None = None, limit: int = 20) -> list[dict]:
    """Return still-deferred conversations, most-recently-set first.

    Each entry: {conv_id, topic, defer_until, ts, note}. Drives the
    'Deferred' footer so the CEO can see and undo a defer.
    """
    today = today or datetime.now(get_default_tz()).date()
    topics = _fetch_topics(data_root)
    rows = []
    for conv_id, entry in _active_defers(data_root).items():
        until = _parse_date(entry.get("defer_until"))
        if until is None or until <= today:
            continue
        rows.append({
            "conv_id": conv_id,
            "topic": topics.get(conv_id) or conv_id[:80],
            "defer_until": entry.get("defer_until", ""),
            "ts": entry_ts(entry),
            "note": entry.get("note", ""),
        })
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[: max(0, int(limit))]


def read_crm_logged(data_root: Path) -> set[str]:
    """Return conv_ids already logged as a CRM interaction (append-only set)."""
    out = set()
    for entry in _read_jsonl(data_root / CRM_LOGGED_FILE, CRM_LOGGED_MAX_BYTES):
        conv_id = entry.get("conv_id")
        if isinstance(conv_id, str) and conv_id:
            out.add(conv_id)
    return out


def mark_crm_logged(data_root: Path, conv_id: str, slug: str = "") -> tuple[bool, str | None]:
    """Record that `conv_id` was logged to CRM. Append-only, no undo."""
    entry = {
        "conv_id": conv_id,
        "slug": slug,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    return _append_jsonl(data_root / CRM_LOGGED_FILE, _CRM_LOGGED_LOCK, entry)


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
    # `or {}` only replaces a FALSY value, so `"analysis": "some string"` came
    # through as a string and the next `.get()` raised AttributeError, taking
    # every band down over one malformed conversation. The fetch is written by
    # a separate pipeline and is hand-editable; valid JSON of the wrong shape
    # is the case the json.JSONDecodeError guard above cannot see.
    analysis = conv.get("analysis") if isinstance(conv.get("analysis"), dict) else {}
    priority = conv.get("priority") or analysis.get("priority") or "P3"
    if priority not in _PRIORITY_BAND:
        priority = "P3"
    crm = conv.get("crm_context") if isinstance(conv.get("crm_context"), dict) else {}
    pipe = conv.get("pipeline_context") if isinstance(conv.get("pipeline_context"), dict) else {}
    actions = analysis.get("proposed_actions")
    actions = actions if isinstance(actions, list) else []
    # Phase 1.34: flag conversations unread more than 24h so nothing the
    # CEO is deliberately holding quietly slips. No age cap - all unread
    # conversations show; aging is a visual mark only.
    # parse_iso always returns aware (2026-08-20), so the naive->UTC fixup that
    # used to sit here is gone; `now` is aware and the subtraction is safe.
    ts = parse_iso(conv.get("latest_datetime"))
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


def read_inbox(data_root: Path, now: datetime | None = None) -> dict:
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

    Returns:
        {
            "bands": {"needs-you": [row, ...], "fyi": [...], "noise": [...]},
            "counts": {band: int} - rows per band, after filtering,
            "dismissed_count": int - conversations in THIS fetch filtered out
                               as dismissed,
            "dismiss_log_count": int - every active dismiss entry, including
                                 conversations not in this fetch,
            "deferred_count": int - conversations in THIS fetch filtered out
                              as deferred,
            "defer_log_count": int - every still-deferred conversation,
            "data_time": ISO 8601 from the fetch's run_info, or None,
        }

    That block did not exist until 2026-08-25: the function returned seven
    keys and named none of them, so the two count pairs (which measure
    deliberately different things) had to be reverse-engineered from the body.

    Returns empty bands on missing/corrupt fetch (silent degradation;
    the freshness UI surfaces staleness via data_time).

    HEADING OS engine/data split: the fetch file + the dismiss/defer/crm
    logs are DATA, so the single root this takes IS the data root. It used to
    take a ``workspace_root`` as well; the body never read it, and every
    caller was already passing the data root into that slot.
    """
    now = now or datetime.now(timezone.utc)
    # The operator's calendar day, not UTC's. `mark_deferred` validates
    # "must be a future date" against `get_default_tz()`, and the footer
    # (`defer_log_recent`) filters on it too, so deriving "today" from a UTC
    # `now` made the listing disagree with both of them for the hours between
    # local midnight and UTC midnight: a defer set for tomorrow could already
    # read as expired, or an expired one stay hidden.
    today = now.astimezone(get_default_tz()).date()
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
        data = as_mapping(json.loads(fetch_file.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        # `UnicodeDecodeError` is a `ValueError` and neither an `OSError` nor a
        # `json.JSONDecodeError`, so it walked past both names here. The fetch
        # file is machine-written and a write torn mid-codepoint leaves bytes
        # that will not decode. MEASURED 2026-09-01 with one 0xe9 inside a
        # conversation topic: `read_inbox` raised out of the endpoint (a 500)
        # rather than degrading to `_empty()` the way it does for every other
        # unreadable state. `sources/conversations.py` reads the same file and
        # carried the same gap.
        #
        # The log line is the OTHER half, and it landed a few hours after the
        # handler did. `_empty()` is byte-identical to the answer for a healthy
        # mailbox with no mail in it, so a silent degrade here renders an empty
        # inbox panel that means "this file is unreadable" and looks exactly
        # like one that means "you have no mail". Naming the file is what makes
        # the two distinguishable, and an unread panel that reads as a read one
        # is the shape this whole class of defect is about.
        logger.warning("inbox: cannot read %s, reporting an empty inbox: %s",
                       fetch_file, exc)
        return _empty()

    conversations = data.get("conversations", [])
    if not isinstance(conversations, list):
        return _empty()
    # `or {}` only substitutes when the value is FALSY, so a `"run_info"` that
    # arrived as a string or a list came straight through and the `.get` below
    # raised AttributeError - which no `except (json.JSONDecodeError, OSError)`
    # catches, taking the whole /inbox endpoint down. The fetch is written by a
    # separate pipeline and is hand-editable; `_inbox_row` and `read_conversation`
    # already guard their own reads of it this way.
    data_time = as_mapping(data.get("run_info")).get("timestamp")

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
            key=lambda r: parse_iso(r["latest_datetime"]) or _epoch,
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
LATEST_FETCH_FILE = "outputs/operations/email-intelligence/_latest-fetch.json"  # leak-guard: ok (relative suffix rooted by caller)
RAW_EMAIL_SNIPPET_BYTES = 1200  # cap any single raw email body excerpt
MAX_RAW_EMAILS_RETURNED = 5     # cap chain length to avoid huge payloads


def _read_state_conversation(data_root: Path, conv_id: str) -> dict | None:
    """Phase 1.100: fall-back lookup for conversations that aren't in the
    most recent fetch file. The triage state.json keeps a wider rolling
    window than _latest-fetch.json's rich payload, so older conversations
    in the inbox listing have no analysis/CRM context to surface. Return
    the basic info that IS available so the drill-down isn't a dead-end.

    HEADING OS engine/data split: state.json is DATA (resolved under
    the single root this takes IS the data root (it used to take a ``workspace_root`` too, which the body never read).
    """
    state_file = data_root / "outputs" / "operations" / "email-intelligence" / "state.json"
    if not state_file.exists():
        return None
    try:
        data = as_mapping(json.loads(state_file.read_text(encoding="utf-8")))
    # This is the FALLBACK `read_conversation` reaches for when the fetch file
    # is missing or holds no match, so widening that reader without widening
    # this one leaves the promise unkept on the exact path it degrades onto.
    # `UnicodeDecodeError` is a `ValueError`, raised inside `read_text`.
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("email state at %s is unreadable (%s); the drill-down "
                       "fallback for %s is unavailable", state_file, exc, conv_id)
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


def read_conversation(data_root: Path, conv_id: str) -> dict:
    """Look up a single conversation - rich data from _latest-fetch.json
    if present, else degraded fallback from state.json.

    Returns:
        {"ok": True, "conversation": {...trimmed for browser...}}
        {"ok": False, "error": "..."}    (file missing, id missing, etc.)

    The fallback path returns ok=True with a degraded=True flag on the
    conversation so the UI can show the basic info without pretending
    a rich analysis exists.

    HEADING OS engine/data split: the fetch file + state.json are DATA
    the single root this takes IS the data root (it used to take a ``workspace_root`` too, which the body never read).
    """
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
        data = as_mapping(json.loads(fetch_path.read_text(encoding="utf-8")))
    # `UnicodeDecodeError` subclasses `ValueError`, which makes it a SIBLING of
    # `json.JSONDecodeError` rather than a member of it, and it is not an
    # `OSError`. The decode runs inside `read_text`, before `json.loads` is
    # entered, so neither name here could ever see it. MEASURED 2026-09-01 with
    # one 0xe9 byte in the fetch file: this raised out of the drill-down (a
    # 500) instead of the documented `{"ok": False, ...}`. `read_inbox` above
    # carries the same widening; this reader and its two state.json fallbacks
    # did not, which is finding #1 of this campaign in one file.
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        logger.warning("fetch file %s is unreadable (%s); drill-down for %s "
                       "refused", fetch_path, e, conv_id)
        return {"ok": False, "error": f"fetch file {fetch_path} unreadable: {e}"}
    conversations = data.get("conversations", [])
    if not isinstance(conversations, list):
        return {"ok": False, "error": "unexpected fetch schema"}
    # `isinstance` before `.get`: read_inbox already guards this at its own
    # loop, and this reader consumed the same list without it, so one non-dict
    # element 500'd the drill-down while the listing shrugged it off.
    match = next((c for c in conversations
                  if isinstance(c, dict) and c.get("id") == conv_id), None)
    if match is None:
        # Phase 1.100: don't error out - try state.json so the UI can show
        # at least the topic + last_seen instead of a blank drill-down.
        fallback = _read_state_conversation(data_root, conv_id)
        if fallback:
            return {"ok": True, "conversation": fallback}
        return {"ok": False, "error": "conversation older than last fetch window"}

    # Trim raw_emails: cap count + truncate body snippets to bound payload.
    # A dict here used to raise TypeError on the slice below (dicts are not
    # sliceable); the per-element isinstance guard only helps once the
    # CONTAINER is a list.
    raw_emails_in = match.get("raw_emails")
    raw_emails_in = raw_emails_in if isinstance(raw_emails_in, list) else []
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

    analysis = match.get("analysis") if isinstance(match.get("analysis"), dict) else {}
    crm_ctx = match.get("crm_context") if isinstance(match.get("crm_context"), dict) else {}
    pipe_ctx = (match.get("pipeline_context")
                if isinstance(match.get("pipeline_context"), dict) else {})
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
