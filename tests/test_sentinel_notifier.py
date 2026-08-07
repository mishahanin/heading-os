"""Sentinel notification delivery goes through the notifications bot.

Guards the 2026-08-07 transport switch: alerts used to be posted by the
userbot into a named channel ("Urgent Stuff for M"), which does not reliably
push-notify. They now go through scripts/utils/telegram_notify (Bot API), to a
bot-resolvable id read from .env. A human-readable channel name must never
reach the transport again -- a bot cannot resolve one.
"""
import asyncio
import importlib
import logging

import pytest

sentinel = importlib.import_module("scripts.sentinel")


@pytest.fixture
def logger():
    return logging.getLogger("test_sentinel_notifier")


# ---------------------------------------------------------------- target


def test_target_prefers_sentinel_specific_var(monkeypatch):
    monkeypatch.setattr(sentinel, "load_env", lambda *a, **k: None)
    monkeypatch.setenv("SENTINEL_TELEGRAM_TARGET", "111")
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", "222")
    assert sentinel.resolve_notify_target() == "111"


def test_target_falls_back_to_shared_var(monkeypatch):
    monkeypatch.setattr(sentinel, "load_env", lambda *a, **k: None)
    monkeypatch.delenv("SENTINEL_TELEGRAM_TARGET", raising=False)
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", "222")
    assert sentinel.resolve_notify_target() == "222"


def test_target_empty_when_unconfigured(monkeypatch):
    monkeypatch.setattr(sentinel, "load_env", lambda *a, **k: None)
    monkeypatch.delenv("SENTINEL_TELEGRAM_TARGET", raising=False)
    monkeypatch.delenv("ODIN_CADENCE_TELEGRAM_TARGET", raising=False)
    assert sentinel.resolve_notify_target() == ""


def test_target_never_reads_the_yaml_channel_name(monkeypatch):
    """The config's human-readable target_chat must not leak into the target.

    It named a channel for the userbot; the Bot API cannot resolve a name.
    """
    monkeypatch.setattr(sentinel, "load_env", lambda *a, **k: None)
    monkeypatch.delenv("SENTINEL_TELEGRAM_TARGET", raising=False)
    monkeypatch.delenv("ODIN_CADENCE_TELEGRAM_TARGET", raising=False)
    assert "Urgent Stuff" not in sentinel.resolve_notify_target()


# ---------------------------------------------------------------- delivery


def test_send_uses_the_bot_transport(monkeypatch, logger):
    sent = []
    monkeypatch.setattr(
        sentinel.telegram_notify, "notify",
        lambda target, message: sent.append((target, message)) or True,
    )
    n = sentinel.TelegramNotifier("100200300", logger)
    asyncio.run(n.send_digest("digest body"))
    assert sent == [("100200300", "digest body")]


def test_notification_is_formatted_then_sent(monkeypatch, logger):
    sent = []
    monkeypatch.setattr(
        sentinel.telegram_notify, "notify",
        lambda target, message: sent.append(message) or True,
    )
    n = sentinel.TelegramNotifier("100200300", logger)
    asyncio.run(n.send_notification(
        {"subject": "Contract deadline", "source": "email"},
        {"urgency_score": 9, "reason": "hard deadline today"},
    ))
    assert len(sent) == 1
    assert "Contract deadline" in sent[0]


def test_dry_run_sends_nothing(monkeypatch, logger):
    called = []
    monkeypatch.setattr(
        sentinel.telegram_notify, "notify",
        lambda target, message: called.append(1) or True,
    )
    n = sentinel.TelegramNotifier("100200300", logger, dry_run=True)
    asyncio.run(n.send_digest("body"))
    assert called == []


def test_failed_send_is_logged_and_never_raises(monkeypatch, logger, caplog):
    monkeypatch.setattr(sentinel.telegram_notify, "notify", lambda target, message: False)
    n = sentinel.TelegramNotifier("", logger)
    with caplog.at_level(logging.ERROR):
        asyncio.run(n.send_digest("body"))
    assert any("Notification send failed" in r.message for r in caplog.records)


def test_notifier_needs_no_telethon_client(logger):
    """The notifier must be constructible without a Telegram session.

    This is what lets alerts survive a Telegram read-side outage.
    """
    n = sentinel.TelegramNotifier("100200300", logger)
    assert not hasattr(n, "client")
