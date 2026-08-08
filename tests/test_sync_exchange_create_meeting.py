"""Tests for create_meeting() invite-send behavior in scripts/sync-exchange.py.

The spine under test: create_meeting() must only email a meeting invitation when
send_invites is True AND attendees are present. Otherwise it saves a private HOLD
with send_meeting_invitations=SEND_TO_NONE. This guards the default that motivated
the --send-invites flag: a bare --create-meeting must never silently notify
attendees. The module is loaded by path because its filename is kebab-case.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("exchangelib")
from exchangelib.items import SEND_ONLY_TO_ALL, SEND_TO_NONE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "sync_exchange", ROOT / "scripts" / "sync-exchange.py"
)
sync_exchange = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_exchange)


def _patch_exchange_globals(monkeypatch):
    """Replace the lazily-bound exchangelib globals with lightweight fakes and
    return the list that captures every CalendarItem create_meeting() builds."""
    import datetime as _dt

    created = []

    class FakeCalendarItem:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.saved_with = None
            created.append(self)

        def save(self, send_meeting_invitations=None):
            self.saved_with = send_meeting_invitations

    monkeypatch.setattr(sync_exchange, "CalendarItem", FakeCalendarItem)
    monkeypatch.setattr(
        sync_exchange, "EWSTimeZone",
        types.SimpleNamespace(from_timezone=lambda z: z),
    )
    monkeypatch.setattr(
        sync_exchange, "EWSDateTime",
        lambda *a, **k: _dt.datetime(*a[:6], tzinfo=k.get("tzinfo")),
    )
    return created


def _fake_account():
    return types.SimpleNamespace(calendar=object())


def test_send_invites_with_attendees_sends_invite(monkeypatch):
    created = _patch_exchange_globals(monkeypatch)
    sync_exchange.create_meeting(
        _fake_account(),
        subject="31C / ExampleCorp - technical call",
        start_time="2026-07-16 11:00",
        duration_minutes=60,
        attendees=["dana.reyes@example.com"],
        send_invites=True,
        timezone_str="Asia/Dubai",
    )
    assert created[0].saved_with is SEND_ONLY_TO_ALL


def test_no_send_invites_is_hold_only(monkeypatch):
    """Default path: attendees listed but no invite emailed (HOLD)."""
    created = _patch_exchange_globals(monkeypatch)
    sync_exchange.create_meeting(
        _fake_account(),
        subject="Hold",
        start_time="2026-07-16 11:00",
        attendees=["dana.reyes@example.com"],
        send_invites=False,
        timezone_str="Asia/Dubai",
    )
    assert created[0].saved_with is SEND_TO_NONE


def test_send_invites_without_attendees_sends_nothing(monkeypatch):
    """send_invites has no effect when there is nobody to invite."""
    created = _patch_exchange_globals(monkeypatch)
    sync_exchange.create_meeting(
        _fake_account(),
        subject="Solo hold",
        start_time="2026-07-16 11:00",
        attendees=None,
        send_invites=True,
        timezone_str="Asia/Dubai",
    )
    assert created[0].saved_with is SEND_TO_NONE
