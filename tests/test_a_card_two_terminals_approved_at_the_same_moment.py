"""One card, two terminals, two copies of the same email.

`approve_and_send` read `card["status"]`, decided the card was sendable, spent
up to 120 seconds inside `send_card`, and only then wrote a status. The read and
the write sat in different locks with the send between them, so two approves of
one `pending` card both passed the read and both sent.

Measured 2026-08-30 before the fix, two threads over one tmp queue: the sender
was invoked TWICE on one card and both calls returned `sent`. The operator
running two terminals - the ordinary case while a long sweep is drafting in one
of them - was one keystroke away from a duplicate to an external counterparty,
which is the single failure a send-gated queue exists to prevent.

The fix is a CLAIM: `claim_card_for_send` checks the status and writes
`sending` inside ONE `_queue_lock`, so the loser of the race finds the card
claimed and is refused. `sending` joins ACTIVE_STATUSES (every lister and the
dedup must see a claimed card) and is excluded from SENDABLE_STATUSES. The batch
executor selects only `approved`, so it cannot pick a claimed card either.

A claim that outlives `STALE_CLAIM_SECONDS` is taken over on the next explicit
`approve`, because nothing inside a claim outlives the sender's own 120-second
timeout - so a claim that old means the process holding it died. A claim whose
`sending_since` cannot be read, or that is stamped in the FUTURE, is NOT taken
over: absent and stale are different facts, and the cost of guessing wrong is a
second copy of a message already delivered.

Nothing here reaches a real send. `send_card` is stubbed at the module seam, and
every gate is verified by making it REFUSE, never by removing it.

Run: python3 -m pytest tests/test_a_card_two_terminals_approved_at_the_same_moment.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon.sources import action_queue as AQS  # noqa: E402


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


AQ = _load("action-queue", "action_queue_claim_cli")
AQX = _load("action-queue-execute", "action_queue_claim_exec")

CARD_ID = "aq-claim-0001"


def _card(**over) -> dict:
    card = {
        "id": CARD_ID,
        "action_type": "email_send",
        "status": "pending",
        "draft_status": "ready_for_review",
        "to": "harriet.vane@example.invalid",
        "subject": "Kestrel Holdings follow-up",
        "draft_body": "Body.",
        "priority": "P2",
        "title": "follow up with Kestrel Holdings",
        "trace_id": "trace-claim-0001",
        "created_at": "2026-08-30T00:00:00+00:00",
    }
    card.update(over)
    return card


@pytest.fixture()
def store(tmp_path):
    """A real queue.json under a tmp data root, plus a reader and a writer."""
    data_root = tmp_path / "data"
    qpath = data_root / "outputs/operations/action-queue/queue.json"
    qpath.parent.mkdir(parents=True, exist_ok=True)

    def put(*cards: dict) -> None:
        qpath.write_text(
            json.dumps({"version": 1, "generated_at": None, "actions": list(cards)}),
            encoding="utf-8")

    def read(action_id: str = CARD_ID) -> dict:
        actions = json.loads(qpath.read_text(encoding="utf-8"))["actions"]
        match = [c for c in actions if c["id"] == action_id]
        assert match, f"no card {action_id} left in the queue"
        return match[0]

    put(_card())
    return {"data_root": data_root, "engine_root": tmp_path / "engine",
            "path": qpath, "put": put, "read": read}


# ==========================================================================
# 1 - THE case: two terminals, one card
# ==========================================================================

def test_two_concurrent_approves_send_the_card_once(store, monkeypatch):
    """The defect, driven at the seam it happened at.

    Both threads enter `approve_and_send` on the same `pending` card. Before the
    claim this counted 2; the send is deliberately slow so the window is wide
    open rather than won by luck.
    """
    sends: list[str] = []
    sends_lock = threading.Lock()

    def slow_send(_engine_root, card):
        with sends_lock:
            sends.append(card["id"])
        time.sleep(0.4)
        return {"result": "sent", "classification": "sent", "attempt": 0}

    monkeypatch.setattr(AQ, "send_card", slow_send)

    results: list[dict] = []
    results_lock = threading.Lock()
    start = threading.Barrier(2)

    def approve():
        start.wait()
        res = AQ.approve_and_send(store["engine_root"], store["data_root"], CARD_ID)
        with results_lock:
            results.append(res)

    threads = [threading.Thread(target=approve) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 2, "the arrangement did not run both approves"
    assert len(sends) == 1, f"one card, {len(sends)} sends"
    outcomes = sorted(r["result"] for r in results)
    assert outcomes == ["blocked", "sent"]
    assert store["read"]()["status"] == "sent"


def test_the_loser_is_told_a_claim_is_running_not_that_nothing_happened(store, monkeypatch):
    """A refusal with no reason reads as a bug. The loser names the claim."""
    monkeypatch.setattr(AQ, "send_card",
                        lambda _e, _c: {"result": "sent", "classification": "sent"})
    AQ.approve_and_send(store["engine_root"], store["data_root"], CARD_ID)
    store["put"](_card(status=AQS.SENDING, sending_since=AQS._now_iso()))

    res = AQ.approve_and_send(store["engine_root"], store["data_root"], CARD_ID)

    assert res["result"] == "blocked"
    assert "may still be sending" in res["error"]
    assert str(int(AQS.STALE_CLAIM_SECONDS)) in res["error"]


# ==========================================================================
# 2 - the claim itself
# ==========================================================================

def test_a_claim_moves_the_card_to_sending_and_stamps_when(store):
    res = AQS.claim_card_for_send(store["data_root"], CARD_ID, AQ.SENDABLE_STATUSES)

    assert res["ok"] is True
    assert res["prev_status"] == "pending"
    on_disk = store["read"]()
    assert on_disk["status"] == AQS.SENDING
    assert AQS.parse_iso(on_disk["sending_since"]) is not None


def test_a_second_claim_inside_the_window_is_refused(store):
    AQS.claim_card_for_send(store["data_root"], CARD_ID, AQ.SENDABLE_STATUSES)

    second = AQS.claim_card_for_send(store["data_root"], CARD_ID, AQ.SENDABLE_STATUSES)

    assert second["ok"] is False
    assert second["status"] == AQS.SENDING
    assert store["read"]()["status"] == AQS.SENDING


def test_a_claim_older_than_the_window_is_taken_over(store):
    """The positive case for takeover. Without it a terminal killed mid-send
    strands its card: `approve` refuses a claimed card and `retry` runs only on
    `send_failed`, so nothing could move it."""
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=AQS.STALE_CLAIM_SECONDS + 60)
    store["put"](_card(status=AQS.SENDING, sending_since=stale.isoformat()))

    res = AQS.claim_card_for_send(store["data_root"], CARD_ID, AQ.SENDABLE_STATUSES)

    assert res["ok"] is True
    assert res["prev_status"] == AQS.SENDING
    fresh = AQS.parse_iso(store["read"]()["sending_since"])
    assert fresh > stale, "the takeover kept the dead claim's timestamp"


def test_a_claim_one_second_inside_the_window_is_still_held(store):
    """The bound needs a case ON the line, not only far either side of it."""
    held_since = datetime.now(timezone.utc) - timedelta(
        seconds=AQS.STALE_CLAIM_SECONDS - 1)
    store["put"](_card(status=AQS.SENDING, sending_since=held_since.isoformat()))

    res = AQS.claim_card_for_send(store["data_root"], CARD_ID, AQ.SENDABLE_STATUSES)

    assert res["ok"] is False


def test_a_claim_with_no_readable_stamp_is_not_taken_over(store):
    """Absent is not stale. Guessing costs a second copy of a delivered mail, so
    the refusal names the way out instead."""
    store["put"](_card(status=AQS.SENDING))

    res = AQS.claim_card_for_send(store["data_root"], CARD_ID, AQ.SENDABLE_STATUSES)

    assert res["ok"] is False
    assert "dismiss it" in res["error"]

    store["put"](_card(status=AQS.SENDING, sending_since="not-a-date"))
    assert AQS.claim_card_for_send(
        store["data_root"], CARD_ID, AQ.SENDABLE_STATUSES)["ok"] is False


def test_a_claim_stamped_in_the_future_is_not_taken_over(store):
    """A future stamp read as a huge age would hand the claim straight over.
    `_claim_age_seconds` returns a NEGATIVE number, which is below the window,
    so the card stays held."""
    future = datetime.now(timezone.utc) + timedelta(days=2)
    store["put"](_card(status=AQS.SENDING, sending_since=future.isoformat()))

    assert AQS._claim_age_seconds(store["read"]()) < 0
    assert AQS.claim_card_for_send(
        store["data_root"], CARD_ID, AQ.SENDABLE_STATUSES)["ok"] is False


def test_a_terminal_card_is_never_claimable(store):
    """The gate is verified by making it REFUSE. A `sent` card claimed again is
    the duplicate this whole file is about."""
    for status in AQS.TERMINAL_STATUSES:
        store["put"](_card(status=status))
        res = AQS.claim_card_for_send(store["data_root"], CARD_ID, AQ.SENDABLE_STATUSES)
        assert res["ok"] is False, f"{status} was claimable"
        assert res["status"] == status
    assert AQS.TERMINAL_STATUSES, "the loop above asserted nothing"


def test_an_approved_card_is_never_claimable(store):
    """`approved` means the BATCH executor owns it. Claiming it synchronously
    would send it here and again from there."""
    store["put"](_card(status="approved"))

    res = AQS.claim_card_for_send(store["data_root"], CARD_ID, AQ.SENDABLE_STATUSES)

    assert res["ok"] is False
    assert res["status"] == "approved"


def test_a_missing_card_is_not_found_rather_than_claimed(store):
    res = AQS.claim_card_for_send(store["data_root"], "no-such-card",
                                  AQ.SENDABLE_STATUSES)
    assert res == {"ok": False, "error": "not found", "status": None}


# ==========================================================================
# 3 - giving the claim back
# ==========================================================================

def test_a_release_puts_the_card_back_and_drops_the_stamp(store):
    claim = AQS.claim_card_for_send(store["data_root"], CARD_ID, AQ.SENDABLE_STATUSES)

    AQS.release_claim(store["data_root"], CARD_ID, claim["prev_status"])

    card = store["read"]()
    assert card["status"] == "pending"
    assert "sending_since" not in card


def test_a_released_takeover_keeps_its_stamp_so_it_can_go_stale_again(store):
    """Releasing a takeover back to `sending` must not strand the card. It keeps
    THIS claim's fresh timestamp and ages out on the normal schedule."""
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=AQS.STALE_CLAIM_SECONDS + 60)
    store["put"](_card(status=AQS.SENDING, sending_since=stale.isoformat()))
    claim = AQS.claim_card_for_send(store["data_root"], CARD_ID, AQ.SENDABLE_STATUSES)
    assert claim["prev_status"] == AQS.SENDING

    AQS.release_claim(store["data_root"], CARD_ID, claim["prev_status"])

    card = store["read"]()
    assert card["status"] == AQS.SENDING
    assert AQS.parse_iso(card["sending_since"]) > stale


def test_a_sender_that_raises_gives_the_claim_back_and_re_raises(store, monkeypatch):
    """`send_card` returns a result for every failure it anticipates. Anything
    else never reached a send, so the card must not be left claimed - no
    `approve` and no `retry` could move it for five minutes."""
    def boom(_engine_root, _card):
        raise RuntimeError("the sender module was not importable")

    monkeypatch.setattr(AQ, "send_card", boom)

    with pytest.raises(RuntimeError, match="not importable"):
        AQ.approve_and_send(store["engine_root"], store["data_root"], CARD_ID)

    assert store["read"]()["status"] == "pending"


def test_a_draft_that_is_not_ready_leaves_no_claim_behind(store, monkeypatch):
    """The refusal happens AFTER the claim, on the freshly read card, so it has
    to hand the claim back."""
    store["put"](_card(draft_status="drafting"))
    monkeypatch.setattr(AQ, "send_card",
                        lambda _e, _c: pytest.fail("a not-ready draft was sent"))

    res = AQ.approve_and_send(store["engine_root"], store["data_root"], CARD_ID)

    assert res["result"] == "blocked"
    assert "ready_for_review" in res["error"]
    assert store["read"]()["status"] == "pending"


def test_the_send_reads_the_claimed_card_not_the_earlier_snapshot(store, monkeypatch):
    """The snapshot the resolver returns predates the lock. A draft edited
    between the two used to be sent against a `ready_for_review` that no longer
    held, so the claimed card is what every later check reads."""
    monkeypatch.setattr(AQ, "list_action_queue",
                        lambda _r: {"items": [_card(draft_status="ready_for_review",
                                                    draft_body="STALE")]})
    store["put"](_card(draft_status="drafting", draft_body="FRESH"))
    monkeypatch.setattr(AQ, "send_card",
                        lambda _e, _c: pytest.fail("the stale snapshot was sent"))

    res = AQ.approve_and_send(store["engine_root"], store["data_root"], CARD_ID)

    assert res["result"] == "blocked"


# ==========================================================================
# 4 - the claim is visible to everything that must see it
# ==========================================================================

def test_sending_is_active_and_not_sendable():
    assert AQS.SENDING in AQS.ACTIVE_STATUSES
    assert AQS.SENDING not in AQS.TERMINAL_STATUSES
    assert AQS.SENDING not in AQ.SENDABLE_STATUSES


def test_a_claimed_card_is_still_listed(store):
    """A card that vanishes from the list while it sends is a card the operator
    cannot see is stuck."""
    AQS.claim_card_for_send(store["data_root"], CARD_ID, AQ.SENDABLE_STATUSES)

    env = AQS.list_action_queue(store["data_root"])
    ids = [c["id"] for c in env.get("items", [])]

    assert CARD_ID in ids


def test_the_batch_executor_will_not_pick_a_claimed_card(store, monkeypatch, capsys):
    """The other end of the duplicate. `action-queue-execute.py` selects cards
    whose status IS `approved`; a claim that used `approved` would have handed
    the card straight to it."""
    sends: list[str] = []
    monkeypatch.setattr(AQX, "get_workspace_root", lambda: store["engine_root"])
    monkeypatch.setattr(AQX, "get_outputs_dir", lambda: store["data_root"] / "outputs")
    monkeypatch.setattr(AQX, "send_card",
                        lambda _r, card, now=None: sends.append(card["id"]) or
                        {"action_id": card["id"], "result": "sent",
                         "classification": "sent", "attempt": 0})

    store["put"](_card(status=AQS.SENDING, sending_since=AQS._now_iso()))
    assert AQX.main() == 0
    assert json.loads(capsys.readouterr().out) == []
    assert sends == []

    # The control: the SAME arrangement with `approved` does send, so the empty
    # result above is the status and not a broken harness.
    store["put"](_card(status="approved"))
    assert AQX.main() == 0
    assert sends == [CARD_ID]


def test_a_stranded_claim_still_reaches_the_ops_radar(store):
    """`ops_signals` banded a card by `pending`/`approved`/`send_failed`, and a
    claim is none of those. A claim whose process died never leaves `sending`
    on its own, so the send would have been invisible to the radar forever."""
    from scripts.utils import ops_signals

    store["put"](_card(status=AQS.SENDING, sending_since=AQS._now_iso()))
    claimed = ops_signals.queue_state(store["data_root"])

    store["put"](_card(status="pending"))
    pending = ops_signals.queue_state(store["data_root"])

    store["put"](_card(status="sent"))
    gone = ops_signals.queue_state(store["data_root"])

    assert claimed == pending, "a claimed card counted differently from a pending one"
    assert claimed != gone, "the control failed: every status counted the same"


def test_a_claimed_card_blocks_a_duplicate_deposit(store):
    """Dedup reads ACTIVE_STATUSES. A claimed card is live, so a producer must
    not deposit a second card for the same contact while it sends."""
    AQS.claim_card_for_send(store["data_root"], CARD_ID, AQ.SENDABLE_STATUSES)

    res = AQS.append_cards(store["data_root"], [_card(id="aq-claim-0002")])

    assert res["added"] == 0, "a second card was deposited over a live claim"


# ==========================================================================
# 5 - the ordinary path still works
# ==========================================================================

def test_one_approve_still_sends_and_records(store, monkeypatch):
    """The negative control. A guard that refused everything would pass every
    assertion above and break the command the operator actually types."""
    sends: list[str] = []
    monkeypatch.setattr(AQ, "send_card",
                        lambda _e, card: sends.append(card["id"]) or
                        {"result": "sent", "classification": "sent", "attempt": 0})

    res = AQ.approve_and_send(store["engine_root"], store["data_root"], CARD_ID)

    assert res == {"result": "sent", "action_id": CARD_ID}
    assert sends == [CARD_ID]
    card = store["read"]()
    assert card["status"] == "sent"
    assert card.get("sent_at")


def test_a_failed_send_lands_on_send_failed_and_is_retryable(store, monkeypatch):
    """`send_failed` is in SENDABLE_STATUSES, so the claim must clear off the
    card or the operator's retry would be refused by his own last attempt."""
    monkeypatch.setattr(AQ, "send_card",
                        lambda _e, _c: {"result": "send_failed",
                                        "error": "smtp refused the recipient",
                                        "classification": "transient", "attempt": 1})

    res = AQ.approve_and_send(store["engine_root"], store["data_root"], CARD_ID)

    assert res["result"] == "send_failed"
    assert store["read"]()["status"] == "send_failed"
    assert AQS.claim_card_for_send(
        store["data_root"], CARD_ID, AQ.SENDABLE_STATUSES)["ok"] is True
