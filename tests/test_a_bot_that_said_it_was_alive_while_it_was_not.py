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

Two more landed on 2026-09-01, both found by mutating the production code in a
copy of this tree rather than by reading it.

*A rule spelled twice, guarded once.* "A slot is filled when it has a SPEAKER,
bound or not" is written at two sites: `find_swap_candidates` (`if e.get(...`)
and `_vacancy_swap_locked`'s occupancy recheck (`and (e.get(...`). One test
here searched the source for the `if` spelling, so the second site was bound by
nothing in this repository - narrowing it to the handle alone left the whole
suite green while `_apply_vacancy_swap` double-booked a slot held by an unbound
name. Both sites are now driven, not grepped.

*Three readers that died on bytes they could not decode.*
`fireside-pulse.load_checkpoint`, `fireside-bot._read_jsonl_rows` and
`fireside_topics.load_topic_state` each read UTF-8 inside a handler catching
`OSError` and `json.JSONDecodeError`. `UnicodeDecodeError` is a `ValueError`
and neither, so a torn write raised out of all three - past a docstring
promising defaults for corrupt state, past a reader that skips a line it cannot
parse, and past the tool whose job is to report status. Same gap `watchdog_core`
had already closed.

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


@pytest.fixture(autouse=True)
def state(fb, tmp_path, monkeypatch):
    """Redirect the one constant every fireside writer resolves through.

    Autouse since 2026-08-29. Opt-in, it covered the tests whose author knew
    they wrote and left the rest resolving at the operator's live overlay: the
    same shape cost four tests in `test_a_promise_that_misha_would_help.py` and
    three in `test_eleven_guards_the_fireside_bot_applied_to_one_side.py`, where
    an error path nobody expected to reach disk called `log_error`.
    """
    monkeypatch.setattr(fb, "state_dir", lambda p=tmp_path: p)
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
    monkeypatch.setattr(fb, "tribe_xlsx", lambda p=sheet: p)
    with pytest.raises(ValueError) as exc:
        fb.load_tribe_metadata()
    assert "appears twice" in str(exc.value)
    assert "@akim" in str(exc.value)


def test_a_clean_sheet_still_loads(fb, tmp_path, monkeypatch):
    sheet = tmp_path / "31C_Tribe.xlsx"
    _write_tribe_sheet(sheet, [("Alex Kim", "akim"), ("Dana Reid", "dreid")])
    monkeypatch.setattr(fb, "tribe_xlsx", lambda p=sheet: p)
    roster = fb.load_tribe_metadata()
    assert set(roster) == {"akim", "dreid"}
    assert roster["akim"]["name"] == "Alex Kim"


def test_the_duplicate_check_is_case_insensitive(fb, tmp_path, monkeypatch):
    """Telegram handles are case-insensitive; @Akim and @akim are one account."""
    sheet = tmp_path / "31C_Tribe.xlsx"
    _write_tribe_sheet(sheet, [("Alex Kim", "Akim"), ("Dana Reid", "akim")])
    monkeypatch.setattr(fb, "tribe_xlsx", lambda p=sheet: p)
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
    monkeypatch.setattr(fb, "tribe_xlsx", lambda p=sheet: p)
    roster = fb.load_tribe_metadata()
    assert roster["akim"]["title"] == "Chief Engineer"
    assert roster["akim"]["is_vp"] is True


# ============================================================
# A named speaker is not a vacancy
# ============================================================

def _entry(date_str, slot, name=None, username=None):
    return {"session_date": date_str, "slot": slot, "week": 1, "day": "Mon",
            "theme": "T", "speaker_name": name, "speaker_username": username}


TODAY = date(2026, 9, 1)


def test_the_swap_picker_does_not_offer_a_slot_held_by_an_unbound_name(fb):
    """`build_schedule()` writes exactly this shape when a roster lookup
    misses, and keying on the handle alone offered it as an open slot.

    Driven through `find_swap_candidates` since 2026-09-01. It used to be two
    source greps, and the SECOND of them could never fire: it looked for
    `filled_slots = {e["slot"] for e in entries if e.get("speaker_username")}`
    on ONE line, while the code has always wrapped that comprehension across
    two, so a revert written the way the file is actually formatted walked past
    it. The first grep did bind this site, and nothing bound the other one -
    see the two tests below.
    """
    schedule = [
        _entry("2026-09-07", 1, "Named But Unbound", None),
        _entry("2026-09-07", 2, "Also Unbound", None),
        _entry("2026-09-07", 3, "Bound One", "bound"),
        _entry("2026-09-21", 1, "Alex Kim", "akim"),
    ]
    kinds = {c["kind"] for c in fb.find_swap_candidates(schedule, "akim", TODAY)}
    assert "vacancy" not in kinds, (
        "a session whose three slots all carry a speaker was offered as having "
        "an opening, because two of those speakers never bound to a handle"
    )


def test_the_swap_picker_still_offers_a_genuinely_empty_slot(fb):
    """The other direction. A picker that finds no vacancy is not a picker."""
    schedule = [
        _entry("2026-09-07", 1, "Bound One", "bound"),
        _entry("2026-09-21", 1, "Alex Kim", "akim"),
    ]
    vacancies = [c for c in fb.find_swap_candidates(schedule, "akim", TODAY)
                 if c["kind"] == "vacancy"]
    assert vacancies and vacancies[0]["date"] == "2026-09-07"


def test_a_vacancy_swap_refuses_a_target_held_by_an_unbound_name(fb, state):
    """The site the grep above could not reach, and the double-booking it cost.

    `_vacancy_swap_locked`'s occupancy recheck spells the same rule as
    `find_swap_candidates`, but with `and (e.get(...)` rather than
    `if e.get(...)`, so the literal the old test searched for never matched it.
    Measured 2026-09-01 in a copy of this tree, with that one site narrowed to
    `and (e.get("speaker_username"))`:

        .venv/bin/python -m pytest -q \\
            tests/test_a_bot_that_said_it_was_alive_while_it_was_not.py
        83 passed

        _apply_vacancy_swap(...) -> True
        rows at target: ('2026-09-14', 2, 'Named But Unbound', None)
                        ('2026-09-14', 2, 'Alex Kim', 'akim')

    Two speakers, one slot, nobody told - which is the exact defect this
    file's own module docstring says the recheck exists to prevent. No other
    file in `tests/` names the vacancy swap at all.
    """
    fb.save_state(fb.SCHEDULE, [
        _entry("2026-09-07", 1, "Alex Kim", "akim"),
        _entry("2026-09-14", 2, "Named But Unbound", None),
    ])

    assert fb._apply_vacancy_swap("akim", "2026-09-07", 1,
                                  "2026-09-14", 2, "Mon", "Theme", 2) is False

    after = fb.load_state(fb.SCHEDULE)
    at_target = [e for e in after
                 if e["session_date"] == "2026-09-14" and e["slot"] == 2]
    assert len(at_target) == 1, f"the slot was double-booked: {at_target}"
    assert at_target[0]["speaker_name"] == "Named But Unbound"
    assert any(e["speaker_username"] == "akim"
               and e["session_date"] == "2026-09-07" for e in after), (
        "a refused swap still lifted A out of the slot they held")


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


def _unlocked_read_modify_writes(state_const: str,
                                 source: str | None = None) -> list[str]:
    """Functions that save back a list they loaded from `state_const`, unlocked.

    `source` defaults to fireside-bot.py. It is a parameter so the detector can
    be pointed at a known offender and shown to report it: a guard that has
    never refused anything is not known to be a guard.

    Asks the AST, not the text. The check here was
    `code.count("with locked_state(SCHEDULE, []) as schedule:") == 2` plus
    `"save_state(SCHEDULE, new_schedule)" not in code`, which forbids ONE
    variable name: `cmd_log_session` loaded the schedule, marked entries
    completed and called `save_state(SCHEDULE, schedule)`, and both clauses
    passed it. A count is also a guard that a third correct caller breaks.

    A full REBUILD is not this shape and is not reported: `cmd_cycle_rollover`
    saves `entries` from `build_schedule`, an object it never loaded.
    """
    import ast

    tree = ast.parse(source if source is not None
                     else SRC.read_text(encoding="utf-8"))
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Names bound from `load_state(<state_const>)`, directly or through
        # ANY operand of an `or`. This unwrapped `values[0]` alone until
        # 2026-08-30, which reads the left operand and no other: the ordinary
        # spelling `s = load_state(SCHEDULE) or []` was caught and the equally
        # ordinary `s = [] or load_state(SCHEDULE)` was not, so one operand
        # order of exactly the load-modify-save shape this whole file exists
        # to eradicate was silently exempt.
        loaded = set()
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            if not isinstance(node.targets[0], ast.Name):
                continue
            value = node.value
            candidates = (list(value.values) if isinstance(value, ast.BoolOp)
                          else [value])
            if any(isinstance(c, ast.Call)
                   and isinstance(c.func, ast.Name)
                   and c.func.id == "load_state"
                   and c.args
                   and isinstance(c.args[0], ast.Name)
                   and c.args[0].id == state_const
                   for c in candidates):
                loaded.add(node.targets[0].id)
        if not loaded:
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "save_state"
                    and len(node.args) == 2
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == state_const
                    and isinstance(node.args[1], ast.Name)
                    and node.args[1].id in loaded):
                offenders.append(fn.name)
    return sorted(set(offenders))


@pytest.mark.parametrize("binding", [
    "s = load_state(SCHEDULE)",
    "s = load_state(SCHEDULE) or []",
    # The operand order the detector could not see until 2026-08-30.
    "s = [] or load_state(SCHEDULE)",
])
def test_the_lock_detector_refuses_a_known_offender(binding):
    """The negative case. Nothing had ever made this guard say no.

    `_unlocked_read_modify_writes` ships a `source` parameter whose docstring
    says it exists so the detector "can be pointed at a known offender and
    shown to report it" - and no test used it, so the guard's only evidence
    was that the real file is clean, which is equally true of a detector that
    reports nothing at all.
    """
    src = f"def f():\n    {binding}\n    save_state(SCHEDULE, s)\n"
    assert _unlocked_read_modify_writes("SCHEDULE", source=src) == ["f"]


def test_the_lock_detector_does_not_report_a_full_rebuild():
    """The other direction: a save of an object never loaded is not this shape.

    `cmd_cycle_rollover` in the real file does exactly this, so a detector
    that flagged it would be unusable - and one that flags everything passes
    the parametrised test above for the wrong reason.
    """
    src = ("def f():\n"
           "    entries = build_schedule()\n"
           "    save_state(SCHEDULE, entries)\n")
    assert _unlocked_read_modify_writes("SCHEDULE", source=src) == []


def test_no_schedule_read_modify_write_escapes_the_lock():
    assert _unlocked_read_modify_writes("SCHEDULE") == [], (
        "a load-modify-save of schedule.json outside `locked_state` loses "
        "whatever the webhook daemon wrote in between, and the daemon's own "
        "lock cannot protect a process that never asks for it"
    )


def test_both_swap_paths_still_hold_the_lock():
    """The count stays as a floor, not as an equality: a new correct caller
    must not break it, and the two swap paths must not quietly lose it."""
    code = _code_only()
    assert code.count("with locked_state(SCHEDULE, []) as schedule:") >= 3
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
    """`responded_user_ids` is filled from SESSIONS_LOG, and from nothing else.

    Scoped to the loop that fills the set, via the AST. It used to be a fixed
    600-character source window with `"DM_LOG" not in window`, which is a
    claim about layout rather than about behaviour: the same function
    legitimately reads the DM log for delivery status (three tests in this
    file depend on that), so moving that read to within 600 characters of the
    initialisation - an arrangement nothing forbids - failed this test against
    correct code, while moving the OFFENDING read 601 characters away passed
    it.
    """
    import ast

    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    loops = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and any(isinstance(t, ast.Name) and t.id == "responded_user_ids"
                for sub in ast.walk(node)
                for t in ([sub.func.value] if isinstance(sub, ast.Call)
                          and isinstance(sub.func, ast.Attribute)
                          and isinstance(sub.func.value, ast.Name) else []))
    ]
    assert len(loops) == 1, (
        f"expected exactly one loop filling responded_user_ids, found {len(loops)}")
    names = {n.id for n in ast.walk(loops[0].iter) if isinstance(n, ast.Name)}
    assert "SESSIONS_LOG" in names, (
        "the engagement set is no longer built from the log that records "
        f"engagement events; it iterates {sorted(names)}")
    assert "DM_LOG" not in names, (
        "the engagement set is built from the DM log, which records what the "
        "bot SENT, not what the member did")


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


def test_the_jsonl_reader_survives_a_file_it_cannot_decode(fb, state):
    """One unparseable LINE costs one row; one undecodable FILE cost the run.

    `_read_jsonl_rows` caught only `OSError` around
    `read_text(encoding="utf-8")`, and `UnicodeDecodeError` is a `ValueError`.
    So a torn append to `dm-log.jsonl` or `sessions.jsonl` raised out of this
    reader, out of `cmd_email_backup`, and no speaker got a backup email -
    from a reader that already `continue`s past a line it cannot parse.
    Measured 2026-09-01 before the fix: `UnicodeDecodeError: invalid start
    byte`.

    The first fix widened the whole-file read to `except (OSError,
    UnicodeError)`, which stopped the raise by returning the file EMPTY and
    logged "could not read". That trades a crash for losing every INTACT row
    over one bad byte, and this test's own first line says the contract is one
    bad line costing one row. The reader now decodes a line at a time through
    `scripts/utils/jsonl_lines`, so the wording moved with it. What is asserted
    here has not moved: the drop is named rather than silent.
    """
    p = state / "torn.jsonl"
    p.write_bytes(UNDECODABLE)
    assert p.stat().st_size > 0
    with pytest.raises(UnicodeDecodeError):
        p.read_text(encoding="utf-8")

    assert fb._read_jsonl_rows(p) == []
    assert "undecodable" in (state / fb.ERRORS_LOG).read_text(encoding="utf-8"), (
        "the file was dropped in silence; a reader that returns [] and says "
        "nothing is indistinguishable from an empty log")


def test_the_jsonl_reader_keeps_the_intact_rows_beside_the_torn_one(fb, state):
    """The half the wording change above exists to buy, asserted rather than
    described. A file with one good row and one undecodable line yields the good
    row; the earlier whole-file handler yielded nothing."""
    p = state / "half-torn.jsonl"
    p.write_bytes(b'{"dm_type": "poll-tick"}\n' + UNDECODABLE + b"\n")
    assert [r.get("dm_type") for r in fb._read_jsonl_rows(p)] == ["poll-tick"]


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
    assert code.count("with locked_state(HELMSMEN, {}) as fresh:") >= 2
    assert "save_state(HELMSMEN, helmsmen)" not in code
    # The same AST question the schedule guard now asks. The literal above
    # forbids one variable name; this forbids the SHAPE, whatever it is called.
    assert _unlocked_read_modify_writes("HELMSMEN") == []


# ============================================================
# The module imports even when its config does not
# ============================================================

def test_a_broken_cycle_config_does_not_stop_the_module_importing():
    """`--help` used to die before argparse ran, so the operator got a
    traceback instead of the usage text naming the file to fix."""
    code = _code_only()
    assert "def _fireside_config_error()" in code
    assert "def require_fireside_config()" in code
    # All four, named. A missing file is OSError, malformed JSON is ValueError,
    # a missing key is KeyError; narrowing the tuple re-opens the crash for
    # whichever one was dropped.
    assert "except (OSError, ValueError, KeyError, TypeError) as exc:" in code


def test_build_schedule_refuses_rather_than_building_a_zero_week_cycle(fb, monkeypatch):
    monkeypatch.setattr(fb, "_fireside_config_error", lambda p="config.json: boom": p)
    monkeypatch.setattr(fb, "cycle_1_start_monday", lambda p=None: p)
    monkeypatch.setattr(fb, "week_1_to_9_schedule", lambda p=[]: p)
    with pytest.raises(SystemExit) as exc:
        fb.build_schedule({})
    assert "could not be read" in str(exc.value)


def test_require_fireside_config_is_silent_when_the_config_loaded(fb, monkeypatch):
    monkeypatch.setattr(fb, "_fireside_config_error", lambda p=None: p)
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


# Bytes that are not valid UTF-8 under any interpretation: 0xFF never starts a
# UTF-8 sequence. Written as an escape, never as a literal, per
# `.claude/rules/hidden-chars.md`. This is what a torn write leaves behind, and
# a truncated write is the first corruption `load_checkpoint`'s own docstring
# names.
UNDECODABLE = b"\xff\xfe\x00{"


def test_a_corrupt_pulse_checkpoint_rebaselines_instead_of_crashing(tmp_path, monkeypatch, capsys):
    pulse = _load("fireside_pulse_probe", PULSE_SRC)
    bad = tmp_path / "pulse-checkpoint.json"
    bad.write_text("{", encoding="utf-8")
    monkeypatch.setattr(pulse, "checkpoint", lambda p=bad: p)
    assert pulse.load_checkpoint() is None
    assert "re-baselining" in capsys.readouterr().err


def test_a_byte_corrupt_pulse_checkpoint_also_rebaselines(tmp_path, monkeypatch,
                                                          capsys):
    """The half "corrupt" did not cover until 2026-09-01.

    `load_checkpoint` caught `(OSError, json.JSONDecodeError)` and read the
    file as UTF-8. Undecodable bytes raise `UnicodeDecodeError`, which is a
    `ValueError` and neither of those - `json.JSONDecodeError` only fires on
    text that DECODED and then failed to parse. So the case above (`{`) was
    handled and this one raised straight out of every run, from the tool whose
    entire job is to report status. Measured 2026-09-01 before the fix:

        UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0

    Same gap, same shape, same fix as `watchdog_core._read_beat`, which this
    tree had already closed - see
    `tests/test_a_beat_reader_that_died_on_bytes_it_could_not_decode.py`.
    """
    pulse = _load("fireside_pulse_probe5", PULSE_SRC)
    bad = tmp_path / "pulse-checkpoint.json"
    bad.write_bytes(UNDECODABLE)
    # Guard the guard: the corpus is non-empty and really is undecodable, so a
    # silently-absent file cannot make this pass for the wrong reason.
    assert bad.stat().st_size > 0
    with pytest.raises(UnicodeDecodeError):
        bad.read_text(encoding="utf-8")

    monkeypatch.setattr(pulse, "checkpoint", lambda p=bad: p)
    assert pulse.load_checkpoint() is None
    assert "unreadable" in capsys.readouterr().err


def test_a_checkpoint_of_the_wrong_type_also_rebaselines(tmp_path, monkeypatch, capsys):
    pulse = _load("fireside_pulse_probe2", PULSE_SRC)
    bad = tmp_path / "pulse-checkpoint.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setattr(pulse, "checkpoint", lambda p=bad: p)
    assert pulse.load_checkpoint() is None
    assert "not an object" in capsys.readouterr().err


def test_a_good_checkpoint_still_loads(tmp_path, monkeypatch):
    pulse = _load("fireside_pulse_probe3", PULSE_SRC)
    good = tmp_path / "pulse-checkpoint.json"
    good.write_text(json.dumps({"session_count": 4}), encoding="utf-8")
    monkeypatch.setattr(pulse, "checkpoint", lambda p=good: p)
    assert pulse.load_checkpoint() == {"session_count": 4}


def test_an_absent_checkpoint_is_none(tmp_path, monkeypatch):
    pulse = _load("fireside_pulse_probe4", PULSE_SRC)
    monkeypatch.setattr(pulse, "checkpoint", lambda p=tmp_path / "nothing.json": p)
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
    assert 'raw_port = _svc().get("webhook_port", 8443)' in code


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


def test_byte_corrupt_topic_state_returns_the_documented_defaults(tmp_path):
    """`load_topic_state` promises defaults when the state is "absent/corrupt".

    It caught `(json.JSONDecodeError, OSError)` around a UTF-8 `read_text`, so
    the promise held for `[]` and for `{` and not for bytes that are not UTF-8
    at all. Measured 2026-09-01 before the fix: `UnicodeDecodeError: invalid
    start byte` out of the reader. Third site of one shape in this shard, with
    `fireside-pulse.load_checkpoint` and `fireside-bot._read_jsonl_rows`; all
    three were widened together, because a fix that lands in one of several
    copies is the copy the next reader trusts.
    """
    topics = _load("fireside_topics_probe3", ROOT / "scripts" / "fireside_topics.py")
    (tmp_path / "topic-collection-state.json").write_bytes(UNDECODABLE)
    assert topics.load_topic_state(tmp_path) == {
        "last_digest_idea_id": None, "pending_cycle_invite": None}


def test_good_topic_state_is_returned_with_defaults_filled(tmp_path):
    topics = _load("fireside_topics_probe2", ROOT / "scripts" / "fireside_topics.py")
    (tmp_path / "topic-collection-state.json").write_text(
        json.dumps({"last_digest_idea_id": "abc"}), encoding="utf-8")
    got = topics.load_topic_state(tmp_path)
    assert got["last_digest_idea_id"] == "abc"
    assert got["pending_cycle_invite"] is None
