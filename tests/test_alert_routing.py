"""Severity -> channel matrix tests for the alert router (R14, Step 4).

Asserts that scripts.utils.alert.alert() fires the right channels per severity
and that a Telegram-send failure degrades to card+log without raising. The live
Action Queue (outputs/operations/action-queue/queue.json) and real Telegram are
NEVER touched: append_cards is monkeypatched to a recorder, and
telegram_notify.notify() is monkeypatched (post-migration to the dedicated
notifications bot - see plans/2026-07-17-heading-os-notification-bot.md). A
fake ODIN_CADENCE_TELEGRAM_TARGET is set in the fixture so _telegram_target()
resolves to a real (fake) channel instead of falling through to "" (which
notify() would legitimately reject as unconfigured - not the failure mode this
matrix is testing). get_workspace_root is pointed at a temp dir.

Matrix under test:
    critical -> telegram + card + log
    warning  ->            card + log   (no telegram)
    info     ->                   log   (log only)
    + a Telegram-send failure on a critical alert degrades to card+log, no raise.

(The watchdog-dedup test belongs to Step 9 / Wave 3 - not written here.)

Run: python3 -m pytest tests/test_alert_routing.py
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import alert as alert_mod


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    """Isolate alert() from the live queue and real Telegram.

    Returns an object capturing the append_cards calls and the
    telegram_notify.notify(target, message) calls, with a knob to make the
    notify call fail.
    """

    class Rec:
        def __init__(self):
            self.cards = []          # list of card lists passed to append_cards
            self.telegram_calls = []  # (target, message) tuples passed to notify()
            self.telegram_ok = True   # flip to simulate a send failure

    rec = Rec()

    monkeypatch.setattr(alert_mod, "get_workspace_root", lambda: tmp_path)
    # A real (fake) channel target so _telegram_target() resolves to it
    # instead of falling through to "" (unconfigured) - that's a distinct
    # case from the send-success/-failure matrix this file tests.
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", "-100test")

    def fake_append_cards(workspace_root, cards):
        rec.cards.append(cards)
        return {"ok": True, "added": len(cards), "skipped": 0, "ids": ["x"]}

    monkeypatch.setattr(alert_mod, "_aq_append_fn", fake_append_cards)

    def fake_notify(target, message):
        rec.telegram_calls.append((target, message))
        return rec.telegram_ok

    monkeypatch.setattr(alert_mod.telegram_notify, "notify", fake_notify)

    return rec


def test_critical_fires_all_three(recorder):
    fired = alert_mod.alert("critical", "daemon sentinel silent 6m", source="watchdog")
    assert fired == {"telegram": True, "card": True, "log": True}
    assert len(recorder.cards) == 1
    assert recorder.cards[0][0]["action_type"] == "alert"
    assert recorder.cards[0][0]["priority"] == "P1"
    assert len(recorder.telegram_calls) == 1
    target, message = recorder.telegram_calls[0]
    assert target == "-100test"
    assert "daemon sentinel silent 6m" in message


def test_warning_skips_telegram(recorder):
    fired = alert_mod.alert("warning", "transient SMTP blip", source="executor")
    assert fired["card"] is True
    assert fired["log"] is True
    assert fired["telegram"] is False
    assert len(recorder.cards) == 1
    assert recorder.cards[0][0]["priority"] == "P2"
    assert recorder.telegram_calls == []  # never attempted


def test_info_is_log_only(recorder):
    fired = alert_mod.alert("info", "heartbeat resumed", source="watchdog")
    assert fired == {"telegram": False, "card": False, "log": True}
    assert recorder.cards == []          # no card
    assert recorder.telegram_calls == []  # no telegram


def test_telegram_failure_degrades_to_card_and_log(recorder):
    recorder.telegram_ok = False
    fired = alert_mod.alert("critical", "daemon bridge down", source="watchdog")
    # Did not raise; telegram reported failed but card + log still fired.
    assert fired["telegram"] is False
    assert fired["card"] is True
    assert fired["log"] is True
    assert len(recorder.cards) == 1            # card still created
    assert len(recorder.telegram_calls) == 1   # send was attempted, then failed


def test_telegram_notify_returning_false_does_not_propagate(recorder, monkeypatch):
    # telegram_notify.notify() is contracted to never raise (CAP-2) - its
    # worst case is returning False.
    #
    # This case is the EASY half and it is not the guard. The comment here used
    # to add that "there is no longer a raising transport call inside
    # _send_telegram to defend against", which stopped being true when the
    # handler went back into `_send_telegram` on 2026-08-30 after a raising
    # `notify` was measured propagating through `alert()`. A `return False` stub
    # exercises no handler at all: deleting `_send_telegram`'s try/except left
    # every test in this file passing - MEASURED 2026-09-01. The raising case
    # below is the one that binds it.
    def fake_notify(target, message):
        return False

    monkeypatch.setattr(alert_mod.telegram_notify, "notify", fake_notify)
    # Must not raise; degrades to card+log.
    fired = alert_mod.alert("critical", "daemon eval-drift down", source="watchdog")
    assert fired["telegram"] is False
    assert fired["card"] is True
    assert fired["log"] is True


@pytest.mark.parametrize("exc", [
    OSError("network unreachable"),   # the measured 2026-08-30 reproduction
    RuntimeError("bot token rejected"),
    ValueError("target is not a channel"),
])
def test_a_raising_telegram_transport_degrades_to_card_and_log(recorder,
                                                               monkeypatch,
                                                               exc):
    """`_send_telegram` enforces never-raises HERE, not by delegation.

    Three promises ride on this: the module docstring's "degrades to card+log
    and NEVER raises", `alert()`'s own "Never raises", and `_send_telegram`'s.
    The callers are watchdogs, so the alert fires exactly when the network is
    already broken - the one moment the contract has to hold. `notify` is
    contracted not to raise, but this handler exists precisely because the
    contract was once broken, and a guard that trusts a promise measures the
    promise rather than the guard.
    """
    def fake_notify(target, message):
        raise exc

    monkeypatch.setattr(alert_mod.telegram_notify, "notify", fake_notify)

    fired = alert_mod.alert("critical", "daemon bridge down", source="watchdog")

    assert fired["telegram"] is False
    assert fired["card"] is True, "the card channel was lost to a telegram error"
    assert fired["log"] is True
    assert len(recorder.cards) == 1
