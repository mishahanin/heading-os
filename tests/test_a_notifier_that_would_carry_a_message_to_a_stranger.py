"""`telegram_notify.notify()` must reach the operator's own sink and no one else.

Seven callers drive `notify()` with no human in the loop: ops-radar-notify,
council-models-notify, reminders-notify, sentinel, alert, odin-cadence-notify,
and the checkpoint-offer hook. Before 2026-08-30 the function rejected only an
empty target and Telegram's own-account sentinels ("me"/"self"/"saved"), so any
other string it was handed went on the wire. A recipient that arrives through
the running process (a caller's literal, a value derived from fetched content,
an argument a skill supplies) is the third leg of the lethal trifecta, and this
transport is the one place it can be closed for all seven at once.

Every case here asserts on the RECORDED transport calls, not only on the return
value: a send guard that returns False while still handing the message to
`TelegramBot.send_message` would be indistinguishable otherwise.

No socket is opened. `TelegramBot.send_message` is replaced by a recorder, and
an autouse fixture makes `socket.socket.connect` raise, so a path that slipped
past the recorder fails loudly instead of reaching the network.

Run: .venv/bin/python -m pytest tests/test_a_notifier_that_would_carry_a_message_to_a_stranger.py
"""
import logging
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import telegram_notify as notify_mod
from scripts.utils.telegram_bot import TelegramBot

# Invented example data. No real chat id, channel or account appears here.
OWN_SINK = "-1009000000001"
OWN_SINK_USERNAME = "@example_own_alerts"
STRANGER = "@example_stranger_channel"
STRANGER_NUMERIC = "-1009000000002"

TARGET_VARS = (
    notify_mod.SELF_TARGET_ENV_VAR,
    *notify_mod._FEATURE_TARGET_ENV_VARS,
)


@pytest.fixture(autouse=True)
def no_real_env_and_no_socket(monkeypatch):
    """Isolate the guard from the operator's .env and from the network.

    `notify()` calls `load_env()` first, which would read the real gitignored
    .env and populate the very variables this file is asserting about. It is
    neutralised, then every target variable is cleared, so each test declares
    its own world explicitly.
    """
    monkeypatch.setattr(notify_mod, "load_env", lambda *a, **kw: None)
    for name in TARGET_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TELEGRAM_NOTIFY_BOT_TOKEN", "1234567:AAexample-not-a-real-token")

    def refuse_connect(self, *args, **kwargs):
        raise AssertionError("a test opened a real socket; the transport was reached")

    monkeypatch.setattr(socket.socket, "connect", refuse_connect)


@pytest.fixture
def transport(monkeypatch):
    """Record every (chat_id, text) the transport is asked to send."""
    calls: list[tuple] = []

    def record(self, chat_id, text, **kwargs):
        calls.append((chat_id, text))
        return {"message_id": len(calls)}

    monkeypatch.setattr(TelegramBot, "send_message", record)
    return calls


# ============================================================
# The red direction: a stranger target must never reach the transport
# ============================================================
@pytest.mark.parametrize("stranger", [STRANGER, STRANGER_NUMERIC])
def test_a_stranger_target_never_reaches_the_transport(
    monkeypatch, transport, caplog, stranger
):
    """FAILS on the pre-fix code: the stranger was sent to.

    The operator's sink is declared and a caller asks for a different
    recipient. Pre-fix, `notify` checked only emptiness and the three
    sentinels, so this returned True and `send_message` was called with the
    stranger. The message here is the shape that makes this matter: a
    notification body carries workspace state.
    """
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", OWN_SINK)

    with caplog.at_level(logging.ERROR, logger="telegram_notify"):
        sent = notify_mod.notify(stranger, "ops radar: 3 items due")

    assert sent is False
    assert transport == [], f"the transport was handed a stranger target: {transport}"
    assert "REFUSED" in caplog.text


def test_the_refusal_names_the_stranger_and_stays_out_of_the_body(
    monkeypatch, transport, caplog
):
    """The refusal has to be findable in a log, and it must not be silent."""
    monkeypatch.setenv("OPS_RADAR_TELEGRAM_TARGET", OWN_SINK)

    with caplog.at_level(logging.DEBUG, logger="telegram_notify"):
        assert notify_mod.notify(STRANGER, "reminder: renew the domain") is False

    refusals = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert refusals, "the refusal was logged below ERROR, or not at all"
    assert STRANGER in refusals[0].getMessage()
    assert transport == []


def test_an_allowed_prefix_does_not_admit_a_longer_stranger(monkeypatch, transport):
    """Membership is exact, never a prefix or substring test.

    A public channel whose name merely starts with the allowed one is a
    different chat, and the cheap version of this guard (`startswith`, or `in`
    on the joined string) would let it through.
    """
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", OWN_SINK_USERNAME)

    assert notify_mod.notify(OWN_SINK_USERNAME + "_public", "state") is False
    assert notify_mod.notify(OWN_SINK_USERNAME[:8], "state") is False
    assert transport == []


# ============================================================
# The negative control: the legitimate own target still goes through
# ============================================================
def test_the_declared_own_sink_still_sends(monkeypatch, transport):
    """The fix must not be "refuse everything"."""
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", OWN_SINK)

    assert notify_mod.notify(OWN_SINK, "odin: weekly reflect is due") is True
    assert transport == [(OWN_SINK, "odin: weekly reflect is due")]


@pytest.mark.parametrize(
    "var",
    [
        "SENTINEL_TELEGRAM_TARGET",
        "COUNCIL_MODELS_TELEGRAM_TARGET",
        "OPS_RADAR_TELEGRAM_TARGET",
        "REMINDERS_TELEGRAM_TARGET",
        "CHECKPOINT_TELEGRAM_TARGET",
        "ODIN_CADENCE_TELEGRAM_TARGET",
    ],
)
def test_every_caller_variable_the_call_sites_read_is_honoured(
    monkeypatch, transport, var
):
    """Each of the six per-feature variables is a real production source.

    Measured 2026-08-30 by reading all seven call sites. If the allowlist
    missed one, that caller's notifications would go dark, which is a
    regression dressed as a fix.
    """
    monkeypatch.setenv(var, OWN_SINK)

    assert notify_mod.notify(OWN_SINK, "hello") is True
    assert transport == [(OWN_SINK, "hello")]


def test_the_username_form_matches_however_it_is_spelled(monkeypatch, transport):
    """"@Example_Own_Alerts" and "example_own_alerts" are one chat, not two."""
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", OWN_SINK_USERNAME)

    assert notify_mod.notify("@Example_Own_Alerts", "a") is True
    assert notify_mod.notify("example_own_alerts", "b") is True
    assert [c[1] for c in transport] == ["a", "b"]


def test_the_pin_narrows_the_allowlist_to_itself(monkeypatch, transport):
    """With the pin set, a per-feature variable that disagrees is refused.

    This is the whole point of the pin: one designated value governs all seven
    callers, so redirecting a single per-feature variable cannot redirect the
    fleet.
    """
    monkeypatch.setenv(notify_mod.SELF_TARGET_ENV_VAR, OWN_SINK)
    monkeypatch.setenv("OPS_RADAR_TELEGRAM_TARGET", STRANGER)

    assert notify_mod.notify(STRANGER, "radar") is False
    assert transport == []
    assert notify_mod.notify(OWN_SINK, "radar") is True
    assert transport == [(OWN_SINK, "radar")]


# ============================================================
# Failing closed: absent, blank, and garbage configuration
# ============================================================
def test_nothing_declared_refuses_every_target(monkeypatch, transport):
    """No configuration must not resolve to "send anyway, somewhere"."""
    assert notify_mod.own_targets() == set()

    for candidate in (OWN_SINK, STRANGER, "@anything", "12345"):
        assert notify_mod.notify(candidate, "x") is False
    assert transport == []


@pytest.mark.parametrize("garbage", ["", "   ", ",,,", " ; , ; "])
def test_a_garbage_declaration_declares_nothing(monkeypatch, transport, garbage):
    """A blank or separator-only .env value yields an empty allowlist.

    A trailing space on an edited .env line is the ordinary way a value becomes
    whitespace-only. It must not become an allowlist entry, and it must not
    open the gate.
    """
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", garbage)

    assert notify_mod.own_targets() == set()
    assert notify_mod.notify(OWN_SINK, "x") is False
    assert notify_mod.notify(garbage, "x") is False
    assert transport == []


@pytest.mark.parametrize("sentinel", ["me", "self", "saved", "Me", "@SAVED"])
def test_a_sentinel_declared_in_env_never_becomes_an_allowlist_entry(
    monkeypatch, transport, sentinel
):
    """Declaring "me" must not make "me" sendable.

    A bot cannot resolve its caller's own account, so Saved Messages has been a
    permanent refusal here since the transport was written. Routing it through
    the new allowlist must not quietly reinstate it.
    """
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", sentinel)

    assert notify_mod.own_targets() == set()
    assert notify_mod.notify(sentinel, "x") is False
    assert transport == []


@pytest.mark.parametrize("sentinel", ["me", "self", "saved", "Me", "@SAVED"])
def test_a_sentinel_is_diagnosed_as_unresolvable_and_not_as_a_stranger(
    monkeypatch, transport, caplog, sentinel
):
    """The two refusals must not collapse into one message.

    `own_targets()` already strips the sentinels, so the allowlist check alone
    refuses "me" and the earlier `wanted in _UNRESOLVABLE_TARGETS` branch adds
    no safety. What it adds is the DIAGNOSIS, and the module docstring promises
    "a clear, distinct log hint" per failure mode: this branch is an ordinary
    "not configured for a bot" state, while REFUSED at ERROR means something
    asked this transport to reach a recipient the operator never declared. A
    sentinel reported as REFUSED is a false alarm in the log a reader searches
    for real ones.

    MEASURED 2026-09-01: deleting `or wanted in _UNRESOLVABLE_TARGETS` from
    `notify` left the whole file green at 29 passed, because every other case
    here reads only the return value and the empty transport.
    """
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", OWN_SINK)

    with caplog.at_level(logging.DEBUG, logger="telegram_notify"):
        assert notify_mod.notify(sentinel, "x") is False

    assert "not bot-resolvable" in caplog.text, caplog.text
    assert "REFUSED" not in caplog.text, (
        "Saved Messages was reported as an undeclared recipient; the log a "
        "reader greps for real refusals now carries a routine one"
    )
    assert transport == []


@pytest.mark.parametrize("target", [None, "", "   ", 12345, object()])
def test_a_malformed_target_refuses_instead_of_raising(monkeypatch, transport, target):
    """notify() never raises, whatever a caller hands it."""
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", OWN_SINK)

    assert notify_mod.notify(target, "x") is False
    assert transport == []


def test_the_allowlist_is_read_from_the_environment_at_call_time(
    monkeypatch, transport, tmp_path
):
    """The seam the whole test suite's containment stands on.

    `own_targets()` reads `os.environ`, never the `.env` FILE, and that is load
    bearing in both directions. `tests/conftest.py` contains every test in this
    repository by BLANKING each `*_TELEGRAM_TARGET` name in `os.environ`; a
    resolver that went to the file would walk straight past that and let a test
    run message the operator, which is the accident that containment exists to
    prevent.

    So this pins the direction a well-meaning "hardening" would break: a target
    present ONLY in a `.env` on disk, with the environment blank, must resolve to
    nothing. The cost of the same seam is recorded in `own_targets`' own
    docstring, measured: a value the running process assigns to one of these
    names is accepted, because nothing can tell it from one the operator typed.
    """
    (tmp_path / ".env").write_text(
        f"ODIN_CADENCE_TELEGRAM_TARGET={STRANGER}\n", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))

    assert notify_mod.own_targets() == set(), (
        "a target reached the allowlist from a file rather than from the "
        "environment; tests/conftest.py can no longer contain this transport"
    )
    assert notify_mod.notify(STRANGER, "x") is False
    assert transport == []


def test_a_missing_token_refuses_before_the_allowlist_is_consulted(
    monkeypatch, transport
):
    """Two independent rings. Removing either must not open the other."""
    monkeypatch.delenv("TELEGRAM_NOTIFY_BOT_TOKEN", raising=False)
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", OWN_SINK)

    assert notify_mod.notify(OWN_SINK, "x") is False
    assert transport == []
