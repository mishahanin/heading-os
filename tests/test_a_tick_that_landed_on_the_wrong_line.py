"""Counting the right things, in the right window, and saying so honestly.

Covers the k3 audit shard `scripts-11-p3`.

Every defect pinned here is the same shape: a tool counted or scanned something
WIDER than the thing it named. `thread.py --done N` counted checkboxes across a
whole file while the help said "follow-up". `render_srt` numbered cues before it
dropped the empty ones. `--delete` capped at fifty and reported the fifty as the
whole match set. `_find_thread_by_id` scanned two directories and returned the
first hit. `turn-check`'s fingerprint hashed the files that still exist, so the
turn that DELETED one looked unchanged. `connect()` printed "Connected" for a
constructor that had not opened a socket.

None of them raised. Each one produced a confident number or a confident
sentence, which is the expensive way to be wrong.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(mod_name: str, rel: str):
    spec = importlib.util.spec_from_file_location(mod_name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _code_only(path: Path, comment: str = "#") -> str:
    """The file with whole-line comments removed.

    Every fix in this session leaves a comment quoting the code it deleted, so a
    plain `grep` for the old shape finds its own tombstone and the assertion
    passes for the wrong reason. Strip the commentary first.
    """
    lines = [
        ln for ln in path.read_text(encoding="utf-8").split("\n")
        if not ln.lstrip().startswith(comment)
    ]
    return "\n".join(lines)


th = _load("thread_mod_wrongline", "scripts/thread.py")
sx = _load("sync_exchange_mod_wrongline", "scripts/sync-exchange.py")
tm = _load("transcribe_mod_wrongline", "scripts/transcribe-media.py")
um = _load("update_manager_mod_wrongline", "scripts/update-manager.py")
tc = _load("turn_check_mod_wrongline", "scripts/turn-check.py")


# ============================================================
# thread.py -- --done ticks a follow-up, not "the Nth box anywhere"
# ============================================================

BODY = """# A thread

## Notes
- [ ] a stray box someone wrote in Notes
- [ ] a second stray box

## Open follow-ups
- [ ] the real first follow-up
- [ ] the real second follow-up

## Decisions
- [ ] a box under Decisions
"""


def test_done_zero_ticks_the_first_follow_up_not_the_first_box_in_the_file():
    out = th._tick_followup(BODY, 0)
    assert "- [x] the real first follow-up" in out
    assert "- [ ] a stray box someone wrote in Notes" in out


def test_done_one_ticks_the_second_follow_up():
    out = th._tick_followup(BODY, 1)
    assert "- [x] the real second follow-up" in out
    assert "- [ ] the real first follow-up" in out


def test_boxes_after_the_section_are_out_of_range():
    """Two boxes in the section, so index 2 is past the end -- Decisions is not next in line."""
    with pytest.raises(IndexError) as exc:
        th._tick_followup(BODY, 2)
    assert "2 open item" in str(exc.value)


def test_a_thread_with_no_follow_ups_section_says_so():
    with pytest.raises(IndexError) as exc:
        th._tick_followup("# A thread\n\n## Notes\n- [ ] box\n", 0)
    assert "Open follow-ups" in str(exc.value)


def test_section_bounds_stops_at_the_next_level_two_header():
    start, end = th._section_bounds(BODY, "## Open follow-ups")
    inside = BODY.split("\n")[start:end]
    assert any("real first follow-up" in ln for ln in inside)
    assert not any("Decisions" in ln for ln in inside)


def test_section_bounds_runs_to_end_of_file_for_a_trailing_section():
    body = "# T\n\n## Open follow-ups\n- [ ] one\n- [ ] two\n"
    start, end = th._section_bounds(body, "## Open follow-ups")
    assert end == len(body.split("\n"))


def test_section_bounds_returns_none_when_absent():
    assert th._section_bounds("# T\n\n## Notes\n", "## Open follow-ups") is None


# ============================================================
# thread.py -- an ID in both trees is ambiguous, not "business wins"
# ============================================================

def _mk_thread(root: Path, type_: str, tid: str) -> Path:
    d = root / type_
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{tid}.md"
    p.write_text("# t\n", encoding="utf-8")
    return p


def test_an_id_present_in_both_trees_refuses_instead_of_picking_business(tmp_path):
    _mk_thread(tmp_path, "business", "dup")
    _mk_thread(tmp_path, "personal", "dup")
    with pytest.raises(ValueError) as exc:
        th._find_thread_by_id(tmp_path, "dup")
    assert "business" in str(exc.value) and "personal" in str(exc.value)


def test_a_personal_only_id_still_resolves(tmp_path):
    want = _mk_thread(tmp_path, "personal", "solo")
    assert th._find_thread_by_id(tmp_path, "solo") == want


def test_a_business_only_id_still_resolves(tmp_path):
    want = _mk_thread(tmp_path, "business", "solo")
    assert th._find_thread_by_id(tmp_path, "solo") == want


def test_a_missing_id_is_a_file_not_found(tmp_path):
    (tmp_path / "business").mkdir()
    with pytest.raises(FileNotFoundError):
        th._find_thread_by_id(tmp_path, "nope")


# ============================================================
# thread.py -- the quiet flags express ONE choice
# ============================================================

def test_quiet_refuses_until_together_with_clear(capsys):
    with pytest.raises(SystemExit):
        th.main(["quiet", "some-thread", "--until", "2026-09-01", "--clear"])
    assert "not allowed with" in capsys.readouterr().err


def test_quiet_refuses_indefinite_together_with_clear(capsys):
    with pytest.raises(SystemExit):
        th.main(["quiet", "some-thread", "--indefinite", "--clear"])
    assert "not allowed with" in capsys.readouterr().err


def test_quiet_refuses_no_mode_at_all(capsys):
    with pytest.raises(SystemExit):
        th.main(["quiet", "some-thread"])
    assert "one of the arguments" in capsys.readouterr().err


def test_every_thread_subcommand_has_a_handler():
    """Replaces the unreachable runtime `hasattr(args, "func")` guard.

    That branch could not fire while every subparser sets `func`, so it was
    untestable code guarding a developer mistake. The mistake is now caught
    here, before the command ships, instead of at the operator's terminal.
    """
    import ast

    src = (ROOT / "scripts" / "thread.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    add_parser = sum(
        1 for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "add_parser"
    )
    set_func = sum(
        1 for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "set_defaults"
        and any(kw.arg == "func" for kw in n.keywords)
    )
    assert add_parser > 0
    assert add_parser == set_func, (
        f"{add_parser} subparsers but {set_func} handlers: one subcommand would "
        f"crash with AttributeError instead of running"
    )


# ============================================================
# transcribe-media -- SRT cue numbers must be sequential
# ============================================================

def test_srt_cue_numbers_stay_sequential_when_an_empty_segment_is_dropped():
    segs = [
        {"text": "one", "start": 0.0, "end": 1.0},
        {"text": "   ", "start": 1.0, "end": 2.0},
        {"text": "three", "start": 2.0, "end": 3.0},
    ]
    out = tm.render_srt(segs)
    numbers = [ln for ln in out.split("\n") if ln.strip().isdigit()]
    assert numbers == ["1", "2"], out


def test_srt_keeps_the_surviving_text():
    segs = [
        {"text": "", "start": 0.0, "end": 1.0},
        {"text": "kept", "start": 1.0, "end": 2.0},
    ]
    out = tm.render_srt(segs)
    assert "kept" in out
    assert out.strip().startswith("1")


def test_word_timestamps_with_a_non_json_format_warns():
    code = _code_only(ROOT / "scripts" / "transcribe-media.py")
    assert 'args.word_timestamps and args.fmt != "json"' in code, (
        "the wasted-work warning is the point; a reworded guard is fine, a "
        "removed one is not"
    )
    assert "discards them" in code


# ============================================================
# update-manager -- status must survive a corrupt state file
# ============================================================

def test_status_on_a_corrupt_state_file_exits_one_without_a_traceback(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "state.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(um, "state_path", lambda: bad)
    assert um.cmd_status(None) == 1
    assert "run: python scripts/update-manager.py check" in capsys.readouterr().out


def test_status_on_a_valid_state_file_prints_the_row(tmp_path, monkeypatch, capsys):
    good = tmp_path / "state.json"
    good.write_text(json.dumps({"components": {"widget": {
        "current": "1.0", "latest": "1.1", "tier": "minor", "status": "behind"}}}),
        encoding="utf-8")
    monkeypatch.setattr(um, "state_path", lambda: good)
    assert um.cmd_status(None) == 0
    assert "widget" in capsys.readouterr().out


def test_status_on_a_state_file_with_no_components_exits_one(tmp_path, monkeypatch, capsys):
    empty = tmp_path / "state.json"
    empty.write_text(json.dumps({"components": {}}), encoding="utf-8")
    monkeypatch.setattr(um, "state_path", lambda: empty)
    assert um.cmd_status(None) == 1
    assert "no components" in capsys.readouterr().out


def test_apply_gates_the_fcntl_import_on_posix():
    """`fcntl` does not exist on Windows; a hard import lost the whole command."""
    code = _code_only(ROOT / "scripts" / "update-manager.py")
    assert 'if os.name == "posix":' in code
    assert code.index('if os.name == "posix":') < code.index("import fcntl"), (
        "the import must sit inside the guard"
    )


def test_the_guard_can_actually_run_because_os_is_imported():
    """The test above is a STRING MATCH, and a string match cannot see a missing
    import. On 2026-08-24 commit 76c63fd added `if os.name == "posix":` with no
    `import os` in the module; this file's assertion passed, the whole suite
    passed, and the update-manager systemd timer failed at 07:00 the next
    morning with `NameError: name 'os' is not defined`, taking the auto-apply
    tier down for a full cycle.

    Asked of the MODULE OBJECT, not of its text, so the name has to exist.
    """
    assert hasattr(um, "os"), (
        "scripts/update-manager.py references os.name but never imports os, so "
        "every `apply` raises NameError before it reaches the lock"
    )
    assert um.os.name in ("posix", "nt")


def test_a_platform_without_fcntl_degrades_instead_of_raising(tmp_path, monkeypatch, capsys):
    """The comment promised a named warning and the code delivered an
    AttributeError: `except BlockingIOError` does not catch `None.flock`, so on
    a non-posix host the command was lost with a traceback - the exact outcome
    the lazy import exists to prevent. It was unreachable only because the
    NameError above fired first, so fixing that one made this one live.
    """
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"components": {}}), encoding="utf-8")
    monkeypatch.setattr(um, "state_path", lambda: state)
    monkeypatch.setattr(um, "_import_fcntl", lambda: None)
    monkeypatch.setattr(um, "load_registry", lambda path: [])
    monkeypatch.setattr(um, "registry_path", lambda: tmp_path / "registry.yaml")

    applied = {}

    def _fake_apply(args, registry, path):
        applied["ran"] = True
        return 0

    import scripts.utils.update_apply as ua
    monkeypatch.setattr(ua, "cmd_apply", _fake_apply)

    assert um.main(["apply", "--auto"]) == 0
    assert applied.get("ran") is True, "the command was lost instead of degrading"
    assert "no advisory lock on this platform" in capsys.readouterr().out


def test_the_helper_returns_a_real_module_or_nothing_at_all():
    """`None` is the sentinel the branch below tests, so a truthy stand-in is
    worse than useless: `if fcntl is None` would be False and `fcntl.flock`
    would then raise AttributeError on whatever was returned - the traceback
    the degraded path exists to prevent, one step later.

    The non-posix arm cannot be exercised here (patching `os.name` makes
    pathlib build a WindowsPath and the interpreter refuses), so it is read
    from the source. The posix arm is exercised for real.
    """
    import ast
    src = (ROOT / "scripts" / "update-manager.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_import_fcntl")
    returns = [n.value for n in ast.walk(fn) if isinstance(n, ast.Return)]
    constants = [r for r in returns if isinstance(r, ast.Constant)]
    assert constants, "the no-lock arm no longer returns a constant"
    assert all(c.value is None for c in constants), (
        "the no-lock arm returns something truthy, so the None check below "
        "stops firing")

    got = um._import_fcntl()
    assert got is None or hasattr(got, "flock")


def test_a_busy_lock_is_skipped_not_crashed(tmp_path, monkeypatch, capsys):
    """`flock` raises BlockingIOError when another apply holds the lock, and
    that is a SKIP, not an error: two interleaved applies would snapshot and
    swap the same component. Catching a different exception class turns the
    skip back into a traceback."""
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"components": {}}), encoding="utf-8")
    monkeypatch.setattr(um, "state_path", lambda: state)
    monkeypatch.setattr(um, "load_registry", lambda path: [])
    monkeypatch.setattr(um, "registry_path", lambda: tmp_path / "registry.yaml")

    class _Busy:
        LOCK_EX = 2
        LOCK_NB = 4

        @staticmethod
        def flock(fh, flags):
            raise BlockingIOError(11, "Resource temporarily unavailable")

    monkeypatch.setattr(um, "_import_fcntl", lambda: _Busy)

    import scripts.utils.update_apply as ua
    monkeypatch.setattr(ua, "cmd_apply", lambda *a, **k: pytest.fail(
        "a second apply ran while the lock was held"))

    assert um.main(["apply", "--auto"]) == 0
    assert "another apply is in progress" in capsys.readouterr().out


def test_the_lock_is_still_taken_on_this_platform(tmp_path, monkeypatch, capsys):
    """The degradation must not have become the normal path."""
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"components": {}}), encoding="utf-8")
    monkeypatch.setattr(um, "state_path", lambda: state)
    monkeypatch.setattr(um, "load_registry", lambda path: [])
    monkeypatch.setattr(um, "registry_path", lambda: tmp_path / "registry.yaml")

    import scripts.utils.update_apply as ua
    monkeypatch.setattr(ua, "cmd_apply", lambda args, registry, path: 0)

    assert um.main(["apply", "--auto"]) == 0
    assert "no advisory lock" not in capsys.readouterr().out
    assert um._import_fcntl() is not None, "posix must still get a real lock"


# ============================================================
# sync-exchange -- the delete cap, and the blank query
# ============================================================

class _FakeMail:
    def __init__(self, subject):
        self.subject = subject
        self.datetime_received = None
        self.sender = None
        self.deleted = False

    def delete(self):
        self.deleted = True


class _FakeHits:
    def __init__(self, items, total=None):
        self._items = items
        self._total = len(items) if total is None else total

    def order_by(self, _key):
        return self

    def count(self):
        return self._total

    def __getitem__(self, sl):
        return self._items[sl]


class _FakeFolder:
    def __init__(self, items, total=None):
        self._items = items
        self._total = total

    def filter(self, **_kw):
        return _FakeHits(self._items, self._total)


class _FakeAccount:
    def __init__(self, folder):
        self.inbox = folder


def test_a_blank_delete_query_is_refused():
    acct = _FakeAccount(_FakeFolder([]))
    with pytest.raises(ValueError) as exc:
        sx.delete_emails(acct, "   ")
    assert "every message" in str(exc.value)


def test_a_small_match_set_reports_no_cap(capsys):
    items = [_FakeMail("hello") for _ in range(3)]
    acct = _FakeAccount(_FakeFolder(items))
    assert sx.delete_emails(acct, "hello", confirm=False) == 3
    out = capsys.readouterr().out
    assert "Capped at" not in out
    assert all(m.deleted for m in items)


def test_a_capped_match_set_names_both_numbers(capsys):
    items = [_FakeMail("spam") for _ in range(sx.DELETE_MATCH_CAP)]
    acct = _FakeAccount(_FakeFolder(items, total=137))
    deleted = sx.delete_emails(acct, "spam", confirm=False)
    out = capsys.readouterr().out
    assert deleted == sx.DELETE_MATCH_CAP
    assert f"Capped at {sx.DELETE_MATCH_CAP}" in out
    assert "137 match" in out
    assert "re-run" in out


def test_a_match_set_exactly_at_the_cap_with_no_more_behind_it_does_not_cry_wolf(capsys):
    items = [_FakeMail("x") for _ in range(sx.DELETE_MATCH_CAP)]
    acct = _FakeAccount(_FakeFolder(items, total=sx.DELETE_MATCH_CAP))
    sx.delete_emails(acct, "x", confirm=False)
    assert "Capped at" not in capsys.readouterr().out


def test_no_matches_deletes_nothing():
    acct = _FakeAccount(_FakeFolder([]))
    assert sx.delete_emails(acct, "nothing", confirm=False) == 0


# ============================================================
# sync-exchange -- localisation failures are reported, not guessed
# ============================================================

def test_a_start_with_no_astimezone_returns_none_and_warns(capsys):
    sx._LOCALISE_WARNED.clear()
    assert sx._to_local(date(2026, 8, 24), "irrelevant") is None
    assert "could not convert" in capsys.readouterr().out


def test_the_warning_fires_once_per_message(capsys):
    sx._LOCALISE_WARNED.clear()
    sx._to_local(date(2026, 8, 24), "tz")
    sx._to_local(date(2026, 8, 25), "tz")
    assert capsys.readouterr().out.count("could not convert") == 1


def test_an_all_day_start_still_renders_a_label():
    sx._LOCALISE_WARNED.clear()
    assert sx._event_time_str(date(2026, 8, 24), "tz") == "All day"


def test_a_real_datetime_renders_its_local_time():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    when = datetime(2026, 8, 24, 9, 30, tzinfo=ZoneInfo("UTC"))
    assert sx._event_time_str(when, ZoneInfo("UTC")) == "09:30"


# ============================================================
# sync-exchange -- stale per-day files are pruned, carefully
# ============================================================

def test_a_day_file_inside_the_window_that_we_did_not_write_is_pruned(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sx, "CALENDAR_DIR", tmp_path)
    stale = tmp_path / "2026-08-25.md"
    stale.write_text("old meeting", encoding="utf-8")
    kept = tmp_path / "2026-08-24.md"
    kept.write_text("current", encoding="utf-8")
    sx._prune_stale_day_files({"2026-08-24.md"}, date(2026, 8, 24), date(2026, 8, 31))
    assert not stale.exists()
    assert kept.exists()
    assert "Pruned stale" in capsys.readouterr().out


def test_a_day_file_outside_the_window_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(sx, "CALENDAR_DIR", tmp_path)
    older = tmp_path / "2026-07-01.md"
    older.write_text("last month", encoding="utf-8")
    sx._prune_stale_day_files(set(), date(2026, 8, 24), date(2026, 8, 31))
    assert older.exists()


def test_upcoming_md_is_never_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(sx, "CALENDAR_DIR", tmp_path)
    combined = tmp_path / "upcoming.md"
    combined.write_text("the combined file", encoding="utf-8")
    sx._prune_stale_day_files(set(), date(2026, 8, 24), date(2026, 8, 31))
    assert combined.exists()


def test_a_compact_iso_name_we_never_write_is_left_alone(tmp_path, monkeypatch):
    """`date.fromisoformat` accepts `20260825` since Python 3.11.

    So the date parse alone is not a sufficient filter -- it would happily prune
    a `20260825.md` that this script never created. The `YYYY-MM-DD.md` pattern
    is what confines the delete to our own output.
    """
    monkeypatch.setattr(sx, "CALENDAR_DIR", tmp_path)
    foreign = tmp_path / "20260825.md"
    foreign.write_text("not ours", encoding="utf-8")
    sx._prune_stale_day_files(set(), date(2026, 8, 24), date(2026, 8, 31))
    assert foreign.exists()


def test_a_hand_named_note_is_never_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(sx, "CALENDAR_DIR", tmp_path)
    note = tmp_path / "2026-08-25-notes.md"
    note.write_text("mine", encoding="utf-8")
    sx._prune_stale_day_files(set(), date(2026, 8, 24), date(2026, 8, 31))
    assert note.exists()


# ============================================================
# sync-exchange -- sync_calendar end to end, against a fake mailbox
# ============================================================

class _FakeEvent:
    def __init__(self, start, end, subject, location=None):
        self.start = start
        self.end = end
        self.subject = subject
        self.location = location
        self.body = None
        self.required_attendees = None
        self.optional_attendees = None


class _FakeCalendar:
    def __init__(self, events):
        self._events = events

    def view(self, start=None, end=None):
        return list(self._events)


class _FakeCalendarAccount:
    def __init__(self, events):
        self.calendar = _FakeCalendar(events)


@pytest.fixture
def calendar_sandbox(tmp_path, monkeypatch):
    """A writable calendar dir with exchangelib's two datetime types stubbed out."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    cal = tmp_path / "calendar"
    cal.mkdir()
    monkeypatch.setattr(sx, "CALENDAR_DIR", cal)
    monkeypatch.setattr(sx, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(sx, "EWSDateTime", _dt)

    class _TZ:
        @staticmethod
        def from_timezone(zone):
            return zone

    monkeypatch.setattr(sx, "EWSTimeZone", _TZ)
    sx._LOCALISE_WARNED.clear()
    return cal, ZoneInfo("UTC"), _dt


def test_sync_calendar_writes_the_combined_file_and_a_day_file(calendar_sandbox):
    cal, utc, dt = calendar_sandbox
    today = dt.now(utc)
    ev = _FakeEvent(today.replace(hour=9, minute=0), today.replace(hour=10, minute=0),
                    "Standup", "Room 1")
    total = sx.sync_calendar(_FakeCalendarAccount([ev]), days=7, timezone_str="UTC")
    assert total == 1
    assert (cal / "upcoming.md").exists()
    day_files = [p.name for p in cal.glob("*.md") if p.name != "upcoming.md"]
    assert len(day_files) == 1
    assert "Standup" in (cal / day_files[0]).read_text(encoding="utf-8")


def test_sync_calendar_prunes_a_day_file_whose_meeting_is_gone(calendar_sandbox):
    """The regression the pruner exists for: a cancelled meeting's file survived.

    Run once with a meeting tomorrow, run again with the meeting gone, and the
    tomorrow file must not still be sitting there advertising it.
    """
    cal, utc, dt = calendar_sandbox
    from datetime import timedelta as _td

    tomorrow = dt.now(utc).replace(hour=9, minute=0) + _td(days=1)
    ev = _FakeEvent(tomorrow, tomorrow + _td(hours=1), "Cancelled later")
    sx.sync_calendar(_FakeCalendarAccount([ev]), days=7, timezone_str="UTC")
    day_file = cal / f"{tomorrow.date().isoformat()}.md"
    assert day_file.exists()

    sx.sync_calendar(_FakeCalendarAccount([]), days=7, timezone_str="UTC")
    assert not day_file.exists(), (
        "the meeting was cancelled but its day file kept presenting it as fact"
    )


def test_sync_calendar_leaves_a_day_file_beyond_the_window(calendar_sandbox):
    cal, utc, dt = calendar_sandbox
    far = cal / "2030-01-01.md"
    far.write_text("a run with a longer --days wrote this", encoding="utf-8")
    sx.sync_calendar(_FakeCalendarAccount([]), days=7, timezone_str="UTC")
    assert far.exists()


# ============================================================
# sync-exchange -- honest wording, per-call timezone
# ============================================================

def test_connect_does_not_claim_a_connection_the_constructor_never_made():
    code = _code_only(ROOT / "scripts" / "sync-exchange.py")
    assert "[OK] Connected as" not in code
    assert "not yet contacted" in code


def test_the_timezone_default_is_resolved_per_call_not_at_import():
    import inspect

    for fn in (sx.sync_calendar, sx.create_meeting):
        sig = inspect.signature(fn)
        assert sig.parameters["timezone_str"].default is None, fn.__name__


def test_both_timezone_defaults_are_resolved_in_the_function_body():
    code = _code_only(ROOT / "scripts" / "sync-exchange.py")
    assert code.count("timezone_str = get_default_tz_name()") == 2, (
        "both sync_calendar and create_meeting resolve it in the body"
    )


# ============================================================
# turn-check -- a deletion moves the fingerprint
# ============================================================

def test_a_deletion_changes_the_fingerprint(tmp_path):
    live = tmp_path / "still_here.py"
    live.write_text("x = 1\n", encoding="utf-8")
    before = tc.fingerprint([live])
    after = tc.fingerprint([live], ["scripts/gone.py"])
    assert before != after, (
        "the turn that deletes a module must not read as 'cached'"
    )


def test_the_deleted_set_is_order_independent(tmp_path):
    live = tmp_path / "a.py"
    live.write_text("x = 1\n", encoding="utf-8")
    a = tc.fingerprint([live], ["scripts/b.py", "scripts/a.py"])
    b = tc.fingerprint([live], ["scripts/a.py", "scripts/b.py"])
    assert a == b


def test_no_deletions_leaves_the_fingerprint_where_it_was(tmp_path):
    live = tmp_path / "a.py"
    live.write_text("x = 1\n", encoding="utf-8")
    assert tc.fingerprint([live]) == tc.fingerprint([live], [])


def test_a_deletion_busts_the_pass_cache(monkeypatch, tmp_path):
    """The end-to-end shape of the bug: same surviving bytes, new deletion.

    `run()` used to hash only the files still on disk, so the turn that removed
    a module hashed identically to the turn before it and returned `cached`.
    """
    live = tmp_path / "still_here.py"
    live.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(tc, "changed_python_files", lambda: [live])
    monkeypatch.setattr(tc, "narrow", lambda paths, _t: (paths, 0))
    monkeypatch.setattr(tc, "deleted_python_files", list)
    clean_fp = tc.fingerprint([live])
    monkeypatch.setattr(tc, "read_state", lambda: {"last_pass": clean_fp})

    assert tc.run(timeout=30, use_cache=True)["status"] == "cached"

    monkeypatch.setattr(tc, "deleted_python_files", lambda: ["scripts/gone.py"])
    assert tc.run(timeout=30, use_cache=True)["status"] != "cached"


def test_a_turn_that_only_deletes_is_idle_and_says_how_many(monkeypatch):
    monkeypatch.setattr(tc, "changed_python_files", list)
    monkeypatch.setattr(tc, "narrow", lambda paths, _t: (paths, 0))
    monkeypatch.setattr(tc, "deleted_python_files", lambda: ["scripts/gone.py"])
    result = tc.run(timeout=30, use_cache=True)
    assert result["status"] == "idle"
    assert result["deleted"] == 1
    assert "1 deleted" in result["reason"]


def test_deleted_python_files_reports_a_tracked_path_that_is_gone(monkeypatch):
    monkeypatch.setattr(tc, "_git", lambda _a: ["scripts/never-existed-xyz.py",
                                                "scripts/turn-check.py"])
    gone = tc.deleted_python_files()
    assert gone == ["scripts/never-existed-xyz.py"]


def test_deleted_python_files_ignores_unwatched_trees(monkeypatch):
    monkeypatch.setattr(tc, "_git", lambda _a: ["somewhere/else/gone.py"])
    assert tc.deleted_python_files() == []


# ============================================================
# transfer-contact -- one admin resolver, not two
# ============================================================

def test_transfer_contact_uses_the_shared_admin_resolver():
    code = _code_only(ROOT / "scripts" / "transfer-contact.py")
    assert "get_admin_slugs()" in code
    assert "load_admin_config" not in code, (
        "the local reimplementation fell back to an EMPTY admin set, which "
        "routed the operator's own contacts into a per-exec overlay"
    )


def test_transfer_contact_backup_is_date_stamped_and_never_clobbers():
    code = _code_only(ROOT / "scripts" / "transfer-contact.py")
    assert 'with_suffix(".md.transferred")' not in code
    assert "while backup_path.exists():" in code
    assert '.md.transferred-{stamp}' in code


def test_transfer_contact_docstring_documents_no_phantom_flag():
    head = (ROOT / "scripts" / "transfer-contact.py").read_text(encoding="utf-8").split('"""')[1]
    assert "--repo" not in head, "argparse defines no --repo"


# ============================================================
# The shell and unit files nobody's test suite runs
# ============================================================

def test_the_marp_runner_checks_the_exit_code_of_every_native_call():
    code = _code_only(ROOT / "scripts" / "test-marp.ps1")
    assert "$LASTEXITCODE -ne 0" in code, (
        "$ErrorActionPreference='Stop' does not trap a native non-zero exit in "
        "PowerShell 5.1, so a failing pytest printed a success line and exited 0. "
        "A `$LASTEXITCODE` that is only echoed, never COMPARED, is the same bug."
    )
    # Every `python ...` invocation goes through the checked wrapper.
    bare = [
        ln.strip() for ln in code.split("\n")
        if ln.strip().startswith("python ") or ln.strip().startswith("python -m")
    ]
    assert not bare, f"unchecked native calls: {bare}"
    assert "exit $LASTEXITCODE" in code


def test_the_update_manager_unit_quotes_its_placeholders():
    src = (ROOT / "scripts" / "templates" / "systemd" / "update-manager.service").read_text(encoding="utf-8")
    exec_line = [ln for ln in src.split("\n") if ln.startswith("ExecStart=")][0]
    assert '"{{PYTHON}}"' in exec_line
    assert '"{{WORKSPACE}}/scripts/update-manager.py"' in exec_line


def test_the_systemd_readme_accounts_for_every_template():
    readme = (ROOT / "scripts" / "templates" / "systemd" / "README.md").read_text(encoding="utf-8")
    tpl_dir = ROOT / "scripts" / "templates" / "systemd"
    stems = {p.stem for p in tpl_dir.glob("*.timer")}
    missing = sorted(s for s in stems if s not in readme)
    assert not missing, f"timers absent from the README: {missing}"


def test_the_systemd_readme_placeholder_list_matches_the_templates():
    tpl_dir = ROOT / "scripts" / "templates" / "systemd"
    import re as _re

    tokens = set()
    for p in tpl_dir.iterdir():
        if p.name == "README.md":
            continue
        tokens |= set(_re.findall(r"\{\{[A-Z_]+\}\}", p.read_text(encoding="utf-8")))
    readme = (tpl_dir / "README.md").read_text(encoding="utf-8")
    assert tokens == {"{{WORKSPACE}}", "{{PYTHON}}", "{{TZ}}"}, tokens
    for tok in tokens:
        assert tok in readme, f"{tok} is substituted but undocumented"


def test_every_systemd_unit_that_names_a_timezone_sets_the_tz_environment():
    tpl_dir = ROOT / "scripts" / "templates" / "systemd"
    missing = []
    for svc in sorted(tpl_dir.glob("*.service")):
        if not (tpl_dir / f"{svc.stem}.timer").exists():
            continue  # a daemon, not a scheduled task
        text = svc.read_text(encoding="utf-8")
        if "Environment=TZ={{TZ}}" not in text:
            missing.append(svc.name)
    assert not missing, (
        f"a scheduled unit whose timer names {{{{TZ}}}} but whose service does not "
        f"set it runs its date math on the host libc timezone: {missing}"
    )
