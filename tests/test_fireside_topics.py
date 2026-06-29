"""Unit tests for scripts/fireside_topics.py (pure logic, no Telegram I/O)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

# Engine root = parent of tests/. scripts/ lives beside tests/ in this clone.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import fireside_topics as ft  # noqa: E402


@pytest.mark.parametrize("raw,expected", [
    ("/idea hands-on DPI capture", "hands-on DPI capture"),
    ("/idea   spaced   out  ", "spaced   out"),
    ("/idea@TribeFiresideBot war stories", "war stories"),
    ("/idea\nmultiline idea", "multiline idea"),
])
def test_parse_idea_valid(raw, expected):
    assert ft.parse_idea_command(raw) == expected


@pytest.mark.parametrize("raw", ["/idea", "/idea   ", "/idea ab", "not a command"])
def test_parse_idea_rejected(raw):
    assert ft.parse_idea_command(raw) is None


def test_parse_idea_truncates_overlong():
    long = "/idea " + ("x" * 5000)
    out = ft.parse_idea_command(long)
    assert out is not None and len(out) == ft.MAX_IDEA_LEN


def test_render_nudge_has_call_to_action():
    text = ft.render_nudge()
    assert "/idea" in text and len(text) > 0


def test_render_cycle_end_invite_mentions_next_cycle():
    text = ft.render_cycle_end_invite()
    assert "/idea" in text and "topic" in text.lower()


def test_render_digest_empty_and_nonempty():
    assert ft.render_digest([]) == ""
    ideas = [{"name": "Alice", "text": "DPI deep dive", "ts": "2026-06-25T12:00:00+04:00"}]
    out = ft.render_digest(ideas)
    assert "Alice" in out and "DPI deep dive" in out


def test_render_backlog_summary_counts():
    ideas = [
        {"name": "A", "text": "one", "ts": "2026-06-20T10:00:00+04:00"},
        {"name": "B", "text": "two", "ts": "2026-06-21T10:00:00+04:00"},
    ]
    out = ft.render_backlog_summary(ideas)
    assert "2" in out and "one" in out and "two" in out


def _seed(state_dir, n, cycle=1):
    ids = []
    for k in range(n):
        ids.append(ft.append_idea(
            state_dir,
            now_iso=f"2026-06-2{k}T10:00:00+04:00",
            user_id=100 + k, username=f"user{k}", name=f"User {k}",
            text=f"idea number {k}", cycle=cycle,
        ))
    return ids


def test_append_and_load_roundtrip(tmp_path):
    ids = _seed(tmp_path, 3)
    assert len(set(ids)) == 3  # uuids unique
    ideas = ft.load_ideas(tmp_path)
    assert [i["text"] for i in ideas] == ["idea number 0", "idea number 1", "idea number 2"]
    assert ideas[0]["user_id"] == 100 and ideas[0]["cycle"] == 1


def test_load_ideas_cycle_filter(tmp_path):
    _seed(tmp_path, 2, cycle=1)
    _seed(tmp_path, 1, cycle=2)
    assert len(ft.load_ideas(tmp_path, cycle=1)) == 2
    assert len(ft.load_ideas(tmp_path, cycle=2)) == 1


def test_load_ideas_skips_corrupt_lines(tmp_path):
    _seed(tmp_path, 1)
    (tmp_path / ft.TOPIC_IDEAS_FILE).open("a", encoding="utf-8").write("{not json\n")
    assert len(ft.load_ideas(tmp_path)) == 1  # corrupt line ignored


def test_topic_state_default_and_roundtrip(tmp_path):
    st = ft.load_topic_state(tmp_path)
    assert st == {"last_digest_idea_id": None, "pending_cycle_invite": None}
    st["last_digest_idea_id"] = "abc"
    ft.save_topic_state(tmp_path, st)
    assert ft.load_topic_state(tmp_path)["last_digest_idea_id"] == "abc"


def test_new_ideas_since_no_cursor_returns_all(tmp_path):
    ids = _seed(tmp_path, 3)
    new, cursor = ft.new_ideas_since(tmp_path, None)
    assert [i["idea_id"] for i in new] == ids
    assert cursor == ids[-1]


def test_new_ideas_since_partial_cursor(tmp_path):
    ids = _seed(tmp_path, 3)
    new, cursor = ft.new_ideas_since(tmp_path, ids[0])
    assert [i["idea_id"] for i in new] == ids[1:]
    assert cursor == ids[-1]


def test_new_ideas_since_cursor_at_head_returns_empty(tmp_path):
    ids = _seed(tmp_path, 3)
    new, cursor = ft.new_ideas_since(tmp_path, ids[-1])
    assert new == []
    assert cursor == ids[-1]  # unchanged


def test_new_ideas_since_unknown_cursor_returns_all(tmp_path):
    ids = _seed(tmp_path, 2)
    new, cursor = ft.new_ideas_since(tmp_path, "deadbeef-not-present")
    assert [i["idea_id"] for i in new] == ids
    assert cursor == ids[-1]


# A 2-week toy cycle: week 1 = Mon 2026-06-29 / Wed 2026-07-01,
#                     week 2 = Mon 2026-07-06 / Wed 2026-07-08 (final week).
_SCHED = [
    {"week": 1, "cycle": 1, "day": "Mon", "session_date": "2026-06-29"},
    {"week": 1, "cycle": 1, "day": "Wed", "session_date": "2026-07-01"},
    {"week": 2, "cycle": 1, "day": "Mon", "session_date": "2026-07-06"},
    {"week": 2, "cycle": 1, "day": "Wed", "session_date": "2026-07-08"},
]


def test_upcoming_week():
    assert ft._upcoming_week(_SCHED, date(2026, 6, 25)) == 1
    assert ft._upcoming_week(_SCHED, date(2026, 7, 2)) == 2   # next session is wk2 Mon
    assert ft._upcoming_week(_SCHED, date(2026, 7, 9)) is None  # cycle over


def test_is_final_week():
    assert ft.is_final_week(_SCHED, date(2026, 6, 29)) is False  # week 1
    assert ft.is_final_week(_SCHED, date(2026, 7, 5)) is True    # Sun before final Mon
    assert ft.is_final_week(_SCHED, date(2026, 7, 9)) is False   # cycle over


def test_cycle_end_trigger_today_only_final_sunday():
    assert date(2026, 7, 5).weekday() == 6                       # Sunday
    assert ft.cycle_end_trigger_today(_SCHED, date(2026, 7, 5)) is True
    assert date(2026, 6, 28).weekday() == 6                      # earlier Sunday
    assert ft.cycle_end_trigger_today(_SCHED, date(2026, 6, 28)) is False
    assert ft.cycle_end_trigger_today(_SCHED, date(2026, 7, 6)) is False  # final Monday


def test_cycle_detection_empty_schedule():
    assert ft._upcoming_week([], date(2026, 7, 5)) is None
    assert ft.is_final_week([], date(2026, 7, 5)) is False
    assert ft.cycle_end_trigger_today([], date(2026, 7, 5)) is False


def test_current_cycle_reads_schedule():
    sched = [
        {"week": 1, "day": "Mon", "session_date": "2026-06-29", "cycle": 1},
        {"week": 1, "day": "Mon", "session_date": "2026-07-20", "cycle": 2},
    ]
    assert ft.current_cycle(sched, date(2026, 6, 25)) == 1
    assert ft.current_cycle(sched, date(2026, 7, 10)) == 2   # cycle 1 over, next is cycle 2
    assert ft.current_cycle([], date(2026, 6, 25)) == 1      # empty -> 1
