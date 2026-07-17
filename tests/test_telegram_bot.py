"""Regression tests for the extracted scripts/utils/telegram_bot.py wrapper.

Confirms CAP-1: the shared TelegramBot class builds the correct request shape,
always redacts the bot token from error/log output, routes errors through the
injectable on_error callback (or the module logger when omitted, without
raising a secondary error), and send_dm still rejects a non-integer user_id.

Real Telegram is NEVER touched: requests.post is monkeypatched throughout.

Run: python3 -m pytest tests/test_telegram_bot.py
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import telegram_bot as tb_mod
from scripts.utils.telegram_bot import TelegramAPIError, TelegramBot

TOKEN = "123456:AA-fake-secret-token-value"


class FakeResponse:
    def __init__(self, status_code, json_data, ok=None):
        self.status_code = status_code
        self._json_data = json_data
        self.ok = ok if ok is not None else (200 <= status_code < 300)
        self.text = str(json_data)

    def json(self):
        return self._json_data


def test_send_message_builds_correct_payload(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse(200, {"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr(tb_mod.requests, "post", fake_post)

    bot = TelegramBot(TOKEN)
    result = bot.send_message("-100123", "hello")

    assert result == {"message_id": 1}
    assert captured["url"] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert captured["json"]["chat_id"] == "-100123"
    assert captured["json"]["text"] == "hello"
    assert captured["json"]["parse_mode"] == "Markdown"
    assert captured["json"]["disable_web_page_preview"] is True


def test_send_message_omits_parse_mode_when_falsy(monkeypatch):
    """parse_mode=None/'' must be omitted entirely so Telegram sends plain text
    and never tries to parse Markdown entities (would 400 on an unbalanced _)."""
    captured = {}

    def fake_post(url, json, timeout):
        captured["json"] = json
        return FakeResponse(200, {"ok": True, "result": {"message_id": 2}})

    monkeypatch.setattr(tb_mod.requests, "post", fake_post)

    bot = TelegramBot(TOKEN)
    bot.send_message("-100123", "path is 2026-07-17_odin-reflect-proposal.md", parse_mode=None)

    assert "parse_mode" not in captured["json"]
    assert captured["json"]["text"] == "path is 2026-07-17_odin-reflect-proposal.md"


def test_401_raises_with_token_redacted(monkeypatch):
    def fake_post(url, json, timeout):
        # Simulate Telegram echoing the bad token back in its description,
        # to prove _redact() scrubs it even from data returned by the API.
        return FakeResponse(
            401,
            {"ok": False, "error_code": 401, "description": f"Unauthorized: {TOKEN}"},
        )

    monkeypatch.setattr(tb_mod.requests, "post", fake_post)

    bot = TelegramBot(TOKEN)
    with pytest.raises(TelegramAPIError) as exc_info:
        bot.send_message("-100123", "hello")

    message = str(exc_info.value)
    assert TOKEN not in message
    assert "<REDACTED_TOKEN>" in message
    assert exc_info.value.status_code == 401


def test_on_error_invoked_with_redacted_message_on_failure(monkeypatch):
    def fake_post(url, json, timeout):
        return FakeResponse(401, {"ok": False, "error_code": 401, "description": "bad token"})

    monkeypatch.setattr(tb_mod.requests, "post", fake_post)

    captured_messages = []
    bot = TelegramBot(TOKEN, on_error=captured_messages.append)

    with pytest.raises(TelegramAPIError):
        bot.send_message("-100123", "hello")

    assert len(captured_messages) == 1
    assert TOKEN not in captured_messages[0]


def test_omitting_on_error_falls_back_to_logger_without_raising_secondary_error(monkeypatch):
    def fake_post(url, json, timeout):
        return FakeResponse(401, {"ok": False, "error_code": 401, "description": "bad token"})

    monkeypatch.setattr(tb_mod.requests, "post", fake_post)

    bot = TelegramBot(TOKEN)  # no on_error supplied
    # The only exception raised must be TelegramAPIError itself - the logging
    # fallback inside _log_error must not itself raise.
    with pytest.raises(TelegramAPIError):
        bot.send_message("-100123", "hello")


def test_send_dm_rejects_non_integer_user_id():
    bot = TelegramBot(TOKEN)
    with pytest.raises(TypeError):
        bot.send_dm("not-an-int", "hello")
