#!/usr/bin/env python3
"""The daemon that could be locked out of its own PID file, and nine siblings.

`scripts/sentinel.py` watches the operator's mail, Telegram and calendar and is
the thing that tells him something is urgent. Every defect pinned here is a way
it went quiet, or said something it had not checked. Ten were found on
2026-08-25; nine are fixed and pinned, one is deliberately NOT fixed and is
pinned as it stands, with the reason.

THE PID FILE. `open(PID_FILE, "w")` is O_WRONLY|O_CREAT|O_TRUNC, so the
truncation happens at open(2) - before the `flock` two lines below can refuse.
A second instance starting beside a healthy daemon therefore EMPTIED the live
daemon's PID file and then exited on the lock, and the write-back at the bottom
of that block is never reached on the losing branch. `--status` then printed
UNKNOWN, `--stop` deleted the empty file without signalling anything, and the
only way left to stop the running daemon was a manual pkill.

THE TELEGRAM CURSOR. `get_messages(limit=N, min_id=cursor)` returns the N
NEWEST messages above the cursor. Both readers then set the cursor to
`max(m.id)`, so when more than N messages had arrived since the last cycle,
everything between the cursor and the oldest fetched message was skipped
PERMANENTLY - no log line, no counter, no second chance. The default is 30 and a
30-message burst in a group chat between two fifteen-minute cycles is ordinary.

THE ESCALATIONS. `_escalate_invite` returns False when a configured notifier
failed, and one call site in five read it. The other four marked the invite
processed anyway, so an invite whose whole purpose was "a human must see this"
was consumed with nobody told and never came back.

THE SENTENCES. `--status` said "Sentinel is RUNNING" on the strength of
`os.kill(pid, 0)`, which establishes only that SOME process holds that number;
after a crash the number gets reused. The digest scheduler's comment said its
window "matches check interval" while the window was a hardcoded 15 and the
interval was configurable. Both are `.claude/rules/scope-claims.md` defects: a
tool saying more than its method established.

THE ONE NOT FIXED. `_check_back_to_back` reads `0 < gap < min_gap`, so a
zero-minute gap - a meeting starting the instant another ends - is judged
compliant against a `min_gap_minutes: 15` policy. The fix is one character. It
is NOT applied, because a back_to_back violation routes to `decline` and a
decline SENDS a message to the organizer: that changes which real people receive
an automated refusal, and the calendar auto-reply is the operator's own design,
frozen on 2026-08-23 pending his redesign. `test_a_zero_gap_is_currently_judged_compliant`
below pins the CURRENT behaviour so the defect stays visible instead of drifting
out of sight. A green test there is NOT approval of the behaviour.

NOTE ON METHOD: nothing here connects to Exchange, Telegram or the network.
Fakes stand in for the Telethon client and for exchangelib items; the PID tests
use a real file in tmp_path and never signal a real process.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SENTINEL_SRC = ROOT / "scripts" / "sentinel.py"

from scripts import sentinel as sen  # noqa: E402


# ============================================================
# Finding 1 - the PID file truncated before the lock
# ============================================================

def test_the_pid_file_is_opened_without_truncating(tmp_path, monkeypatch):
    """The whole finding. `"w"` truncates at open(2); the flock comes after."""
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    assert 'open(PID_FILE, "w")' not in src, (
        "the PID file is opened with a truncating mode again, so a losing "
        "instance empties the live daemon's PID file before the lock refuses"
    )
    assert 'open(PID_FILE, "a+")' in src


def test_the_truncate_happens_after_the_lock_not_before():
    """Order is the entire defect: truncate must sit below the flock."""
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    opened = src.index('open(PID_FILE, "a+")')
    locked = src.index("fcntl.flock(self._pid_file_handle")
    truncated = src.index("self._pid_file_handle.truncate()")
    assert opened < locked < truncated, (
        "the PID file is emptied before the lock decides who owns it"
    )


def test_a_losing_instance_leaves_the_live_pid_intact(tmp_path):
    """Behavioural proof, with no daemon involved: the mode is what matters.

    `"a+"` on an existing file leaves its bytes alone, so a second instance
    that opens the path and then loses the lock returns the file untouched.
    `"w"` empties it at open, before any lock can be consulted.
    """
    pid_file = tmp_path / "sentinel.pid"
    pid_file.write_text("4242", encoding="utf-8")

    with open(pid_file, "a+"):              # the fixed mode
        pass                                # ...loser exits without writing
    assert pid_file.read_text(encoding="utf-8") == "4242"

    with open(pid_file, "w"):               # the old mode
        pass
    assert pid_file.read_text(encoding="utf-8") == "", (
        "if this ever stops emptying the file, the finding was misdiagnosed"
    )


def test_the_winner_still_leaves_only_its_own_pid(tmp_path):
    """"a+" appends by default, so the truncate is load-bearing: without it a
    restart would leave "42424242" and `int()` would read a nonsense PID."""
    pid_file = tmp_path / "sentinel.pid"
    pid_file.write_text("4242", encoding="utf-8")
    with open(pid_file, "a+") as handle:
        handle.seek(0)
        handle.truncate()
        handle.write("99")
    assert pid_file.read_text(encoding="utf-8") == "99"


def test_the_seek_sits_before_the_truncate(tmp_path):
    """`truncate()` with no argument cuts at the CURRENT position, and "a+"
    opens at the END of the file. Without the seek the old bytes survive and
    the new PID is appended: a file holding "4242" becomes "424299", which
    `int()` reads back as a real-looking PID belonging to nothing.

    Proven here, then pinned structurally, because the daemon's own write is
    not reachable from a test without starting it.
    """
    p = tmp_path / "pid"
    p.write_text("4242", encoding="utf-8")
    with open(p, "a+") as fh:
        fh.truncate()          # no seek: the defect
        fh.write("99")
    assert p.read_text(encoding="utf-8") == "424299"

    src = SENTINEL_SRC.read_text(encoding="utf-8")
    assert (src.index("self._pid_file_handle.seek(0)")
            < src.index("self._pid_file_handle.truncate()"))


def test_the_lock_is_non_blocking():
    """`LOCK_NB` is what makes a second instance EXIT. Without it `flock` waits
    forever, so the loser neither runs nor reports - it just hangs, holding a
    systemd unit in `activating` until something times it out. Structural: two
    live processes are not something a unit test can arrange."""
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    assert "fcntl.LOCK_EX | fcntl.LOCK_NB" in src


def test_a_pid_file_left_by_the_loser_still_reads_back(tmp_path, monkeypatch):
    """End to end through the real reader: an untouched file is not corrupt."""
    pid_file = tmp_path / "sentinel.pid"
    pid_file.write_text("4242", encoding="utf-8")
    monkeypatch.setattr(sen, "PID_FILE", pid_file)
    assert sen._read_pid_file() == 4242


def test_an_emptied_pid_file_reads_as_none(tmp_path, monkeypatch):
    """The state the old code produced, and why it was unrecoverable."""
    pid_file = tmp_path / "sentinel.pid"
    pid_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(sen, "PID_FILE", pid_file)
    assert sen._read_pid_file() is None


# ============================================================
# Finding 2 - the Telegram cursor that skipped a backlog
# ============================================================

class _Msg:
    def __init__(self, msg_id, text="hi", out=False):
        self.id = msg_id
        self.text = text
        self.out = out
        self.media = None


class _PagingClient:
    """Telethon's get_messages contract, including the parts that bite.

    Newest-first, capped at `limit`, `min_id` exclusive lower bound, `max_id`
    exclusive upper bound with 0 meaning "none".
    """

    def __init__(self, messages):
        self._messages = list(messages)
        self.calls = []
        # What each call actually handed back, so a test can assert the paging
        # contract (page N+1 asks below page N's oldest id) against the pages
        # themselves rather than against ids copied out of one fixture run.
        self.returned = []

    async def get_messages(self, entity, limit=None, min_id=0, max_id=0):
        self.calls.append({"limit": limit, "min_id": min_id, "max_id": max_id})
        window = [m for m in self._messages if m.id > min_id]
        if max_id:
            window = [m for m in window if m.id < max_id]
        window.sort(key=lambda m: m.id, reverse=True)
        page = window[:limit]
        self.returned.append([m.id for m in page])
        return page


def _source(client, logger=None):
    src = sen.TelegramSource(
        config={}, state=None,
        logger=logger or logging.getLogger("test-sentinel-p4"))
    src.client = client
    return src


def test_a_backlog_larger_than_one_page_is_fully_read():
    """The finding. 25 new messages, a page size of 10: the old single fetch
    returned ids 25..16 and moved the cursor to 25, losing 15 messages."""
    client = _PagingClient(_Msg(i) for i in range(1, 26))
    got = asyncio.run(_source(client)._fetch_since(object(), 0, 10, "chat"))
    assert sorted(m.id for m in got) == list(range(1, 26))


def test_a_short_page_stops_the_paging_immediately():
    """One API call when one is enough. Paging must not cost a round trip per
    cycle on a quiet dialog."""
    client = _PagingClient([_Msg(101), _Msg(102)])
    got = asyncio.run(_source(client)._fetch_since(object(), 100, 30, "chat"))
    assert len(got) == 2
    assert len(client.calls) == 1


def test_nothing_new_costs_one_empty_call_and_no_more():
    client = _PagingClient([])
    got = asyncio.run(_source(client)._fetch_since(object(), 100, 30, "chat"))
    assert got == []
    assert len(client.calls) == 1


def test_the_cursor_bound_is_carried_into_every_page():
    """`min_id` must stay pinned to the stored cursor across pages, or the
    second page walks back past it and re-reports old messages."""
    client = _PagingClient(_Msg(i) for i in range(1, 26))
    asyncio.run(_source(client)._fetch_since(object(), 5, 10, "chat"))
    assert {c["min_id"] for c in client.calls} == {5}


def test_each_page_asks_for_messages_older_than_the_last():
    """Every call after the first carries the previous page's LOWEST id as its
    upper bound, which is what makes the walk move backwards and terminate.

    This read `maxes == sorted(maxes, reverse=True)[:len(maxes)] or
    maxes[1:] == [16, 6]`. The first clause can never hold once there is more
    than one page - the first call's `max_id` is 0, meaning "no upper bound",
    so a descending list starting at 0 is impossible - and it is vacuously true
    for a single call. The only clause that ever decided anything was the
    hardcoded pair, which pins this fixture rather than the contract.
    """
    client = _PagingClient(_Msg(i) for i in range(1, 26))
    asyncio.run(_source(client)._fetch_since(object(), 0, 10, "chat"))
    maxes = [c["max_id"] for c in client.calls]
    assert maxes[0] == 0, "the first page must have no upper bound"
    assert len(maxes) >= 3, (
        f"the fixture must page at least three times or the loop below asserts "
        f"nothing; it made {len(maxes)} call(s)")
    for i in range(1, len(maxes)):
        previous = client.returned[i - 1]
        assert previous, "a page that returned nothing must end the walk"
        assert maxes[i] == min(previous), (
            f"call {i} asked below {maxes[i]}, but page {i - 1} reached down to "
            f"{min(previous)}; the gap between them is never fetched")


def test_no_message_is_returned_twice_across_pages():
    client = _PagingClient(_Msg(i) for i in range(1, 26))
    got = asyncio.run(_source(client)._fetch_since(object(), 0, 10, "chat"))
    ids = [m.id for m in got]
    assert len(ids) == len(set(ids))


def test_a_backlog_past_the_page_cap_is_reported_not_swallowed(caplog):
    """The bound has to exist - a month-quiet dialog must not stall the cycle -
    but a silent bound is the original defect wearing a smaller hat.

    The cursor is 1, not 0. With a cursor of 0 there is no lower bound, so
    `oldest - 0 - 1` counts every id below the oldest page - the whole chat
    history, which this function never measured. That case now logs a "first
    sight" line instead; the warning belongs to a REAL cursor, which is what
    this test exercises."""
    client = _PagingClient(_Msg(i) for i in range(1, 501))
    logger = logging.getLogger("test-sentinel-p4-cap")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        got = asyncio.run(
            _source(client, logger)._fetch_since(object(), 1, 10, "loud-chat"))
    assert len(got) == 10 * sen.TelegramSource.MAX_FETCH_PAGES
    assert "were NOT read" in caplog.text
    assert "loud-chat" in caplog.text


def test_the_skipped_count_is_the_real_gap(caplog):
    """The number in that warning has to be the number actually lost.

    Cursor 1 for the same reason as the test above: only a real cursor gives
    the subtraction a floor that means something."""
    client = _PagingClient(_Msg(i) for i in range(1, 101))
    logger = logging.getLogger("test-sentinel-p4-count")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        asyncio.run(_source(client, logger)._fetch_since(object(), 1, 10, "c"))
    # Pages cover ids 100..51. Cursor 1, so ids 2..50 were never read.
    assert "49 message(s)" in caplog.text


def test_a_backlog_that_exactly_fills_the_pages_warns_about_nothing(caplog):
    """The boundary. Five full pages that happen to reach the cursor exactly
    leave nothing behind, so the loop runs out of pages with a gap of zero. A
    warning here would cry wolf on a complete read.

    Cursor 1, so the corpus runs to `total + 1` and the window is exactly the
    five full pages. A cursor of 0 would route this to the "first sight" line
    and never reach the subtraction this test is about."""
    total = 10 * sen.TelegramSource.MAX_FETCH_PAGES
    client = _PagingClient(_Msg(i) for i in range(1, total + 2))
    logger = logging.getLogger("test-sentinel-p4-exact")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        got = asyncio.run(_source(client, logger)._fetch_since(object(), 1, 10, "c"))
    assert len(got) == total
    assert "were NOT read" not in caplog.text


def test_a_full_read_logs_no_warning(caplog):
    """A warning on every cycle is a warning nobody reads."""
    client = _PagingClient(_Msg(i) for i in range(1, 26))
    logger = logging.getLogger("test-sentinel-p4-quiet")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        asyncio.run(_source(client, logger)._fetch_since(object(), 0, 10, "c"))
    assert "were NOT read" not in caplog.text


def test_both_readers_go_through_the_paging_helper():
    """Structural. The DM reader and the monitored-chat reader each had their
    own copy of the single-fetch bug; a fix applied to one is not a fix."""
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    assert src.count("await self._fetch_since(") == 2
    assert "self.client.get_messages(entity, limit=max_msgs, min_id=last_known_id)" not in src


# ============================================================
# Finding 3 - escalations whose delivery was never checked
# ============================================================

def test_every_escalation_call_site_reads_the_return_value():
    """Five call sites. One read the bool; four marked the invite processed
    whatever happened, so a failed notify consumed the invite silently."""
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    body = src[src.index("async def _process_meeting_invites"):
               src.index("async def _notify_invite_decision")]
    discarded = [line.strip() for line in body.splitlines()
                 if "_escalate_invite(" in line
                 and line.strip().startswith("await self._escalate_invite(")]
    assert discarded == [], f"these call sites throw the bool away: {discarded}"


def test_every_escalation_site_can_leave_the_invite_unprocessed():
    """The bool has to lead somewhere. Reading it and then marking processed
    anyway would pass the test above and change nothing."""
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    body = src[src.index("async def _process_meeting_invites"):
               src.index("async def _notify_invite_decision")]
    assert body.count("_unprocessed_after_failed_escalation(") == 5


def test_every_unprocessed_call_sits_behind_a_live_condition():
    """Counting the calls is not enough: five of them inside `if False:` blocks
    would pass that count and change nothing. Each has to follow a test of the
    escalation result."""
    import ast
    tree = ast.parse(SENTINEL_SRC.read_text(encoding="utf-8"))
    target = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "_process_meeting_invites")
    def _is_the_call(stmt):
        return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Attribute)
                and stmt.value.func.attr == "_unprocessed_after_failed_escalation")

    guarded = 0
    for node in ast.walk(target):
        # Only the `if` that DIRECTLY holds the call. Walking every enclosing
        # `if` counts each site once per level of nesting.
        if not isinstance(node, ast.If) or not any(
                _is_the_call(stmt) for stmt in node.body):
            continue
        # A constant test (`if False:`) is dead. The condition must depend on
        # the escalation's result.
        assert not isinstance(node.test, ast.Constant), (
            "an unprocessed-invite branch is behind a constant condition")
        guarded += 1
    assert guarded == 5


def test_the_warning_names_the_invite_and_promises_the_retry(caplog):
    logger = logging.getLogger("test-sentinel-p4-esc")
    sentinel = object.__new__(sen.Sentinel)
    sentinel.logger = logger
    with caplog.at_level(logging.WARNING, logger=logger.name):
        sentinel._unprocessed_after_failed_escalation("INV-9", "the recurring-change")
    assert "INV-9" in caplog.text
    assert "retried next cycle" in caplog.text
    assert "the recurring-change" in caplog.text


def test_a_missing_notifier_still_reports_delivered():
    """The one case that must NOT retry forever. `_escalate_invite` returns
    True when there is no notifier at all, because an unconfigured workspace is
    a permanent state, not a transient failure."""
    logger = logging.getLogger("test-sentinel-p4-nonotif")
    sentinel = object.__new__(sen.Sentinel)
    sentinel.logger = logger
    sentinel.notifier = None
    result = asyncio.run(
        sentinel._escalate_invite({"subject": "Board sync"}, ["VIP"]))
    assert result is True


# ============================================================
# Finding 4 - NOT FIXED, pinned as it stands
# ============================================================

def test_a_zero_gap_is_currently_judged_compliant():
    """A KNOWN DEFECT, pinned green on purpose. Read the docstring of
    `_check_back_to_back` before touching this.

    `0 < gap < min_gap` excludes an exact zero, so a meeting starting the
    instant another ends passes a 15-minute-gap policy. The fix is `0 <= gap`.
    It is not applied because a back_to_back violation routes to `decline`, and
    a decline sends a message to a real organizer - the calendar auto-reply is
    the operator's design and is frozen. This test exists so the defect cannot
    quietly disappear from view, NOT because the behaviour is correct.
    """
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    assert src.count("if 0 < gap < min_gap:") == 2, (
        "the zero-gap comparison changed. If that was deliberate and the "
        "operator asked for it, update this test and the docstring above "
        "_check_back_to_back together"
    )


def test_the_frozen_defect_is_documented_where_the_code_is():
    """A finding recorded only in a test file is a finding the next reader of
    the source never sees."""
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    doc = src[src.index("def _check_back_to_back"):][:2000]
    assert "KNOWN DEFECT" in doc
    assert "frozen" in doc


def test_a_back_to_back_violation_really_does_route_to_decline():
    """The reason the one-character fix is not a one-character change. If this
    ever stops being true, the freeze argument has to be re-examined."""
    engine = object.__new__(sen.CalendarPolicyEngine)
    decision = engine._make_decision(
        [{"type": "back_to_back", "detail": "0m gap"}], is_vip=False)
    assert decision == "decline"


# ============================================================
# Finding 5 - the JSON extractor that counted braces in strings
# ============================================================

def _analyzer():
    return object.__new__(sen.UrgencyAnalyzer)


@pytest.mark.parametrize("text,why", [
    ('{"urgency_score": 8, "summary": "a } b", "reason": "x"}',
     "a closing brace inside a string ended the count early"),
    ('{"urgency_score": 3, "summary": "use { braces"}\n\nHope that helps!',
     "an opening brace inside a string meant NO truncation happened at all, so "
     "the model's trailing prose reached json.loads"),
    ('{"summary": "he said \\"} \\" ok", "urgency_score": 2}',
     "an escaped quote before a brace, same shape as the first"),
])
def test_a_brace_inside_a_string_value_no_longer_breaks_the_parse(text, why):
    assert _analyzer()._extract_json(text)["urgency_score"] in (2, 3, 8), why


def test_trailing_prose_after_the_object_is_still_cut_off():
    """The one thing this function exists to do."""
    out = _analyzer()._extract_json('{"urgency_score": 4}\n\nLet me know!')
    assert out == {"urgency_score": 4}


def test_a_fenced_response_still_parses():
    out = _analyzer()._extract_json('```json\n{"urgency_score": 6}\n```')
    assert out == {"urgency_score": 6}


def test_leading_prose_before_the_object_parses_too():
    out = _analyzer()._extract_json('Sure, here it is:\n{"urgency_score": 7}')
    assert out == {"urgency_score": 7}


def test_a_fenced_scalar_still_parses():
    """Fence stripping is NOT redundant now that raw_decode cuts the object out
    of prose. A fenced OBJECT parses either way, because the scan starts at the
    first brace. A fenced SCALAR has no brace, so without the strip the whole
    fence reaches json.loads and raises - and the caller then reports urgency 5
    over a number the model really sent."""
    assert _analyzer()._extract_json("```json\n7\n```") == 7


def test_a_response_with_no_object_still_raises_for_the_caller():
    """The caller has a JSONDecodeError branch that returns a safe fallback.
    Swallowing the error here would take that branch away."""
    with pytest.raises(json.JSONDecodeError):
        _analyzer()._extract_json("no json at all")


def test_a_scalar_response_still_reaches_the_caller_unchanged():
    """A bare number parses; the caller's own guard decides what to do with it,
    and that guard is tested in tests/test_a_dry_run_that_was_not_dry.py."""
    assert _analyzer()._extract_json("7") == 7


def test_the_brace_counter_is_gone():
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    assert "brace_depth" not in src
    assert "raw_decode" in src


# ============================================================
# Finding 6 - "Notification failed" logged for a delivered alert
# ============================================================

class _Notifier(sen.TelegramNotifier):
    def __init__(self, logger):
        self.logger = logger
        self.sent = []

    async def _send(self, message):
        self.sent.append(message)
        return True

    def _format_message(self, item, analysis):
        return "formatted"


def test_a_response_with_no_score_does_not_raise_after_the_send(caplog):
    """The alert is already on the wire by the time this line runs. Raising
    here made the caller log "Notification failed" for a DELIVERED alert, skip
    the `urgent_sent` counter, and never record the content hash - so a
    byte-identical repeat alerted again."""
    logger = logging.getLogger("test-sentinel-p4-notif")
    notifier = _Notifier(logger)
    with caplog.at_level(logging.INFO, logger=logger.name):
        asyncio.run(notifier.send_notification({"subject": "Wire transfer"}, {}))
    assert notifier.sent == ["formatted"]
    assert "Notification sent" in caplog.text


def test_the_missing_score_is_clamped_like_everywhere_else(caplog):
    """Every other reader of this field goes through `_clamp_score`. The one
    that did not was the one that ran after the send."""
    logger = logging.getLogger("test-sentinel-p4-clamp")
    notifier = _Notifier(logger)
    with caplog.at_level(logging.INFO, logger=logger.name):
        asyncio.run(notifier.send_notification({"subject": "x"}, {}))
    expected = sen.UrgencyAnalyzer._clamp_score(None)
    assert f"[{expected}/10]" in caplog.text


def test_a_normal_score_still_appears_in_the_log(caplog):
    logger = logging.getLogger("test-sentinel-p4-normal")
    notifier = _Notifier(logger)
    with caplog.at_level(logging.INFO, logger=logger.name):
        asyncio.run(notifier.send_notification({"subject": "x"}, {"urgency_score": 9}))
    assert "[9/10]" in caplog.text


def test_the_hard_subscript_is_gone():
    """Structural, and it has to ignore comments: the fix's own comment quotes
    the old expression, so a plain substring search over the file finds it."""
    import ast
    tree = ast.parse(SENTINEL_SRC.read_text(encoding="utf-8"))
    target = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "send_notification")
    hard = [n for n in ast.walk(target)
            if isinstance(n, ast.Subscript)
            and isinstance(n.value, ast.Name) and n.value.id == "analysis"]
    assert not hard, "send_notification subscripts `analysis` again"


# ============================================================
# Finding 7 - the digest window that could never open
# ============================================================

def _sentinel_with_interval(minutes):
    sentinel = object.__new__(sen.Sentinel)
    sentinel.config = object.__new__(sen.SentinelConfig)
    sentinel.config.check_interval = minutes * 60
    return sentinel


@pytest.mark.parametrize("interval,expected", [
    (5, 30), (15, 30), (20, 40), (30, 60), (60, 120),
])
def test_the_window_is_computed_from_the_interval(interval, expected):
    """The comment claimed the 15-minute window "matches check interval" while
    the 15 was a literal. At any longer interval the digest never fired."""
    assert _sentinel_with_interval(interval)._digest_window_minutes() == expected


def test_a_long_interval_can_still_reach_its_digest():
    """The headline case: a 60-minute interval with an 08:00 digest."""
    sentinel = _sentinel_with_interval(60)
    assert sentinel._time_in_window("08:45", "08:00") is True


def test_a_drifted_cycle_at_the_default_interval_still_fires():
    """It missed at the SHIPPED default too. The run loop sleeps the interval
    AFTER each cycle, so the period is the interval plus the cycle's duration
    and the start time drifts forward: a day straddling 07:58 -> 08:16 skipped
    the whole 08:00-08:15 window with no config change at all."""
    sentinel = _sentinel_with_interval(15)
    assert sentinel._time_in_window("08:16", "08:00") is True


def test_a_time_before_the_target_never_fires():
    sentinel = _sentinel_with_interval(15)
    assert sentinel._time_in_window("07:59", "08:00") is False


def test_the_target_minute_itself_fires():
    sentinel = _sentinel_with_interval(15)
    assert sentinel._time_in_window("08:00", "08:00") is True


def test_hours_later_is_outside_the_window():
    """A bounded catch-up, not an all-day one: a morning digest at 14:00 is
    noise, and the operator would rather see it missing than wrong."""
    sentinel = _sentinel_with_interval(15)
    assert sentinel._time_in_window("14:00", "08:00") is False


def test_a_malformed_time_is_false_not_a_traceback():
    sentinel = _sentinel_with_interval(15)
    assert sentinel._time_in_window("not-a-time", "08:00") is False
    assert sentinel._time_in_window("08:00", "") is False


def test_the_window_literal_is_gone_from_the_scheduler():
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    assert "return 0 <= (c_mins - t_mins) < 15" not in src
    assert "# Check within a 15-minute window (matches check interval)" not in src


# ============================================================
# Finding 8 - liveness reported as identity
# ============================================================

def test_status_refuses_to_call_a_reused_pid_the_daemon(tmp_path, monkeypatch, capsys):
    """`os.kill(pid, 0)` proves only that SOME process holds that number."""
    pid_file = tmp_path / "sentinel.pid"
    pid_file.write_text("4242", encoding="utf-8")
    monkeypatch.setattr(sen, "PID_FILE", pid_file)
    with patch.object(sen, "_is_pid_alive", return_value=True), \
         patch.object(sen, "_pid_is_sentinel", return_value=False):
        sen.check_status()
    out = capsys.readouterr().out
    assert "is NOT running" in out
    assert "RUNNING (PID" not in out
    assert not pid_file.exists(), "the stale file should be cleaned up"


def test_status_still_reports_a_real_daemon_as_running(tmp_path, monkeypatch, capsys):
    """The guard must not have become a wall."""
    pid_file = tmp_path / "sentinel.pid"
    pid_file.write_text("4242", encoding="utf-8")
    monkeypatch.setattr(sen, "PID_FILE", pid_file)
    monkeypatch.setattr(sen, "STATE_FILE", tmp_path / "missing-state.json")
    with patch.object(sen, "_is_pid_alive", return_value=True), \
         patch.object(sen, "_pid_is_sentinel", return_value=True):
        sen.check_status()
    assert "Sentinel is RUNNING (PID: 4242)" in capsys.readouterr().out


def test_a_dead_pid_is_still_reported_as_not_running(tmp_path, monkeypatch, capsys):
    pid_file = tmp_path / "sentinel.pid"
    pid_file.write_text("4242", encoding="utf-8")
    monkeypatch.setattr(sen, "PID_FILE", pid_file)
    with patch.object(sen, "_is_pid_alive", return_value=False):
        sen.check_status()
    assert "NOT running (stale PID file" in capsys.readouterr().out


def test_all_three_cli_paths_check_identity_not_just_liveness():
    """`--stop` checked identity from the start; `--status` and the
    already-running guard in main did not. The module's own docstring stated
    the principle - "Liveness is not identity" - and applied it in one of three
    places."""
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    assert src.count("_pid_is_sentinel(pid)") >= 3, (
        "a CLI path went back to trusting liveness alone"
    )


def test_the_already_running_guard_no_longer_refuses_a_legitimate_start():
    """This guard REFUSES a start, so its failure direction matters most: on a
    reused PID it told the operator the daemon was already running when nothing
    was. It is advisory anyway - the real second-instance guard is the flock."""
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    assert ("if pid is not None and _is_pid_alive(pid) and _pid_is_sentinel(pid):"
            in src)


# ============================================================
# Finding 9 - a state file that parsed but did not fit
# ============================================================

def _state(tmp_path, payload):
    path = tmp_path / "state.json"
    if payload is not None:
        path.write_text(payload, encoding="utf-8")
    manager = object.__new__(sen.StateManager)
    manager.path = path
    manager.read_only = True
    return manager._load()


@pytest.mark.parametrize("payload", [
    '{}',
    '{"version": 2}',
    '{"email": {}}',
    '{"email": {"processed_ids": []}}',
    '{"digest": {}, "calendar": {}}',
    '{"telegram": {"per_chat": {}}}',
])
def test_a_short_state_file_is_filled_out(tmp_path, payload):
    """Each of these parses as JSON and then killed the first cycle on a bare
    subscript deeper in the daemon."""
    data = _state(tmp_path, payload)
    assert data["email"]["processed_ids"] == [] or isinstance(
        data["email"]["processed_ids"], list)
    assert "last_check" in data["email"]
    assert "per_chat" in data["telegram"]
    assert "today" in data["digest"]
    assert "processed_invite_ids" in data["calendar"]
    assert "notified_hashes" in data


def test_real_content_survives_the_merge(tmp_path):
    """Filling gaps must never overwrite what the daemon already knew."""
    data = _state(tmp_path, json.dumps({
        "email": {"processed_ids": ["msg-1", "msg-2"]},
        "telegram": {"per_chat": {"7": {"last_id": 99}}},
    }))
    assert data["email"]["processed_ids"] == ["msg-1", "msg-2"]
    assert data["telegram"]["per_chat"] == {"7": {"last_id": 99}}
    assert data["email"]["last_check"] is None


def test_a_section_of_the_wrong_type_is_replaced(tmp_path):
    """A string where a dict belongs subscripts as badly as a missing key."""
    data = _state(tmp_path, '{"email": "corrupted"}')
    assert isinstance(data["email"], dict)
    assert data["email"]["processed_ids"] == []


def test_a_corrupt_file_still_falls_back_to_the_skeleton(tmp_path):
    data = _state(tmp_path, "{ not valid json")
    assert data["version"] == 2
    assert data["email"]["processed_ids"] == []


def test_a_top_level_list_is_not_treated_as_state(tmp_path):
    """Valid JSON, wrong shape. `.setdefault` on a list raises."""
    data = _state(tmp_path, '["not", "a", "state"]')
    assert isinstance(data, dict)
    assert data["email"]["processed_ids"] == []


def test_a_missing_file_gives_the_skeleton(tmp_path):
    data = _state(tmp_path, None)
    assert data["digest"]["urgent_sent"] == 0


def test_the_loaded_state_survives_the_readers_that_used_to_crash(tmp_path):
    """The three call sites the scout named, driven for real."""
    manager = object.__new__(sen.StateManager)
    manager.path = tmp_path / "state.json"
    manager.path.write_text('{"version": 2}', encoding="utf-8")
    manager.read_only = True
    manager.data = manager._load()
    assert manager.is_email_processed("anything") is False
    manager.data["email"]["last_check"] = "now"
    manager.data["telegram"]["last_check"] = "now"
    assert manager.data["digest"].get("today") is None


# ============================================================
# Finding 10 - the slot comment that named a time never generated
# ============================================================

def test_the_slot_comment_names_the_last_start_it_actually_generates():
    """`range(9, 18)` yields 9..17, so with minutes [0, 30] the last start is
    17:30. The comment said 18:00. Only the sentence is corrected: extending
    the range would change which alternative time is offered inside a decline
    message sent to a real organizer, on the frozen calendar path."""
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    assert "from 09:30 to 18:00" not in src
    assert "from 09:30 to 17:30" in src


def test_the_comment_records_why_the_range_was_not_extended():
    """The reason is the decision. A future reader who finds only "17:30" will
    read it as an oversight and widen the range, which changes what an
    automated decline offers a real organizer."""
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    comment = src[src.index("# Generate 30-min increment slots"):][:700]
    assert "frozen" in comment
    assert "_check_back_to_back" in comment


def test_the_slot_range_itself_is_unchanged():
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    assert "for hour in range(9, 18):" in src


def test_the_unreachable_end_guard_is_named_not_deleted():
    """`(slot_end.hour == 19 and slot_end.minute > 0)` cannot be reached:
    `slot_end.hour >= 19` short-circuits every hour-19 case first. It is dead
    on its own merits and predates the comment defect, so per the restraint
    rule it stays and is named here rather than folded into an unrelated fix."""
    src = SENTINEL_SRC.read_text(encoding="utf-8")
    assert "slot_end.hour == 19 and slot_end.minute > 0" in src
