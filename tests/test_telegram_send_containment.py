"""A test run may never send the operator a real Telegram message.

On 2026-08-19 the operator received nine alerts reading "HEADING OS: unattended
run stopped. no progress across 3 consecutive continuations" between 17:01 and
19:13 local. No unattended run had stalled. Every one of them was a pytest run.

The path: `_notify_stall` in .claude/hooks/checkpoint-offer.py sends that line
when the no-progress fuse trips, `test_the_stall_record_is_written_once` calls
`_pause_unattended` directly to assert the record is written once, and the target
it sends to comes from os.environ - which any earlier test that called the real
`load_env()` had already populated from the operator's .env, because load_env
uses setdefault. The test that was supposed to be hermetic deleted
HEADING_OS_TELEGRAM_CHAT_ID, a name `_notify_stall` does not read.

An alert a test can raise is worse than no alert, for the same reason a deadman a
test can turn green is worse than no deadman (see
tests/test_deadman_ping_containment.py, the same defect one instrument along):
it is believed, and it teaches the operator to stop believing the real ones.

These tests hold the containment in tests/conftest.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import telegram_notify  # noqa: E402
from scripts.utils.paths import load_env  # noqa: E402

# The three names `_notify_stall` walks, in its order. Spelled out rather than
# derived: the point of this test is to fail when that list and the containment
# disagree, and deriving both from one source would make them agree by
# construction.
_STALL_TARGETS = (
    "CHECKPOINT_TELEGRAM_TARGET",
    "OPS_RADAR_TELEGRAM_TARGET",
    "ODIN_CADENCE_TELEGRAM_TARGET",
)


def _live_targets() -> list[str]:
    return sorted(k for k, v in os.environ.items()
                  if k.endswith("_TELEGRAM_TARGET") and v.strip())


def test_load_env_cannot_reintroduce_a_notification_target():
    """The real load_env() must not hand a live chat id to the suite.

    This is the exact path that produced the nine false alerts. On a clone with
    no .env there is nothing to reintroduce and this passes trivially - it is the
    operator workspace, where .env is real, that it guards.
    """
    load_env()
    leaked = _live_targets()
    assert not leaked, (
        f"live Telegram target(s) in the test environment: {leaked}. "
        "A test that reaches a notifier would message the operator for real."
    )


def test_the_checkpoint_stall_alert_has_no_target_to_send_to():
    """Asserted at the three names the fuse actually reads.

    The previous test covers the suffix; this one covers the specific chain, so
    that renaming a target to something not ending in _TELEGRAM_TARGET fails here
    rather than silently re-opening the channel.
    """
    load_env()
    for name in _STALL_TARGETS:
        assert not os.environ.get(name, "").strip(), (
            f"{name} is set during the test session; the checkpoint stall alert "
            "would reach the operator's bot."
        )


def test_notify_is_a_no_op_without_a_token():
    """The second ring: with the token blanked, notify() cannot send at all.

    The token is checked BEFORE notify() is called, and that order is the test's
    whole safety property. Calling first and asserting after would, on a run with
    the containment removed, open a real socket to Telegram with a live token -
    the exact act this file exists to prevent - and then report the leak it had
    already committed. Verified at the public seam afterwards, so the guard is
    read where sending actually happens rather than only in os.environ.
    """
    load_env()
    assert not os.environ.get("TELEGRAM_NOTIFY_BOT_TOKEN", "").strip(), (
        "the notifications bot token is live during the test session; any test "
        "reaching a notifier would send for real."
    )
    assert telegram_notify.notify("-100probe", "probe: this must not send") is False


def test_the_containment_blanks_a_target_it_never_saw_at_import(monkeypatch):
    """The belt: the sweep is by suffix, not by this machine's `.env`.

    The containment's mute list is frozen at conftest import from `.env` plus
    the inherited environment, so it names only the targets THIS machine
    configures. Everything else is a name nothing re-blanks - and a name nothing
    re-blanks is where a leak lands, because a test that removes it with
    `monkeypatch.delenv(name, raising=False)` gets no undo entry at all
    (pytest's `MonkeyPatch.delitem` returns early when the name is already
    absent), so a `load_env()` in that test creates it for the rest of the
    session. MEASURED 2026-08-31: that is how CHECKPOINT_TELEGRAM_TARGET and
    OPS_RADAR_TELEGRAM_TARGET reached 'fixture-sink-9911' and stayed there,
    failing the two tests above in a full serial run only.

    Driven at the containment function rather than by collection ORDER, so it
    measures the guard instead of measuring which file pytest happened to
    collect first.
    """
    from tests import conftest

    name = "A_TARGET_THIS_MACHINE_NEVER_CONFIGURED_TELEGRAM_TARGET"
    assert name not in conftest._MUTED_TELEGRAM, (
        "the fixture name is in the frozen mute list, so this test would pass "
        "through the old list-driven loop and prove nothing"
    )
    monkeypatch.setenv(name, "-1009999999999")

    conftest._mute_telegram_targets()

    assert os.environ[name] == "", (
        f"{name} survived the containment sweep. A target absent from .env is "
        "exactly the name a leaked fixture value lands on, and the frozen list "
        "cannot reach it."
    )
