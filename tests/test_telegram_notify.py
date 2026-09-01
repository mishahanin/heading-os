"""Regression tests for scripts/utils/telegram_notify.py's notify().

Confirms CAP-2 (never raises; degrades to False on missing token, missing/
falsy target, or an unresolvable "me"/"self"/"saved" sentinel; True on a
mocked success) and CAP-2a (the system-wide "no self-send" invariant): none
of the five migrated notification scripts may carry "me"/"self"/"saved" as a
literal, reachable fallback value.

Real Telegram is NEVER touched: TelegramBot.send_message is monkeypatched.

Run: python3 -m pytest tests/test_telegram_notify.py
"""
import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import telegram_notify as notify_mod
from scripts.utils.telegram_bot import TelegramAPIError, TelegramBot

REPO_ROOT = Path(__file__).resolve().parent.parent

MIGRATED_FILES = [
    REPO_ROOT / "scripts" / "odin-cadence-notify.py",
    REPO_ROOT / "scripts" / "ops-radar-notify.py",
    REPO_ROOT / "scripts" / "council-models-notify.py",
    REPO_ROOT / "scripts" / "reminders-notify.py",
    REPO_ROOT / "scripts" / "utils" / "alert.py",
]

_UNRESOLVABLE = {"me", "self", "saved"}


@pytest.fixture(autouse=True)
def no_real_env_load(monkeypatch):
    """Never let notify() read the real .env during tests."""
    monkeypatch.setattr(notify_mod, "load_env", lambda *a, **kw: None)


@pytest.fixture
def sends(monkeypatch):
    """Record every send instead of raising on one.

    A stub that raises `AssertionError` reports "the guard held" only while the
    code under test happens to catch nothing wider than `TelegramAPIError`. The
    day a handler around the send widens to `except Exception`, the test's own
    AssertionError is swallowed by the code under test and converted into the
    same quiet False a working guard returns, so the test passes whether the
    guard is there or not. Asserting on the recorded calls cannot be turned
    green that way, and it is the same rule as asserting a refusal by the
    ABSENCE of the side effect rather than by the presence of a log line.
    """
    calls: list[tuple] = []

    def record(self, chat_id, text, **kwargs):
        calls.append((chat_id, text))
        return {"message_id": len(calls)}

    monkeypatch.setattr(TelegramBot, "send_message", record)
    return calls


# A `.env` line typed as `TELEGRAM_NOTIFY_BOT_TOKEN=` with nothing after it, and
# the same line with a stray space, are the two ways this variable is present
# and useless. `tests/conftest.py` produces the first shape on every run when it
# blanks the name, so it is the state the whole suite actually executes in.
#
# A whitespace-only value is deliberately NOT in the list. `parse_env_line`
# strips, so `TELEGRAM_NOTIFY_BOT_TOKEN=   ` in a `.env` arrives as `""` and is
# already the blank case; only the quoted form `="   "` survives it, and there
# the transport degrades correctly anyway (a space in the URL raises
# `requests.InvalidURL`, which `_call` wraps). Asserting on it would be a
# negative case chosen for being easy to fail rather than for being reachable.
@pytest.mark.parametrize("token", [None, ""], ids=["absent", "blank"])
def test_a_token_that_is_missing_or_blank_sends_nothing_and_never_raises(
        monkeypatch, sends, token):
    """Only the ABSENT case was covered, and absent is the easy one.

    MEASURED 2026-09-01: narrowing the guard to `if token is None:` left this
    file, and every neighbour that names telegram_notify, green. With that
    narrowing a blank token reaches `TelegramBot("")`, whose constructor raises
    ValueError - out of a function whose docstring says it NEVER raises, called
    by six timer-driven scripts and the checkpoint hook with no human in the
    loop. A notifier that raises inside a systemd timer fails the job it was
    attached to, not just the notification.

    The target is declared so the allowlist is not what refuses; the token
    check has to be.
    """
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", "-100123")
    if token is None:
        monkeypatch.delenv("TELEGRAM_NOTIFY_BOT_TOKEN", raising=False)
    else:
        monkeypatch.setenv("TELEGRAM_NOTIFY_BOT_TOKEN", token)

    assert notify_mod.notify("-100123", "hi") is False
    assert sends == []


@pytest.mark.parametrize("target", ["me", "Self", "SAVED", "", None])
def test_unresolvable_targets_return_false_no_call(monkeypatch, sends, target):
    monkeypatch.setenv("TELEGRAM_NOTIFY_BOT_TOKEN", "fake-token")
    assert notify_mod.notify(target, "hi") is False
    assert sends == []


def test_success_returns_true(monkeypatch):
    monkeypatch.setenv("TELEGRAM_NOTIFY_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", "-100123")
    monkeypatch.setattr(TelegramBot, "send_message", lambda self, *a, **kw: {"message_id": 1})
    assert notify_mod.notify("-100123", "hi") is True


def test_notify_sends_as_plain_text(monkeypatch):
    """notify() must request plain text (parse_mode falsy) so a message with an
    unbalanced Markdown char (a file path's _) cannot 400. Guards the live bug
    found against '@headingos_bot' / '..._odin-reflect-proposal.md'."""
    monkeypatch.setenv("TELEGRAM_NOTIFY_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", "-100123")
    captured = {}

    def capture(self, target, message, **kwargs):
        captured["kwargs"] = kwargs
        return {"message_id": 1}

    monkeypatch.setattr(TelegramBot, "send_message", capture)
    assert notify_mod.notify("-100123", "path 2026-07-17_odin-reflect-proposal.md") is True
    assert captured["kwargs"].get("parse_mode") is None


def test_telegram_api_error_returns_false_not_raised(monkeypatch):
    """The `reached` recorder is the load-bearing half of this test.

    On 2026-08-30 `notify()` gained a recipient allowlist, and an undeclared
    target now refuses before the transport is ever built. That turned this
    case green for the wrong reason: it still returned False, but it had
    stopped exercising the transport-error contract it exists to hold. A
    `False` that never reached the transport proves nothing here, so the
    target is declared above and the arrival is asserted below.
    """
    monkeypatch.setenv("TELEGRAM_NOTIFY_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", "-100123")
    reached = []

    def raise_api_error(self, *a, **kw):
        reached.append(True)
        raise TelegramAPIError("boom", status_code=500)

    monkeypatch.setattr(TelegramBot, "send_message", raise_api_error)
    assert notify_mod.notify("-100123", "hi") is False
    assert reached, "the allowlist refused first; the transport error was never raised"


def _string_const(node):
    """Return the str value of a Constant/Str AST node, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def test_no_migrated_script_has_me_self_saved_as_a_reachable_fallback():
    """CAP-2a: the 'me'/'self'/'saved' sentinel must not be a literal default
    or return value in any of the five migrated notification scripts.

    AST-based (not a text/regex scan) so it inspects actual code - assignment
    targets named DEFAULT_RECIPIENT and return statements - and does not false
    -positive on the sentinel merely being mentioned in a docstring or comment
    (e.g. this file's own module docstring, or alert.py's defect-history note).
    """
    offenders = []
    for path in MIGRATED_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value = _string_const(node.value)
                if value and value.lower() in _UNRESOLVABLE:
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "DEFAULT_RECIPIENT":
                            offenders.append(
                                f"{path.relative_to(REPO_ROOT)}:{node.lineno}: "
                                f"DEFAULT_RECIPIENT = {value!r}"
                            )
            elif isinstance(node, ast.Return) and node.value is not None:
                value = _string_const(node.value)
                if value and value.lower() in _UNRESOLVABLE:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: return {value!r}")
    assert offenders == [], "found 'me'/'self'/'saved' as a reachable fallback:\n" + "\n".join(offenders)


# ============================================================
# CAP-2, the transport half: "never raises" must survive every requests error
# ============================================================
@pytest.mark.parametrize("err_name", [
    "TooManyRedirects", "InvalidURL", "ChunkedEncodingError",
    "ContentDecodingError", "RetryError",
])
def test_notify_never_raises_on_any_requests_error(monkeypatch, err_name):
    """`_call` caught only ConnectionError and Timeout, so five sibling
    `requests` errors escaped unwrapped and `notify` -- which catches only
    TelegramAPIError -- re-raised them. Six timer-driven scripts call notify and
    rely on the documented contract that it degrades to False instead.
    """
    import requests
    exc_cls = getattr(requests.exceptions, err_name)

    monkeypatch.setenv("TELEGRAM_NOTIFY_BOT_TOKEN", "1234567:AAtest-token")
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", "12345")
    reached = []

    def _boom(*a, **k):
        reached.append(True)
        raise exc_cls("simulated")
    monkeypatch.setattr(requests, "post", _boom)

    assert notify_mod.notify("12345", "hello") is False
    assert reached, "the allowlist refused first; requests.post was never called"


def test_a_raised_requests_error_does_not_leak_the_token(monkeypatch, caplog):
    import logging
    import requests
    monkeypatch.setenv("TELEGRAM_NOTIFY_BOT_TOKEN", "1234567:AAsecret-value-xyz")
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", "12345")
    reached = []

    def _boom(*a, **k):
        reached.append(True)
        raise requests.exceptions.TooManyRedirects(
            "https://api.telegram.org/bot1234567:AAsecret-value-xyz/sendMessage")
    monkeypatch.setattr(requests, "post", _boom)

    with caplog.at_level(logging.DEBUG):
        assert notify_mod.notify("12345", "hello") is False
    assert reached, (
        "the allowlist refused first, so no URL carrying the token was ever built; "
        "an empty log would then pass this test while proving nothing about redaction")
    assert "AAsecret-value-xyz" not in caplog.text
