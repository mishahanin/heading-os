"""Severity -> channel matrix tests for the alert router (R14, Step 4).

Asserts that scripts.utils.alert.alert() fires the right channels per severity
and that a Telegram-send failure degrades to card+log without raising. The live
Action Queue (outputs/operations/action-queue/queue.json) and real Telegram are
NEVER touched: append_cards is monkeypatched to a recorder, and the Telegram
subprocess is monkeypatched. get_workspace_root is pointed at a temp dir.

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

    Returns an object capturing the append_cards calls and the Telegram
    subprocess argv, with a knob to make the Telegram send fail.
    """

    class Rec:
        def __init__(self):
            self.cards = []          # list of card lists passed to append_cards
            self.telegram_calls = []  # argv lists passed to subprocess.run
            self.telegram_ok = True   # flip to simulate a send failure

    rec = Rec()

    # Point the router at a temp workspace and give it a telegram client file
    # so _send_telegram does not short-circuit on a missing client.
    client = tmp_path / ".claude" / "skills" / "telegram" / "scripts"
    client.mkdir(parents=True)
    (client / "telegram_client.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(alert_mod, "get_workspace_root", lambda: tmp_path)

    def fake_append_cards(workspace_root, cards):
        rec.cards.append(cards)
        return {"ok": True, "added": len(cards), "skipped": 0, "ids": ["x"]}

    monkeypatch.setattr(alert_mod, "_aq_append_fn", fake_append_cards)

    class FakeCompleted:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = ""
            self.stderr = "" if rc == 0 else "boom"

    def fake_run(argv, **kwargs):
        rec.telegram_calls.append(argv)
        return FakeCompleted(0 if rec.telegram_ok else 1)

    monkeypatch.setattr(alert_mod.subprocess, "run", fake_run)

    return rec


def test_critical_fires_all_three(recorder):
    fired = alert_mod.alert("critical", "daemon sentinel silent 6m", source="watchdog")
    assert fired == {"telegram": True, "card": True, "log": True}
    assert len(recorder.cards) == 1
    assert recorder.cards[0][0]["action_type"] == "alert"
    assert recorder.cards[0][0]["priority"] == "P1"
    assert len(recorder.telegram_calls) == 1
    # argv: [python, client_path, "send", target, message]
    argv = recorder.telegram_calls[0]
    assert argv[2] == "send"
    assert argv[3] == "me"  # default target


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


def test_telegram_subprocess_raise_does_not_propagate(recorder, monkeypatch):
    def boom(argv, **kwargs):
        raise OSError("no such session")

    monkeypatch.setattr(alert_mod.subprocess, "run", boom)
    # Must not raise; degrades to card+log.
    fired = alert_mod.alert("critical", "daemon eval-drift down", source="watchdog")
    assert fired["telegram"] is False
    assert fired["card"] is True
    assert fired["log"] is True
