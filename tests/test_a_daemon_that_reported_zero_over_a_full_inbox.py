"""Shard scripts-14-p1: the always-on daemon, and four things it got wrong.

* ``UrgencyAnalyzer.analyze_batch`` guarded the CONTAINER type of the model's
  reply and not the element type. A reply that IS a list, of scalars, walked
  past the guard, was padded to length, and every consumer then called
  ``.get`` on a string. AttributeError out of a daemon whose ``run_cycle`` and
  ``start`` catch neither.

* ``EmailSource.check_new`` read a newest-first slice of ``max_per_check``
  unread. Sentinel never marks mail read, so once those N were all processed
  every later cycle re-fetched the same N, skipped them all, and logged
  "0 new unread messages" while older unread mail sat permanently outside the
  window. Nothing ages back INTO a newest-first slice.

* ``TelegramSource._fetch_since`` pages until a short page. On FIRST SIGHT
  there is no lower bound, so it paged through history and pulled five times
  the intended limit, then warned about a "skipped" count covering the whole
  chat.

* ``find_alternative_slot`` searched up to 12 days out while its caller fetched
  7 days of events, so a proposed time past day 7 was conflict-checked against
  an empty list - and that time goes inside a decline sent to a real organizer.

Run: python3 -m pytest tests/test_a_daemon_that_reported_zero_over_a_full_inbox.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def sn():
    spec = importlib.util.spec_from_file_location("sentinel_under_test",
                                                  ROOT / "scripts" / "sentinel.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["sentinel_under_test"] = module
    spec.loader.exec_module(module)
    return module


# ============================================================
# The reply that was a list of the wrong thing
# ============================================================

class _Reply:
    def __init__(self, text):
        self.text = text


@pytest.fixture
def analyzer(sn, monkeypatch):
    def _build(payload):
        obj = sn.UrgencyAnalyzer.__new__(sn.UrgencyAnalyzer)
        obj.logger = logging.getLogger("probe")
        obj.model = "not-a-live-model"
        obj.max_tokens = 100
        obj.client = None
        obj.business_context = ""
        obj.operator_name = "Operator"
        calls = {"individual": 0}

        def _analyze(item):
            calls["individual"] += 1
            return {"urgency_score": 5, "reason": "fallback",
                    "summary": item.get("subject", ""),
                    "recommended_action": "Review manually"}

        obj.analyze = _analyze
        monkeypatch.setattr(sn, "call_anthropic_with_fallback",
                            lambda **kw: _Reply(payload))
        return obj, calls
    return _build


ITEMS = [{"subject": "a"}, {"subject": "b"}]


@pytest.mark.parametrize("payload", [
    '["urgent", "ignore"]',
    "[1, 2]",
    '[null, null]',
    '[{"urgency_score": 9}, "not an object"]',
    "[[1], [2]]",
])
def test_a_list_of_the_wrong_thing_falls_back(sn, analyzer, payload):
    """It padded the list and handed strings to callers that call `.get`."""
    obj, calls = analyzer(payload)

    result = obj.analyze_batch(ITEMS)

    assert len(result) == len(ITEMS)
    assert all(isinstance(entry, dict) for entry in result), (
        "a non-dict reached the caller, which then calls .get on it")
    assert calls["individual"] == len(ITEMS), "the batch did not fall back"


def test_a_proper_list_of_objects_is_used_as_is(sn, analyzer):
    obj, calls = analyzer(json.dumps([
        {"urgency_score": 9, "reason": "r1", "summary": "s1",
         "recommended_action": "a1"},
        {"urgency_score": 2, "reason": "r2", "summary": "s2",
         "recommended_action": "a2"},
    ]))

    result = obj.analyze_batch(ITEMS)

    assert [r["urgency_score"] for r in result] == [9, 2]
    assert calls["individual"] == 0, "a valid batch reply was thrown away"


def test_a_short_list_of_objects_is_still_padded(sn, analyzer):
    """The padding path must survive the element check in front of it."""
    obj, _calls = analyzer(json.dumps([{"urgency_score": 9, "reason": "r",
                                        "summary": "s",
                                        "recommended_action": "a"}]))

    result = obj.analyze_batch(ITEMS)

    assert len(result) == 2
    assert result[1]["reason"] == "Missing from batch response"


@pytest.mark.parametrize("payload", ['"a bare string"', "7", "null"])
def test_a_non_list_reply_still_falls_back(sn, analyzer, payload):
    """The guard that was already there must not regress."""
    obj, calls = analyzer(payload)

    result = obj.analyze_batch(ITEMS)

    assert all(isinstance(entry, dict) for entry in result)
    assert calls["individual"] == len(ITEMS)


# ============================================================
# The inbox that reported zero
# ============================================================

class _Item:
    def __init__(self, i):
        self.id = f"id{i}"
        self.message_id = f"m{i}"
        self.sender = None
        self.text_body = "body"
        self.body = "body"
        self.has_attachments = False
        self.attachments = []
        self.datetime_received = i
        self.subject = f"s{i}"
        self.to_recipients = []
        self.importance = "Normal"


class _Query:
    def __init__(self, items):
        self._items = items

    def count(self):
        return len(self._items)

    def order_by(self, key):
        return _Query(sorted(self._items, key=lambda i: i.datetime_received,
                             reverse=key.startswith("-")))

    def __getitem__(self, item):
        return self._items[item]

    def __iter__(self):
        return iter(self._items)


class _Folder:
    def __init__(self, items):
        self._items = items

    def filter(self, **_kw):
        return _Query(self._items)


class _State:
    def __init__(self):
        self.done = set()

    def is_email_processed(self, mid):
        return mid in self.done

    def mark_email_processed(self, mid):
        self.done.add(mid)


@pytest.fixture
def inbox(sn):
    def _build(count, max_per_check=50):
        source = sn.EmailSource.__new__(sn.EmailSource)
        source.config = {"max_per_check": max_per_check, "folder": "inbox",
                         "ignore_senders": []}
        source.logger = logging.getLogger("probe")
        folder = _Folder([_Item(i) for i in range(count)])
        source.account = type("Acct", (), {"inbox": folder})()
        source.state = _State()
        source._is_ignored = lambda _addr: False
        return source
    return _build


def test_a_backlog_larger_than_the_window_is_eventually_drained(inbox):
    """Once the newest N were processed, every later cycle reported zero."""
    source = inbox(120, max_per_check=50)
    seen = set()

    for _cycle in range(6):
        for item in source.check_new():
            source.state.mark_email_processed(item["message_id"])
            seen.add(item["message_id"])

    assert len(seen) == 120, f"{120 - len(seen)} message(s) never entered a cycle"


def test_the_newest_mail_is_still_read_first(inbox):
    """This is an urgency daemon: fresh mail must not queue behind a backlog."""
    source = inbox(120, max_per_check=50)

    first = source.check_new()
    newest = max(int(i["message_id"][1:]) for i in first)

    assert newest == 119


def test_a_mailbox_inside_the_window_costs_no_extra_walk(inbox, caplog):
    source = inbox(10, max_per_check=50)

    with caplog.at_level(logging.INFO):
        got = source.check_new()

    assert len(got) == 10
    assert "backlog" not in caplog.text


def test_the_log_says_how_much_it_examined(inbox, caplog):
    """"0 new unread messages" over a server holding hundreds is the sentence
    that hid this."""
    source = inbox(120, max_per_check=50)

    with caplog.at_level(logging.INFO):
        source.check_new()

    assert "of 120 unread" in caplog.text


def test_a_count_failure_degrades_and_says_so(inbox, caplog, monkeypatch):
    source = inbox(120, max_per_check=50)

    def _boom(self):
        raise RuntimeError("EWS said no")

    monkeypatch.setattr(_Query, "count", _boom)

    with caplog.at_level(logging.WARNING):
        got = source.check_new()

    assert len(got) == 50, "it must still read the newest window"
    assert "could not count unread" in caplog.text


# ============================================================
# The first sight that read the whole history
# ============================================================

class _Message:
    def __init__(self, i):
        self.id = i


class _TelegramClient:
    def __init__(self, count):
        self.messages = [_Message(i) for i in range(1, count + 1)]

    async def get_messages(self, _entity, limit=None, min_id=0, max_id=0):
        pool = [m for m in self.messages
                if m.id > min_id and (max_id == 0 or m.id < max_id)]
        pool.sort(key=lambda m: m.id, reverse=True)
        return pool[:limit]


@pytest.fixture
def telegram(sn):
    source = sn.TelegramSource.__new__(sn.TelegramSource)
    source.client = _TelegramClient(1000)
    source.logger = logging.getLogger("probe")
    return source


def test_first_sight_reads_one_page_not_five(telegram):
    """It pulled 5 * limit, and 4/5 of that was already-read history."""
    got = asyncio.run(telegram._fetch_since(None, 0, 30, "Counterpart",
                                            max_pages=1))

    assert len(got) == 30


def test_first_sight_does_not_warn_about_a_number_it_never_measured(
        telegram, caplog):
    """`oldest - 0 - 1` is the whole chat history, logged as missed messages."""
    with caplog.at_level(logging.INFO):
        asyncio.run(telegram._fetch_since(None, 0, 30, "Counterpart",
                                          max_pages=1))

    assert "were NOT read" not in caplog.text
    assert "first sight" in caplog.text


def test_a_real_cursor_still_pages_to_close_the_gap(telegram):
    """The behaviour `_fetch_since` exists for must not regress."""
    got = asyncio.run(telegram._fetch_since(None, 900, 30, "Counterpart"))

    assert len(got) == 100
    assert min(m.id for m in got) == 901


def test_a_real_cursor_hitting_the_page_cap_still_warns(telegram, caplog):
    """Cursor 1, not 0: with a real lower bound the skipped count is a real
    number, and the bound biting must still be said out loud."""
    with caplog.at_level(logging.WARNING):
        asyncio.run(telegram._fetch_since(None, 1, 30, "Counterpart"))

    assert "were NOT read" in caplog.text, (
        "a bounded scan with a real cursor must still say what it left")


# ============================================================
# The alternative time nobody checked
# ============================================================

def test_the_conflict_window_covers_the_whole_search_reach(sn):
    """Days 8-12 were judged against an event list that stopped at day 7."""
    reach = 1 + sn.ALTERNATIVE_SEARCH_DAYS + sn._WEEKEND_BUFFER_DAYS

    assert sn.conflict_window_days() >= reach


@pytest.mark.parametrize("search_days", [1, 3, 5, 10])
def test_the_window_tracks_a_changed_search(sn, search_days):
    """Two numbers that must agree, written twice, drifted. One function cannot."""
    reach = 1 + search_days + sn._WEEKEND_BUFFER_DAYS

    assert sn.conflict_window_days(search_days) >= reach


def test_the_caller_fetches_the_derived_window(sn):
    source = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")

    assert "timedelta(days=conflict_window_days())" in source
    assert "# Fetch next 7 days of calendar for conflict checking" not in source


def test_the_search_default_comes_from_the_constant(sn):
    import inspect

    signature = inspect.signature(sn.CalendarPolicyEngine.find_alternative_slot)

    assert signature.parameters["search_days"].default == sn.ALTERNATIVE_SEARCH_DAYS


def test_a_single_object_reply_is_still_used_for_the_first_item(sn, analyzer):
    """The `elif isinstance(parsed, dict)` branch, which the element check
    added above it must not swallow."""
    obj, calls = analyzer(json.dumps({"urgency_score": 9, "reason": "r",
                                      "summary": "s",
                                      "recommended_action": "a"}))

    result = obj.analyze_batch(ITEMS)

    assert result[0]["urgency_score"] == 9, "the single-object branch was lost"
    assert calls["individual"] == len(ITEMS) - 1, (
        "only the items AFTER the first should fall back")


def test_a_mailbox_deeper_than_the_scan_cap_says_it_stopped(inbox, caplog, sn):
    """A bound that does not say it bit is the original defect in miniature."""
    total = sn.BACKLOG_SCAN_CAP + 200
    source = inbox(total, max_per_check=50)
    # The cap only bites once the oldest end is already handled: the walk has
    # to page PAST the processed ones to reach anything new.
    for i in range(sn.BACKLOG_SCAN_CAP + 50):
        source.state.mark_email_processed(f"m{i}")

    with caplog.at_level(logging.WARNING):
        source.check_new()

    assert "stopped scanning the backlog" in caplog.text
    assert "was NOT examined" in caplog.text


def test_the_scan_cap_is_a_real_bound(inbox, sn):
    """It must stop, not walk the whole mailbox."""
    source = inbox(sn.BACKLOG_SCAN_CAP + 200, max_per_check=50)

    got = source.check_new()

    assert len(got) <= 2 * 50, "the cycle read more than the two windows allow"
    assert len(got) > 0, "it read nothing at all"


def test_first_sight_is_wired_to_one_page(sn):
    """A source-shape check, and it says so.

    The choice lives inside `_check_personal_dms`, an async method that only
    runs behind a live Telethon client and its dialog iterator. The BEHAVIOUR
    of the one-page fetch is pinned by the tests above; what this pins is that
    the caller still asks for it, which is the wiring a refactor drops.
    """
    source = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")

    assert "max_pages=1 if last_known_id == 0 else None" in source


def test_an_offered_slot_never_lands_outside_the_checked_window(sn):
    """The search reach and the fetched window must agree, measured on the
    slot the code actually returns rather than on the arithmetic."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    engine = sn.CalendarPolicyEngine.__new__(sn.CalendarPolicyEngine)
    engine.tz = ZoneInfo("UTC")
    engine.logger = logging.getLogger("probe")
    engine._check_protected_time_simple = lambda *_a: True   # refuse every slot
    engine._has_conflict = lambda *_a: False

    seen_days = []
    original_replace = None

    reference = datetime(2026, 8, 26, tzinfo=ZoneInfo("UTC"))

    def _record(start, end, weekday):
        seen_days.append((start.date() - reference.date()).days)
        return True

    engine._check_protected_time_simple = _record
    engine.find_alternative_slot(30, "s", [], reference_date=reference)

    assert seen_days, "no candidate day was generated"
    assert max(seen_days) <= sn.conflict_window_days(), (
        f"the search reached day {max(seen_days)} while only "
        f"{sn.conflict_window_days()} days of events are fetched")
