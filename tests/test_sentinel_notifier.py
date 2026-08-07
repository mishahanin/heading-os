"""Sentinel notification delivery goes through the notifications bot.

Guards the 2026-08-07 transport switch: alerts used to be posted by the
userbot into a named channel ("Urgent Stuff for M"), which does not reliably
push-notify. They now go through scripts/utils/telegram_notify (Bot API), to a
bot-resolvable id read from .env. A human-readable channel name must never
reach the transport again -- a bot cannot resolve one.
"""
import asyncio
import importlib
import inspect
import logging
import sys
import threading

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

    The assertion this replaces was `"Urgent Stuff" not in
    resolve_notify_target()` with both env vars deleted, which is
    `"Urgent Stuff" not in ""` — true of every implementation there could be,
    including one that reads the yaml. It restated
    `test_target_empty_when_unconfigured` above and proved nothing beyond it.
    Naming a vacuous test inside the subsystem built to name vacuous tests is
    the whole of this docstring.

    What actually forbids the leak is structural: the resolver takes no config
    at all and reads two named environment variables, so that is what is pinned
    here. The signature carries no parameter a config could arrive through, and
    a config-shaped name in the environment is ignored rather than picked up.
    """
    monkeypatch.setattr(sentinel, "load_env", lambda *a, **k: None)
    monkeypatch.delenv("SENTINEL_TELEGRAM_TARGET", raising=False)
    monkeypatch.delenv("ODIN_CADENCE_TELEGRAM_TARGET", raising=False)
    monkeypatch.setenv("SENTINEL_NOTIFICATION_TARGET_CHAT", "Urgent Stuff for M")

    assert list(inspect.signature(sentinel.resolve_notify_target).parameters) == []
    assert sentinel.resolve_notify_target() == ""


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
    """A failed send is reported as a failure and never as a success.

    Both halves are asserted, and the second is the one that was missing. The
    2026-08-07 refactor made `send_digest` and `send_notification` log their
    success line only on a truthy `_send`, and nothing held them to it: an
    unguarded version that logged "Digest sent" after a failed send passed the
    whole file. A daemon whose log says it delivered an alert it dropped is
    worse than one that says nothing, because the log is the only surface an
    operator has to notice the drop at all.
    """
    monkeypatch.setattr(sentinel.telegram_notify, "notify", lambda target, message: False)
    n = sentinel.TelegramNotifier("", logger)
    with caplog.at_level(logging.INFO):
        asyncio.run(n.send_digest("body"))
        asyncio.run(n.send_notification(
            {"subject": "Contract deadline", "source": "email"},
            {"urgency_score": 9, "reason": "hard deadline today"},
        ))
    assert any("Notification send failed" in r.message for r in caplog.records)
    assert not any("Digest sent" in r.message for r in caplog.records)
    assert not any("Notification sent" in r.message for r in caplog.records)


def test_the_blocking_transport_runs_off_the_event_loop(monkeypatch, logger):
    """`notify` is blocking urllib, so it must not run on the daemon's loop.

    `_send` says so in a comment and hands the call to `asyncio.to_thread`;
    nothing held it to that. Replacing the `to_thread` hop with a direct
    blocking call passed every test in this file. The cost of the regression is
    not cosmetic: Sentinel's heartbeat and scheduler share that loop, so a slow
    Telegram HTTP call would stall the liveness signal the fleet-health check
    reads, and a stalled heartbeat is indistinguishable from a dead daemon.

    Asserted by thread identity rather than by patching `asyncio.to_thread`,
    because the claim is that the call ran off the loop thread and not that one
    particular helper was invoked to get it there.
    """
    seen: list[int] = []
    monkeypatch.setattr(
        sentinel.telegram_notify, "notify",
        lambda target, message: seen.append(threading.get_ident()) or True,
    )
    n = sentinel.TelegramNotifier("100200300", logger)

    async def drive():
        await n.send_digest("body")
        return threading.get_ident()

    loop_thread = asyncio.run(drive())

    assert len(seen) == 1
    assert seen[0] != loop_thread


def test_notifier_needs_no_telethon_client(monkeypatch, logger):
    """The notifier must deliver with no Telegram session and no telethon.

    This is what lets alerts survive a Telegram read-side outage, and it is the
    reason the notifier is built before (and independently of) the reading
    connect in `Sentinel.start`.

    The assertion this replaces was `not hasattr(n, "client")` — a negative
    existence check on a private attribute name, which a rename to `_client`
    would leave green while the coupling returned. The repo's own instrument
    agreed: `probe --after-build tests/test_sentinel_notifier.py` named this
    test as having stayed green while `scripts.sentinel` was replaced by all
    three wrong implementations. What is pinned now is the positive property
    the old check stood for: a send completes with `telethon` unimportable.
    """
    monkeypatch.setitem(sys.modules, "telethon", None)
    sent = []
    monkeypatch.setattr(
        sentinel.telegram_notify, "notify",
        lambda target, message: sent.append((target, message)) or True,
    )

    n = sentinel.TelegramNotifier("100200300", logger)
    asyncio.run(n.send_digest("body"))

    assert sent == [("100200300", "body")]


def test_a_whitespace_only_override_does_not_mask_the_fallback(monkeypatch):
    """A blank-but-present specific target must not silently disable alerts.

    The resolver stripped once, AFTER the `or` chain, so a whitespace-only
    `SENTINEL_TELEGRAM_TARGET` was truthy, won the chain over a valid
    `ODIN_CADENCE_TELEGRAM_TARGET`, and only then collapsed to "". Measured
    2026-08-07 before the fix: `("   ", "100200300")` resolved to `""`, the
    daemon logged "alerts will NOT be delivered", and ran on. A trailing space
    on a hand-edited `.env` line is the ordinary way a value becomes
    whitespace-only, and the whole point of the fallback is that a missing
    specific target is survivable.
    """
    monkeypatch.setattr(sentinel, "load_env", lambda *a, **k: None)
    monkeypatch.setenv("SENTINEL_TELEGRAM_TARGET", "   ")
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", "100200300")

    assert sentinel.resolve_notify_target() == "100200300"
