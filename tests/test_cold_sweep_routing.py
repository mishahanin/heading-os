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


# --- the producer boundary -------------------------------------------------
#
# `_fetch_rows` is the other half of this module and had no test of any kind.
# Its docstring makes one promise: every way `crm-health.py --json` can fail
# comes back as a RuntimeError that NAMES the producer, because a raw traceback
# "says nothing about which of the two scripts is at fault" and an empty card
# list "reads to the caller as 'no one is overdue'". Each case below is one way
# through that promise, driven with a real child process rather than a stub -
# a stub that raises unconditionally would measure the handler, not the read.

import pytest  # noqa: E402


def _fake_producer(tmp_path, body: str):
    """A scratch workspace whose crm-health.py is whatever this test needs."""
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "crm-health.py").write_text(body, encoding="utf-8")
    return tmp_path


def test_a_healthy_producer_is_read_through(tmp_path):
    """Anchor. Without it, every refusal below could be a function that only refuses."""
    root = _fake_producer(tmp_path, "print('[{\"name\": \"Jane Doe\"}]')\n")
    assert csc._fetch_rows(root) == [{"name": "Jane Doe"}]


def test_a_producer_that_cannot_be_run_names_itself(tmp_path):
    root = tmp_path / "empty-workspace"
    root.mkdir()
    with pytest.raises(RuntimeError, match="crm-health.py --json could not be run"):
        csc._fetch_rows(root)


def test_a_producer_that_answers_with_prose_names_itself(tmp_path):
    root = _fake_producer(tmp_path, "print('no contacts configured')\n")
    with pytest.raises(RuntimeError, match="is not JSON"):
        csc._fetch_rows(root)


def test_a_producer_that_answers_with_an_object_is_refused_not_iterated(tmp_path):
    """A dict is valid JSON and iterates - over its KEYS. Silently building
    cards from that is worse than refusing."""
    root = _fake_producer(tmp_path, "print('{\"contacts\": []}')\n")
    with pytest.raises(RuntimeError, match="returned dict, expected a list"):
        csc._fetch_rows(root)


def test_undecodable_producer_output_is_refused_by_name_not_a_raw_traceback(tmp_path):
    """The decode class, at the one boundary in this module that can meet it.

    `subprocess.run(..., text=True)` with no `errors=` decodes STRICTLY, and the
    failure happens inside the read, before `json.loads` is ever reached. A
    `UnicodeDecodeError` is a `ValueError`, a sibling of `JSONDecodeError` and
    no relation to `OSError` or `SubprocessError`, so it walked past BOTH
    handlers here and out of `run()` as the bare traceback this function's own
    docstring exists to prevent. MEASURED 2026-09-01: this test raised
    `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff` before the fix.

    The child writes to its file descriptor rather than through `print`, because
    a lone byte is only reachable below the text layer - which is exactly how it
    reaches the parent in production too.
    """
    root = _fake_producer(
        tmp_path,
        "import os, sys\n"
        "os.write(sys.stdout.fileno(), b'[{\"name\": \"J\\xff\\xfeane\"}]')\n",
    )
    with pytest.raises(RuntimeError) as exc:
        csc._fetch_rows(root)
    assert "crm-health.py" in str(exc.value), str(exc.value)


# ============================================================
# The card must carry the address the routing check validated
#
# Shard `scripts-04-p1` F5. `route()` decides email_send on
# `(row.get("email") or "").strip()`, so a padded CRM field routes as a real
# address; `build_cards` then stamped `card["to"] = row.get("email")`, the raw
# value. `to` on an email_send card is what a drafter and then a sender use
# verbatim, and nothing between here and the transport normalises it.
# ============================================================

PADDED = "  jane@acme.com  "


def test_a_padded_address_reaches_the_card_stripped():
    cards = csc.build_cards([_row(email=PADDED)], now=NOW)
    assert cards[0]["to"] == "jane@acme.com", repr(cards[0]["to"])


def test_the_card_carries_exactly_what_route_validated():
    """The invariant behind the assertion above, stated as the relation.

    `route()` and `build_cards` read the same field and must agree on its value.
    Written as a comparison rather than a literal so it keeps binding if the
    fixture address ever changes.
    """
    row = _row(email="\tjane@acme.com\n")
    routed = csc.route(row, NOW)
    assert routed is not None and routed[2] == "email_send"

    card = csc.build_cards([row], now=NOW)[0]
    assert card["to"] == (row["email"] or "").strip()
    assert card["to"] == card["to"].strip(), "the card carries an unstrippable address"


def test_a_whitespace_only_address_still_routes_to_a_note():
    """The other side of the same `.strip()`: padding is not an address.

    Regression cover for a fix applied the wrong way round, stripping in
    `build_cards` while leaving `route()` to see a truthy blank string.
    """
    cards = csc.build_cards([_row(email="   ")], now=NOW)
    assert cards[0]["action_type"] == "note"
    assert "to" not in cards[0]
