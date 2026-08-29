"""Action Queue source + mutate helpers for the bridge daemon (R1).

The Action Queue is where proactive agents (Cold-Sweep, future autonomy)
deposit drafted actions for one-click CEO go/no-go. Backing store:
``outputs/operations/action-queue/queue.json`` (the authoritative card store)
plus ``disposition-log.jsonl`` (an append-only audit/undo trail).

Design (plan 2026-06-03, Design Decisions 3-5; scrutiny L2):

- **These helpers are the only writers of ``queue.json``, and they run in SEVERAL
  processes.** Every write goes through ``append_cards`` / ``apply_status`` /
  ``edit_card`` / ``annotate_card`` / ``undo_card`` here, all of which take
  ``_queue_lock`` and write atomically.

  This paragraph read "the daemon process is the single writer" until
  2026-08-27, and that stopped being true on 2026-06-27 when the queue went
  terminal-native. ``scripts/action-queue.py``, ``scripts/cold-sweep.py`` and
  ``scripts/dead-letter.py`` each import these helpers and run as separate,
  short-lived processes, and the module lock they relied on was a
  ``threading.Lock`` - invisible across a process boundary. Two overlapping runs
  were an ordinary lost update. ``_queue_lock`` now pairs that thread lock with
  an flock on ``queue.json.lock``, so the read-modify-write is serialised
  between processes as well.
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

import contextlib
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from scripts.bridge_daemon._atomic import atomic_write_text
from scripts.bridge_daemon._jsonl import append_jsonl
from scripts.utils import dead_letter, tool_risk, tracing
from scripts.utils.checkpoint_paths import file_lock
from scripts.utils.quarantine import quarantine_file, quarantine_ref
from scripts.utils.timeparse import parse_iso

QUEUE_FILE = "outputs/operations/action-queue/queue.json"  # leak-guard: ok (relative suffix rooted by caller)
DISPOSITION_LOG = "outputs/operations/action-queue/disposition-log.jsonl"  # leak-guard: ok (relative suffix rooted by caller)

COOLDOWN_DAYS = 14            # dismissed contact not re-proposed within this window
PRUNE_TERMINAL_DAYS = 90     # drop terminal cards older than this (bound growth)
ROW_CAP = 100                # max active cards returned to the UI

ACTION_TYPES = ("email_send", "note", "pipeline_update", "alert")

# The CLAIM. A card sits here for the life of one synchronous send and nothing
# else may send it. Measured 2026-08-30 before it existed: two threads calling
# `approve_and_send` on one pending card both read the status, both passed the
# guard, and the sender ran TWICE on one card, each call returning `sent`.
#
# Deliberately NOT `approved`: `action-queue-execute.py` selects cards whose
# status IS `approved`, so claiming with it would hand the same card to the
# batch executor mid-send and rebuild the duplicate from the other end.
SENDING = "sending"

# How long a claim is believed before `approve` may take it over. Longer than
# `SEND_TIMEOUT_S` (120 s in action-queue-execute.py), which bounds the only slow
# step inside a claim, so a live sender is never overrun. Without a ceiling a
# terminal killed mid-send would strand its card in `sending`: `approve` refuses
# a claimed card and `retry` only runs on `send_failed`, so nothing could move
# it. Takeover happens on an explicit operator `approve`, never on its own.
STALE_CLAIM_SECONDS = 300

ACTIVE_STATUSES = ("pending", "approved", SENDING, "send_failed")

# A card is either ACTIVE or TERMINAL; there is no third state, and every writer
# of `status` has to land in one of these two tuples.
#
# `applied` was in neither until 2026-08-28. The daemon's own tier sweep
# (`_sweep_non_gated_cards` in scripts/bridge-daemon.py) stamps it on every
# auto-applied notify card, so those cards were invisible to `list_action_queue`
# (which filters on ACTIVE_STATUSES) AND immune to the prune below (which read
# the literal `("sent", "dismissed")`). They accumulated in the queue file with
# no surface that could show them and no age at which they were dropped, under a
# comment promising bound growth. `undo_card` still found them by id, so the
# reversibility the notify tier is built on survived - the CEO simply had no way
# to see an id to undo.
TERMINAL_STATUSES = ("sent", "dismissed", "applied")
PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2}

# Fields `undo_card` will not write, whatever a card's `prev_field` names.
#
# `prev_field` is producer-supplied data that decides which key gets written, so
# without this list a card stamped `prev_field: "status"` would let an undo set
# a card's status - and `annotate_card` two functions below drops `status` from
# its fields for exactly that reason ("an advisory layer can annotate, never
# approve/dismiss/send"). An undo is not an advisory layer, but it is not a
# state transition either: `status` has exactly three writers, and all of them
# are state transitions (`apply_status`, plus `claim_card_for_send` and
# `release_claim`, which move a card in and out of `SENDING`), and
# `tier` / `action_type` are what `tool_risk.tier_for` bands a card by, so a
# rewrite of either moves a card between the gated and non-gated lanes.
_UNDO_PROTECTED = frozenset({
    "id", "status", "tier", "action_type", "created_at", "trace_id",
})

# In-process lock. It orders the uvicorn threadpool against the daemon-scheduled
# job, and nothing else: a `threading.Lock` is invisible to another PROCESS.
#
# The comment here used to stop at that first sentence, and it was true when the
# bridge daemon was the only writer. Since the queue went terminal-native on
# 2026-06-27 it is not: `scripts/action-queue.py`, `scripts/cold-sweep.py` and
# `scripts/dead-letter.py` each import these helpers and run as SEPARATE,
# short-lived processes. Two of them overlapping is an ordinary lost update -
# both load the same queue, both write it back atomically, and the second write
# erases the first wholesale. The dangerous direction is the one that erases a
# terminal status: a card stamped `sent` that reverts to `approved` is a card
# the CEO can approve and send a second time.
#
# `_queue_lock` below adds the cross-process half. This lock stays: `file_lock`
# is a per-open-file-description flock, so it does not order two threads of one
# process against each other.
_LOCK = threading.Lock()

# Longer than file_lock's 2 s default. That default is tuned for a Stop hook with
# a 90-second budget; a queue mutation is interactive and a card mutation that
# waits ten seconds is far cheaper than one that proceeds unlocked and loses
# another process's write.
QUEUE_LOCK_WAIT_SECONDS = 10.0

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _queue_lock(workspace_root: Path):
    """Hold BOTH locks for one read-modify-write of queue.json.

    Threads first, then the file lock, in that order everywhere - a mixed order
    between two call sites is how a deadlock is built.

    `file_lock` is bounded and never blocks forever: when the wait expires it
    proceeds UNLOCKED and says so on stderr. That is the right trade for a
    backup hook and the wrong silence for this store, so the degraded case also
    gets a log line naming the consequence.
    """
    with _LOCK:
        lock_path = workspace_root / (QUEUE_FILE + ".lock")
        with file_lock(lock_path, wait=QUEUE_LOCK_WAIT_SECONDS,
                       label="action-queue") as held:
            if not held:
                logger.warning(
                    "queue.json lock was busy for %.0fs; writing UNLOCKED. A "
                    "concurrent action-queue, cold-sweep or dead-letter process "
                    "may lose this write or have its own erased.",
                    QUEUE_LOCK_WAIT_SECONDS,
                )
            yield held


# ============================================================
# Store IO (callers hold _queue_lock for any read-modify-write)
# ============================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_queue() -> dict:
    return {"version": 1, "generated_at": None, "actions": []}


def _quarantine_corrupt_queue(path: Path, why: str) -> None:
    """Move an unreadable queue.json aside, loudly, before anything overwrites it.

    ABSENT and CORRUPT both used to return the empty default, and every mutating
    helper then atomically wrote that empty structure back. One torn write or a
    full disk therefore destroyed every pending and approved card -- drafted
    email bodies included -- with no error, no backup and no log line, while the
    endpoint still answered ``{"ok": true}``.

    Renaming costs nothing and keeps the wreck recoverable. The daemon carries
    on with an empty queue, which is the same behaviour as before; the
    difference is that the old cards still exist somewhere.

    WHERE it lands is the second half, and it was wrong until 2026-08-29. The
    wreck was `queue.json.corrupt-<stamp>` NEXT TO the live file: `queue.json`
    is gitignored in the data overlay precisely because it carries recipient
    addresses and whole drafted email bodies, and that name matched no rule in
    either repository. `push-all.py` runs `git add -A`, so one `GET
    /action-queue` over a torn file would have committed every pending draft
    into permanent history. `quarantine_file` puts it in a `.quarantine/`
    sibling, which both repositories ignore whole.
    """
    try:
        target = quarantine_file(path)
    except OSError:
        logger.error(
            "queue.json is unreadable (%s) AND could not be moved aside; the "
            "next write will overwrite it and the pending cards are lost",
            why, exc_info=True,
        )
        return
    logger.error(
        "queue.json was unreadable (%s); moved to %s and starting from an empty "
        "queue. Recover any pending cards from that file by hand.",
        why, quarantine_ref(target),
    )


def _load_queue(workspace_root: Path) -> dict:
    """Read queue.json. Empty default when absent; quarantine when corrupt."""
    path = workspace_root / QUEUE_FILE
    if not path.exists():
        return _empty_queue()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _quarantine_corrupt_queue(path, f"{type(exc).__name__}: {exc}")
        return _empty_queue()
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
        _quarantine_corrupt_queue(path, f"unexpected shape: {type(data).__name__}")
        return _empty_queue()
    return data


def _write_queue(workspace_root: Path, data: dict) -> None:
    data["generated_at"] = _now_iso()
    atomic_write_text(
        workspace_root / QUEUE_FILE,
        json.dumps(data, indent=2, ensure_ascii=False),
        mode=0o600,  # may carry draft bodies + recipient addresses
    )


def _log_event(workspace_root: Path, event: dict) -> None:
    """Append one audit event to disposition-log.jsonl. Caller holds _queue_lock.

    Through the shared `append_jsonl` (O_APPEND), not a read-whole-file plus
    atomic rewrite. The rewrite was O(file size) PER EVENT, so the lifetime cost
    grew quadratically and peak memory was twice the log -- all of it under the
    global `_LOCK`, so a long-lived queue made every card mutation wait on a
    full re-serialisation of its own history. `approvals.py` already appends;
    this was the last rewriter.
    """
    event = {"ts": _now_iso(), "trace_id": tracing.get() or "-", **event}
    try:
        append_jsonl(workspace_root / DISPOSITION_LOG, event, mode=0o600)
    except OSError:
        # Audit trail is best-effort; never fail a mutation on a log write. But
        # a swallowed write is a hole in the trail, so it gets a line.
        logger.warning("could not append to the disposition log", exc_info=True)


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
    with _queue_lock(workspace_root):
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
                # Already live -> skip. Read from ACTIVE_STATUSES, never from a
                # second copy of it: this was the literal ("pending",
                # "approved"), which left out `send_failed`. A send_failed card
                # IS live - every lister shows it, and `SENDABLE_STATUSES` in
                # scripts/action-queue.py includes it, so the CEO can approve it
                # - and a re-deposit of the same dedup key therefore created a
                # SECOND live card for the same contact. Approving both mails
                # the person twice, which is the one outcome this dedup exists
                # to stop.
                if any(c.get("status") in ACTIVE_STATUSES for c in existing):
                    skipped += 1
                    continue
                # Dismissed within cooldown -> skip (re-propose suppression).
                in_cooldown = False
                for c in existing:
                    if c.get("status") == "dismissed":
                        dt = parse_iso(c.get("dismissed_at"))
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
            card.setdefault("trace_id", tracing.get() or "-")
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
            if c.get("status") in TERMINAL_STATUSES:
                stamp = parse_iso(c.get("sent_at") or c.get("dismissed_at")
                                  or c.get("applied_at") or c.get("created_at"))
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
    with _queue_lock(workspace_root):
        data = _load_queue(workspace_root)
        card = _find(data["actions"], action_id)
        if card is None:
            return {"ok": False, "error": "not found"}
        card["status"] = status
        # `applied` joined this map on 2026-08-28. Without a stamp of its own the
        # prune fell back to `created_at`, so an auto-apply that happened today
        # on a card deposited four months ago was eligible for pruning the
        # instant it became terminal. Every terminal status now dates itself.
        stamp_field = {
            "approved": "approved_at",
            "dismissed": "dismissed_at",
            "sent": "sent_at",
            "applied": "applied_at",
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


def _claim_age_seconds(card: dict, now: datetime | None = None) -> float | None:
    """Seconds since this card was claimed, or None when that cannot be read.

    A stamp in the FUTURE returns a negative number rather than a clamped zero,
    and every caller treats "below the stale window" as still held, so a skewed
    stamp refuses the takeover instead of granting it. That is the safe
    direction here: the cost of refusing is a card the operator must dismiss,
    and the cost of granting is a second copy of an email already sent.
    """
    stamp = parse_iso(card.get("sending_since"))
    if stamp is None:
        return None
    now = datetime.now(timezone.utc) if now is None else now
    return (now - stamp).total_seconds()


def claim_card_for_send(workspace_root: Path, action_id: str,
                        sendable, stale_after: float = STALE_CLAIM_SECONDS,
                        now: datetime | None = None) -> dict:
    """Atomically move one card from a sendable status to ``SENDING``.

    THE compare-and-set that closes the concurrent-approve race. The status
    check and the status write happen inside ONE ``_queue_lock``, so a second
    approve of the same card finds it already ``SENDING`` and is refused. Read
    and write used to sit in different locks with a send between them, and two
    approves therefore both passed the check and both sent (measured
    2026-08-30: sender invoked twice, both calls returning ``sent``).

    A claim older than ``stale_after`` is taken over: it can only mean the
    process holding it died, since nothing inside a claim outlives the sender's
    own 120-second timeout. A claim whose ``sending_since`` cannot be read is
    NOT taken over - absent and stale are different facts, and guessing costs a
    duplicate outbound message - so the refusal names ``dismiss`` as the way out.

    Returns {ok: True, card, prev_status} or {ok: False, error, status}.
    """
    if not action_id:
        return {"ok": False, "error": "action_id required", "status": None}
    with _queue_lock(workspace_root):
        data = _load_queue(workspace_root)
        card = _find(data["actions"], action_id)
        if card is None:
            return {"ok": False, "error": "not found", "status": None}
        status = card.get("status")
        if status == SENDING:
            held = _claim_age_seconds(card, now=now)
            if held is None:
                return {"ok": False, "status": status,
                        "error": ("card is already 'sending' and carries no "
                                  "readable claim time, so it cannot be shown "
                                  "abandoned; dismiss it if no send is running")}
            if held < stale_after:
                return {"ok": False, "status": status,
                        "error": (f"another approve claimed this card {int(held)}s "
                                  f"ago and may still be sending; the claim frees "
                                  f"after {int(stale_after)}s")}
        elif status not in sendable:
            return {"ok": False, "status": status,
                    "error": (f"card is {status!r}; approve only sends a card in "
                              f"{sorted(sendable)}")}
        card["status"] = SENDING
        card["sending_since"] = _now_iso()
        _write_queue(workspace_root, data)
        _log_event(workspace_root, {"event": "claim", "action_id": action_id,
                                    "from": status})
        return {"ok": True, "card": dict(card), "prev_status": status}


def release_claim(workspace_root: Path, action_id: str, prev_status: str) -> dict:
    """Undo a claim, for the path where the send never ran.

    Only the sender raising something nobody planned for reaches here. Putting
    the card back where the claim found it reproduces the behaviour that path
    had before claiming existed, rather than inventing a `send_failed` about a
    send that was never attempted.

    ``prev_status`` may itself be ``SENDING`` (this claim was a takeover). The
    card then keeps THIS claim's fresh timestamp and goes stale again on the
    normal schedule, so it is never stranded.
    """
    if not action_id or not prev_status:
        return {"ok": False, "error": "action_id and prev_status required"}
    with _queue_lock(workspace_root):
        data = _load_queue(workspace_root)
        card = _find(data["actions"], action_id)
        if card is None:
            return {"ok": False, "error": "not found"}
        card["status"] = prev_status
        if prev_status != SENDING:
            card.pop("sending_since", None)
        _write_queue(workspace_root, data)
        _log_event(workspace_root, {"event": "claim_released",
                                    "action_id": action_id, "to": prev_status})
    return {"ok": True, "card": card}


def annotate_card(workspace_root: Path, action_id: str, **fields) -> dict:
    """Stamp arbitrary advisory fields onto a card WITHOUT changing its status.

    Distinct from ``apply_status`` (state transitions + the DLQ side-effect) and
    from ``edit_card`` (rewrites an email draft). ``annotate_card`` attaches
    advisory metadata - the R5b pre-approval ``critique`` is the first consumer -
    and is *structurally incapable* of changing ``status``: a ``status`` key in
    ``fields`` is dropped before the write. This preserves the lethal-trifecta
    control - an advisory layer can annotate, never approve/dismiss/send. Atomic
    under ``_queue_lock`` + logged. Returns {ok, card} or {ok: False, error}.
    """
    if not action_id:
        return {"ok": False, "error": "action_id required"}
    fields.pop("status", None)  # an annotation can never be a state transition
    if not fields:
        return {"ok": False, "error": "no fields to annotate"}
    with _queue_lock(workspace_root):
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
    with _queue_lock(workspace_root):
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

    The notify producer stamps ``prev_value`` - a MAPPING of field to pre-edit
    value - on the card *before* it auto-applies:
    ``apply_status(..., prev_value={"stage": "Qualified"},
    applied_value={"stage": "Negotiation"})``. Undo writes each of those keys
    back onto the card and logs an ``undo`` event.

    **Until 2026-08-25 it wrote nothing back.** It popped ``prev_value``, parked
    it under a new key ``restored_value``, and returned
    ``{ok: True, noop: False}`` - so every field the auto-apply had changed kept
    its post-edit value while the caller was told a revert had happened. Both
    this docstring and `.claude/rules/tiered-risk.md` describe the notify tier
    as "auto-applied with a one-click undo", and the undo was a rename. What was
    wrong was the promise, on the one control that makes an auto-apply
    acceptable at all.

    The relabel survives for a ``prev_value`` this cannot act on - a scalar, or
    a mapping naming only protected fields. The value stays recoverable by hand
    under ``restored_value`` and the result says ``restored: False`` rather than
    implying a rollback. A caller must read that field, never ``noop``.

    No-op-safe (scrutiny M2): if ``prev_value`` is absent (the card was never a
    reversible notify apply, or a malformed producer never stamped it), this
    NEVER raises and NEVER corrupts state. It logs an ``undo_noop`` event and
    returns ``{ok: True, noop: True, restored: False, card}``. Returns
    ``{ok: False, error}`` only when the card id is missing or not found.
    """
    if not action_id:
        return {"ok": False, "error": "action_id required"}
    with _queue_lock(workspace_root):
        data = _load_queue(workspace_root)
        card = _find(data["actions"], action_id)
        if card is None:
            return {"ok": False, "error": "not found"}
        if "prev_value" not in card:
            # Nothing to revert. Record the attempt; do not mutate the card.
            _log_event(workspace_root, {"event": "undo_noop", "action_id": action_id})
            return {"ok": True, "noop": True, "restored": False, "card": card}
        prev = card.pop("prev_value")
        # A MAPPING of field -> pre-edit value. That is the shape the producer
        # side already uses: `apply_status(..., prev_value={"stage": "Qualified"},
        # applied_value={"stage": "Negotiation"})`. Writing each key back is the
        # restore the docstring has always described.
        restored_fields = {}
        if isinstance(prev, dict):
            restored_fields = {k: v for k, v in prev.items()
                               if k not in _UNDO_PROTECTED}
            card.update(restored_fields)
        if not restored_fields:
            # A scalar, or a mapping naming nothing this may write. There is
            # nowhere to put it back, so keep it reachable by hand and say
            # plainly that no rollback occurred.
            card["restored_value"] = prev
        restored = bool(restored_fields)
        card["undone_at"] = _now_iso()
        _write_queue(workspace_root, data)
        _log_event(workspace_root, {
            "event": "undo" if restored else "undo_unrestorable",
            "action_id": action_id,
            "fields": sorted(restored_fields) or None})
    return {"ok": True, "noop": False, "restored": restored, "card": card}


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

    Every TERMINAL status is summarised as a count: sent, dismissed, and
    applied. `applied` was counted nowhere until 2026-08-28, so a notify card
    the daemon auto-applied left no trace on any surface the CEO reads.
    `.claude/rules/tiered-risk.md` describes that tier as "auto-applied by the
    daemon, with a one-click undo" - and `undo_card` takes an id, which the CEO
    had no way to see. A count is not the undo affordance, but it is the signal
    that there is something to undo.
    """
    data = _load_queue(workspace_root)
    actions = data.get("actions", [])
    active = [c for c in actions if c.get("status") in ACTIVE_STATUSES]
    sent_count = sum(1 for c in actions if c.get("status") == "sent")
    dismissed_count = sum(1 for c in actions if c.get("status") == "dismissed")
    applied_count = sum(1 for c in actions if c.get("status") == "applied")

    def _sort_key(c: dict):
        status_rank = 0 if c.get("status") == "pending" else 1
        prio = PRIORITY_ORDER.get(c.get("priority", "P3"), 9)
        return (status_rank, prio, c.get("created_at", ""))

    active.sort(key=_sort_key)
    actionable = [c for c in active if _card_tier(c) == "gated"]
    fyi = [c for c in active if _card_tier(c) != "gated"]
    return {
        # NOT capped, and the docstring above says why: the daemon's tier sweep
        # and `scripts/action-queue.py` both walk `items` to find a card by id.
        # `active[:ROW_CAP]` made card 101 invisible to both -- the sweep never
        # applied it and `action-queue.py approve <id>` answered "not found" --
        # and the sort puts the OLDEST, lowest-priority cards past the cap.
        # ROW_CAP still bounds the two UI lanes below, which is what it is for.
        "items": active,
        "total": len(active),
        "actionable": actionable[:ROW_CAP],
        "actionable_total": len(actionable),
        "fyi": fyi[:ROW_CAP],
        "fyi_total": len(fyi),
        "sent_count": sent_count,
        "dismissed_count": dismissed_count,
        "applied_count": applied_count,
        "data_time": data.get("generated_at"),
    }
