"""Integration tests for today's (2026-04-19) narrow-except hardening fixes.

Each test exercises one of the 7 narrowed except blocks in sentinel.py. Tests
are numbered 10-17 per plan plans/2026-04-19-sentinel-integration-tests.md.

All assertions use MagicMock(spec=logging.Logger) and inspect call_args_list,
NOT caplog - per plan's mock-strategy decision.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Test 10: MeetingInviteSource duration calc with incompatible datetime
# ---------------------------------------------------------------------------

def test_meeting_duration_calc_with_incompatible_datetime(
    mock_config, state_manager, mock_logger, mock_exchange_account
):
    """Covers MeetingInviteSource.check_new_invites duration-calc except block (~L494).

    Truthy but incompatible start/end (aware vs naive datetime) -> subtraction
    raises TypeError -> except block catches -> duration_minutes defaults to 0 ->
    self.logger.debug emitted with 'meeting duration calc fallback' text.

    NOTE: Do NOT use invite.end = None. Line 491's `if invite.start and invite.end:`
    guard would skip the try/except entirely. We need both values truthy but the
    subtraction to fail.
    """
    from scripts.sentinel import MeetingInviteSource
    from exchangelib import UTC

    # Build an invite: past-guard check passes (future datetime), but end-start fails.
    # Use aware datetime for start (to pass `invite.start < now` check) and a
    # MagicMock that raises TypeError on __sub__ for end.
    future_start = datetime(2030, 1, 1, 10, 0, 0, tzinfo=UTC)

    bad_end = MagicMock()
    # end - start raises (invite.end - invite.start in sentinel.py:493)
    bad_end.__sub__ = MagicMock(side_effect=TypeError(
        "can't subtract offset-naive and offset-aware datetimes"
    ))

    invite = SimpleNamespace(
        message_id="invite-bad-duration",
        id="invite-bad-duration",
        subject="TEST-MEETING-BAD-DURATION",
        sender=SimpleNamespace(email_address="alice@example.com", name="Alice"),
        start=future_start,
        end=bad_end,  # truthy, but subtraction raises
        body=None,
        text_body=None,
        location="",
        datetime_received=future_start,
        required_attendees=[],
        optional_attendees=[],
        type="SingleInstance",
    )

    # Wire the mock account.inbox.filter chain to return [invite]
    filter_mock = MagicMock()
    filter_mock.order_by.return_value = [invite]
    mock_exchange_account.inbox.filter.return_value = filter_mock

    source = MeetingInviteSource(mock_config.__dict__, state_manager, mock_logger)
    source.account = mock_exchange_account

    # Act
    result = source.check_new_invites()

    # Assert: debug log emitted for the duration fallback
    debug_messages = [call.args[0] for call in mock_logger.debug.call_args_list]
    assert any(
        "meeting duration calc fallback" in msg for msg in debug_messages
    ), f"Expected 'meeting duration calc fallback' in debug logs. Got: {debug_messages}"

    # And the invite SURVIVES the failed subtraction with the documented
    # fallback value. `isinstance(result, list)` was the only outcome check
    # here, and check_new_invites returns a list on every path including the
    # ones that drop the invite entirely, so a handler changed to `continue`
    # would have passed.
    assert len(result) == 1, result
    assert result[0]["duration_minutes"] == 0, result[0]


# ---------------------------------------------------------------------------
# Test 11: LLM theme classify fallback (anthropic-specific exception)
# ---------------------------------------------------------------------------

def test_theme_classify_llm_fails_falls_back_to_keywords(
    mock_config, mock_logger
):
    """Covers CalendarPolicyEngine._detect_theme_mismatch LLM classify except block (~L792).

    _classify_theme_llm raises anthropic-specific error -> except catches ->
    falls through to keyword matching. Uses a specific exception class here;
    test 12 uses a custom exception to guarantee broad-catch preservation.
    """
    from scripts.sentinel import CalendarPolicyEngine
    from zoneinfo import ZoneInfo

    cfg = dict(mock_config.__dict__)
    cfg["use_llm_for_theme"] = True  # enable the LLM path
    cfg["day_themes"] = {0: "Tribe", 1: "Product"}

    engine = CalendarPolicyEngine(
        cfg, ZoneInfo("Etc/GMT-4"), mock_logger, analyzer=MagicMock()
    )

    # Patch _classify_theme_llm to raise a plausible anthropic-style error
    class FakeAPIConnectionError(Exception):
        """Simulates anthropic.APIConnectionError shape."""

    with patch.object(
        engine, "_classify_theme_llm",
        side_effect=FakeAPIConnectionError("network dropped"),
    ):
        # Monday (weekday=0) has theme "Tribe". Four "Technical & Product"
        # keywords, which is what makes the fallback OBSERVABLE: the keyword
        # path only speaks when the winning theme scores at least 2 and differs
        # from the day theme. The old subject, "Product discussion", scored 1,
        # so the fallback ran and returned "" - and `isinstance(result, str)`
        # could not tell that apart from the fallback never running at all.
        result = engine._check_theme_alignment(
            subject="Sprint demo and architecture review",
            body="Lorem ipsum",
            weekday=0,
        )

    # Assert: debug log emitted (fallback path ran)
    debug_messages = [call.args[0] for call in mock_logger.debug.call_args_list]
    assert any(
        "LLM theme classification fallback" in msg for msg in debug_messages
    ), f"Expected fallback debug log. Got: {debug_messages}"

    # And the keyword matcher reached its verdict, which is the whole point of
    # falling back rather than giving up.
    assert "Technical & Product" in result, result
    assert "Tribe" in result, result


# ---------------------------------------------------------------------------
# Test 12: LLM theme classify fallback - CUSTOM exception (broad-catch guard)
# ---------------------------------------------------------------------------

def test_theme_classify_custom_exception_falls_back(
    mock_config, mock_logger
):
    """Covers same broad-catch at ~L792 with a NON-anthropic exception.

    Guards against a future engineer narrowing the broad `except Exception` to
    just anthropic classes. If narrowed, this test fails because ValueError
    would propagate instead of being caught.
    """
    from scripts.sentinel import CalendarPolicyEngine
    from zoneinfo import ZoneInfo

    cfg = dict(mock_config.__dict__)
    cfg["use_llm_for_theme"] = True
    cfg["day_themes"] = {0: "Tribe", 1: "Product"}

    engine = CalendarPolicyEngine(
        cfg, ZoneInfo("Etc/GMT-4"), mock_logger, analyzer=MagicMock()
    )

    with patch.object(
        engine, "_classify_theme_llm",
        side_effect=ValueError("unexpected shape in LLM response"),
    ):
        # Should not raise; broad except should catch ValueError just like
        # APIConnectionError. Same observable subject as the sibling above, and
        # for the same reason.
        result = engine._check_theme_alignment(
            subject="Sprint demo and architecture review",
            body="Lorem ipsum",
            weekday=0,
        )

    debug_messages = [call.args[0] for call in mock_logger.debug.call_args_list]
    assert any(
        "LLM theme classification fallback" in msg for msg in debug_messages
    ), f"Expected fallback debug log even for custom exception. Got: {debug_messages}"

    assert "Technical & Product" in result, result
    assert "Tribe" in result, result


# ---------------------------------------------------------------------------
# Test 13: Telegram WAL checkpoint on locked session file
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.asyncio
async def test_telegram_wal_checkpoint_on_locked_session(
    mock_config, state_manager, mock_logger, tmp_session_dir
):
    """Covers TelegramSource.connect WAL checkpoint except block (~L1016).

    Creates a session file, locks it with an EXCLUSIVE sqlite transaction,
    then calls connect(). The WAL checkpoint raises sqlite3.OperationalError
    -> except catches -> debug logged.
    """
    from scripts.sentinel import TelegramSource

    # Arrange: session file + competing exclusive lock
    session_file = tmp_session_dir / "test_telegram.session"
    session_file.write_bytes(b"")
    lock_conn = sqlite3.connect(str(session_file))
    lock_conn.execute("BEGIN EXCLUSIVE")

    source = TelegramSource(mock_config.__dict__, state_manager, mock_logger)

    try:
        # Patch the session path module-wide + telethon.TelegramClient (imported
        # inside TelegramSource.connect() via `from telethon import TelegramClient`).
        session_base = tmp_session_dir / "test_telegram"
        with patch("scripts.sentinel.TELEGRAM_SESSION_PATH", session_base):
            with patch("scripts.sentinel.TELEGRAM_SESSION_DIR", tmp_session_dir):
                # Stub the TelegramClient so the test doesn't try to connect to real Telegram
                fake_client = AsyncMock()
                fake_client.connect = AsyncMock()
                fake_client.is_user_authorized = AsyncMock(return_value=True)
                fake_client.get_me = AsyncMock(
                    return_value=SimpleNamespace(first_name="X", username="x")
                )
                fake_client.session = MagicMock()
                fake_client.session._conn = None
                with patch("telethon.TelegramClient", return_value=fake_client):
                    # Patch _configure_session_wal to a no-op (it would double-lock)
                    with patch("scripts.sentinel._configure_session_wal"):
                        with patch.dict(os.environ, {
                            "TELEGRAM_API_ID": "12345",
                            "TELEGRAM_API_HASH": "test_hash",
                        }):
                            await source.connect()
    finally:
        lock_conn.close()

    # Assert
    debug_messages = [call.args[0] for call in mock_logger.debug.call_args_list]
    assert any(
        "telegram session WAL checkpoint fallback" in msg for msg in debug_messages
    ), f"Expected WAL checkpoint fallback log. Got: {debug_messages}"


# ---------------------------------------------------------------------------
# Test 14: TelegramSource.disconnect with pre-closed sqlite connection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_telegram_disconnect_with_preclosed_connection(
    mock_config, state_manager, mock_logger
):
    """Covers TelegramSource.disconnect session._conn.close() except block (~L1286).

    session._conn is a sqlite connection that has already been closed. Calling
    close() again raises sqlite3.ProgrammingError. Except catches, debug logged.
    Uses self.logger (today's L1 fix: was module-level logging.getLogger).
    """
    from scripts.sentinel import TelegramSource

    source = TelegramSource(mock_config.__dict__, state_manager, mock_logger)

    # Build a client whose session._conn raises sqlite3.ProgrammingError on close()
    fake_conn = MagicMock()
    fake_conn.close.side_effect = sqlite3.ProgrammingError(
        "Cannot operate on a closed database."
    )

    fake_session = MagicMock()
    fake_session._conn = fake_conn

    source.client = AsyncMock()
    source.client.is_connected = MagicMock(return_value=False)  # skip disconnect()
    source.client.session = fake_session

    await source.disconnect()

    debug_messages = [call.args[0] for call in mock_logger.debug.call_args_list]
    assert any(
        "session _conn close fallback" in msg for msg in debug_messages
    ), f"Expected session _conn close fallback log. Got: {debug_messages}"


# ---------------------------------------------------------------------------
# Test 15: check_status prints fallback message on corrupt state file
# ---------------------------------------------------------------------------

def test_status_prints_on_corrupt_state(tmp_state_dir, capsys):
    """Covers check_status digest-print except block (~L2198).

    Existing PID file + corrupt state.json -> json.JSONDecodeError ->
    except catches -> stderr message printed. Uses capsys (not caplog)
    because emission is via `print(..., file=sys.stderr)`.
    """
    pid_file = tmp_state_dir / "sentinel.pid"
    state_file = tmp_state_dir / "state.json"

    pid_file.write_text(str(os.getpid()))
    state_file.write_text("{ not valid json")

    with patch("scripts.sentinel.PID_FILE", pid_file), patch("scripts.sentinel.STATE_FILE", state_file):
        # Both guards, not just liveness. Since 2026-08-25 `check_status` also
        # asks whether the live PID is THIS daemon before saying it is running,
        # because `os.kill(pid, 0)` proves only that some process holds that
        # number. Without this second patch the pytest process itself answers
        # "alive but not sentinel", check_status returns early, and the state
        # file this test is about is never read at all.
        with patch("scripts.sentinel._is_pid_alive", return_value=True), \
             patch("scripts.sentinel._pid_is_sentinel", return_value=True):
            from scripts.sentinel import check_status
            check_status()  # must not raise

    captured = capsys.readouterr()
    # The fallback print goes to stderr with "state file unreadable" text
    combined = captured.err + captured.out
    assert "state file unreadable" in combined, (
        f"Expected 'state file unreadable' in output. stdout={captured.out!r} "
        f"stderr={captured.err!r}"
    )


# ---------------------------------------------------------------------------
# Test 16: Telegram disconnect-for-sleep fails (post-cycle disconnect raises)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_telegram_disconnect_during_sleep_fails(
    mock_config, mock_logger, tmp_state_dir
):
    """The disconnect-for-sleep failure is GUARDED in sentinel.py.

    What this test used to do, and why it was replaced on 2026-08-23: it built an
    `AsyncMock`, re-implemented the try/except inline in its own body, and
    asserted on the mock logger. Its docstring claimed it constructed a Sentinel;
    it did not. Deleting the whole except block from `scripts/sentinel.py` left it
    green -- it tested Python's `try`, not this workspace.

    What this test does now, and its honest limit: it reads the source and pins
    that the disconnect call sits inside a `try` with a broad `except` that logs
    the named fallback. That catches the regression that actually happens -- the
    guard being removed or narrowed -- and it does NOT prove runtime behaviour.
    Proving that needs a constructed Sentinel, which needs the daemon fixture
    this repo does not carry, because sentinel runs on the Steward VM.
    """
    import ast

    src = (Path(__file__).resolve().parents[2] / "scripts" / "sentinel.py")
    tree = ast.parse(src.read_text(encoding="utf-8"))

    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        body = ast.unparse(ast.Module(body=node.body, type_ignores=[]))
        if "telegram_source.disconnect()" not in body:
            continue
        for handler in node.handlers:
            names = ast.unparse(handler.type) if handler.type else "Exception"
            logged = ast.unparse(ast.Module(body=handler.body, type_ignores=[]))
            if "Exception" in names and "disconnect-for-sleep fallback" in logged:
                guarded = True

    assert guarded, (
        "sentinel.py no longer wraps the sleep-transition "
        "telegram_source.disconnect() in a try/except that logs "
        "'Telegram disconnect-for-sleep fallback'")


# ---------------------------------------------------------------------------
# Test 17: Telegram retry-disconnect fails (second disconnect raises)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_telegram_retry_disconnect_fails_second_disconnect(
    mock_logger
):
    """The retry-loop disconnect failure is GUARDED in sentinel.py.

    Same replacement as the sleep-transition test above, for the same reason:
    the old body mocked a source, re-ran the try/except itself, and would have
    stayed green with the production guard deleted. Same honest limit too --
    this pins that the guard exists, not that it behaves at runtime.
    """
    import ast

    src = (Path(__file__).resolve().parents[2] / "scripts" / "sentinel.py")
    tree = ast.parse(src.read_text(encoding="utf-8"))

    guarded = any(
        isinstance(node, ast.Try)
        and "telegram_source.disconnect()" in ast.unparse(
            ast.Module(body=node.body, type_ignores=[]))
        and any("retry-disconnect fallback" in ast.unparse(
            ast.Module(body=h.body, type_ignores=[])) for h in node.handlers)
        for node in ast.walk(tree)
    )
    assert guarded, (
        "sentinel.py no longer wraps the retry-loop "
        "telegram_source.disconnect() in a try/except that logs "
        "'Telegram retry-disconnect fallback'")
