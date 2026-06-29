"""Action Queue source + mutate helpers for the bridge daemon (R1).

The Action Queue is where proactive agents (Cold-Sweep, future autonomy)
deposit drafted actions for one-click CEO go/no-go. Backing store:
``outputs/operations/action-queue/queue.json`` (the authoritative card store)
plus ``disposition-log.jsonl`` (an append-only audit/undo trail).

Design (plan 2026-06-03, Design Decisions 3-5; scrutiny L2):

- **The daemon process is the single writer of ``queue.json``.** Every write
  goes through ``append_cards`` / ``apply_status`` / ``edit_card`` here, all of
  which hold the module ``_LOCK`` and write atomically. Two depositors share
  these helpers: the deposit endpoint (external/manual callers POST), and the
  daemon-scheduled Cold-Sweep job (calls ``append_cards`` in-process - no
  self-HTTP). No second process writes the file (the executor only prints
  stdout; the spawning daemon job applies status here).
- **``append_cards`` is the sole dedup authority.** Callers never pre-dedup. A
  card is skipped when its contact already has a pending/approved card, or was
  dismissed within ``COOLDOWN_DAYS``.

Card schema (see plan Step 4): ``id, created_at, trace_id, source,
action_type(email_send|note), status(pending|approved|sent|dismissed|
send_failed), priority(P1|P2|P3), title, reasoning, citations[{source,
excerpt}], contact_file``; for ``email_send`` also ``to, subject, draft_body,
draft_status(needs_draft|ready_for_review)``. Mutations stamp ``approved_at /
dismissed_at / sent_at / error``.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from scripts.bridge_daemon._atomic import atomic_write_text
from scripts.utils import dead_letter, tool_risk, trace

QUEUE_FILE = "outputs/operations/action-queue/queue.json"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)
DISPOSITION_LOG = "outputs/operations/action-queue/disposition-log.jsonl"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)

COOLDOWN_DAYS = 14            # dismissed contact not re-proposed within this window
PRUNE_TERMINAL_DAYS = 90     # drop sent/dismissed cards older than this (bound growth)
ROW_CAP = 100                # max active cards returned to the UI

ACTION_TYPES = ("email_send", "note", "pipeline_update", "alert")
ACTIVE_STATUSES = ("pending", "approved", "send_failed")
PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2}

# Single in-process lock. Both the deposit endpoint (uvicorn threadpool) and the
# daemon-scheduled Cold-Sweep job run in the daemon process, so one lock
# serialises every write to queue.json.
_LOCK = threading.Lock()


# ============================================================
# Store IO (callers hold _LOCK for any read-modify-write)
# ============================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_queue(workspace_root: Path) -> dict:
    """Read queue.json. Returns the default empty structure if absent/corrupt."""
    path = workspace_root / QUEUE_FILE
    if not path.exists():
        return {"version": 1, "generated_at": None, "actions": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "generated_at": None, "actions": []}
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
        return {"version": 1, "generated_at": None, "actions": []}
    return data


def _write_queue(workspace_root: Path, data: dict) -> None:
    data["generated_at"] = _now_iso()
    atomic_write_text(
        workspace_root / QUEUE_FILE,
        json.dumps(data, indent=2, ensure_ascii=False),
        mode=0o600,  # may carry draft bodies + recipient addresses
    )


def _log_event(workspace_root: Path, event: dict) -> None:
    """Append one audit event to disposition-log.jsonl. Caller holds _LOCK."""
    event = {"ts": _now_iso(), "trace_id": trace.get() or "-", **event}
    log_path = workspace_root / DISPOSITION_LOG
    log_path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if log_path.exists():
        try:
            existing = log_path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    try:
        atomic_write_text(log_path, existing + json.dumps(event, ensure_ascii=False) + "\n", mode=0o600)
    except OSError:
        pass  # audit trail is best-effort; never fail a mutation on log write


def _parse_iso(s: str | None) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _dedup_key(card: dict) -> str | None:
    """Identity used for dedup: contact_file, else recipient, else title."""
    for k in ("contact_file", "to", "title"):
        v = card.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return None


# ============================================================
# Public helpers (used by app.py endpoints AND the daemon cold-sweep job)
# ============================================================

def append_cards(workspace_root: Path, cards: list[dict]) -> dict:
    """Append cards to the queue with dedup. THE sole dedup authority.

    Skips a card whose dedup key already has a pending/approved card, or was
    dismissed within COOLDOWN_DAYS. Normalises id/created_at/status/trace_id.
    Prunes terminal cards older than PRUNE_TERMINAL_DAYS. Returns
    {ok, added, skipped, ids}.
    """
    if not isinstance(cards, list):
        return {"ok": False, "error": "cards must be a list"}
    now = datetime.now(timezone.utc)
    added_ids: list[str] = []
    skipped = 0
    with _LOCK:
        data = _load_queue(workspace_root)
        actions = data["actions"]

        # Index existing cards by dedup key for O(1)-ish lookup.
        by_key: dict[str, list[dict]] = {}
        for c in actions:
            k = _dedup_key(c)
            if k:
                by_key.setdefault(k, []).append(c)

        for raw in cards:
            if not isinstance(raw, dict):
                skipped += 1
                continue
            atype = raw.get("action_type")
            if atype not in ACTION_TYPES:
                skipped += 1
                continue
            key = _dedup_key(raw)
            if key and key in by_key:
                existing = by_key[key]
                # Already queued (pending or approved) -> skip.
                if any(c.get("status") in ("pending", "approved") for c in existing):
                    skipped += 1
                    continue
                # Dismissed within cooldown -> skip (re-propose suppression).
                in_cooldown = False
                for c in existing:
                    if c.get("status") == "dismissed":
                        dt = _parse_iso(c.get("dismissed_at"))
                        if dt and (now - dt).days < COOLDOWN_DAYS:
                            in_cooldown = True
                            break
                if in_cooldown:
                    skipped += 1
                    continue

            card = dict(raw)
            card["id"] = card.get("id") or uuid.uuid4().hex
            card["created_at"] = card.get("created_at") or _now_iso()
            card["status"] = "pending"
            card["tier"] = tool_risk.tier_for(card["action_type"])
            card.setdefault("trace_id", trace.get() or "-")
            card.setdefault("priority", "P3")
            card.setdefault("citations", [])
            actions.append(card)
            added_ids.append(card["id"])
            if key:
                by_key.setdefault(key, []).append(card)

        # Prune old terminal cards to bound growth.
        cutoff = now
        kept = []
        for c in actions:
            if c.get("status") in ("sent", "dismissed"):
                stamp = _parse_iso(c.get("sent_at") or c.get("dismissed_at") or c.get("created_at"))
                if stamp and (cutoff - stamp).days > PRUNE_TERMINAL_DAYS:
                    continue
            kept.append(c)
        data["actions"] = kept

        _write_queue(workspace_root, data)
        if added_ids:
            _log_event(workspace_root, {"event": "deposit", "added": added_ids, "skipped": skipped})
    return {"ok": True, "added": len(added_ids), "skipped": skipped, "ids": added_ids}


def _find(actions: list[dict], action_id: str) -> dict | None:
    for c in actions:
        if c.get("id") == action_id:
            return c
    return None


def apply_status(workspace_root: Path, action_id: str, status: str,
                 event: str | None = None, **fields) -> dict:
    """Set a card's status (+ optional extra fields) atomically and log it.

    Used by approve/dismiss and by the daemon's executor-result application
    (sent / send_failed). Returns {ok, card} or {ok: False, error}.
    """
    if not action_id:
        return {"ok": False, "error": "action_id required"}
    with _LOCK:
        data = _load_queue(workspace_root)
        card = _find(data["actions"], action_id)
        if card is None:
            return {"ok": False, "error": "not found"}
        card["status"] = status
        stamp_field = {
            "approved": "approved_at",
            "dismissed": "dismissed_at",
            "sent": "sent_at",
        }.get(status)
        if stamp_field:
            card[stamp_field] = _now_iso()
        for k, v in fields.items():
            card[k] = v
        _write_queue(workspace_root, data)
        _log_event(workspace_root, {"event": event or status, "action_id": action_id})
        # A permanently-failed send becomes a durable, trace-keyed dead-letter
        # artifact instead of vanishing (R14). dead_letter.record never raises.
        if status == "send_failed" and fields.get("classification") == "permanent":
            dead_letter.record(
                trace_id=card.get("trace_id") or "-",
                kind=card.get("action_type") or "unknown",
                payload=card,
                classification="permanent",
                error=str(card.get("error") or fields.get("error") or ""),
                workspace_root=workspace_root,
            )
    return {"ok": True, "card": card}


def annotate_card(workspace_root: Path, action_id: str, **fields) -> dict:
    """Stamp arbitrary advisory fields onto a card WITHOUT changing its status.

    Distinct from ``apply_status`` (state transitions + the DLQ side-effect) and
    from ``edit_card`` (rewrites an email draft). ``annotate_card`` attaches
    advisory metadata - the R5b pre-approval ``critique`` is the first consumer -
    and is *structurally incapable* of changing ``status``: a ``status`` key in
    ``fields`` is dropped before the write. This preserves the lethal-trifecta
    control - an advisory layer can annotate, never approve/dismiss/send. Atomic
    under ``_LOCK`` + logged. Returns {ok, card} or {ok: False, error}.
    """
    if not action_id:
        return {"ok": False, "error": "action_id required"}
    fields.pop("status", None)  # an annotation can never be a state transition
    if not fields:
        return {"ok": False, "error": "no fields to annotate"}
    with _LOCK:
        data = _load_queue(workspace_root)
        card = _find(data["actions"], action_id)
        if card is None:
            return {"ok": False, "error": "not found"}
        for k, v in fields.items():
            card[k] = v
        _write_queue(workspace_root, data)
        _log_event(workspace_root, {"event": "annotate", "action_id": action_id})
    return {"ok": True, "card": card}


def edit_card(workspace_root: Path, action_id: str, *, subject: str | None = None,
              draft_body: str | None = None, draft_status: str | None = None) -> dict:
    """Rewrite an email card's subject / draft_body (and optionally flip
    draft_status). Atomic + logged. Returns {ok, card} or {ok: False, error}."""
    if not action_id:
        return {"ok": False, "error": "action_id required"}
    with _LOCK:
        data = _load_queue(workspace_root)
        card = _find(data["actions"], action_id)
        if card is None:
            return {"ok": False, "error": "not found"}
        if subject is not None:
            card["subject"] = subject
        if draft_body is not None:
            card["draft_body"] = draft_body
        if draft_status is not None:
            card["draft_status"] = draft_status
        _write_queue(workspace_root, data)
        _log_event(workspace_root, {"event": "edit", "action_id": action_id})
    return {"ok": True, "card": card}


def approve_card(workspace_root: Path, action_id: str) -> dict:
    """Mark a card approved. Does NOT send - the executor does (off the request
    path). ``note`` cards approve to a no-op disposition."""
    return apply_status(workspace_root, action_id, "approved", event="approved")


def dismiss_card(workspace_root: Path, action_id: str, reason: str = "") -> dict:
    """Tombstone a card (status dismissed). Starts the re-propose cooldown."""
    fields = {}
    safe = (reason or "").replace("\n", " ").replace("\r", " ").strip()[:200]
    if safe:
        fields["dismiss_reason"] = safe
    return apply_status(workspace_root, action_id, "dismissed", event="dismissed", **fields)


def undo_card(workspace_root: Path, action_id: str) -> dict:
    """Revert a ``notify``-tier auto-apply by restoring the card's ``prev_value``.

    The notify producer (R4, future) stamps ``prev_value`` - the pre-edit state -
    on the card *before* it auto-applies. Undo restores that state and logs an
    ``undo`` event.

    No-op-safe (scrutiny M2): if ``prev_value`` is absent (the card was never a
    reversible notify apply, or a malformed producer never stamped it), this
    NEVER raises and NEVER corrupts state. It logs an ``undo_noop`` event and
    returns ``{ok: True, noop: True, card}``. Returns ``{ok: False, error}`` only
    when the card id is missing or not found.
    """
    if not action_id:
        return {"ok": False, "error": "action_id required"}
    with _LOCK:
        data = _load_queue(workspace_root)
        card = _find(data["actions"], action_id)
        if card is None:
            return {"ok": False, "error": "not found"}
        if "prev_value" not in card:
            # Nothing to revert. Record the attempt; do not mutate the card.
            _log_event(workspace_root, {"event": "undo_noop", "action_id": action_id})
            return {"ok": True, "noop": True, "card": card}
        prev = card.pop("prev_value")
        card["restored_value"] = prev
        card["undone_at"] = _now_iso()
        _write_queue(workspace_root, data)
        _log_event(workspace_root, {"event": "undo", "action_id": action_id})
    return {"ok": True, "noop": False, "card": card}


# ============================================================
# Read path (GET /action-queue)
# ============================================================

def _card_tier(card: dict) -> str:
    """Resolve a card's risk tier, recomputing from action_type when the
    stored ``tier`` is absent (legacy cards) so banding is never wrong."""
    tier = card.get("tier")
    if isinstance(tier, str) and tier:
        return tier
    return tool_risk.tier_for(card.get("action_type", ""))


def list_action_queue(workspace_root: Path) -> dict:
    """Return the active queue envelope, banded by risk tier.

    Active = pending / approved / send_failed, pending first, then by priority,
    then created_at.

    ``items`` / ``total`` stay the full active set: the daemon's tier sweep
    (``_sweep_non_gated_cards``) and the send executor both iterate ``items``
    to find notify/note cards to apply or surface, so narrowing it would hide
    those from the daemon. For the UI, the same active set is ALSO published
    pre-split into two lanes so the dashboard never mixes a draft you must
    approve with an FYI note/alert you only read (tiered-risk.md: ``gated``
    sends need a click; ``autonomous`` / ``notify`` items are read-only):

      - ``actionable``: gated tier (``email_send`` / ``telegram_send``) - the
        approve/send lane. ``actionable_total`` counts these.
      - ``fyi``:        autonomous / notify tier (``note`` / ``alert`` /
        ``pipeline_update``) - read-only context. ``fyi_total`` counts these.

    Dismissed and sent cards are summarised as counts.
    """
    data = _load_queue(workspace_root)
    actions = data.get("actions", [])
    active = [c for c in actions if c.get("status") in ACTIVE_STATUSES]
    sent_count = sum(1 for c in actions if c.get("status") == "sent")
    dismissed_count = sum(1 for c in actions if c.get("status") == "dismissed")

    def _sort_key(c: dict):
        status_rank = 0 if c.get("status") == "pending" else 1
        prio = PRIORITY_ORDER.get(c.get("priority", "P3"), 9)
        return (status_rank, prio, c.get("created_at", ""))

    active.sort(key=_sort_key)
    actionable = [c for c in active if _card_tier(c) == "gated"]
    fyi = [c for c in active if _card_tier(c) != "gated"]
    return {
        "items": active[:ROW_CAP],
        "total": len(active),
        "actionable": actionable[:ROW_CAP],
        "actionable_total": len(actionable),
        "fyi": fyi[:ROW_CAP],
        "fyi_total": len(fyi),
        "sent_count": sent_count,
        "dismissed_count": dismissed_count,
        "data_time": data.get("generated_at"),
    }
