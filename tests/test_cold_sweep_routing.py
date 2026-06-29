"""Routing tests for the deterministic Cold-Sweep core (R2).

Exercises ``cold_sweep_core.build_cards`` as a pure function: synthetic
crm-health rows in, Action Queue cards out. No LLM, no network, no daemon.
Dedup/cooldown is NOT tested here - it lives in the deposit/append helper
(see tests/test_action_queue_endpoints.py).

Run: python3 -m pytest tests/test_cold_sweep_routing.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import cold_sweep_core as csc

NOW = datetime(2026, 6, 3, tzinfo=timezone.utc)


def _row(**kw):
    base = {
        "name": "Jane Doe", "company": "Acme", "email": "jane@acme.com",
        "type": "prospect", "stage": "Lead", "last_touch": "2026-05-01",
        "cadence": 14, "health": "red", "days_since": 33, "days_overdue": 19,
        "radar_freeze_until": "", "commitments": [], "file": "jane-doe.md",
    }
    base.update(kw)
    return base


def test_red_with_email_is_warm_p1_email():
    cards = csc.build_cards([_row(health="red")], now=NOW)
    assert len(cards) == 1
    c = cards[0]
    assert c["priority"] == "P1"
    assert c["action_type"] == "email_send"
    assert c["route"] == "warm"
    assert c["draft_status"] == "needs_draft"
    assert c["to"] == "jane@acme.com"
    assert c["contact_file"] == "crm/contacts/jane-doe.md"
    assert c["citations"] and "overdue" in c["citations"][0]["excerpt"]


def test_yellow_with_email_is_followup_p2():
    cards = csc.build_cards([_row(health="yellow")], now=NOW)
    assert cards[0]["priority"] == "P2"
    assert cards[0]["route"] == "follow-up"
    assert cards[0]["action_type"] == "email_send"


def test_no_email_is_note_p3():
    cards = csc.build_cards([_row(email="", health="red")], now=NOW)
    assert len(cards) == 1
    assert cards[0]["action_type"] == "note"
    assert cards[0]["priority"] == "P3"
    assert "to" not in cards[0]


def test_green_is_skipped():
    assert csc.build_cards([_row(health="green")], now=NOW) == []


def test_gray_is_skipped():
    # gray = dormant / no-cadence: never routed.
    assert csc.build_cards([_row(health="gray")], now=NOW) == []


def test_active_radar_freeze_is_skipped():
    future = (NOW + timedelta(days=10)).date().isoformat()
    assert csc.build_cards([_row(radar_freeze_until=future)], now=NOW) == []


def test_expired_radar_freeze_is_not_skipped():
    past = (NOW - timedelta(days=10)).date().isoformat()
    cards = csc.build_cards([_row(radar_freeze_until=past)], now=NOW)
    assert len(cards) == 1


def test_build_cards_does_not_dedup():
    # Two identical overdue rows -> two cards. Dedup is the append helper's job.
    cards = csc.build_cards([_row(), _row()], now=NOW)
    assert len(cards) == 2


def test_missing_file_yields_no_contact_file():
    cards = csc.build_cards([_row(file="")], now=NOW)
    assert cards[0]["contact_file"] is None


def test_reasoning_contains_overdue_and_cadence():
    c = csc.build_cards([_row(days_overdue=21, cadence=14)], now=NOW)[0]
    assert "21d overdue" in c["reasoning"]
    assert "cadence 14d" in c["reasoning"]
