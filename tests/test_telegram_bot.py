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

TOKEN = "123456:AA-fake-secret-token-value"  # noqa: S105 - fake token fixture, not a real secret


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


@pytest.mark.parametrize("falsy", [None, ""], ids=["none", "empty-string"])
def test_send_message_omits_parse_mode_when_falsy(monkeypatch, falsy):
    """parse_mode=None/'' must be omitted entirely so Telegram sends plain text
    and never tries to parse Markdown entities (would 400 on an unbalanced _).

    BOTH falsy values, because the contract above names both and the guard is a
    truthiness test. Measured 2026-09-01: with only the `None` case here,
    rewriting `if parse_mode:` to `if parse_mode is not None:` left this file
    green, and `parse_mode=""` then reached Telegram as an empty parse mode.
    """
    captured = {}

    def fake_post(url, json, timeout):
        captured["json"] = json
        return FakeResponse(200, {"ok": True, "result": {"message_id": 2}})

    monkeypatch.setattr(tb_mod.requests, "post", fake_post)

    bot = TelegramBot(TOKEN)
    bot.send_message("-100123", "path is 2026-07-17_odin-reflect-proposal.md",
                     parse_mode=falsy)

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


def test_a_200_carrying_ok_false_is_an_error_not_a_result(monkeypatch):
    """Telegram answers some failures with HTTP 200 and `{"ok": false}`.

    `_handle_response` reads BOTH `r.ok` and the body's own `ok` field, and
    only the second one catches this shape. Nothing in the tree exercised it:
    measured 2026-09-01 by rewriting the condition to `if not r.ok:` and running
    every test file in the tree that names TelegramBot, which stayed green. The
    consequence is not cosmetic. `_call` would return `data.get("result")`,
    which is None here, and `telegram_notify.notify()` reads a clean return as a
    delivered message, so a refused send would be reported as sent.
    """
    def fake_post(url, json, timeout):
        return FakeResponse(
            200,
            {"ok": False, "error_code": 400, "description": "chat not found"},
        )

    monkeypatch.setattr(tb_mod.requests, "post", fake_post)

    bot = TelegramBot(TOKEN)
    with pytest.raises(TelegramAPIError) as exc_info:
        bot.send_message("-100123", "hello")

    assert exc_info.value.telegram_description == "chat not found"
    assert "chat not found" in str(exc_info.value)


def test_an_http_failure_is_an_error_even_when_the_body_says_ok(monkeypatch):
    """The other half of the same condition, and the one nothing measured.

    `_handle_response` refuses on `not r.ok OR not data["ok"]`. The test above
    covers the second disjunct; every other failing case in this repository
    sends a non-2xx status TOGETHER with an `"ok": false` body, so the body
    alone accounts for all of them and the status check was inert under test.
    MEASURED 2026-09-01: rewriting the condition to `if not data.get("ok"):`
    left this file green, and green across every neighbour that names
    TelegramBot as well (test_a_promise_that_misha_would_help,
    test_a_bot_that_said_it_was_alive_while_it_was_not, the three fireside
    files, and the six notifier files).

    The shape that separates them is an intermediary rather than Telegram: a
    proxy, a captive portal or a CDN error page answering 502 while a cached or
    synthesised body still reads `{"ok": true}`. `_call` would then return
    `data.get("result")` for a request that never reached Telegram at all.
    """
    def fake_post(url, json, timeout):
        return FakeResponse(502, {"ok": True, "result": {"message_id": 9}})

    monkeypatch.setattr(tb_mod.requests, "post", fake_post)

    bot = TelegramBot(TOKEN)
    with pytest.raises(TelegramAPIError) as exc_info:
        bot.send_message("-100123", "hello")

    assert exc_info.value.status_code == 502


@pytest.mark.parametrize("body", [[], "plain text", 7, True],
                         ids=["list", "string", "number", "bool"])
def test_a_json_body_that_is_not_an_object_is_refused(monkeypatch, body):
    """`isinstance(data, dict)` guards four shapes; only `None` was measured.

    The guard was added on 2026-08-26 after `data.get("ok")` raised a bare
    AttributeError on a non-object body, which walks past every caller's
    `except TelegramAPIError`. Nothing pinned it: MEASURED 2026-09-01 by
    narrowing the guard to `if data is None:` and running this file plus every
    neighbour that names TelegramBot, all green. `None` alone is the one shape
    the narrowed guard still catches, so the case that would have failed is
    exactly the case nobody wrote.

    A truncated write, a proxy error page parsed as JSON, and a captive portal
    all produce one of these four rather than `null`.
    """
    def fake_post(url, json, timeout):
        return FakeResponse(200, body)

    monkeypatch.setattr(tb_mod.requests, "post", fake_post)

    bot = TelegramBot(TOKEN)
    with pytest.raises(TelegramAPIError) as exc_info:
        bot.send_message("-100123", "hello")

    assert "not an object" in str(exc_info.value)
    assert type(body).__name__ in str(exc_info.value)


def test_a_json_object_body_is_still_read_as_one(monkeypatch):
    """Anchor: refusing every body would satisfy the test above."""
    def fake_post(url, json, timeout):
        return FakeResponse(200, {"ok": True, "result": {"message_id": 3}})

    monkeypatch.setattr(tb_mod.requests, "post", fake_post)

    assert TelegramBot(TOKEN).send_message("-100123", "hi") == {"message_id": 3}


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
