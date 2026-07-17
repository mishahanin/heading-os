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
def send_message_must_not_be_called(monkeypatch):
    def boom(self, *args, **kwargs):
        raise AssertionError("send_message must not be called for this case")

    monkeypatch.setattr(TelegramBot, "send_message", boom)


def test_missing_token_returns_false_no_call(monkeypatch, send_message_must_not_be_called):
    monkeypatch.delenv("TELEGRAM_NOTIFY_BOT_TOKEN", raising=False)
    assert notify_mod.notify("-100123", "hi") is False


@pytest.mark.parametrize("target", ["me", "Self", "SAVED", "", None])
def test_unresolvable_targets_return_false_no_call(
    monkeypatch, send_message_must_not_be_called, target
):
    monkeypatch.setenv("TELEGRAM_NOTIFY_BOT_TOKEN", "fake-token")
    assert notify_mod.notify(target, "hi") is False


def test_success_returns_true(monkeypatch):
    monkeypatch.setenv("TELEGRAM_NOTIFY_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(TelegramBot, "send_message", lambda self, *a, **kw: {"message_id": 1})
    assert notify_mod.notify("-100123", "hi") is True


def test_notify_sends_as_plain_text(monkeypatch):
    """notify() must request plain text (parse_mode falsy) so a message with an
    unbalanced Markdown char (a file path's _) cannot 400. Guards the live bug
    found against '@headingos_bot' / '..._odin-reflect-proposal.md'."""
    monkeypatch.setenv("TELEGRAM_NOTIFY_BOT_TOKEN", "fake-token")
    captured = {}

    def capture(self, target, message, **kwargs):
        captured["kwargs"] = kwargs
        return {"message_id": 1}

    monkeypatch.setattr(TelegramBot, "send_message", capture)
    assert notify_mod.notify("-100123", "path 2026-07-17_odin-reflect-proposal.md") is True
    assert captured["kwargs"].get("parse_mode") is None


def test_telegram_api_error_returns_false_not_raised(monkeypatch):
    monkeypatch.setenv("TELEGRAM_NOTIFY_BOT_TOKEN", "fake-token")

    def raise_api_error(self, *a, **kw):
        raise TelegramAPIError("boom", status_code=500)

    monkeypatch.setattr(TelegramBot, "send_message", raise_api_error)
    assert notify_mod.notify("-100123", "hi") is False


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
