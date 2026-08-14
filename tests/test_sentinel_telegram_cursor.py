"""Sentinel's Telegram DM reader must follow its cursor, not the unread badge.

The reader used to open with `if dialog.unread_count == 0: continue`, which made
it blind in two ways that no log line reported. A message read on the phone
before the next fifteen-minute cycle was never seen again, because the following
cycle no longer counted it as unread. And a conversation where the operator
himself wrote last has a zero unread count, so it was dropped whole -- the
reader could not see the operator's own commitments at all.

The newness test is the dialog's top message id against the stored cursor.
`iter_dialogs` already carries that id, so this costs no extra API call and
cannot trip FloodWait.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import pytest

# F-7.1: skip on a core-only clone (needs the telegram extra). Every other test
# module that reaches for an optional extra already guards it this way; this one
# did not, and a bare `from telethon import types` turns the absence into a
# COLLECTION error. One collection error aborts the whole run: the py3.12 CI job
# reported "16 skipped, 36 deselected, 1 error" and exit 2, so five thousand
# unrelated tests never ran. Note this skips only when telethon is genuinely
# ABSENT; an installed-but-broken telethon still errors, which is the right way
# round, because a broken dependency should be loud.
types = pytest.importorskip("telethon.types", reason="optional `telegram` extra")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.sentinel import TelegramSource  # noqa: E402


class FakeState:
    def __init__(self, cursors: dict[str, int] | None = None):
        self.cursors = dict(cursors or {})
        self.writes: list[tuple[str, str, int]] = []

    def get_telegram_last_id(self, chat_id: str) -> int:
        return self.cursors.get(str(chat_id), 0)

    def set_telegram_last_id(self, chat_id: str, name: str, msg_id: int) -> None:
        self.cursors[str(chat_id)] = msg_id
        self.writes.append((str(chat_id), name, msg_id))


class FakeMessage:
    def __init__(self, msg_id: int, text: str = "", out: bool = False):
        self.id = msg_id
        self.text = text
        self.out = out
        self.media = None


class FakeDialog:
    def __init__(self, entity, top_message_id: int, unread_count: int):
        self.entity = entity
        self.unread_count = unread_count
        self.message = FakeMessage(top_message_id) if top_message_id else None


class FakeClient:
    """Records every get_messages call so a test can assert one never happened."""

    def __init__(self, dialogs: list[FakeDialog], messages: list[FakeMessage] | None = None):
        self._dialogs = dialogs
        self._messages = messages or []
        self.calls: list[dict] = []

    def iter_dialogs(self, limit=None):
        dialogs = self._dialogs

        async def _gen():
            for dialog in dialogs:
                yield dialog

        return _gen()

    async def get_messages(self, entity, limit=None, min_id=0):
        self.calls.append({"entity": entity, "limit": limit, "min_id": min_id})
        return [m for m in self._messages if m.id > min_id][:limit]


def _user(user_id: int = 7, first_name: str = "Orkhan", bot: bool = False):
    return types.User(id=user_id, bot=bot, first_name=first_name)


def _source(client, state, config=None):
    src = TelegramSource(
        config=config if config is not None else {},
        state=state,
        logger=logging.getLogger("test-sentinel-telegram"),
    )
    src.client = client
    return src


def _run(source):
    return asyncio.run(source._check_personal_dms())


def test_a_dm_read_on_the_phone_is_still_seen():
    """The original defect. Zero unread, but the cursor has not caught up yet."""
    user = _user()
    client = FakeClient(
        dialogs=[FakeDialog(user, top_message_id=105, unread_count=0)],
        messages=[FakeMessage(105, "the shipment slipped to Tuesday")],
    )
    state = FakeState({"7": 100})

    items = _run(_source(client, state))

    assert len(items) == 1
    assert "shipment slipped" in items[0]["body"]
    assert state.cursors["7"] == 105


def test_a_dialog_where_the_operator_spoke_last_advances_without_alerting():
    """His own message is not an alert, but it must still move the cursor."""
    user = _user()
    client = FakeClient(
        dialogs=[FakeDialog(user, top_message_id=105, unread_count=0)],
        messages=[FakeMessage(105, "I will send the NDA tonight", out=True)],
    )
    state = FakeState({"7": 100})

    items = _run(_source(client, state))

    assert items == []
    assert state.cursors["7"] == 105, "cursor must advance or the dialog is rescanned forever"


def test_an_incoming_message_is_kept_when_the_operator_also_replied():
    user = _user()
    client = FakeClient(
        dialogs=[FakeDialog(user, top_message_id=106, unread_count=0)],
        messages=[
            FakeMessage(105, "can you confirm the price?"),
            FakeMessage(106, "checking now", out=True),
        ],
    )
    state = FakeState({"7": 100})

    items = _run(_source(client, state))

    assert len(items) == 1
    assert "confirm the price" in items[0]["body"]
    assert "checking now" not in items[0]["body"]
    assert state.cursors["7"] == 106


def test_a_dialog_with_nothing_new_is_never_fetched():
    """Guards the FloodWait budget: no unread gate must not mean no gate."""
    user = _user()
    client = FakeClient(
        dialogs=[FakeDialog(user, top_message_id=105, unread_count=0)],
        messages=[FakeMessage(105, "old news")],
    )
    state = FakeState({"7": 105})

    items = _run(_source(client, state))

    assert items == []
    assert client.calls == [], "a dialog at its cursor must cost zero API calls"


def test_first_sight_of_a_quiet_dialog_seeds_the_cursor_without_backfilling():
    """Otherwise the first run after this fix pulls history out of every dialog."""
    user = _user()
    client = FakeClient(
        dialogs=[FakeDialog(user, top_message_id=900, unread_count=0)],
        messages=[FakeMessage(900, "conversation from last year")],
    )
    state = FakeState()

    items = _run(_source(client, state))

    assert items == []
    assert client.calls == []
    assert state.cursors["7"] == 900


def test_first_sight_with_unread_still_reports_exactly_the_unread():
    """A brand new counterpart must not be silent for a cycle."""
    user = _user()
    client = FakeClient(
        dialogs=[FakeDialog(user, top_message_id=902, unread_count=2)],
        messages=[FakeMessage(901, "introducing myself"), FakeMessage(902, "are you free?")],
    )
    state = FakeState()

    items = _run(_source(client, state))

    assert len(items) == 1
    assert client.calls[0]["limit"] == 2
    assert state.cursors["7"] == 902


@pytest.mark.parametrize(
    "dialog, config",
    [
        (FakeDialog(_user(bot=True), 105, 0), {}),
        (FakeDialog(_user(first_name="Family"), 105, 0), {"ignore_chats": ["Family"]}),
    ],
    ids=["bot", "ignored-chat"],
)
def test_bots_and_ignored_chats_stay_out(dialog, config):
    client = FakeClient(dialogs=[dialog], messages=[FakeMessage(105, "hello")])
    state = FakeState({"7": 100})

    items = _run(_source(client, state, config))

    assert items == []
    assert client.calls == []
