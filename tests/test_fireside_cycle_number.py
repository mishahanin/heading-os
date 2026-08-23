"""A cycle rollover has to advance the cycle number.

Found by the 2026-08-23 audit. `build_schedule` wrote `"cycle": 1` on every one
of its 54 entries, and `cmd_cycle_rollover` rebuilt the schedule through it with
no cycle argument. So after a rollover the schedule described cycle 2's dates
while still calling itself cycle 1.

`fireside_topics.current_cycle()` reads that field, and three things read it:

* `/idea` stamps each submission with the current cycle;
* the cycle-end backlog filter is `load_ideas(cycle=cycle)`;
* `cycle_end_invite` uses the cycle number as its idempotency key.

With the number frozen at 1, cycle-2 ideas land in cycle 1's bucket forever, the
end-of-cycle backlog shows every idea ever submitted, and the invite's
"already sent for this cycle" check keeps matching the invite sent for cycle 1 —
so the cycle-2 invite is never sent at all.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import fireside_topics as ft  # noqa: E402


@pytest.fixture(scope="module")
def bot():
    spec = importlib.util.spec_from_file_location(
        "fireside_bot_cycle", ROOT / "scripts" / "fireside-bot.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["fireside_bot_cycle"] = module
    spec.loader.exec_module(module)
    return module


ROSTER = {"Alice Example": {"telegram_username": "alice"}}
WEEKS = [{"week": 1, "theme": "Opening", "mon": ["Alice Example"], "wed": []}]


def test_build_schedule_defaults_to_cycle_one(bot):
    """The first build is cycle 1, and that must not change."""
    entries, _ = bot.build_schedule(
        ROSTER, start_monday=date(2026, 1, 5), weeks=WEEKS)
    assert entries and {e["cycle"] for e in entries} == {1}


def test_build_schedule_stamps_the_cycle_it_is_given(bot):
    entries, _ = bot.build_schedule(
        ROSTER, start_monday=date(2026, 6, 1), weeks=WEEKS, cycle=2)
    assert {e["cycle"] for e in entries} == {2}


def test_current_cycle_reads_what_the_builder_wrote(bot):
    """The consumer side: the number has to travel."""
    entries, _ = bot.build_schedule(
        ROSTER, start_monday=date(2026, 6, 1), weeks=WEEKS, cycle=3)
    assert ft.current_cycle(entries, date(2026, 6, 1)) == 3


def test_next_cycle_is_one_past_the_highest_in_the_old_schedule(bot):
    """The rollover arithmetic, isolated from the command's file I/O."""
    old = [{"cycle": 1, "session_date": "2026-01-05"},
           {"cycle": 2, "session_date": "2026-06-01"}]
    assert bot.next_cycle_number(old) == 3
    assert bot.next_cycle_number([]) == 1
    assert bot.next_cycle_number([{"session_date": "2026-01-05"}]) == 2, (
        "an entry with no cycle field must be read as cycle 1, not as zero"
    )


def test_a_rollover_produces_a_schedule_in_the_next_cycle(bot, tmp_path, monkeypatch):
    """End to end through the command, on a real state directory."""
    import json

    state = tmp_path / "state"
    state.mkdir()

    def _state_path(name):
        return state / name

    old_monday = date(2026, 1, 5)
    new_monday = old_monday + timedelta(weeks=20)

    old_entries, _ = bot.build_schedule(ROSTER, start_monday=old_monday, weeks=WEEKS)
    (state / "schedule.json").write_text(json.dumps(old_entries), encoding="utf-8")
    (state / "tribe-roster.json").write_text(
        json.dumps({"alice": {"name": "Alice Example"}}), encoding="utf-8")

    monkeypatch.setattr(bot, "state_path", _state_path)
    monkeypatch.setattr(bot, "STATE_DIR", state)
    monkeypatch.setattr(bot, "_load_fireside_config_fresh",
                        lambda: (new_monday, WEEKS))
    monkeypatch.setattr(bot, "_today_local_date", lambda: new_monday)
    monkeypatch.setattr(bot, "_log_event", lambda *a, **k: None)

    # `cmd_cycle_rollover` ends by DMing the CEO, and it reads the target from
    # `os.environ["MISHA_TELEGRAM_USER_ID"]` — which any earlier test in the same
    # xdist worker can populate just by calling `load_env()`. So the transport is
    # replaced with a recorder rather than left to whichever environment this
    # test happens to inherit; nothing here may reach Telegram.
    sent = []

    class _Recorder:
        def send_message(self, chat_id, text, **kw):
            sent.append((chat_id, text))

    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "424242")
    monkeypatch.setattr(bot, "get_bot", lambda: _Recorder())

    class _Args:
        dry_run = False

    bot.cmd_cycle_rollover(_Args())

    assert len(sent) == 1 and sent[0][0] == 424242, (
        "the rollover heads-up DM was not attempted"
    )

    rebuilt = json.loads((state / "schedule.json").read_text(encoding="utf-8"))
    assert rebuilt, "the rollover wrote nothing"
    assert {e["cycle"] for e in rebuilt} == {2}, (
        "the rebuilt schedule still calls itself cycle 1, so every cycle-2 "
        "idea will be filed under cycle 1"
    )
    assert ft.current_cycle(rebuilt, new_monday) == 2
