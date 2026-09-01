"""The reminders dispatcher: the due loop, and the transport it is wired to.

Until 2026-09-01 this file tested `send_due` alone, with `send_fn` injected. That
left the half that actually reaches Telegram - `_telegram_sender()` and `main()` -
with no test at all, including the unconfigured-recipient case the module
docstring makes a promise about and which had already regressed once.

No real notification can escape here. `TelegramBot.send_message` is replaced by a
recorder in every transport test and the assertion is on the ABSENCE of a recorded
call, never on the presence of a refusal message: a guard that logs "REFUSED" and
then hands the message to the transport anyway would be indistinguishable
otherwise. The allowlist itself is the subject of
tests/test_a_notifier_that_would_carry_a_message_to_a_stranger.py; what is pinned
here is that THIS caller cannot walk around it.
"""
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

from scripts.utils import reminders_store as rs
from scripts.utils import telegram_notify as notify_mod
from scripts.utils.telegram_bot import TelegramBot

ROOT = Path(__file__).resolve().parent.parent

# Invented example data. No real chat id, channel or account appears here.
OWN_SINK = "-1009000000001"
STRANGER = "@example_stranger_channel"


def _load_dispatcher():
    spec = importlib.util.spec_from_file_location(
        "reminders_notify", ROOT / "scripts" / "reminders-notify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def transport(monkeypatch):
    """Record every (chat_id, text) the Telegram transport is asked to send.

    `notify()` calls `load_env()` first, which would read the real gitignored
    .env and repopulate the very target variables these tests clear, so it is
    neutralised too.
    """
    calls: list[tuple] = []

    def record(self, chat_id, text, **kwargs):
        calls.append((chat_id, text))
        return {"message_id": len(calls)}

    monkeypatch.setattr(TelegramBot, "send_message", record)
    monkeypatch.setattr(notify_mod, "load_env", lambda *a, **kw: None)
    monkeypatch.setenv("TELEGRAM_NOTIFY_BOT_TOKEN", "1234567:AAexample-not-a-real-token")
    for name in (notify_mod.SELF_TARGET_ENV_VAR, *notify_mod._FEATURE_TARGET_ENV_VARS):
        monkeypatch.setenv(name, "")
    return calls


@pytest.fixture
def notify_attempts(monkeypatch):
    """Record every target `_telegram_sender` hands to the notify transport.

    Distinct from `transport`, and both are needed. `notify("")` reaches the
    transport MODULE and is turned away inside it, so an empty recipient shows up
    as zero transport calls either way: MEASURED 2026-09-01, deleting the
    dispatcher's own `if not recipient` guard left an assertion on transport
    calls alone completely green. What the guard actually promises is that an
    unconfigured box makes no send ATTEMPT at all, and this is where that is
    visible. Calls through to the real function so the permitted direction still
    goes all the way to the transport.
    """
    attempts: list[str] = []
    real = notify_mod.notify

    def spy(target, message):
        attempts.append(target)
        return real(target, message)

    monkeypatch.setattr(notify_mod, "notify", spy)
    return attempts


def test_send_due_marks_fired_only_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "store_path", lambda: tmp_path / "reminders.json")
    a = rs.add({"kind": "once", "when": "2026-07-26", "message": "prep"})
    b = rs.add({"kind": "once", "when": "2026-07-26", "message": "fail"})
    disp = _load_dispatcher()

    def send_fn(msg):
        return "fail" not in msg  # b's send "fails"

    sent = disp.send_due(date(2026, 7, 27), send_fn)
    assert a["id"] in sent and b["id"] not in sent
    recs = {r["id"]: r for r in rs.load()}
    assert recs[a["id"]]["status"] == "fired"   # succeeded -> fired
    assert recs[b["id"]]["status"] == "active"  # failed -> still due, retried next tick


def test_send_due_raising_send_does_not_abort_loop(tmp_path, monkeypatch):
    """Verify raising send_fn for one record does not abort processing others."""
    monkeypatch.setattr(rs, "store_path", lambda: tmp_path / "reminders.json")
    a = rs.add({"kind": "once", "when": "2026-07-26", "message": "first"})
    b = rs.add({"kind": "once", "when": "2026-07-26", "message": "second"})
    disp = _load_dispatcher()

    def send_fn(msg):
        if "first" in msg:
            raise RuntimeError("first record send failed")
        return True  # second succeeds

    sent = disp.send_due(date(2026, 7, 27), send_fn)
    recs = {r["id"]: r for r in rs.load()}

    # Second record should be processed despite first raising
    assert b["id"] in sent, "second record should be in sent ids"
    assert recs[b["id"]]["status"] == "fired", "second record should be marked fired"

    # First record should not be in sent, status stays active for retry
    assert a["id"] not in sent, "first record should not be in sent ids"
    assert recs[a["id"]]["status"] == "active", "first record should stay active"


# --- the transport half ------------------------------------------------------

def test_an_unconfigured_recipient_never_reaches_the_transport(
    transport, notify_attempts, monkeypatch
):
    """"unconfigured (no send)" is a promise in the module docstring.

    Calling `notify("")` instead produced a failed send per due reminder per
    tick, forever, and left every reminder unmarked, so an unconfigured box was
    indistinguishable from a broken Telegram. MEASURED 2026-09-01: turning the
    `if not recipient` guard into `if False` left every other test in this file
    green.

    Asserted on the recorded calls, not on the log line.
    """
    disp = _load_dispatcher()
    send = disp._telegram_sender()

    assert send("Reminder: prep the demo") is False
    assert notify_attempts == [], "an unconfigured box still attempted a send"
    assert transport == [], "an unconfigured recipient reached the transport"


def test_a_recipient_outside_the_operators_allowlist_sends_nothing(
    transport, notify_attempts, monkeypatch
):
    """The one shape that can put a stranger in this caller's hands.

    `_telegram_sender` reads two environment names, so a caller literal cannot
    reach it. What CAN: the operator pins HEADING_OS_SELF_TELEGRAM_TARGET to one
    sink, which then IS the whole allowlist, while a stale per-feature variable
    still names something else. The dispatcher must send nothing at all rather
    than deliver a reminder to whatever the stale variable happens to name.
    """
    monkeypatch.setenv(notify_mod.SELF_TARGET_ENV_VAR, OWN_SINK)
    monkeypatch.setenv("REMINDERS_TELEGRAM_TARGET", STRANGER)
    disp = _load_dispatcher()
    send = disp._telegram_sender()

    assert send("Reminder: prep the demo") is False
    assert notify_attempts == [STRANGER], notify_attempts
    assert transport == [], "a target outside the allowlist reached the transport"


def test_a_declared_own_sink_does_reach_the_transport(
    transport, notify_attempts, monkeypatch
):
    """The boundary of the two tests above.

    A guard that refused everything would satisfy both of them and break the
    feature, so the permitted direction is pinned here: a recipient the operator
    declared is delivered, with the reminder body intact.
    """
    monkeypatch.setenv("REMINDERS_TELEGRAM_TARGET", OWN_SINK)
    disp = _load_dispatcher()
    send = disp._telegram_sender()

    assert send("Reminder: prep the demo") is True
    assert notify_attempts == [OWN_SINK], notify_attempts
    assert [chat for chat, _ in transport] == [OWN_SINK]
    assert transport[0][1] == "Reminder: prep the demo"


def test_main_exits_nonzero_on_a_corrupt_store(tmp_path, monkeypatch, capsys):
    """"A corrupt store is a genuine defect and exits non-zero" - module docstring.

    Every other exit in this dispatcher is 0 by design, so an exit code that
    stopped distinguishing the one real defect would leave a systemd unit
    reporting success over an unreadable store forever. MEASURED 2026-09-01:
    changing that `return 1` to `return 0` was caught by nothing.
    """
    store = tmp_path / "reminders.json"
    store.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(rs, "store_path", lambda: store)
    disp = _load_dispatcher()
    # `main()` parses sys.argv directly, so the runner's own flags would reach
    # argparse and abort with exit 2 before the store is ever read.
    monkeypatch.setattr(sys, "argv", ["reminders-notify"])
    monkeypatch.setattr(disp, "load_env", lambda *a, **k: None)

    def _no_sender():
        raise AssertionError("a corrupt store must not build a sender")

    monkeypatch.setattr(disp, "_telegram_sender", _no_sender)

    assert disp.main() == 1
    assert "store corrupt" in capsys.readouterr().err
