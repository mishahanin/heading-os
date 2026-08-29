"""`approve` told the operator the card was kept as send_failed when it was not.

`approve_and_send`'s `sent` path wraps its `apply_status` call in try/except
OSError and checks the returned `ok`. The `send_failed` path a few lines below
made the identical call and did neither, so:

  - a pruned or quarantined card (`apply_status` -> {"ok": False, "error":
    "not found"}) still produced `card kept as send_failed - fix and
    retry <id>`, naming a command that refuses any card whose status is not
    `send_failed`; nothing on disk had that status.
  - a full disk or a read-only queue store raised OSError straight out of
    `cmd_approve`, a raw traceback out of a file whose module docstring
    contracts `Exit codes: 0 ok, 1 request/usage error`.

Measured before the fix: `OSError: [Errno 28] No space left on device` out of
`approve_and_send`, and result `send_failed` for the not-found case.

Nothing here reaches a real send. `send_card` is stubbed at the module seam and
the gate is asserted to REFUSE, never removed.

Tests: this file.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


AQ = _load("action-queue", "action_queue_status_write")

CARD_ID = "aq-bond-0001"


def _card(**over) -> dict:
    card = {
        "id": CARD_ID,
        "action_type": "email_send",
        "status": "pending",
        "draft_status": "ready_for_review",
        "to": "james.bond@example.com",
        "subject": "Acme Telecom follow-up",
        "draft_body": "Body.",
        "trace_id": "trace-0001",
    }
    card.update(over)
    return card


class _Args:
    def __init__(self, aid: str) -> None:
        self.id = aid


@pytest.fixture()
def queued(monkeypatch, tmp_path):
    """One pending email card, a stubbed sender, and a recording apply_status.

    Every write target is tmp_path; the sender is never spawned.
    """
    state = {"card": _card(), "sent": 0, "apply_calls": []}
    monkeypatch.setattr(AQ, "list_action_queue", lambda data_root: {"items": [state["card"]]})

    def fake_send(engine_root, card):
        state["sent"] += 1
        return {"result": "send_failed", "error": "smtp refused the recipient"}

    monkeypatch.setattr(AQ, "send_card", fake_send)
    state["roots"] = (tmp_path / "engine", tmp_path / "data")
    return state


def test_a_status_write_that_raised_no_longer_escapes_as_a_traceback(queued, monkeypatch):
    """The OSError case. A full disk must not become a traceback out of approve."""
    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(AQ, "apply_status", boom)
    engine_root, data_root = queued["roots"]

    res = AQ.approve_and_send(engine_root, data_root, CARD_ID)

    assert res["result"] == "send_failed_unrecorded"
    assert "No space left on device" in res["record_error"]
    assert res["error"] == "smtp refused the recipient"


def test_a_card_that_vanished_is_not_reported_as_kept(queued, monkeypatch, capsys):
    """The not-found case, through the command the operator actually types."""
    monkeypatch.setattr(AQ, "apply_status",
                        lambda *a, **k: {"ok": False, "error": "not found"})
    engine_root, data_root = queued["roots"]

    rc = AQ.cmd_approve(engine_root, data_root, _Args(CARD_ID))
    err = capsys.readouterr().err

    assert rc == 1
    assert "kept as send_failed" not in err
    assert "not found" in err
    # The operator is told the retry path is closed, because it is: cmd_retry
    # refuses any card whose status is not send_failed, and nothing wrote one.
    assert "will not" in err


def test_a_recorded_failure_still_says_the_card_was_kept(queued, monkeypatch, capsys):
    """The other direction. When the write LANDS, the old message is correct and
    must survive: a guard that also rewrote the healthy path would be a
    regression, not a fix."""
    monkeypatch.setattr(AQ, "apply_status", lambda *a, **k: {"ok": True})
    engine_root, data_root = queued["roots"]

    rc = AQ.cmd_approve(engine_root, data_root, _Args(CARD_ID))
    err = capsys.readouterr().err

    assert rc == 1
    assert "card kept as send_failed" in err


def test_the_send_gate_still_refuses_an_already_approved_card(queued, monkeypatch):
    """The gate is verified by making it REFUSE, never by removing it. An
    `approved` card is outside SENDABLE_STATUSES, so no send may be attempted -
    this is the duplicate-mail failure the queue exists to prevent."""
    queued["card"] = _card(status="approved")
    monkeypatch.setattr(AQ, "list_action_queue",
                        lambda data_root: {"items": [queued["card"]]})
    monkeypatch.setattr(AQ, "apply_status", lambda *a, **k: {"ok": True})
    engine_root, data_root = queued["roots"]

    res = AQ.approve_and_send(engine_root, data_root, CARD_ID)

    assert res["result"] == "blocked"
    assert queued["sent"] == 0
    assert "approved" not in AQ.SENDABLE_STATUSES
