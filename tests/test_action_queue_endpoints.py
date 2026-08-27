"""Action Queue read endpoints + in-process mutate-helper tests (R1; redesigned
2026-06-27 terminal-native).

Drives the FastAPI app via TestClient for the RETAINED read-only surface (GET
/action-queue, POST /action-queue/deposit, auth, tier banding) against a hermetic
temp workspace. The mutation endpoints (approve/edit/dismiss POST) were REMOVED
when the queue went terminal-native; the same behaviours are now exercised
directly against the in-process helpers (`approve_card`, `edit_card`,
`dismiss_card`, `apply_status`) the terminal CLI uses. No Exchange, no network,
no daemon. Covers:

  - auth required; deposit dedup + cooldown; tier banding (web read path);
  - approve_card marks approved without sending (helper);
  - edit_card updates the draft (helper);
  - dismiss_card tombstones + starts the cooldown (helper);
  - unknown id -> {ok: False} from the helper;
  - executor-selection idempotency: once a card is sent the batch executor's
    selection set (approved + email_send) is empty.

Run: python3 -m pytest tests/test_action_queue_endpoints.py
"""
import json
import sys
from pathlib import Path

import pytest
pytest.importorskip("fastapi")  # F-7.1: skip on a core-only clone (needs the dashboard extra)

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.bridge_daemon.app import build_app
from scripts.bridge_daemon.state import State
from scripts.bridge_daemon.sources import action_queue as aq

TOKEN = "test-token-aq"
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def root(tmp_path):
    (tmp_path / "outputs" / "operations" / "action-queue").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def client(root):
    app = build_app(root, State(), TOKEN, "misha", data_root=root)
    # base_url loopback: the F-9.2 host-origin guard rejects a non-loopback Host.
    return TestClient(app, base_url="http://127.0.0.1")


def _email_card(to="jane@acme.com", contact="crm/contacts/jane.md", title="Nudge Jane"):
    return {
        "action_type": "email_send", "to": to, "contact_file": contact,
        "title": title, "priority": "P1", "reasoning": "20d overdue",
        "draft_status": "needs_draft", "citations": [{"source": contact, "excerpt": "overdue"}],
    }


def _deposit(client, cards):
    return client.post("/action-queue/deposit", json={"cards": cards}, headers=H)


def _list(client):
    return client.get("/action-queue", headers=H).json()


def test_requires_token(client):
    assert client.get("/action-queue").status_code == 401


def test_deposit_and_list(client):
    r = _deposit(client, [_email_card()])
    assert r.status_code == 200 and r.json()["added"] == 1
    d = _list(client)
    assert d["total"] == 1 and d["items"][0]["status"] == "pending"


def test_deposit_dedup_same_contact(client):
    _deposit(client, [_email_card()])
    r2 = _deposit(client, [_email_card(title="Dup Jane")])
    assert r2.json()["added"] == 0 and r2.json()["skipped"] == 1
    assert _list(client)["total"] == 1


def test_deposit_dedup_within_one_batch(client):
    """Two same-key cards in ONE call, not two calls.

    `append_cards` calls itself THE sole dedup authority and carries two
    mechanisms: an index built from the cards already on disk, and a
    within-batch registration that adds each accepted card to that index as it
    goes. Every dedup test in the suite deposited duplicates as two SEPARATE
    calls, so only the first mechanism was exercised - and every multi-card
    deposit anywhere in the suite used cards with DISTINCT keys.

    Deleting the within-batch registration left the whole suite green. Then one
    cold sweep whose build_cards emits two rows for the same contact - the shape
    its own test pins as legal - queues two pending email_send cards, and the
    CEO is shown two drafts to one recipient from a single sweep.
    """
    r = _deposit(client, [_email_card(), _email_card(title="Second nudge")])
    assert r.status_code == 200
    assert (r.json()["added"], r.json()["skipped"]) == (1, 1), r.json()
    assert _list(client)["total"] == 1


def test_two_different_contacts_in_one_batch_both_land(client):
    """Anchor. The test above passes on an `append_cards` that keeps only the
    first card of any batch, which would silently drop most of a cold sweep."""
    r = _deposit(client, [
        _email_card(to="jane@acme.com", contact="crm/contacts/jane.md"),
        _email_card(to="raj@acme.com", contact="crm/contacts/raj.md",
                    title="Nudge Raj"),
    ])
    assert r.json()["added"] == 2, r.json()
    assert r.json()["skipped"] == 0, r.json()
    assert _list(client)["total"] == 2


def test_within_batch_dedup_holds_for_a_three_card_run(client):
    """Three cards, two of them the same contact. The counts have to add up, or
    the queue and the report disagree about what happened."""
    r = _deposit(client, [
        _email_card(contact="crm/contacts/jane.md"),
        _email_card(contact="crm/contacts/raj.md", title="Nudge Raj"),
        _email_card(contact="crm/contacts/jane.md", title="Jane again"),
    ])
    assert (r.json()["added"], r.json()["skipped"]) == (2, 1), r.json()
    d = _list(client)
    assert d["total"] == 2
    assert sorted(i["contact_file"] for i in d["items"]) == [
        "crm/contacts/jane.md", "crm/contacts/raj.md"]


def test_approve_card_does_not_send(client, root):
    # approve_card is the non-send disposition helper (note/pipeline_update);
    # for email_send the terminal CLI's approve_and_send sends synchronously
    # (covered in test_action_queue_sync_send.py). Here: marking approved does
    # not stamp sent_at.
    _deposit(client, [_email_card()])
    aid = _list(client)["items"][0]["id"]
    res = aq.approve_card(root, aid)
    assert res["ok"] and res["card"]["status"] == "approved"
    assert "sent_at" not in res["card"]
    assert any(c["id"] == aid and c["status"] == "approved" for c in _list(client)["items"])


def test_dismiss_tombstone(client, root):
    _deposit(client, [_email_card()])
    aid = _list(client)["items"][0]["id"]
    assert aq.dismiss_card(root, aid, "no")["ok"]
    d = _list(client)
    assert d["total"] == 0 and d["dismissed_count"] == 1


def test_dismiss_cooldown_blocks_redeposit(client, root):
    _deposit(client, [_email_card()])
    aid = _list(client)["items"][0]["id"]
    aq.dismiss_card(root, aid)
    r2 = _deposit(client, [_email_card(title="Jane again")])
    assert r2.json()["added"] == 0 and r2.json()["skipped"] == 1


def test_edit_updates_draft(client, root):
    _deposit(client, [_email_card()])
    aid = _list(client)["items"][0]["id"]
    res = aq.edit_card(root, aid, subject="Quick hello", draft_body="Hi Jane, ...",
                       draft_status="ready_for_review")
    assert res["ok"]
    card = res["card"]
    assert card["subject"] == "Quick hello" and card["draft_body"].startswith("Hi Jane")
    assert card["draft_status"] == "ready_for_review"


def test_unknown_action_id_returns_not_found(client, root):
    res = aq.approve_card(root, "nope")
    assert res["ok"] is False and "not found" in res["error"]


def _executor_selection(root):
    """Replicate action-queue-execute.py's selection predicate against the store."""
    data = json.loads((root / aq.QUEUE_FILE).read_text(encoding="utf-8"))
    return [c for c in data["actions"]
            if c.get("status") == "approved" and c.get("action_type") == "email_send"]


def _note_card(title="Logged a note", contact="crm/contacts/note-x.md"):
    return {
        "action_type": "note", "contact_file": contact, "title": title,
        "priority": "P3", "reasoning": "fyi context",
    }


def test_tier_banding_splits_actionable_from_fyi(client):
    # One gated send + one autonomous note. items/total stay the full active
    # set (the daemon sweep iterates items); the UI lanes split by tier.
    _deposit(client, [_email_card(), _note_card()])
    d = _list(client)
    assert d["total"] == 2, "items stays the full active set for the daemon sweep"
    # Actionable lane = gated send only.
    assert d["actionable_total"] == 1
    assert len(d["actionable"]) == 1 and d["actionable"][0]["action_type"] == "email_send"
    # FYI lane = the note; it never leaks into the actionable lane.
    assert d["fyi_total"] == 1
    assert len(d["fyi"]) == 1 and d["fyi"][0]["action_type"] == "note"
    assert all(c["action_type"] != "note" for c in d["actionable"])


def test_executor_idempotency(client, root):
    # Deposit, fill a body, approve via the helpers -> the batch executor's
    # selection set has it once; once sent, a second pass selects nothing.
    _deposit(client, [_email_card()])
    aid = _list(client)["items"][0]["id"]
    aq.edit_card(root, aid, draft_body="Hi")
    aq.approve_card(root, aid)
    assert len(_executor_selection(root)) == 1
    aq.apply_status(root, aid, "sent", event="sent")
    assert _executor_selection(root) == []
    d = _list(client)
    assert d["total"] == 0 and d["sent_count"] == 1
