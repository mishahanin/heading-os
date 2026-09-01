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

    # `Account` first, and it is not decoration. `create_meeting` calls
    # `_ensure_exchangelib()` since 2026-08-26, because it read `CalendarItem`,
    # `EWSDateTime` and `EWSTimeZone` without ever binding them and only worked
    # when `main()` had connected first. That binder returns early on
    # `Account is not None`, so a fake that leaves `Account` as None sends it
    # off to import the real exchangelib, which overwrites all three fakes below
    # and then rejects this test's stand-in account. Setting `Account` is how
    # this fixture says "the module's exchangelib names are already resolved".
    monkeypatch.setattr(sync_exchange, "Account", object)
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


def test_the_invited_addresses_actually_land_on_the_item(monkeypatch):
    """SEND_ONLY_TO_ALL with an empty attendee list emails nobody.

    Every case above reads `saved_with` and nothing else, so the send MODE was
    the whole of what this file measured. MEASURED 2026-09-01 by replacing
    `if attendees:` with `if False:`, which stops the addresses ever reaching
    `item.required_attendees`: this file stayed green, and so did every
    neighbour that names `create_meeting`
    (`test_a_tick_that_landed_on_the_wrong_line.py`,
    `test_a_sort_that_could_not_compare_a_date_to_a_time.py`,
    `test_a_sync_that_reported_failure_after_it_succeeded.py`). A meeting saved
    SEND_ONLY_TO_ALL with no attendee on it is a hold the operator believes was
    an invitation, which is the same wrong belief as an invite sent by accident,
    pointing the other way.

    Whitespace around one address is in the case because `--attendees` is a
    comma-separated CLI string and `email.strip()` is the line that handles it.
    """
    created = _patch_exchange_globals(monkeypatch)
    sync_exchange.create_meeting(
        _fake_account(),
        subject="31C / ExampleCorp - technical call",
        start_time="2026-07-16 11:00",
        attendees=["dana.reyes@example.com", "  sam.okafor@example.com  "],
        send_invites=True,
        timezone_str="Asia/Dubai",
    )
    invited = [a.mailbox.email_address for a in created[0].required_attendees]
    assert invited == ["dana.reyes@example.com", "sam.okafor@example.com"]


def test_a_hold_with_no_attendees_carries_no_attendee_list(monkeypatch):
    """Anchor: attaching everybody always would satisfy the test above."""
    created = _patch_exchange_globals(monkeypatch)
    sync_exchange.create_meeting(
        _fake_account(),
        subject="Solo hold",
        start_time="2026-07-16 11:00",
        attendees=None,
        send_invites=False,
        timezone_str="Asia/Dubai",
    )
    assert not getattr(created[0], "required_attendees", None)


def test_the_meeting_lasts_the_duration_it_was_asked_for(monkeypatch):
    """`duration_minutes` was passed by two cases above and read by none.

    MEASURED 2026-09-01 by rewriting `end = start + timedelta(...)` to
    `end = start`: this file and all three neighbours stayed green, and the
    default 30 is what a bare `--create-meeting` books, so the survivor is a
    zero-length calendar entry on the operator's real calendar.
    """
    created = _patch_exchange_globals(monkeypatch)
    sync_exchange.create_meeting(
        _fake_account(),
        subject="Ninety minutes",
        start_time="2026-07-16 11:00",
        duration_minutes=90,
        timezone_str="Asia/Dubai",
    )
    kwargs = created[0].kwargs
    assert (kwargs["end"] - kwargs["start"]).total_seconds() == 90 * 60


def test_the_default_duration_is_not_zero(monkeypatch):
    created = _patch_exchange_globals(monkeypatch)
    sync_exchange.create_meeting(
        _fake_account(),
        subject="Default",
        start_time="2026-07-16 11:00",
        timezone_str="Asia/Dubai",
    )
    kwargs = created[0].kwargs
    assert (kwargs["end"] - kwargs["start"]).total_seconds() == 30 * 60
