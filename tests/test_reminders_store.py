import json
from datetime import date
from pathlib import Path

import pytest

from scripts.utils import reminders_store as rs


def test_first_friday_minus_1_known_months():
    # First Friday of Aug 2026 is Aug 7 -> minus 1 = Aug 6 (Thursday).
    assert rs.first_friday_minus_1(2026, 8) == date(2026, 8, 6)
    # First Friday of Sep 2026 is Sep 4 -> minus 1 = Sep 3.
    assert rs.first_friday_minus_1(2026, 9) == date(2026, 9, 3)
    # First Friday of May 2026 is May 1 -> minus 1 = Apr 30 (crosses month).
    assert rs.first_friday_minus_1(2026, 5) == date(2026, 4, 30)


def test_once_due_on_and_after_date():
    rec = {"kind": "once", "when": "2026-07-26", "status": "active"}
    assert rs.is_due(rec, date(2026, 7, 26)) is True
    assert rs.is_due(rec, date(2026, 7, 27)) is True   # missed day still fires
    assert rs.is_due(rec, date(2026, 7, 25)) is False


def test_once_fired_not_due():
    rec = {"kind": "once", "when": "2026-07-26", "status": "fired"}
    assert rs.is_due(rec, date(2026, 7, 27)) is False


def test_recurring_due_only_on_rule_date_once_per_period():
    rec = {"kind": "recurring", "when": "first-friday-minus-1", "last_fired": None}
    assert rs.is_due(rec, date(2026, 8, 6)) is True
    # Day before the target: the previous period's target (Jul 2) is far in
    # the past (well beyond the catch-up grace window), so not due.
    assert rs.is_due(rec, date(2026, 8, 5)) is False
    fired = {**rec, "last_fired": "2026-08-06"}
    assert rs.is_due(fired, date(2026, 8, 6)) is False  # already fired this period


def test_recurring_catchup_within_grace_window():
    # Target Aug 6 2026 missed; host boots 2 days late (within the 3-day
    # grace window) -> still fires as a catch-up.
    rec = {"kind": "recurring", "when": "first-friday-minus-1", "last_fired": None}
    assert rs.is_due(rec, date(2026, 8, 8)) is True


def test_recurring_not_due_beyond_grace_window():
    # Target Aug 6 2026 missed; host boots 4 days late (beyond the 3-day
    # grace window) -> best-effort catch-up gives up, no longer due.
    rec = {"kind": "recurring", "when": "first-friday-minus-1", "last_fired": None}
    assert rs.is_due(rec, date(2026, 8, 10)) is False


def test_recurring_due_across_month_boundary():
    # First Friday of May 2026 is May 1 -> target is Apr 30 (previous month).
    # A month-keyed `today.month` lookup never matches this target: on Apr 30
    # `today.month=4` computes first-friday-minus-1 of April, not May's.
    rec = {"kind": "recurring", "when": "first-friday-minus-1", "last_fired": None}
    assert rs.is_due(rec, date(2026, 4, 30)) is True
    # Catch-up semantics: May 1 is 1 day after the Apr 30 target, within the
    # grace window, so it IS now due (supersedes the old exact-match
    # expectation that this was False).
    assert rs.is_due(rec, date(2026, 5, 1)) is True
    # Beyond grace: May 5 is 5 days after the Apr 30 target -> no longer due.
    assert rs.is_due(rec, date(2026, 5, 5)) is False
    fired = {**rec, "last_fired": "2026-04-30"}
    assert rs.is_due(fired, date(2026, 4, 30)) is False


def test_mark_fired_recurring_catchup_writes_matched_target(tmp_path, monkeypatch):
    # On a catch-up day, mark_fired must record the MATCHED target (Apr 30),
    # not `today` (May 1) -- otherwise is_due would stay True for the actual
    # target date and re-fire spuriously.
    monkeypatch.setattr(rs, "store_path", lambda: tmp_path / "reminders.json")
    saved = rs.add({"kind": "recurring", "when": "first-friday-minus-1", "last_fired": None, "message": "m"})
    rs.mark_fired(saved["id"], date(2026, 5, 1))
    rec = rs.load()[0]
    assert rec["last_fired"] == "2026-04-30"
    assert rs.is_due(rec, date(2026, 4, 30)) is False
    assert rs.is_due(rec, date(2026, 5, 1)) is False


def test_upcoming_recurring_across_month_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "store_path", lambda: tmp_path / "reminders.json")
    rs.add({"kind": "recurring", "when": "first-friday-minus-1", "last_fired": None, "message": "m"})
    hits = rs.upcoming(date(2026, 4, 27), days=7)
    assert len(hits) == 1 and hits[0]["message"] == "m"
    # A window that reaches neither this month's nor next month's candidate.
    no_hits = rs.upcoming(date(2026, 5, 10), days=3)
    assert no_hits == []


def test_add_load_roundtrip_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "store_path", lambda: tmp_path / "reminders.json")
    saved = rs.add({"kind": "once", "when": "2026-07-26", "message": "hi"})
    assert saved["id"] and saved["created"]
    got = rs.load()
    assert len(got) == 1 and got[0]["message"] == "hi"


def test_load_corrupt_raises(tmp_path, monkeypatch):
    p = tmp_path / "reminders.json"
    p.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(rs, "store_path", lambda: p)
    with pytest.raises(ValueError):
        rs.load()


def test_mark_fired_once_and_recurring(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "store_path", lambda: tmp_path / "reminders.json")
    a = rs.add({"kind": "once", "when": "2026-07-26", "message": "a"})
    b = rs.add({"kind": "recurring", "when": "first-friday-minus-1", "message": "b"})
    rs.mark_fired(a["id"], date(2026, 7, 26))
    rs.mark_fired(b["id"], date(2026, 8, 6))
    recs = {r["id"]: r for r in rs.load()}
    assert recs[a["id"]]["status"] == "fired"
    assert recs[b["id"]]["last_fired"] == "2026-08-06"
