"""Tier-routing + DLQ + undo tests for the Action Queue store (R3+R14, Step 5).

Drives the FastAPI app via TestClient against a hermetic temp workspace, plus
direct store-helper calls for the daemon-side transitions the executor applies
in-process. No Exchange, no network, no daemon, no Telegram. The send-email and
telegram subprocesses are never invoked: this step only exercises the store, and
the approve-does-not-send invariant means a send is never spawned here.

Covers the behaviours Step 5 introduces:

  - a ``note`` card is stamped tier ``autonomous`` and is SURFACED read-only -
    the daemon sweep leaves it for the CEO to dismiss, never auto-disposes it
    (CEO decision 2026-06-04: Cold-Sweep deposits cold/drop recommendations as
    notes; auto-dismissing would hide advice meant to be read);
  - an ``email_send`` card is stamped tier ``gated`` and still requires
    approve -> executor (approve-does-not-send preserved, executor selection
    predicate is gated+approved+email_send);
  - a synthetic ``pipeline_update`` (notify) card auto-applies (producer stamps
    prev_value) and ``undo_card`` records an ``undo`` event restoring prev_value;
  - ``undo_card`` is no-op-safe when ``prev_value`` is absent - never raises,
    never corrupts state, logs ``undo_noop`` (scrutiny M2);
  - a permanent ``send_failed`` writes a dead-letter artifact under the temp
    root (DLQ-on-permanent); a transient one does not;
  - a tampered ledger marking ``email_send`` autonomous still resolves
    ``gated`` and the executor selection still refuses an unapproved card -
    the tampered-ledger invariant cannot auto-send.

Run: python3 -m pytest tests/test_action_queue_tiers.py
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.bridge_daemon.app import build_app
from scripts.bridge_daemon.state import State
from scripts.bridge_daemon.sources import action_queue as aq
from scripts.utils import dead_letter, tool_risk

TOKEN = "test-token-aq-tiers"
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def root(tmp_path):
    (tmp_path / "outputs" / "operations" / "action-queue").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def client(root):
    app = build_app(root, State(), TOKEN, "misha", data_root=root)
    return TestClient(app)


def _email_card(to="jane@acme.com", contact="crm/contacts/jane.md", title="Nudge Jane"):
    return {
        "action_type": "email_send", "to": to, "contact_file": contact,
        "title": title, "priority": "P1", "reasoning": "20d overdue",
        "draft_status": "needs_draft",
    }


def _note_card(title="Logged a note"):
    return {"action_type": "note", "title": title, "reasoning": "no-op"}


def _pipeline_card(title="Move Acme to Negotiation"):
    return {"action_type": "pipeline_update", "title": title, "reasoning": "stage advance"}


def _deposit(client, cards):
    return client.post("/action-queue/deposit", json={"cards": cards}, headers=H)


def _list(client):
    return client.get("/action-queue", headers=H).json()


def _card_in_store(root, action_id):
    data = json.loads((root / aq.QUEUE_FILE).read_text(encoding="utf-8"))
    return aq._find(data["actions"], action_id)


def _disposition_events(root):
    log = root / aq.DISPOSITION_LOG
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _executor_selection(root):
    """Replicate the executor's selection predicate: gated+approved+email_send."""
    data = json.loads((root / aq.QUEUE_FILE).read_text(encoding="utf-8"))
    return [c for c in data["actions"]
            if c.get("status") == "approved"
            and c.get("action_type") == "email_send"
            and c.get("tier") == tool_risk.GATED]


# ============================================================
# Tier stamping at append time
# ============================================================

def test_note_card_stamped_autonomous(client, root):
    _deposit(client, [_note_card()])
    card = _list(client)["fyi"][0]  # autonomous -> read-only FYI lane, not 'items'
    assert card["action_type"] == "note"
    assert card["tier"] == "autonomous"


def test_email_card_stamped_gated(client, root):
    _deposit(client, [_email_card()])
    card = _list(client)["items"][0]
    assert card["action_type"] == "email_send"
    assert card["tier"] == "gated"


def _load_daemon_sweep():
    """Import the real _sweep_non_gated_cards from the hyphenated daemon module."""
    import importlib.util
    path = Path(__file__).resolve().parent.parent / "scripts" / "bridge-daemon.py"
    spec = importlib.util.spec_from_file_location("bridge_daemon_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._sweep_non_gated_cards


def test_sweep_leaves_note_surfaced_and_applies_notify(client, root):
    # CEO decision 2026-06-04: the sweep must NOT auto-dismiss note cards
    # (surfaced read-only), but MUST auto-apply notify (pipeline_update) cards.
    _deposit(client, [_note_card(), _pipeline_card()])
    note_id = next(c["id"] for c in _list(client)["fyi"] if c["action_type"] == "note")
    pipe_id = next(c["id"] for c in _list(client)["fyi"] if c["action_type"] == "pipeline_update")

    sweep = _load_daemon_sweep()
    applied = sweep(root, aq)

    assert _card_in_store(root, note_id)["status"] == "pending"      # surfaced, not swept
    assert _card_in_store(root, pipe_id)["status"] == "applied"      # notify auto-applied
    assert applied == 1                                              # only the notify card counted


def test_pipeline_card_stamped_notify(client, root):
    _deposit(client, [_pipeline_card()])
    card = _list(client)["fyi"][0]  # notify -> read-only FYI lane, not 'items'
    assert card["action_type"] == "pipeline_update"
    assert card["tier"] == "notify"


# ============================================================
# Gated send still requires approve -> executor (approve does not send)
# ============================================================

def test_email_send_requires_approve_then_executor(client, root):
    _deposit(client, [_email_card()])
    aid = _list(client)["items"][0]["id"]
    # Nothing approved yet -> the batch executor's selection set is empty.
    assert _executor_selection(root) == []
    # Mark approved via the in-process helper (the HTTP approve endpoint was
    # removed when the queue went terminal-native; approve is now the synchronous
    # CLI path). approve_card never stamps sent_at.
    res = aq.approve_card(root, aid)
    card = res["card"]
    assert res["ok"] and card["status"] == "approved" and "sent_at" not in card
    # Now (and only now) the executor selection set includes it.
    sel = _executor_selection(root)
    assert len(sel) == 1 and sel[0]["id"] == aid


# ============================================================
# Notify auto-apply + undo
# ============================================================

def test_notify_auto_apply_then_undo_records_event(client, root):
    _deposit(client, [_pipeline_card()])
    aid = _list(client)["fyi"][0]["id"]  # notify card lives in the FYI lane
    # Producer contract (R4, simulated here): stamp prev_value BEFORE applying.
    aq.apply_status(root, aid, "applied", event="notify_apply",
                    prev_value={"stage": "Qualified"}, applied_value={"stage": "Negotiation"})
    card = _card_in_store(root, aid)
    assert card["prev_value"] == {"stage": "Qualified"}
    # One-click undo restores prev_value and logs an undo event.
    r = aq.undo_card(root, aid)
    assert r["ok"] and r["noop"] is False
    card = _card_in_store(root, aid)
    assert "prev_value" not in card
    assert card["restored_value"] == {"stage": "Qualified"}
    assert any(e.get("event") == "undo" and e.get("action_id") == aid
               for e in _disposition_events(root))


def test_undo_is_noop_safe_without_prev_value(client, root):
    """Scrutiny M2: a malformed producer that never stamped prev_value must not
    corrupt state. undo_card returns noop=True, never raises, leaves the card."""
    _deposit(client, [_pipeline_card()])
    aid = _list(client)["fyi"][0]["id"]  # notify card lives in the FYI lane
    before = dict(_card_in_store(root, aid))
    assert "prev_value" not in before  # the producer never stamped it
    r = aq.undo_card(root, aid)
    assert r["ok"] is True and r["noop"] is True
    after = _card_in_store(root, aid)
    # State untouched apart from being read; no restored_value, no undone_at.
    assert "restored_value" not in after and "undone_at" not in after
    assert after == before
    assert any(e.get("event") == "undo_noop" and e.get("action_id") == aid
               for e in _disposition_events(root))


def test_undo_unknown_id_is_error(root):
    r = aq.undo_card(root, "does-not-exist")
    assert r["ok"] is False and r["error"] == "not found"


# ============================================================
# DLQ on permanent send_failed
# ============================================================

def test_permanent_send_failed_writes_dead_letter(client, root):
    _deposit(client, [_email_card()])
    aid = _list(client)["items"][0]["id"]
    aq.apply_status(root, aid, "send_failed", event="send_failed",
                    classification="permanent", error="empty recipient")
    entries = dead_letter.list_entries(workspace_root=root)
    assert len(entries) == 1
    entry = dead_letter.load(entries[0])
    assert entry["classification"] == "permanent"
    assert entry["kind"] == "email_send"
    assert entry["payload"]["id"] == aid


def test_transient_send_failed_does_not_write_dead_letter(client, root):
    _deposit(client, [_email_card()])
    aid = _list(client)["items"][0]["id"]
    aq.apply_status(root, aid, "send_failed", event="send_failed",
                    classification="transient", error="connection timed out")
    assert dead_letter.list_entries(workspace_root=root) == []


# ============================================================
# Tampered-ledger invariant: a config edit cannot auto-send
# ============================================================

def test_tampered_ledger_cannot_auto_send(client, root, monkeypatch):
    """A tool-risk.json edited to mark email_send autonomous still resolves
    gated (the send-capable invariant in tool_risk), and the executor selection
    predicate refuses an unapproved card - so a tampered ledger cannot
    auto-send."""
    tampered = {
        "version": 1,
        "tiers": {"email_send": {"tier": "autonomous", "reason": "tampered"}},
        "send_capable": ["email_send", "telegram_send"],
    }
    monkeypatch.setattr(tool_risk, "_CACHE", tampered)
    assert tool_risk.tier_for("email_send") == "gated"

    _deposit(client, [_email_card()])
    card = _list(client)["items"][0]
    # Stamped gated despite the tampered ledger marking it autonomous.
    assert card["tier"] == "gated"
    # Pending (never approved) -> the executor selection set is empty: no send.
    assert _executor_selection(root) == []
