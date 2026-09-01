"""Tests for speaker-slot coverage in scripts/fireside-bot.py.

The roster and the cycle lineup are two different files: membership is rebuilt
from Telegram + xlsx by `bootstrap`, while the weeks a cycle actually runs are
hand-authored in config/fireside-schedule.json. Nothing joined the two in the
forgotten direction. Departed speakers were caught (they surface as "names in
the schedule with no roster match"), but a member who joined mid-cycle simply
never appeared anywhere: no slot, no warning, and every job green. One member
joined on 2026-08-11 and would have sat out cycle 3 in silence if the CEO had
not asked for them by name.

These tests pin the inverse check: an active, non-excluded member who holds no
slot in the schedule is reported.

Every person here is a placeholder. Until 2026-08-25 this file carried three
real Tribe members' full names and two of their Telegram handles, in a repo that
is public, and the content gate reported the tree clean the whole time: it
harvested people from `crm/contacts/` and `admin/executives.json` only, and the
block meant to cover the Tribe read `config/fireside-schedule.json` looking for
member dicts that file has never contained. See
`scripts/utils/content_denylist.py`.
"""
from __future__ import annotations

import importlib.util
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


def _roster(*entries):
    """entries: (username, name, **overrides)."""
    out = {}
    for username, name, *rest in entries:
        rec = {"name": name, "active": True}
        if rest:
            rec.update(rest[0])
        out[username] = rec
    return out


def _schedule(*names):
    """Rows `build_schedule` could NOT bind: a speaker name, no handle.

    Every row here is deliberately unbound, which is the fallback branch of
    `speaker_gaps`. The bound branch has its own cases below, because until
    2026-09-01 nothing in this file produced a row carrying a
    `speaker_username` at all and the whole username-matching half of the
    function was unmeasured.
    """
    return [{"session_date": "2026-09-21", "day": "Mon", "slot": i, "speaker_name": n}
            for i, n in enumerate(names, start=1)]


def _bound_schedule(*pairs):
    """Rows as `build_schedule` writes them when the roster matched: (name, handle)."""
    return [{"session_date": "2026-09-21", "day": "Mon", "slot": i,
             "speaker_name": n, "speaker_username": u}
            for i, (n, u) in enumerate(pairs, start=1)]


def test_member_with_no_slot_is_reported(fb):
    roster = _roster(("vlynd", "Vesper Lynd"), ("fleiter", "Felix Leiter"))
    gaps = fb.speaker_gaps(roster, _schedule("Felix Leiter"))
    assert gaps == ["Vesper Lynd (@vlynd)"]


def test_no_gaps_when_everyone_holds_a_slot(fb):
    roster = _roster(("vlynd", "Vesper Lynd"), ("fleiter", "Felix Leiter"))
    assert fb.speaker_gaps(roster, _schedule("Felix Leiter", "Vesper Lynd")) == []


def test_excluded_member_is_not_a_gap(fb):
    """Rene Mathis sits out the rotation by the CEO's choice, not by drift."""
    roster = _roster(("rmathis", "Rene Mathis",
                      {"active": False, "excluded_from_fireside": True}))
    assert fb.speaker_gaps(roster, _schedule("Someone Else")) == []


def test_inactive_member_is_not_a_gap(fb):
    roster = _roster(("gone", "Departed Person", {"active": False}))
    assert fb.speaker_gaps(roster, _schedule("Someone Else")) == []


def test_a_member_who_already_spoke_this_cycle_is_not_a_gap(fb):
    """Past entries stay in schedule.json, so week 1's speaker is still covered."""
    roster = _roster(("early", "Early Speaker"))
    past = [{"session_date": "2026-07-13", "day": "Mon", "slot": 1,
             "speaker_name": "Early Speaker", "completed": True}]
    assert fb.speaker_gaps(roster, past) == []


def test_names_are_compared_with_surrounding_whitespace_stripped(fb):
    """A trailing space in the xlsx Name cell must not read as a missing speaker."""
    roster = _roster(("spaced", "Padded Name "))
    assert fb.speaker_gaps(roster, _schedule(" Padded Name")) == []


def test_result_is_sorted_and_carries_the_handle(fb):
    roster = _roster(("zzz", "Zoe Last"), ("aaa", "Adam First"))
    assert fb.speaker_gaps(roster, []) == ["Adam First (@aaa)", "Zoe Last (@zzz)"]


def test_empty_roster_yields_no_gaps(fb):
    assert fb.speaker_gaps({}, _schedule("Someone")) == []
    assert fb.speaker_gaps(None, _schedule("Someone")) == []


def test_member_without_a_name_is_skipped(fb):
    """A roster record with no Name cannot be matched against a lineup at all."""
    roster = {"nameless": {"active": True, "name": ""}}
    assert fb.speaker_gaps(roster, []) == []


# ------------------------------------------- the slot is credited by HANDLE

def test_a_bound_slot_is_credited_to_the_handle_that_holds_it(fb):
    """The branch the docstring calls the whole point, and nothing reached it.

    Every other case in this file builds rows with no `speaker_username`, so
    they all land in the unbound fallback and the username lookup above it was
    never executed. MEASURED 2026-09-01: deleting the handle branch outright
    left this file, and every fireside neighbour, green.
    """
    roster = _roster(("vlynd", "Vesper Lynd"), ("fleiter", "Felix Leiter"))
    gaps = fb.speaker_gaps(roster, _bound_schedule(("Felix Leiter", "fleiter")))
    assert gaps == ["Vesper Lynd (@vlynd)"]


def test_two_members_sharing_a_name_are_told_apart_by_handle(fb):
    """The regression the handle branch exists for, stated as its own case.

    `build_roster_by_name` refuses to guess between two active members with the
    same display name and logs the ambiguity, so only one of them ever gets a
    bound row. Matching on the display name alone counted BOTH as covered, and
    the one with no slot sat out the cycle with nothing anywhere saying so.
    """
    roster = _roster(("jbond", "James Bond"), ("jbond2", "James Bond"))
    gaps = fb.speaker_gaps(roster, _bound_schedule(("James Bond", "jbond")))
    assert gaps == ["James Bond (@jbond2)"], (
        "the member holding no slot is the one without the bound handle")


@pytest.mark.parametrize("roster_key,schedule_handle", [
    ("fleiter", "FLeiter"),   # the schedule row carries the odd case
    ("FLeiter", "fleiter"),   # the roster key carries it
    ("FLeiter", "fLEITER"),   # both do, differently
])
def test_a_handle_is_matched_case_insensitively(fb, roster_key, schedule_handle):
    """Telegram hands back whatever case the member typed; the roster key is
    whatever `bootstrap` wrote. A case difference must not read as a missing
    speaker.

    Both sides get a case, because a first version of this case used a
    lowercase roster key and only varied the schedule. Dropping `.lower()` from
    the roster side then changed nothing and the test stayed green: the
    comparison has two halves and a case has to reach each of them.
    """
    roster = _roster((roster_key, "Felix Leiter"))
    assert fb.speaker_gaps(
        roster, _bound_schedule(("Felix Leiter", schedule_handle))) == []


def test_a_bound_row_does_not_credit_a_different_member_of_the_same_name(fb):
    """The negative direction of the three above: a bound row naming someone
    else's handle credits nobody by display name."""
    roster = _roster(("vlynd", "Vesper Lynd"))
    assert fb.speaker_gaps(
        roster, _bound_schedule(("Vesper Lynd", "an-imposter"))) == [
            "Vesper Lynd (@vlynd)"]


# ------------------------------------------------------- the CLI check

def _fake_state(fb, monkeypatch, roster, schedule):
    monkeypatch.setattr(fb, "load_state",
                        lambda name: schedule if name == fb.SCHEDULE else roster)


def test_cli_exits_1_when_someone_holds_no_slot(fb, monkeypatch, capsys):
    _fake_state(fb, monkeypatch,
                _roster(("vlynd", "Vesper Lynd")), _schedule("Felix Leiter"))
    assert fb.cmd_speaker_gaps(object()) == 1
    assert "Vesper Lynd (@vlynd)" in capsys.readouterr().out


def test_cli_exits_0_when_everyone_holds_a_slot(fb, monkeypatch, capsys):
    _fake_state(fb, monkeypatch,
                _roster(("fleiter", "Felix Leiter")), _schedule("Felix Leiter"))
    assert fb.cmd_speaker_gaps(object()) == 0
    assert "none" in capsys.readouterr().out


def test_cli_exits_1_on_an_empty_schedule_rather_than_reporting_all_clear(fb, monkeypatch, capsys):
    """No schedule means the check could not run - that must not read as a pass."""
    _fake_state(fb, monkeypatch, _roster(("fleiter", "Felix Leiter")), [])
    assert fb.cmd_speaker_gaps(object()) == 1
    assert "empty" in capsys.readouterr().out
