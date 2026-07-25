"""Tests for Helmsman coverage in scripts/fireside-bot.py.

Cycle 2 rolled over on 2026-07-13 with nine of its ten weeks unassigned, and
every surface fell back to "TBD" in silence: `/next` printed TBD, day-of DMs
went out to speakers reading "[Helmsman TBD]", the pinned Sunday preview told
the whole Tribe the Helmsman was not picked, and the daily helmsman-brief job
logged "nothing to brief" while pinging its healthcheck green.

These tests pin the two behaviours that make that gap impossible to miss again:
gaps are queryable, and a week assigned late is still briefable.
"""
from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def fb():
    """Load fireside-bot.py as a module (hyphen in filename)."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "fireside-bot.py"
    spec = importlib.util.spec_from_file_location("fireside_bot", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _schedule(mondays):
    """Minimal schedule: one Mon + one Wed entry per week."""
    out = []
    for i, m in enumerate(mondays, start=1):
        d = date.fromisoformat(m)
        out.append({"week": i, "session_date": m, "day": "Mon", "slot": 1})
        out.append({"week": i, "session_date": (d + timedelta(days=2)).isoformat(),
                    "day": "Wed", "slot": 1})
    return out


# ---------------------------------------------------------------- gaps

def test_gaps_lists_every_unassigned_week(fb):
    sched = _schedule(["2026-07-13", "2026-07-20", "2026-07-27"])
    helmsmen = {"2026-07-13": {"name": "Week One Helmsman"}}
    assert fb.helmsman_gaps(sched, helmsmen) == ["2026-07-20", "2026-07-27"]


def test_gaps_empty_when_fully_assigned(fb):
    sched = _schedule(["2026-07-13", "2026-07-20"])
    helmsmen = {"2026-07-13": {"name": "A"}, "2026-07-20": {"name": "B"}}
    assert fb.helmsman_gaps(sched, helmsmen) == []


def test_gaps_treats_entry_without_name_as_unassigned(fb):
    """A record can exist with only a note - that is still no Helmsman."""
    sched = _schedule(["2026-07-20"])
    assert fb.helmsman_gaps(sched, {"2026-07-20": {"briefed": False}}) == ["2026-07-20"]


def test_gaps_honours_on_or_after(fb):
    sched = _schedule(["2026-07-13", "2026-07-20", "2026-07-27"])
    gaps = fb.helmsman_gaps(sched, {}, on_or_after=date(2026, 7, 20))
    assert gaps == ["2026-07-20", "2026-07-27"]


def test_gaps_ignores_wednesday_rows(fb):
    """Only Mondays key helmsmen.json; a Wed row must never become a gap."""
    sched = _schedule(["2026-07-20"])
    assert fb.helmsman_gaps(sched, {"2026-07-20": {"name": "A"}}) == []


# ------------------------------------------------- brief candidates (D2)

def test_late_assignment_is_still_briefable(fb):
    """The regression: an entry created ON its own Monday was never briefable.

    The old rule was `today < key_date`, so the 2026-07-13 week - assigned the
    same day it began - could never be picked up. It stays briefable through
    its Wednesday session.
    """
    helmsmen = {"2026-07-13": {"name": "Week One Helmsman", "briefed": False}}
    got = fb.helmsman_brief_candidates(helmsmen, date(2026, 7, 13))
    assert [k for _, k, _ in got] == ["2026-07-13"]


def test_week_is_briefable_through_its_wednesday(fb):
    helmsmen = {"2026-07-13": {"name": "J", "briefed": False}}
    assert fb.helmsman_brief_candidates(helmsmen, date(2026, 7, 15))
    # Thursday - the week's sessions are over, nothing left to brief.
    assert not fb.helmsman_brief_candidates(helmsmen, date(2026, 7, 16))


def test_briefed_entries_are_skipped(fb):
    helmsmen = {"2026-07-20": {"name": "A", "briefed": True}}
    assert fb.helmsman_brief_candidates(helmsmen, date(2026, 7, 14)) == []


def test_beyond_horizon_is_skipped(fb):
    helmsmen = {"2026-08-31": {"name": "A", "briefed": False}}
    assert fb.helmsman_brief_candidates(helmsmen, date(2026, 7, 14)) == []


def test_candidates_sorted_soonest_first(fb):
    helmsmen = {
        "2026-07-27": {"name": "later", "briefed": False},
        "2026-07-20": {"name": "sooner", "briefed": False},
    }
    got = fb.helmsman_brief_candidates(helmsmen, date(2026, 7, 20))
    assert [e["name"] for _, _, e in got] == ["sooner", "later"]


def test_nudge_failure_never_costs_the_briefing(fb, monkeypatch):
    """The nudge runs before the brief; a broken send must not abort the job."""
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "12345")
    monkeypatch.setattr(fb, "get_bot", lambda: (_ for _ in ()).throw(RuntimeError("no token")))
    logged = []
    monkeypatch.setattr(fb, "log_error", lambda *a, **k: logged.append(a))

    sched = _schedule(["2026-07-20"])
    fb._nudge_ceo_on_helmsman_gaps(sched, {}, date(2026, 7, 20))  # must not raise
    assert logged, "a failed nudge must be logged, not silently swallowed"


def test_nudge_silent_when_every_near_week_is_assigned(fb, monkeypatch):
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "12345")
    monkeypatch.setattr(fb, "get_bot", lambda: (_ for _ in ()).throw(
        AssertionError("must not send when there is no gap")))
    sched = _schedule(["2026-07-20"])
    fb._nudge_ceo_on_helmsman_gaps(sched, {"2026-07-20": {"name": "A"}}, date(2026, 7, 20))


def test_nudge_ignores_weeks_beyond_the_lookahead(fb, monkeypatch):
    """A fresh 10-week cycle must not dump every empty week into one message."""
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "12345")
    sent = []

    class _Bot:
        def send_message(self, uid, text, parse_mode=""):
            sent.append(text)

    monkeypatch.setattr(fb, "get_bot", lambda: _Bot())
    monkeypatch.setattr(fb, "_log_event", lambda *a, **k: None)
    sched = _schedule(["2026-07-20", "2026-07-27", "2026-08-31"])
    fb._nudge_ceo_on_helmsman_gaps(sched, {}, date(2026, 7, 20))

    assert len(sent) == 1
    assert "2026-07-20" in sent[0] and "2026-07-27" in sent[0]
    assert "2026-08-31" not in sent[0]
    assert "+1 later week" in sent[0]


def test_malformed_key_is_ignored_not_fatal(fb):
    helmsmen = {"not-a-date": {"name": "A", "briefed": False},
                "2026-07-20": {"name": "B", "briefed": False}}
    got = fb.helmsman_brief_candidates(helmsmen, date(2026, 7, 20))
    assert [k for _, k, _ in got] == ["2026-07-20"]
