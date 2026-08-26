#!/usr/bin/env python3
"""SEC-014: one unresponsive chat may not block the whole Sentinel cycle.

The control: every Telegram read is bounded, so a chat that never answers costs
one timeout and not the cycle.

This file used to prove that by scanning the TEXT of `_check_monitored_chats`
for the substring "wait_for". On 2026-08-25 the read moved into the shared
`_fetch_since` helper - the timeout was still there, still applied to both
readers, and this test failed anyway, because its method looked in one place
rather than measuring the bound. A text scan cannot tell a control that moved
from a control that was deleted, and it also cannot see the way this nearly went
wrong: `_fetch_since` pages up to `MAX_FETCH_PAGES` times, so a per-page timeout
would have quietly raised the real ceiling to five times the budget while every
substring the old test wanted stayed on the page.

The tests below measure the bound instead. A fake client that never answers must
raise inside the budget, and the budget must not scale with the page count.
"""

import asyncio
import inspect
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.sentinel import TelegramSource  # noqa: E402


class _NeverAnswers:
    """A Telegram client whose read never completes."""

    def __init__(self):
        self.calls = 0

    async def get_messages(self, entity, limit=None, min_id=0, max_id=0):
        self.calls += 1
        # Long enough that only the control can end this, short enough that a
        # DELETED control fails the test in seconds instead of hanging the run.
        # A mutation check that removes the deadline must not cost a wall-clock
        # timeout to detect.
        await asyncio.sleep(5)


class _AlwaysFullPage:
    """Answers instantly, always with a full page, so paging never converges.

    This is the shape that turns a per-page timeout into a per-chat budget five
    times larger than the one SEC-014 sets.
    """

    def __init__(self):
        self.calls = 0

    async def get_messages(self, entity, limit=None, min_id=0, max_id=0):
        self.calls += 1
        top = max_id - 1 if max_id else 10_000_000
        return [type("M", (), {"id": top - i, "text": "x", "out": False,
                               "media": None})() for i in range(limit)]


def _source(client):
    src = TelegramSource(config={}, state=None,
                         logger=logging.getLogger("sec-014"))
    src.client = client
    return src


def test_an_unresponsive_chat_times_out_rather_than_hanging():
    """The control itself, measured.

    Wrapped in a SECOND, outer deadline so a deleted control fails here rather
    than stalling the run: without it, "the bound is gone" and "the bound is
    slow" look identical to a test runner.
    """
    client = _NeverAnswers()
    source = _source(client)
    source.FETCH_TIMEOUT_SECONDS = 0.05  # keep the test fast; the bound is real

    async def _bounded():
        return await asyncio.wait_for(
            source._fetch_since(object(), 0, 10, "silent-chat"), timeout=2)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(_bounded())
    assert client.calls == 1


def test_the_budget_does_not_multiply_by_the_page_count():
    """The regression a text scan cannot see.

    With a per-page deadline this returns normally after MAX_FETCH_PAGES full
    pages, having spent up to five budgets. With one deadline for the whole
    loop it raises, because the pages never converge.
    """
    client = _AlwaysFullPage()
    source = _source(client)
    source.FETCH_TIMEOUT_SECONDS = 0

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(source._fetch_since(object(), 0, 10, "loud-chat"))


def test_a_responsive_chat_is_not_penalised():
    """The bound must not have become a wall."""
    class _Answers:
        async def get_messages(self, entity, limit=None, min_id=0, max_id=0):
            return []

    source = _source(_Answers())
    assert asyncio.run(source._fetch_since(object(), 0, 10, "quiet")) == []


def test_the_timeout_is_a_named_constant_not_a_literal():
    """Named so the two readers cannot drift apart, and so the value is
    reviewable in one place rather than grepped for."""
    assert isinstance(TelegramSource.FETCH_TIMEOUT_SECONDS, (int, float))
    assert TelegramSource.FETCH_TIMEOUT_SECONDS > 0


@pytest.mark.parametrize("reader", ["_check_personal_dms", "_check_monitored_chats"])
def test_both_readers_go_through_the_bounded_helper(reader):
    """Both readers had their own unbounded copy of this fetch once. A control
    applied to one of two readers is not a control."""
    src = inspect.getsource(getattr(TelegramSource, reader))
    assert "_fetch_since(" in src, f"{reader} reads Telegram without the bound"
    assert "self.client.get_messages(" not in src, (
        f"{reader} calls get_messages directly again, outside the deadline")
