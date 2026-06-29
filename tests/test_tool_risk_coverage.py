"""Regression: tool-risk ledger must classify crm_write, knowledge_write, task_create (F-L10)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "config" / "tool-risk.json"


def _load_ledger():
    return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))


def test_crm_write_classified():
    ledger = _load_ledger()
    assert ledger["tiers"].get("crm_write", {}).get("tier") == "notify", (
        "crm_write must be classified tier=notify in config/tool-risk.json (F-L10)"
    )


def test_knowledge_write_classified():
    ledger = _load_ledger()
    assert ledger["tiers"].get("knowledge_write", {}).get("tier") == "notify", (
        "knowledge_write must be classified tier=notify in config/tool-risk.json (F-L10)"
    )


def test_task_create_classified():
    ledger = _load_ledger()
    assert ledger["tiers"].get("task_create", {}).get("tier") == "notify", (
        "task_create must be classified tier=notify in config/tool-risk.json (F-L10)"
    )


def test_new_entries_not_in_send_capable():
    """crm_write, knowledge_write, task_create are reversible edits, not outbound sends."""
    send_capable = set(_load_ledger().get("send_capable", []))
    for action_type in ("crm_write", "knowledge_write", "task_create"):
        assert action_type not in send_capable, (
            f"{action_type} is not an outbound send; it must not be in send_capable"
        )


def test_send_gate_invariant_intact():
    """email_send and telegram_send must remain in send_capable (invariant guard)."""
    send_capable = set(_load_ledger().get("send_capable", []))
    assert "email_send" in send_capable, "email_send must remain in send_capable"
    assert "telegram_send" in send_capable, "telegram_send must remain in send_capable"
