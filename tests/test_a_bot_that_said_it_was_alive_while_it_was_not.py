"""The fireside bot: state that lost writes, and liveness that lied.

Covers the k3 audit shard `scripts-01-p1` for `scripts/fireside-bot.py`,
`scripts-06-p1` in full (twenty-two findings, eleven HIGH), and `scripts-06-p2`
for the pulse, webhook, topics and GAL-export siblings.

Two classes did the damage.

*State that silently lost a write.* `schedule.json` and `helmsmen.json` were
load-modify-saved by cron jobs and Telegram callbacks with no cross-process
lock, so the later save simply erased the earlier change. `_apply_vacancy_swap`
also never rechecked that the slot it was moving someone INTO was still empty,
so a stale inline keyboard double-booked a session and told nobody. And a
duplicate Telegram username in the roster spreadsheet overwrote the earlier row
in silence, which made the membership count agree with itself while being short
a member.

*Measurements that measured something else.* `poll` wrote its liveness tick
BEFORE the first `getUpdates`, so a host with a revoked token stamped a fresh
"alive" every five minutes for the length of the outage. `email-backup` looked
for engagement events in `dm-log.jsonl` when `_log_event` writes them to
`sessions.jsonl`, so the set of members who had answered the bot was ALWAYS
empty. `stats` reported "Current week: 1 of 9" after the cycle had finished.

One finding was refuted on reading rather than taken on trust: the audit called
`swapped_with.with_username` self-referential after a bilateral swap. It is not.
Each row records the counterparty of ITS OWN new occupant, and the `old_date` /
`old_slot` beside it describe where that occupant came from. The two fields are
consistent; the audit read `with_username` as "the other row's occupant".

There is no Telegram in these tests. Nothing here constructs a bot, and the two
places that would send are exercised through pure helpers or source reads. This
file must never be the reason a message reaches a real person.
"""
from __future__ import annotations

import importlib.util
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "scripts" / "fireside-bot.py"


@pytest.fixture(scope="module")
def fb():
    spec = importlib.util.spec_from_file_location("fireside_bot_alive", str(SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def state(fb, tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "STATE_DIR", tmp_path)
    return tmp_path


def _code_only() -> str:
    """The source minus whole-line comments.

    Each fix here left a comment quoting the code it removed, so a plain grep
    for the old shape finds its own tombstone and passes for the wrong reason.
    """
    return "\n".join(
        ln for ln in SRC.read_text(encoding="utf-8").split("\n")
        if not ln.lstrip().startswith("#")
    )


# ============================================================
# A column index of 0 is a column, not a missing column
# ============================================================

def test_a_preferred_header_in_the_first_column_is_found(fb):
    headers = {"Title (reconciled)": 0, "Name": 1}
    assert fb._first_present(headers, "Title (reconciled)", "Title") == 0


def test_the_fallback_header_is_used_when_the_preferred_one_is_absent(fb):
    assert fb._first_present({"Title": 3}, "Title (reconciled)", "Title") == 3


def test_no_matching_header_is_none(fb):
    assert fb._first_present({"Name": 0}, "Title (reconciled)", "Title") is None


def test_the_roster_parser_no_longer_uses_or_on_a_column_index():
    code = _code_only()
    assert 'headers.get("Title (reconciled)") or headers.get("Title")' not in code
    assert "_first_present(headers," in code


# ============================================================
# A duplicate is refused; an ambiguous name is left unbound
# ============================================================

def test_two_roster_entries_with_one_display_name_bind_to_neither(fb, state):
    roster = {
        "akim": {"name": "Alex Kim", "active": True},
        "akim2": {"name": "Alex Kim", "active": True},
        "solo": {"name": "Dana Reid", "active": True},
    }
    by_name = fb.build_roster_by_name(roster)
    assert "Alex Kim" not in by_name, (
        "binding one of two identical names sends the DM to the wrong member"
    )
    assert by_name["Dana Reid"]["telegram_username"] == "solo"


def test_the_ambiguity_is_written_to_the_error_log(fb, state):
    fb.build_roster_by_name({
        "a": {"name": "Alex Kim", "active": True},
        "b": {"name": "Alex Kim", "active": True},
    })
    log = (state / fb.ERRORS_LOG).read_text(encoding="utf-8")
    assert "Alex Kim" in log and "UNBOUND" in log


def test_a_single_name_still_binds(fb, state):
    by_name = fb.build_roster_by_name({"u": {"name": "Only One", "active": True}})
    assert by_name["Only One"]["telegram_username"] == "u"


def test_a_nameless_roster_entry_is_skipped(fb, state):
    assert fb.build_roster_by_name({"u": {"name": "  ", "active": True}}) == {}


def _write_tribe_sheet(path: Path, rows: list[tuple[str, str]]) -> None:
    """A minimal 31C_Tribe.xlsx: header row plus (name, telegram username)."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Name", "Telegram Username", "Title (reconciled)", "Email"])
    for name, handle in rows:
        ws.append([name, handle, "Engineer", f"{handle}@example.com"])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def test_the_xlsx_reader_refuses_a_repeated_telegram_username(fb, tmp_path, monkeypatch):
    """A silent overwrite made the roster SHORTER than the sheet, and every
    downstream count agreed with the short version."""
    sheet = tmp_path / "31C_Tribe.xlsx"
    _write_tribe_sheet(sheet, [("Alex Kim", "akim"), ("Dana Reid", "akim")])
    monkeypatch.setattr(fb, "TRIBE_XLSX", sheet)
    with pytest.raises(ValueError) as exc:
        fb.load_tribe_metadata()
    assert "appears twice" in str(exc.value)
    assert "@akim" in str(exc.value)


def test_a_clean_sheet_still_loads(fb, tmp_path, monkeypatch):
    sheet = tmp_path / "31C_Tribe.xlsx"
    _write_tribe_sheet(sheet, [("Alex Kim", "akim"), ("Dana Reid", "dreid")])
    monkeypatch.setattr(fb, "TRIBE_XLSX", sheet)
    roster = fb.load_tribe_metadata()
    assert set(roster) == {"akim", "dreid"}
    assert roster["akim"]["name"] == "Alex Kim"


def test_the_duplicate_check_is_case_insensitive(fb, tmp_path, monkeypatch):
    """Telegram handles are case-insensitive; @Akim and @akim are one account."""
    sheet = tmp_path / "31C_Tribe.xlsx"
    _write_tribe_sheet(sheet, [("Alex Kim", "Akim"), ("Dana Reid", "akim")])
    monkeypatch.setattr(fb, "TRIBE_XLSX", sheet)
    with pytest.raises(ValueError):
        fb.load_tribe_metadata()


def test_the_first_column_title_survives_the_read(fb, tmp_path, monkeypatch):
    """The `or` bug: "Title (reconciled)" at index 0 lost every title."""
    openpyxl = pytest.importorskip("openpyxl")
    sheet = tmp_path / "31C_Tribe.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Title (reconciled)", "Name", "Telegram Username"])
    ws.append(["Chief Engineer", "Alex Kim", "akim"])
    wb.save(sheet)
    monkeypatch.setattr(fb, "TRIBE_XLSX", sheet)
    roster = fb.load_tribe_metadata()
    assert roster["akim"]["title"] == "Chief Engineer"
    assert roster["akim"]["is_vp"] is True


# ============================================================
# A named speaker is not a vacancy
# ============================================================

def _entry(date_str, slot, name=None, username=None):
    return {"session_date": date_str, "slot": slot, "week": 1, "day": "Mon",
            "theme": "T", "speaker_name": name, "speaker_username": username}


def test_a_slot_with_a_name_but_no_bound_handle_is_occupied():
    """`build_schedule()` writes exactly this shape when a roster lookup
    misses, and keying on the handle alone offered it as an open slot."""
    code = _code_only()
    assert 'if e.get("speaker_username") or e.get("speaker_name")' in code
    assert 'filled_slots = {e["slot"] for e in entries if e.get("speaker_username")}' not in code


def test_a_vacancy_swap_refuses_a_slot_taken_since_the_keyboard_was_built(fb, state):
    schedule = [
        _entry("2026-09-07", 1, "Alex Kim", "akim"),
        _entry("2026-09-14", 2, "Someone Else", "other"),   # target now taken
    ]
    fb.save_state(fb.SCHEDULE, schedule)
    assert fb._apply_vacancy_swap("akim", "2026-09-07", 1,
                                  "2026-09-14", 2, "Mon", "Theme", 2) is False
    after = fb.load_state(fb.SCHEDULE)
    assert len(after) == 2, "a refused swap must leave the schedule alone"
    assert any(e["speaker_username"] == "akim" for e in after)


def test_a_vacancy_swap_into_a_genuinely_empty_slot_succeeds(fb, state):
    fb.save_state(fb.SCHEDULE, [_entry("2026-09-07", 1, "Alex Kim", "akim")])
    assert fb._apply_vacancy_swap("akim", "2026-09-07", 1,
                                  "2026-09-14", 2, "Mon", "Theme", 2) is True
    after = fb.load_state(fb.SCHEDULE)
    assert len(after) == 1
    assert after[0]["session_date"] == "2026-09-14" and after[0]["slot"] == 2


def test_a_vacancy_swap_with_no_source_entry_is_refused(fb, state):
    fb.save_state(fb.SCHEDULE, [])
    assert fb._apply_vacancy_swap("ghost", "2026-09-07", 1,
                                  "2026-09-14", 2, "Mon", "T", 2) is False


def test_a_bilateral_swap_exchanges_both_speakers(fb, state):
    fb.save_state(fb.SCHEDULE, [
        _entry("2026-09-07", 1, "Alex Kim", "akim"),
        _entry("2026-09-14", 1, "Dana Reid", "dana"),
    ])
    assert fb._apply_bilateral_swap("akim", "2026-09-07", 1,
                                    "dana", "2026-09-14", 1) is True
    rows = {e["session_date"]: e for e in fb.load_state(fb.SCHEDULE)}
    assert rows["2026-09-07"]["speaker_username"] == "dana"
    assert rows["2026-09-14"]["speaker_username"] == "akim"


def test_the_bilateral_swap_metadata_names_each_occupants_counterparty(fb, state):
    """The audit called this self-referential. It is not, and the pairing with
    `old_date` is the proof: each row describes ITS OWN new occupant."""
    fb.save_state(fb.SCHEDULE, [
        _entry("2026-09-07", 1, "Alex Kim", "akim"),
        _entry("2026-09-14", 1, "Dana Reid", "dana"),
    ])
    fb._apply_bilateral_swap("akim", "2026-09-07", 1, "dana", "2026-09-14", 1)
    rows = {e["session_date"]: e for e in fb.load_state(fb.SCHEDULE)}
    # 2026-09-07 now holds dana, who came from 2026-09-14 and swapped with akim.
    assert rows["2026-09-07"]["swapped_with"]["with_username"] == "akim"
    assert rows["2026-09-07"]["swapped_with"]["old_date"] == "2026-09-14"
    # 2026-09-14 now holds akim, who came from 2026-09-07 and swapped with dana.
    assert rows["2026-09-14"]["swapped_with"]["with_username"] == "dana"
    assert rows["2026-09-14"]["swapped_with"]["old_date"] == "2026-09-07"


# ============================================================
# Read-modify-write under one lock
# ============================================================

def test_locked_state_writes_back_what_the_block_left(fb, state):
    with fb.locked_state("probe.json", {}) as value:
        value["a"] = 1
    assert fb.load_state("probe.json") == {"a": 1}


def test_locked_state_writes_nothing_when_the_block_raises(fb, state):
    fb.save_state("probe.json", {"a": 1})
    with pytest.raises(RuntimeError), fb.locked_state("probe.json", {}) as value:
        value["a"] = 2
        raise RuntimeError("boom")
    assert fb.load_state("probe.json") == {"a": 1}, (
        "a failed block must not leave a half-applied state file"
    )


def test_locked_state_supplies_the_default_for_a_missing_file(fb, state):
    with fb.locked_state("absent.json", []) as value:
        assert value == []
        value.append({"x": 1})
    assert fb.load_state("absent.json") == [{"x": 1}]


def test_locked_state_leaves_a_sidecar_lock_not_a_locked_inode(fb, state):
    """Locking the state file itself would lock an inode that `os.replace`
    is about to stop being the file."""
    with fb.locked_state("probe.json", {}):
        pass
    assert (state / "probe.json.lock").exists()


def test_both_swap_paths_go_through_the_lock():
    code = _code_only()
    assert code.count("with locked_state(SCHEDULE, []) as schedule:") == 2
    assert "save_state(SCHEDULE, new_schedule)" not in code, (
        "the split body mutates in place; the caller under the lock saves"
    )


# ============================================================
# Liveness means "reached Telegram", not "the process started"
# ============================================================

def test_the_poll_tick_is_written_after_getupdates_not_before():
    code = _code_only()
    tick = code.index('"dm_type": "poll-tick",')
    call = code.index("updates = bot.get_updates(")
    assert call < tick, (
        "a revoked token still stamped a fresh 'alive' every five minutes"
    )
    # The gate as well as the order. Deleting `if not ticked:` leaves the
    # append exactly where it is, so a position check alone passes while the
    # tick fires on every loop pass -- or never.
    assert "    ticked = False" in code
    assert "        if not ticked:" in code
    assert "            ticked = True" in code


def test_health_check_survives_an_unreadable_tick_timestamp():
    code = _code_only()
    assert "Liveness is UNKNOWN" in code, (
        "the one command whose job is to notice a problem used to crash on one"
    )
    # BOTH exception types. A non-ISO string raises ValueError and a naive
    # stamp compared against an aware `now` raises TypeError; catching one
    # leaves the other crashing the alerter.
    idx = code.index("Liveness is UNKNOWN")
    guard = code[max(0, idx - 800):idx]
    assert "except (TypeError, ValueError):" in guard


def test_engagement_is_read_from_the_log_that_records_it():
    code = _code_only()
    assert "for e in _read_jsonl_rows(state_path(SESSIONS_LOG)):" in code
    assert "responded_user_ids" in code
    # dm-log carries `dm_type`; `event_type` rows live in sessions.jsonl only.
    idx = code.index("responded_user_ids: set[int] = set()")
    window = code[idx:idx + 600]
    assert "SESSIONS_LOG" in window and "DM_LOG" not in window


def _run_backup(fb, tmp_path, monkeypatch, *, roster_entry, sessions_rows=(),
                dm_rows=(), days_until=3):
    """Run email-backup for one speaker N days out. Returns the sent addresses.

    Nothing here reaches Telegram or a mail transport: `subprocess.run` is
    replaced before the call, so the send is a recorded argv and nothing else.
    """
    session_date = (fb._today_local_date() + timedelta(days=days_until)).isoformat()
    schedule = [{
        "week": 1, "session_date": session_date, "day": "Mon", "slot": 1,
        "theme": "A first job", "speaker_name": "Bond, James Bond",
        "speaker_username": "jbond", "swapped_with": None,
        "no_show": False, "completed": False,
    }]
    for name, rows in ((fb.SESSIONS_LOG, sessions_rows), (fb.DM_LOG, dm_rows)):
        if rows:
            (tmp_path / name).write_text(
                "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    monkeypatch.setattr(fb, "state_path", lambda n: tmp_path / n)
    monkeypatch.setattr(fb, "load_state", lambda n: {
        fb.SCHEDULE: schedule, fb.TRIBE_ROSTER: {"jbond": roster_entry}}.get(n))
    monkeypatch.setattr(fb, "_log_dm", lambda *a, **k: None)

    spawned: list[list[str]] = []

    class _Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr("subprocess.run",
                        lambda cmd, **kw: (spawned.append(cmd), _Result())[1])
    fb.cmd_email_backup(fb.argparse.Namespace())
    return [c[c.index("--to") + 1] for c in spawned]


_BOND = {"name": "Bond, James Bond", "email": "jbond@acme.example",
         "telegram_user_id": 7, "active": True}


def test_a_delivered_dm_that_got_no_answer_still_earns_a_backup_email(fb, tmp_path,
                                                                     monkeypatch):
    """The rule is silence, not a failed DM. Operator ruling, 2026-08-24."""
    session_date = (fb._today_local_date() + timedelta(days=3)).isoformat()
    sent = _run_backup(fb, tmp_path, monkeypatch, roster_entry=_BOND, dm_rows=[
        {"dm_type": "2wk", "speaker_username": "jbond",
         "session_date": session_date, "delivered": True},
    ])
    assert sent == ["jbond@acme.example"]


def test_a_member_the_bot_never_dmd_at_all_is_emailed(fb, tmp_path, monkeypatch):
    """No Telegram account means no DM row, and used to mean no mail either."""
    entry = dict(_BOND, telegram_user_id=None)
    assert _run_backup(fb, tmp_path, monkeypatch,
                       roster_entry=entry) == ["jbond@acme.example"]


def test_a_failed_dm_still_earns_a_backup_email(fb, tmp_path, monkeypatch):
    session_date = (fb._today_local_date() + timedelta(days=3)).isoformat()
    sent = _run_backup(fb, tmp_path, monkeypatch, roster_entry=_BOND, dm_rows=[
        {"dm_type": "2wk", "speaker_username": "jbond",
         "session_date": session_date, "delivered": False},
    ])
    assert sent == ["jbond@acme.example"]


def test_a_member_who_answered_the_bot_is_not_emailed(fb, tmp_path, monkeypatch):
    """Engagement is still the one thing that stops the mail."""
    sent = _run_backup(fb, tmp_path, monkeypatch, roster_entry=_BOND, sessions_rows=[
        {"event_type": "start_received", "user_id": 7},
    ])
    assert sent == []


def test_a_member_with_no_email_address_is_not_mailed(fb, tmp_path, monkeypatch):
    entry = dict(_BOND, email="")
    assert _run_backup(fb, tmp_path, monkeypatch, roster_entry=entry) == []


@pytest.mark.parametrize("days_until", [-1, 0, 15, 40])
def test_a_session_outside_the_two_week_window_is_not_mailed(fb, tmp_path,
                                                             monkeypatch, days_until):
    """Widening WHO gets mail must not widen WHEN. Today and the past are out too."""
    assert _run_backup(fb, tmp_path, monkeypatch, roster_entry=_BOND,
                       days_until=days_until) == []


@pytest.mark.parametrize("days_until", [1, 14])
def test_both_edges_of_the_window_are_inside_it(fb, tmp_path, monkeypatch, days_until):
    assert _run_backup(fb, tmp_path, monkeypatch, roster_entry=_BOND,
                       days_until=days_until) == ["jbond@acme.example"]


def test_the_backup_no_longer_requires_an_undelivered_dm():
    code = _code_only()
    assert 'and not e.get("delivered")' not in code, (
        "the failed-DM precondition is the thing the operator removed"
    )


def test_the_dm_log_is_read_once_with_a_guard(fb, state):
    assert fb._read_jsonl_rows(state / "nothing.jsonl") == []


def test_the_jsonl_reader_skips_a_bad_line_and_keeps_the_rest(fb, state):
    p = state / "mixed.jsonl"
    p.write_text('{"a": 1}\nnot json\n\n{"b": 2}\n"a string"\n', encoding="utf-8")
    assert fb._read_jsonl_rows(p) == [{"a": 1}, {"b": 2}]


# ============================================================
# An operator id that is not a number is "unconfigured", not a crash
# ============================================================

def test_a_malformed_operator_id_reads_as_unconfigured(fb, state, monkeypatch):
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "abc")
    assert fb.misha_user_id() == 0
    assert "not an integer" in (state / fb.ERRORS_LOG).read_text(encoding="utf-8")


def test_a_real_operator_id_parses(fb, monkeypatch):
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "12345")
    assert fb.misha_user_id() == 12345


def test_an_absent_operator_id_is_zero(fb, monkeypatch):
    monkeypatch.delenv("MISHA_TELEGRAM_USER_ID", raising=False)
    assert fb.misha_user_id() == 0


def test_no_call_site_parses_that_variable_by_hand():
    code = _code_only()
    assert 'int(os.environ.get("MISHA_TELEGRAM_USER_ID"' not in code, (
        "seventeen raw parses; one typo raised out of whatever was running"
    )


# ============================================================
# Reporting the truth about a run
# ============================================================

def test_a_finished_cycle_does_not_report_week_one():
    code = _code_only()
    assert "_current_or_upcoming_week(schedule, today) or 1" not in code
    assert "no active week" in code


def test_log_session_refuses_a_date_that_matches_nothing():
    code = _code_only()
    assert "if updated == 0:" in code
    assert "nothing logged" in code


def test_the_discrepancy_report_does_not_use_legacy_markdown():
    code = _code_only()
    assert 'parse_mode="Markdown"' not in code, (
        "one underscore in a handle made Telegram reject the whole message"
    )


def test_a_failed_outsider_forward_does_not_start_a_cooldown():
    code = _code_only()
    assert "outsider forward to Misha failed" in code
    idx = code.index("outsider forward to Misha failed")
    assert "return" in code[idx:idx + 200]


def test_the_post_and_the_pin_are_separate_outcomes():
    code = _code_only()
    assert "Do NOT rerun" in code
    assert "sunday-preview failed to post" in code


def test_an_incomplete_week_skips_the_brief_instead_of_looping_forever():
    code = _code_only()
    assert "if not mon or not wed:" in code
    assert "skipped, not marked briefed" in code
    # The guard must sit BEFORE the two indexes it protects.
    assert code.index("if not mon or not wed:") < code.index('monday_date=mon[0]["session_date"]')


def test_the_helmsman_backlog_is_printed_not_implied():
    code = _code_only()
    assert "Still waiting:" in code
    assert "if len(candidates) > 1:" in code
    assert "{len(candidates)} Helmsmen pending" in code, (
        "the COUNT is the point; 'pending' without it says nothing"
    )


def test_every_helmsmen_mutation_goes_through_the_lock():
    """`helmsman set` and `helmsman-brief` write the same file, and whichever
    saved second erased the other. Both re-read under the lock now."""
    code = _code_only()
    assert code.count("with locked_state(HELMSMEN, {}) as fresh:") == 2
    assert "save_state(HELMSMEN, helmsmen)" not in code


# ============================================================
# The module imports even when its config does not
# ============================================================

def test_a_broken_cycle_config_does_not_stop_the_module_importing():
    """`--help` used to die before argparse ran, so the operator got a
    traceback instead of the usage text naming the file to fix."""
    code = _code_only()
    assert "_FIRESIDE_CONFIG_ERROR" in code
    assert "def require_fireside_config()" in code
    # All four, named. A missing file is OSError, malformed JSON is ValueError,
    # a missing key is KeyError; narrowing the tuple re-opens the crash for
    # whichever one was dropped.
    assert "except (OSError, ValueError, KeyError, TypeError) as _exc:" in code


def test_build_schedule_refuses_rather_than_building_a_zero_week_cycle(fb, monkeypatch):
    monkeypatch.setattr(fb, "_FIRESIDE_CONFIG_ERROR", "config.json: boom")
    monkeypatch.setattr(fb, "CYCLE_1_START_MONDAY", None)
    monkeypatch.setattr(fb, "WEEK_1_TO_9_SCHEDULE", [])
    with pytest.raises(SystemExit) as exc:
        fb.build_schedule({})
    assert "could not be read" in str(exc.value)


def test_require_fireside_config_is_silent_when_the_config_loaded(fb, monkeypatch):
    monkeypatch.setattr(fb, "_FIRESIDE_CONFIG_ERROR", None)
    fb.require_fireside_config()          # must not raise


def test_the_ipv4_pin_no_longer_calls_itself_a_no_op():
    """It rebinds PROCESS-GLOBAL urllib3 state. The measurement justifies
    keeping it; the claim that it costs nothing was what was wrong."""
    text = SRC.read_text(encoding="utf-8")
    assert "No-op on hosts where IPv6 works." not in text
    assert "PROCESS-GLOBAL urllib3 state" in text


# ============================================================
# 06-p2: the pulse, the webhook, the GAL export
# ============================================================

PULSE_SRC = ROOT / "scripts" / "fireside-pulse.py"
WEBHOOK_SRC = ROOT / "scripts" / "fireside_webhook.py"
GAL_SRC = ROOT / "scripts" / "gal-export.py"


def _load(mod_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _no_comments(path: Path) -> str:
    return "\n".join(
        ln for ln in path.read_text(encoding="utf-8").split("\n")
        if not ln.lstrip().startswith("#")
    )


@pytest.mark.parametrize("email,expected", [
    ("alice@acme.example", True),
    ("a@ACME.EXAMPLE", True),                     # domains are case-insensitive
    ("alice@notacme.example", False),             # substring, different tenant
    ("bob@acme.example.evil.test", False),        # substring, attacker-controlled
    ("notacme.example", False),                   # no @ at all
    ("@acme.example", False),                     # no local part
    ("", False),
])
def test_the_gal_filter_matches_a_domain_not_a_substring(email, expected):
    """An invented domain, not the operator's: `test_no_tenant_domain_is_
    compiled_into_the_engine` reads every engine file, this one included, and
    it is right to."""
    gal = _load("gal_export_probe", GAL_SRC)
    assert gal._in_domain(email, "acme.example") is expected


def test_the_gal_sweep_prefixes_follow_the_domain_argument():
    """Two prefixes were the tenant's own name, written in as literals, so
    `--domain example.com` still ran 31C-specific probes."""
    code = _no_comments(GAL_SRC)
    assert 'label = domain.split(".", 1)[0]' in code
    assert 'extra = [label,' in code
    assert '"31c"' not in code


def test_local_liveness_accepts_the_tick_webhook_mode_actually_writes():
    """In webhook mode the poll job is skipped BY DESIGN. Accepting only
    poll-tick fired a stale-poll warning against a healthy daemon."""
    code = _no_comments(PULSE_SRC)
    assert 'if e.get("dm_type") in ("poll-tick", "heartbeat-tick"):' in code
    assert 'if e.get("dm_type") == "poll-tick":' not in code


def test_a_corrupt_pulse_checkpoint_rebaselines_instead_of_crashing(tmp_path, monkeypatch, capsys):
    pulse = _load("fireside_pulse_probe", PULSE_SRC)
    bad = tmp_path / "pulse-checkpoint.json"
    bad.write_text("{", encoding="utf-8")
    monkeypatch.setattr(pulse, "CHECKPOINT", bad)
    assert pulse.load_checkpoint() is None
    assert "re-baselining" in capsys.readouterr().err


def test_a_checkpoint_of_the_wrong_type_also_rebaselines(tmp_path, monkeypatch, capsys):
    pulse = _load("fireside_pulse_probe2", PULSE_SRC)
    bad = tmp_path / "pulse-checkpoint.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setattr(pulse, "CHECKPOINT", bad)
    assert pulse.load_checkpoint() is None
    assert "not an object" in capsys.readouterr().err


def test_a_good_checkpoint_still_loads(tmp_path, monkeypatch):
    pulse = _load("fireside_pulse_probe3", PULSE_SRC)
    good = tmp_path / "pulse-checkpoint.json"
    good.write_text(json.dumps({"session_count": 4}), encoding="utf-8")
    monkeypatch.setattr(pulse, "CHECKPOINT", good)
    assert pulse.load_checkpoint() == {"session_count": 4}


def test_an_absent_checkpoint_is_none(tmp_path, monkeypatch):
    pulse = _load("fireside_pulse_probe4", PULSE_SRC)
    monkeypatch.setattr(pulse, "CHECKPOINT", tmp_path / "nothing.json")
    assert pulse.load_checkpoint() is None


def test_a_daemon_owned_by_another_user_reads_as_alive():
    """`PermissionError` from `kill(pid, 0)` means the process EXISTS and is
    not ours. Reading it as dead made pulse start a SECOND bot on one token."""
    code = _no_comments(PULSE_SRC)
    assert "except ProcessLookupError:" in code
    assert "except PermissionError:" in code
    assert "except (ProcessLookupError, PermissionError):" not in code


def test_an_unknown_tribe_size_prints_a_question_mark_not_a_number():
    code = _no_comments(PULSE_SRC)
    assert "or 55" not in code, "a denominator nothing on this machine measured"
    assert 'tribe_label = str(tribe_size) if tribe_size else "?"' in code


def test_a_bad_webhook_port_does_not_replace_unknown_with_a_traceback():
    code = _no_comments(PULSE_SRC)
    assert "except (TypeError, ValueError):" in code
    assert 'raw_port = _SVC.get("webhook_port", 8443)' in code


def test_the_windows_spawn_waits_for_a_real_pid_file():
    """`start` is a cmd builtin that exits 0 whether or not the target
    launched, so the sentinel claimed success for a missing daemon script."""
    code = _no_comments(PULSE_SRC)
    assert "return -1  # success sentinel" not in code
    assert "alive, real_pid = _daemon_alive()" in code


def test_the_webhook_secret_is_compared_in_constant_time():
    code = _no_comments(WEBHOOK_SRC)
    assert "secrets.compare_digest(" in code
    assert "if x_telegram_bot_api_secret_token != secret_token:" not in code


def test_a_json_body_that_is_not_an_object_is_a_400():
    code = _no_comments(WEBHOOK_SRC)
    assert "if not isinstance(update, dict):" in code
    assert "update must be a JSON object" in code
    # Scoped to the POST handler: `update.get("update_id")` also appears in
    # `_process_in_background`, which runs AFTER this guard, so an unscoped
    # index comparison finds the wrong occurrence.
    handler = code[code.index('update = await request.json()'):]
    assert handler.index("if not isinstance(update, dict):") < handler.index('update_id = update.get("update_id")')


def test_the_webhook_serialises_handlers_and_never_rewinds_the_offset():
    code = _no_comments(WEBHOOK_SRC)
    assert "handler_lock = asyncio.Lock()" in code
    assert "async with handler_lock:" in code
    assert "max(current, update_id + 1)" in code


def test_topic_state_of_the_wrong_type_returns_the_documented_defaults(tmp_path):
    topics = _load("fireside_topics_probe", ROOT / "scripts" / "fireside_topics.py")
    (tmp_path / "topic-collection-state.json").write_text("[]", encoding="utf-8")
    got = topics.load_topic_state(tmp_path)
    assert got == {"last_digest_idea_id": None, "pending_cycle_invite": None}


def test_good_topic_state_is_returned_with_defaults_filled(tmp_path):
    topics = _load("fireside_topics_probe2", ROOT / "scripts" / "fireside_topics.py")
    (tmp_path / "topic-collection-state.json").write_text(
        json.dumps({"last_digest_idea_id": "abc"}), encoding="utf-8")
    got = topics.load_topic_state(tmp_path)
    assert got["last_digest_idea_id"] == "abc"
    assert got["pending_cycle_invite"] is None
