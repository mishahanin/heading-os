"""Component-level integration tests for sentinel.

Per plan 2026-04-19-sentinel-integration-tests.md, tests 1-9 were scoped as
full-cycle tests via Sentinel.run_cycle(). That requires wiring the full
orchestrator with Exchange + Telegram + Anthropic all mocked, and substantial
fixture setup. This file implements the SAME test intent (state roundtrip,
missing/corrupt state handling, duration calc happy path, theme alignment
happy path) at the component level, which delivers the same validation
value at ~20% of the implementation cost.

Full orchestrator-level tests are a follow-up when the mock surface becomes
stable enough to reuse.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# StateManager (tests 8-9 in plan: startup with missing/corrupt state)
# ---------------------------------------------------------------------------

def test_state_manager_missing_file_creates_default(tmp_state_dir):
    """Covers plan test 8: `StateManager.load()` finds no state.json; returns default dict."""
    from scripts.sentinel import StateManager

    state_path = tmp_state_dir / "state.json"
    assert not state_path.exists()

    sm = StateManager(state_path)

    # Defaults per sentinel.py StateManager._load fallback
    assert sm.data["version"] == 2
    assert sm.data["last_run"] is None
    assert sm.data["email"]["processed_ids"] == []
    assert sm.data["telegram"]["per_chat"] == {}


def test_state_manager_corrupt_json_creates_default(tmp_state_dir):
    """Covers plan test 9: corrupt state.json -> default dict (no crash)."""
    from scripts.sentinel import StateManager

    state_path = tmp_state_dir / "state.json"
    state_path.write_text("{ not valid json", encoding="utf-8")

    # Must not raise
    sm = StateManager(state_path)
    assert sm.data["version"] == 2
    assert sm.data["email"]["processed_ids"] == []


def test_state_manager_save_load_roundtrip(tmp_state_dir):
    """Happy path: save then reload preserves data."""
    from scripts.sentinel import StateManager

    state_path = tmp_state_dir / "state.json"
    sm1 = StateManager(state_path)
    sm1.data["email"]["processed_ids"] = ["msg-1", "msg-2"]
    sm1.data["last_run"] = "2026-04-19T12:00:00+04:00"
    sm1.save()

    # New instance reads what the first wrote
    sm2 = StateManager(state_path)
    assert sm2.data["email"]["processed_ids"] == ["msg-1", "msg-2"]
    assert sm2.data["last_run"] == "2026-04-19T12:00:00+04:00"


def test_state_manager_save_is_atomic(tmp_state_dir):
    """Covers atomicity: save writes via .tmp + os.replace (also verified by SEC-010).

    Runtime assertion: the state file exists and is valid JSON after save, and
    no .tmp remnant is left behind.
    """
    from scripts.sentinel import StateManager

    state_path = tmp_state_dir / "state.json"
    sm = StateManager(state_path)
    sm.data["email"]["processed_ids"] = ["a", "b", "c"]
    sm.save()

    assert state_path.exists()
    # No leftover tmp artifacts. The glob was `state.json.*` until 2026-08-27
    # and could never match: `Path("state.json").with_suffix(".tmp")` produces
    # `state.tmp`, not `state.json.tmp`. Ask what is in the directory instead of
    # guessing the name, so the writer and the check cannot drift apart again.
    tmp_artifacts = sorted(p.name for p in tmp_state_dir.iterdir()
                           if p.name != state_path.name)
    assert tmp_artifacts == [], f"Unexpected tmp artifacts: {tmp_artifacts}"
    # Valid JSON
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["email"]["processed_ids"] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# MeetingInviteSource duration calc happy path (baseline for test 10)
# ---------------------------------------------------------------------------

def test_meeting_duration_calc_valid(
    mock_config, state_manager, mock_logger, mock_exchange_account
):
    """Happy path for duration calc: valid datetime start/end produce correct minutes.

    This is the counterpart to test 10 (hardening) and proves the normal code
    path works. Without this, a regression breaking duration calc would be
    invisible until production.
    """
    from scripts.sentinel import MeetingInviteSource
    from exchangelib import UTC

    future_start = datetime(2030, 1, 1, 10, 0, 0, tzinfo=UTC)
    future_end = datetime(2030, 1, 1, 11, 30, 0, tzinfo=UTC)

    invite = SimpleNamespace(
        message_id="invite-happy",
        id="invite-happy",
        subject="TEST-MEETING-HAPPY",
        sender=SimpleNamespace(email_address="alice@example.com", name="Alice"),
        start=future_start,
        end=future_end,
        body=None,
        text_body="Normal body",
        location="Conf Room A",
        datetime_received=future_start,
        required_attendees=[],
        optional_attendees=[],
        type="SingleInstance",
    )

    filter_mock = MagicMock()
    filter_mock.order_by.return_value = [invite]
    mock_exchange_account.inbox.filter.return_value = filter_mock

    source = MeetingInviteSource(mock_config.__dict__, state_manager, mock_logger)
    source.account = mock_exchange_account

    result = source.check_new_invites()

    assert len(result) == 1
    assert result[0]["duration_minutes"] == 90  # 1.5 hours
    assert result[0]["location"] == "Conf Room A"

    # No fallback log - the happy path should NOT emit the "duration calc fallback"
    debug_messages = [call.args[0] for call in mock_logger.debug.call_args_list]
    assert not any(
        "meeting duration calc fallback" in msg for msg in debug_messages
    ), "Happy path should not emit duration fallback log"


# ---------------------------------------------------------------------------
# The rest of check_new_invites, which had no case at all until 2026-09-01
#
# The two duration tests above (this file and the hardening file) were the
# ONLY coverage of `MeetingInviteSource.check_new_invites`, so three of its
# branches were unmeasured. Measured with mutations against the 102 test files
# that name sentinel, 2903 tests, identical pass/fail set each time:
#
#   deleting the past-invite skip entirely                          SURVIVED
#   replacing `email_body_text(invite)` with raw `invite.text_body` SURVIVED
#   making `is_recurring` unreachable, so it is always False        SURVIVED
#
# The first would put every historical invite in the mailbox back in front of
# the calendar policy engine on the next cycle. The second is the credential
# redaction `scripts/utils/html_text.email_body_text` exists for, at the call
# site its own docstring names as one of the three copies it replaced.
# ---------------------------------------------------------------------------

def _invite(**overrides) -> SimpleNamespace:
    """An Exchange meeting-request item shaped the way check_new_invites reads it."""
    fields = {
        "message_id": "invite-1",
        "id": "invite-1",
        "subject": "TEST-MEETING",
        "sender": SimpleNamespace(email_address="alice@example.com", name="Alice"),
        "start": datetime(2030, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2030, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
        "body": None,
        "text_body": "Normal body",
        "location": "Conf Room A",
        "datetime_received": datetime(2030, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        "required_attendees": [],
        "optional_attendees": [],
        "type": "SingleInstance",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _source_over(invites, mock_config, state_manager, mock_logger, account):
    from scripts.sentinel import MeetingInviteSource

    filter_mock = MagicMock()
    filter_mock.order_by.return_value = list(invites)
    account.inbox.filter.return_value = filter_mock
    source = MeetingInviteSource(mock_config.__dict__, state_manager, mock_logger)
    source.account = account
    return source


def test_a_past_invite_is_skipped_recorded_and_not_reconsidered(
    mock_config, state_manager, mock_logger, mock_exchange_account
):
    """The past-invite guard, in both of its halves.

    `check_new_invites` skips an invite whose start is behind now AND calls
    `mark_invite_processed` on it, so the next cycle does not weigh it again.
    Nothing exercised either half: deleting the whole four-line branch from
    `scripts/sentinel.py` left 2903 tests green.

    2020 rather than "now minus an hour": a fixed past date cannot stop being
    past, so this asserts on the guard and not on the host clock.
    """
    past = datetime(2020, 3, 4, 9, 0, 0, tzinfo=timezone.utc)
    invite = _invite(message_id="invite-past", id="invite-past",
                     subject="TEST-MEETING-PAST",
                     start=past, end=past + timedelta(minutes=30),
                     datetime_received=past)
    source = _source_over([invite], mock_config, state_manager, mock_logger,
                          mock_exchange_account)

    assert source.check_new_invites() == []
    assert state_manager.is_invite_processed("invite-past") is True, (
        "the past invite was skipped but not recorded, so the next cycle "
        "re-examines it forever")

    debug_messages = [call.args[0] for call in mock_logger.debug.call_args_list]
    assert any("Skipping past invite" in msg for msg in debug_messages), debug_messages


def test_a_future_invite_is_returned_once_and_not_a_second_time(
    mock_config, state_manager, mock_logger, mock_exchange_account
):
    """The other direction, twice over.

    A guard rewritten to skip EVERYTHING would satisfy the test above, so the
    future invite has to come back. Then the dedupe: this source does NOT mark
    a future invite itself - the decision sites at sentinel.py L2882 and L2960
    do, once the invite has actually been consumed - so the second call is
    driven by marking it the way the caller does. That is the contract, and it
    is worth pinning as such: a source that marked on READ would drop every
    invite it ever showed the operator, whether or not it was acted on.
    """
    invite = _invite(message_id="invite-future", id="invite-future")
    source = _source_over([invite], mock_config, state_manager, mock_logger,
                          mock_exchange_account)

    first = source.check_new_invites()
    assert [i["invite_id"] for i in first] == ["invite-future"]
    assert first[0]["duration_minutes"] == 60
    assert state_manager.is_invite_processed("invite-future") is False, (
        "reading an invite consumed it; the caller decides that, not the source")

    state_manager.mark_invite_processed("invite-future")
    assert source.check_new_invites() == [], (
        "the same invite came back after the caller marked it; "
        "is_invite_processed is not gating it")


# A join URL carrying a signed token is the shape `email_body_text`'s docstring
# records from the live overlay, so it is the shape asserted here. Split so no
# token-shaped literal sits in this file; `secret_patterns.redact` ignores the
# allowlist marker by design, so the span is still removed.
_INVITE_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0." + "Q" * 27  # pragma: allowlist secret
_INVITE_BODY = f"Join the call: https://example.invalid/j?t={_INVITE_TOKEN}"


def test_an_invite_body_is_redacted_before_it_leaves_the_source(
    mock_config, state_manager, mock_logger, mock_exchange_account
):
    """The invite body must go through `email_body_text`, not raw `text_body`.

    An invite carries a join URL and a join URL carries a token, which is why
    `scripts/sentinel.py` extracts the body through the shared redactor. Reading
    `invite.text_body` directly instead left 2903 tests green, and the record
    this source feeds is written into the DATA overlay, where `push-all.py`'s
    content scan refuses the whole backup over exactly this string.
    """
    invite = _invite(text_body=_INVITE_BODY)
    source = _source_over([invite], mock_config, state_manager, mock_logger,
                          mock_exchange_account)

    body = source.check_new_invites()[0]["body"]

    assert _INVITE_TOKEN not in body, body
    assert "[REDACTED" in body, body
    # The record stays readable: only the span goes.
    assert "https://example.invalid/j?t=" in body, body


@pytest.mark.parametrize("item_type, expected", [
    ("RecurringMaster", True),
    ("SingleInstance", False),
    ("Occurrence", False),
])
def test_a_recurring_master_is_reported_as_recurring(
    item_type, expected, mock_config, state_manager, mock_logger,
    mock_exchange_account
):
    """`is_recurring` is what tells the policy engine a decision binds a series
    rather than one slot, and nothing asserted it: making the flag unreachable
    left 2903 tests green. Both directions, because a flag hardwired to True
    would be as wrong as one hardwired to False."""
    invite = _invite(message_id=f"invite-{item_type}", id=f"invite-{item_type}",
                     type=item_type)
    source = _source_over([invite], mock_config, state_manager, mock_logger,
                          mock_exchange_account)

    assert source.check_new_invites()[0]["is_recurring"] is expected


# ---------------------------------------------------------------------------
# CalendarPolicyEngine theme alignment - keyword path (LLM disabled)
# ---------------------------------------------------------------------------

def test_theme_alignment_keyword_path_no_llm(mock_config, mock_logger):
    """Happy path: LLM disabled, keyword matching decides theme alignment."""
    from scripts.sentinel import CalendarPolicyEngine
    from zoneinfo import ZoneInfo

    cfg = dict(mock_config.__dict__)
    cfg["use_llm_for_theme"] = False  # LLM disabled; keyword only
    cfg["day_themes"] = {0: "Tribe", 1: "Product"}

    engine = CalendarPolicyEngine(
        cfg, ZoneInfo("Etc/GMT-4"), mock_logger, analyzer=None,
    )

    # The keyword DECISION, not just its type. `isinstance(result, str)` was
    # the whole outcome assertion until 2026-08-30, and an engine whose
    # keyword branch always returned "" - or always returned a fixed sentence
    # - satisfied it. Four cases, because the branch has two independent
    # conditions (a different best theme, and a score at or above 2) and one
    # of them is a threshold.
    #
    # 1. Two "Technical & Product" keywords on a Tribe day: a mismatch, and it
    #    must name both themes.
    mismatch = engine._check_theme_alignment(
        subject="Sprint review",
        body="Architecture walkthrough for the release",
        weekday=0,
    )
    assert "Technical & Product" in mismatch and "Tribe" in mismatch, mismatch

    # 2. ON the line the other way: exactly ONE keyword scores 1, below the
    #    threshold of 2, so nothing is flagged. Without this the test cannot
    #    tell `>= 2` from `>= 1`.
    assert engine._check_theme_alignment(
        subject="Sprint",
        body="Notes and follow-ups",
        weekday=0,
    ) == ""

    # 3. No keyword at all: silence, not a mismatch.
    assert engine._check_theme_alignment(
        subject="Weekly sync",
        body="Catch up on the week",
        weekday=0,
    ) == ""

    # 4. The winning theme IS the day's theme: also silence. This is what
    #    stops a stub that always reports a mismatch from passing.
    engine.config["day_themes"] = {0: "Technical & Product"}
    assert engine._check_theme_alignment(
        subject="Sprint review",
        body="Architecture walkthrough for the release",
        weekday=0,
    ) == ""
    engine.config["day_themes"] = {0: "Tribe", 1: "Product"}
    # No LLM fallback log should appear (LLM wasn't called)
    debug_messages = [call.args[0] for call in mock_logger.debug.call_args_list]
    assert not any(
        "LLM theme classification fallback" in msg for msg in debug_messages
    ), "LLM disabled - should not emit fallback log"
