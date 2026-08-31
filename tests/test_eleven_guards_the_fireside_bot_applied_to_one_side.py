#!/usr/bin/env python3
"""Shard 38: the fireside bot, and eleven guards it applied to one side only.

`scripts/fireside-bot.py` is 4,722 lines and the thinnest-covered file in the
engine. Almost every defect below is the same shape the earlier shards keep
finding: a rule written down once, applied to one of the two or three places
that needed it, and believed everywhere.

  - the token redaction `TelegramBot._call` promises applied to every Telegram
    call in the file except `cmd_set_webhook`, which built its own
    `.../bot{token}/setWebhook` URL and called `requests.post` on it;
  - the write funnel that refuses any path inside the engine clone applied to
    the five state writers and not to `cmd_stats`, whose own funnel docstring
    says "a guard added to some of them is a guard the next writer will not
    have";
  - the cross-process lock applied to two of the three load-modify-saves of
    schedule.json, and the test that should have caught the third forbidding
    ONE variable name;
  - the "counted and named now" summary of `cmd_email_backup` counting every
    drop except a send that ran and failed;
  - the cycle-progress denominator frozen at 9 while the numerator is read from
    the schedule;
  - a DM-delivery tally naming five categories and counting four;
  - the Monday-plus-2 rule that keeps a week live through its Wednesday session
    applied to `helmsman_brief_candidates` and not to the nudge that tells the
    CEO the slot is empty;
  - the post-then-pin outcome tracked in `cmd_sunday_preview` and swallowed in
    `_handle_cycle_invite_tap`, which then told the CEO "Sent and pinned";
  - the already-sent guard on two of the three send loops;
  - the opt-in gate claiming "authorized" in its comment and using a check that
    asks neither question `_is_authorized_user` asks;
  - and a health-check that returned before alerting on the strongest possible
    evidence that nothing is alive.

There is no Telegram in these tests. Nothing here constructs a real bot or
reaches the network.

Run: .venv/bin/python -m pytest tests/test_eleven_guards_the_fireside_bot_applied_to_one_side.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SRC = ROOT / "scripts" / "fireside-bot.py"

from scripts.utils.telegram_bot import TelegramAPIError, TelegramBot  # noqa: E402

# An invented string shaped like a Telegram token. The tests below assert that
# this exact text never reaches a log line or an exception message, so it has to
# be a literal here.
TOKEN = "1234567890:AAHfakeTOKENnotarealcredential_xyz"  # noqa: S105


@pytest.fixture(scope="module")
def fb():
    spec = importlib.util.spec_from_file_location("fireside_bot_s38", str(SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def state(fb, tmp_path, monkeypatch):
    """Every fireside writer resolves through this one constant, so redirect it
    for every test in the module rather than for the tests that remembered.

    Autouse, and that is the whole point. It was opt-in until 2026-08-29, and
    the three `_nudge_ceo_on_helmsman_gaps` tests below did not opt in: the
    nudge writes a `helmsman_gap_nudge` row through `_log_event`, which landed
    in the operator's live `sessions.jsonl`. The conftest write guard refused
    it, the nudge's own `except Exception` caught the refusal, and `log_error`
    then tried to append to the live `errors.log` and raised out of the test.
    The behaviour under test was fine; the isolation was missing. That is the
    same one-side-only shape this file's docstring is about.
    """
    monkeypatch.setattr(fb, "state_dir", lambda p=tmp_path: p)
    return tmp_path


# ============================================================
# 1. the one Telegram call that carried the token in the clear
# ============================================================

def _patch_post(monkeypatch, handler):
    """Replace requests.post only. Replacing the whole module would also
    replace `requests.RequestException`, and the except clause that catches it
    would raise AttributeError instead."""
    import scripts.utils.telegram_bot as tb
    monkeypatch.setattr(tb.requests, "post", handler)


def test_a_transport_failure_on_the_upload_never_quotes_the_token(monkeypatch):
    boom = requests.ConnectionError(
        f"HTTPSConnectionPool: Max retries exceeded with url: "
        f"/bot{TOKEN}/setWebhook (Caused by NewConnectionError)")

    def _raise(url, **kw):
        raise boom

    _patch_post(monkeypatch, _raise)
    bot = TelegramBot(TOKEN, on_error=lambda m: None)

    with pytest.raises(TelegramAPIError) as caught:
        bot.call_multipart("setWebhook", data={}, files={})

    assert TOKEN not in str(caught.value)
    assert "<REDACTED_TOKEN>" in str(caught.value)


def test_the_upload_failure_is_reported_to_the_error_sink_redacted(monkeypatch):
    def _raise(url, **kw):
        raise requests.Timeout(f"timed out for /bot{TOKEN}/setWebhook")

    _patch_post(monkeypatch, _raise)
    seen = []
    bot = TelegramBot(TOKEN, on_error=seen.append)

    with pytest.raises(TelegramAPIError):
        bot.call_multipart("setWebhook", data={}, files={})

    assert seen and TOKEN not in seen[0]


def test_a_non_json_body_on_the_upload_raises_the_typed_error(monkeypatch):
    """A captive portal answering HTML used to raise JSONDecodeError out of a
    command whose only error path was the `ok` check."""
    class _Resp:
        status_code = 200
        text = "<html>portal</html>"
        ok = True

        def json(self):
            raise json.JSONDecodeError("no", "", 0)

    _patch_post(monkeypatch, lambda url, **kw: _Resp())
    bot = TelegramBot(TOKEN, on_error=lambda m: None)
    with pytest.raises(TelegramAPIError):
        bot.call_multipart("setWebhook", data={}, files={})


def test_the_upload_returns_the_result_field_on_success(monkeypatch):
    class _Resp:
        status_code = 200
        ok = True

        def json(self):
            return {"ok": True, "result": True, "description": "set"}

    seen = {}

    def _post(url, data=None, files=None, timeout=None):
        seen["url"] = url
        return _Resp()

    _patch_post(monkeypatch, _post)
    bot = TelegramBot(TOKEN, on_error=lambda m: None)
    assert bot.call_multipart("setWebhook", data={"url": "x"}, files={}) is True
    assert seen["url"].endswith("/setWebhook")


def test_set_webhook_builds_no_url_of_its_own():
    src = SRC.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.split("\n")
                     if not ln.lstrip().startswith("#"))
    assert "requests.post(" not in code, (
        "every Telegram call in this file goes through TelegramBot, which is "
        "where the token redaction lives")
    assert "bot.call_multipart(" in code


def test_only_one_copy_of_the_redaction_handles_a_response():
    """A second transcription is the one that stops being fixed."""
    tb = (ROOT / "scripts" / "utils" / "telegram_bot.py").read_text(encoding="utf-8")
    assert tb.count("def _handle_response") == 1
    # One place that turns a bad response into a redacted error, not two.
    assert tb.count("raise TelegramAPIError(msg, status_code=status") == 3
    assert tb.count("def _call(") == 1 and tb.count("def call_multipart(") == 1


# ============================================================
# 2. the report writer outside the write funnel
# ============================================================

OFFENDER = '''
def cmd_thing(args):
    schedule = load_state(SCHEDULE) or []
    for entry in schedule:
        entry["completed"] = True
    save_state(SCHEDULE, schedule)
'''

REBUILDER = '''
def cmd_rollover(args):
    schedule = load_state(SCHEDULE) or []
    entries = build_schedule(schedule)
    save_state(SCHEDULE, entries)
'''

LOCKED = '''
def cmd_thing(args):
    with locked_state(SCHEDULE, []) as schedule:
        for entry in schedule:
            entry["completed"] = True
'''


def test_the_lock_detector_reports_a_known_offender():
    """A guard that has never refused anything is not known to be a guard.

    The check it replaced counted a literal and forbade one variable name, and
    `cmd_log_session` passed both while doing exactly this.
    """
    from tests.test_a_bot_that_said_it_was_alive_while_it_was_not import (
        _unlocked_read_modify_writes)
    assert _unlocked_read_modify_writes("SCHEDULE", OFFENDER) == ["cmd_thing"]


def test_the_lock_detector_leaves_a_full_rebuild_alone():
    """`cmd_cycle_rollover` saves an object it never loaded. Flagging it would
    make the guard something an author routes around instead of obeying."""
    from tests.test_a_bot_that_said_it_was_alive_while_it_was_not import (
        _unlocked_read_modify_writes)
    assert _unlocked_read_modify_writes("SCHEDULE", REBUILDER) == []


def test_the_lock_detector_accepts_a_locked_writer():
    from tests.test_a_bot_that_said_it_was_alive_while_it_was_not import (
        _unlocked_read_modify_writes)
    assert _unlocked_read_modify_writes("SCHEDULE", LOCKED) == []


def test_the_stats_writer_refuses_a_path_inside_the_engine_clone(fb, monkeypatch):
    """`python scripts/fireside-bot.py stats` on a clone with no data overlay
    resolved STATS_DIR into the repository and created the tree."""
    inside = ROOT / "examples" / "outputs" / "operations" / "tribe-fireside" / "stats"
    monkeypatch.setattr(fb, "stats_dir", lambda p=inside: p)

    try:
        with pytest.raises(Exception) as caught:
            fb.require_writable_stats_dir()
        assert ("engine" in str(caught.value).lower()
                or "clone" in str(caught.value).lower())
        assert not inside.exists(), "the refusal must not create the tree first"
    finally:
        # This test names a real path inside the repository, so a run where the
        # guard is BROKEN - a mutation run, or a regression - creates the tree
        # for real. Clean up whatever the call left, or the litter outlives the
        # failure that produced it. Empty directories are invisible to
        # `git status`, which is what makes them easy to leave behind.
        path = inside
        while path != ROOT / "examples" / "outputs":
            try:
                path.rmdir()
            except OSError:
                break
            path = path.parent


def test_the_stats_writer_accepts_a_path_outside_the_clone(fb, tmp_path,
                                                           monkeypatch):
    target = tmp_path / "outputs" / "stats"
    monkeypatch.setattr(fb, "stats_dir", lambda p=target: p)
    assert fb.require_writable_stats_dir() == target
    assert target.is_dir()


def test_cmd_stats_goes_through_the_funnel_not_a_bare_mkdir():
    src = SRC.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.split("\n")
                     if not ln.lstrip().startswith("#"))
    # Once, inside the funnel. The point is that `cmd_stats` no longer has
    # its own; a second occurrence anywhere is a writer that skipped it.
    assert code.count("stats_dir().mkdir(") == 1
    funnel = code[code.index("def require_writable_stats_dir"):]
    assert "stats_dir().mkdir(" in funnel[:funnel.index("\ndef ")]
    assert "require_writable_stats_dir()" in code


# ============================================================
# 3. the third schedule writer, outside the lock
# ============================================================

def _sched(dates_and_names):
    return [{"session_date": d, "speaker_name": n, "speaker_username": n.lower(),
             "week": 1, "day": "Wed", "slot": i, "theme": "t"}
            for i, (d, n) in enumerate(dates_and_names, start=1)]


def test_log_session_reads_the_schedule_again_under_the_lock(fb, state,
                                                             monkeypatch):
    """The race, made deterministic: something else rewrites schedule.json
    between the command starting and its write."""
    fb.save_state(fb.SCHEDULE, _sched([("2026-09-09", "Vesper Lynd")]))

    swapped = _sched([("2026-09-09", "Felix Leiter")])
    real_locked = fb.locked_state

    def _swap_then_lock(filename, default):
        if filename == fb.SCHEDULE:
            # The daemon's accepted /swap lands first, before we take the lock.
            fb.save_state(fb.SCHEDULE, swapped)
        return real_locked(filename, default)

    monkeypatch.setattr(fb, "locked_state", _swap_then_lock)
    monkeypatch.setattr(fb, "_log_event", lambda *a, **k: None)

    fb.cmd_log_session(SimpleNamespace(date="2026-09-09", shared="Felix Leiter",
                                       no_shows="", swaps=""))

    final = fb.load_state(fb.SCHEDULE)
    assert [e["speaker_name"] for e in final] == ["Felix Leiter"], (
        "the pre-swap copy was written back over the accepted swap")
    assert final[0]["completed"] is True


def test_log_session_writes_nothing_when_the_date_matches_no_entry(fb, state):
    original = _sched([("2026-09-09", "Vesper Lynd")])
    fb.save_state(fb.SCHEDULE, original)

    with pytest.raises(SystemExit):
        fb.cmd_log_session(SimpleNamespace(date="2026-09-16",
                                           shared="Vesper Lynd",
                                           no_shows="", swaps=""))

    assert fb.load_state(fb.SCHEDULE) == original, (
        "the refusal must not save the list it was about to reject")


def test_log_session_marks_a_no_show_as_completed(fb, state, monkeypatch):
    fb.save_state(fb.SCHEDULE, _sched([("2026-09-09", "Vesper Lynd")]))
    monkeypatch.setattr(fb, "_log_event", lambda *a, **k: None)
    fb.cmd_log_session(SimpleNamespace(date="2026-09-09", shared="Nobody",
                                       no_shows="Vesper Lynd", swaps=""))
    row = fb.load_state(fb.SCHEDULE)[0]
    assert row["no_show"] is True and row["completed"] is True


# ============================================================
# 4. the summary that counted every drop except a failed send
# ============================================================

def _backup_fixture(fb, state, monkeypatch, returncode):
    today = fb._today_local_date()
    session = (today + timedelta(days=3)).isoformat()
    fb.save_state(fb.SCHEDULE, [{
        "session_date": session, "speaker_name": "Vesper Lynd",
        "speaker_username": "vesper", "week": 1, "day": "Wed", "slot": 1,
        "theme": "Signals",
    }])
    fb.save_state(fb.TRIBE_ROSTER, {"vesper": {
        "name": "Vesper Lynd", "email": "vesper@example.test",
        "telegram_user_id": None, "active": True,
    }})
    # `subprocess` is imported inside `cmd_email_backup`, so the patch goes on
    # the real module the local import resolves to.
    import subprocess as _sp
    monkeypatch.setattr(
        _sp, "run",
        lambda *a, **k: SimpleNamespace(returncode=returncode, stdout="",
                                        stderr="EWS auth failed"))
    errors = []
    monkeypatch.setattr(fb, "log_error", lambda *a, **k: errors.append(a))
    monkeypatch.setattr(fb, "hc_ping", lambda *a, **k: None)
    return errors


def test_a_failed_backup_email_is_counted(fb, state, monkeypatch, capsys):
    _backup_fixture(fb, state, monkeypatch, returncode=1)
    fb.cmd_email_backup(SimpleNamespace(dry_run=False))
    out = capsys.readouterr().out
    assert "failed=1" in out
    assert "@vesper" in out, "the people who got no mail are named, not counted"


def test_a_failed_backup_email_reaches_the_error_log(fb, state, monkeypatch,
                                                     capsys):
    errors = _backup_fixture(fb, state, monkeypatch, returncode=1)
    fb.cmd_email_backup(SimpleNamespace(dry_run=False))
    capsys.readouterr()
    assert errors, (
        "the exception branch always logged; the ordinary non-zero exit, which "
        "is the class that happens routinely, did not")


def test_a_healthy_backup_run_reports_no_failures(fb, state, monkeypatch,
                                                  capsys):
    _backup_fixture(fb, state, monkeypatch, returncode=0)
    fb.cmd_email_backup(SimpleNamespace(dry_run=False))
    out = capsys.readouterr().out
    assert "sent=1" in out
    assert "failed=" not in out


# ============================================================
# 5 and 6. the stats report: a frozen denominator and a phantom category
# ============================================================

def _stats_fixture(fb, state, tmp_path, monkeypatch, weeks):
    monkeypatch.setattr(fb, "stats_dir", lambda p=tmp_path / "stats": p)
    rows = []
    for w in range(1, weeks + 1):
        rows.append({"session_date": f"2026-09-{w:02d}", "speaker_name": f"S{w}",
                     "speaker_username": f"s{w}", "week": w, "day": "Mon",
                     "slot": 1, "theme": "t"})
    fb.save_state(fb.SCHEDULE, rows)
    fb.save_state(fb.TRIBE_ROSTER, {})
    fb.save_state(fb.HELMSMEN, {})
    fb.save_state(fb.OPT_INS, {"helmsman": [], "wildcard": []})
    return rows


def test_the_cycle_denominator_follows_the_schedule(fb, state, tmp_path,
                                                    monkeypatch, capsys):
    _stats_fixture(fb, state, tmp_path, monkeypatch, weeks=11)
    monkeypatch.setattr(fb, "_current_or_upcoming_week", lambda *a, **k: 3)
    fb.cmd_stats(SimpleNamespace(show=True))
    out = capsys.readouterr().out
    assert "**3** of 11" in out
    assert "of 9" not in out, "the denominator was a literal while the cycle is data"


def test_a_shorter_cycle_is_reported_at_its_own_length(fb, state, tmp_path,
                                                       monkeypatch, capsys):
    _stats_fixture(fb, state, tmp_path, monkeypatch, weeks=8)
    monkeypatch.setattr(fb, "_current_or_upcoming_week", lambda *a, **k: 8)
    fb.cmd_stats(SimpleNamespace(show=True))
    assert "**8** of 8" in capsys.readouterr().out


def test_no_dm_category_is_counted_that_nothing_writes():
    """`helmsman_brief` was in the filter and in no writer, so the percentage
    beneath it spoke for a set it never contained."""
    import ast

    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    written = set()
    for node in ast.walk(tree):
        # A literal first argument to _log_dm.
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_log_dm" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            written.add(node.args[0].value)
        # ...or a literal bound to `dm_type` by a loop that unpacks a table.
        # `cmd_speaker_dms` writes "2wk" and "3day" that way, so a scan of
        # literal call arguments alone sees neither and would call both
        # phantoms.
        if isinstance(node, ast.For) and isinstance(node.target, ast.Tuple):
            names = [e.id for e in node.target.elts if isinstance(e, ast.Name)]
            if "dm_type" in names:
                for sub in ast.walk(node.iter):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        written.add(sub.value)
    assert {"2wk", "3day", "dayof", "email-backup"} <= written, (
        "the _log_dm writers moved; re-point this test")

    counted = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Compare) and node.ops
                and isinstance(node.ops[0], ast.In)
                and isinstance(node.comparators[0], ast.Tuple)):
            values = [e.value for e in node.comparators[0].elts
                      if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if "2wk" in values and "3day" in values and "email-backup" in values:
                counted = set(values)
    assert counted, "the dm_type tally moved; re-point this test"
    assert counted <= written, (
        f"the tally counts categories nothing writes: {sorted(counted - written)}")


# ============================================================
# 7. the helmsman nudge that went quiet mid-week
# ============================================================

def _week(monday: str):
    wednesday = (date.fromisoformat(monday) + timedelta(days=2)).isoformat()
    return [
        {"session_date": monday, "day": "Mon", "week": 1, "slot": 1,
         "speaker_name": "A", "speaker_username": "a", "theme": "t"},
        {"session_date": wednesday, "day": "Wed", "week": 1, "slot": 1,
         "speaker_name": "B", "speaker_username": "b", "theme": "t"},
    ]


def _nudges(fb, monkeypatch, today):
    sent = []
    monkeypatch.setattr(fb, "misha_user_id", lambda: 42)
    monkeypatch.setattr(fb, "get_bot",
                        lambda: SimpleNamespace(
                            send_message=lambda *a, **k: sent.append(a)))
    fb._nudge_ceo_on_helmsman_gaps(_week("2026-07-13"), {}, today)
    return sent


def test_the_nudge_still_fires_on_the_monday(fb, monkeypatch):
    assert _nudges(fb, monkeypatch, date(2026, 7, 13))


def test_the_nudge_still_fires_on_the_tuesday(fb, monkeypatch):
    """The finding. The gap is unfixed and the session is tomorrow, and this
    was the day the nudge fell silent."""
    assert _nudges(fb, monkeypatch, date(2026, 7, 14))


def test_the_nudge_still_fires_on_the_wednesday_session_day(fb, monkeypatch):
    assert _nudges(fb, monkeypatch, date(2026, 7, 15))


def test_the_nudge_stops_once_the_week_is_over(fb, monkeypatch):
    assert _nudges(fb, monkeypatch, date(2026, 7, 16)) == []


def test_the_nudge_and_the_brief_agree_on_when_a_week_is_live(fb):
    """Two functions, one rule. They disagreed for two days of every week."""
    schedule = _week("2026-07-13")
    helmsmen = {"2026-07-13": {"name": "", "briefed": False}}
    for offset in range(4):
        today = date(2026, 7, 13) + timedelta(days=offset)
        brief_live = bool(fb.helmsman_brief_candidates(
            {"2026-07-13": {"briefed": False}}, today))
        gap_live = bool(fb.helmsman_gaps(schedule, helmsmen,
                                         on_or_after=today - timedelta(days=2)))
        assert brief_live == gap_live, f"they disagree on {today}"


# ============================================================
# 8. the pin failure reported as a success
# ============================================================

def test_the_cycle_invite_card_says_the_pin_failed(fb):
    """`cmd_sunday_preview` tracks the pin outcome; this path swallowed it and
    told the CEO 'Sent to the Tribe and pinned.' either way."""
    src = SRC.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.split("\n")
                     if not ln.lstrip().startswith("#"))
    invite = code[code.index("def _handle_cycle_invite_tap"):]
    invite = invite[:invite.index("\ndef ")]
    assert "pinned = False" in invite, "the pin outcome is not tracked"
    assert "except TelegramAPIError:\n            pass\n        _log_event(" not in invite
    assert "pin FAILED" in invite


def test_the_cycle_invite_event_records_whether_it_pinned(fb):
    src = SRC.read_text(encoding="utf-8")
    marker = 'cycle_end_invite_sent"'
    body = src[src.index(marker):src.index(marker) + 300]
    assert "pinned=pinned" in body


# ============================================================
# 9. the send loop with no already-sent guard
# ============================================================

def test_every_speaker_send_loop_asks_before_it_sends():
    """Three loops write dm-log rows; one of them read none of its own."""
    import ast

    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    for name in ("cmd_speaker_dms", "cmd_dayof_reminders", "cmd_email_backup"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        calls = {n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "_dm_already_sent" in calls, (
            f"{name} writes dm-log rows and reads none of them, so a cron "
            f"double-fire or a manual rerun sends the same people twice")


def test_a_rerun_of_the_dayof_job_sends_nobody_twice(fb, state, monkeypatch,
                                                     capsys):
    today = fb._today_local_date().isoformat()
    fb.save_state(fb.SCHEDULE, [{
        "session_date": today, "speaker_name": "Vesper Lynd",
        "speaker_username": "vesper", "week": 1, "day": "Wed", "slot": 1,
        "theme": "Signals",
    }])
    fb.save_state(fb.TRIBE_ROSTER, {"vesper": {
        "name": "Vesper Lynd", "telegram_user_id": 7, "active": True}})
    fb.save_state(fb.HELMSMEN, {})
    sends = []
    monkeypatch.setattr(fb, "get_bot", lambda: SimpleNamespace(
        send_dm=lambda uid, text: sends.append(uid)))
    monkeypatch.setattr(fb, "hc_ping", lambda *a, **k: None)
    monkeypatch.setattr(fb, "_zoom_url", lambda: "https://example.test/j")

    fb.cmd_dayof_reminders(SimpleNamespace(dry_run=False))
    fb.cmd_dayof_reminders(SimpleNamespace(dry_run=False))
    out = capsys.readouterr().out

    assert sends == [7], "the second run re-sent the same Zoom link"
    assert "already-sent-today=1" in out


# ============================================================
# 10. the opt-in gate that was weaker than the file's own word
# ============================================================

def _react(fb, monkeypatch, user_id):
    monkeypatch.setenv("FIRESIDE_LAUNCH_ANNOUNCEMENT_MSG_ID", "555")
    monkeypatch.setattr(fb, "_log_event", lambda *a, **k: None)
    fb._handle_message_reaction({
        "message_id": 555,
        "user": {"id": user_id, "username": "vesper"},
        "new_reaction": [{"type": "emoji", "emoji": "\U0001f9ed"}],
    })
    return fb.load_state(fb.OPT_INS) or {"helmsman": [], "wildcard": []}


def test_an_active_member_may_still_opt_in(fb, state, monkeypatch):
    fb.save_state(fb.TRIBE_ROSTER, {"vesper": {
        "name": "Vesper Lynd", "telegram_user_id": 7, "active": True}})
    opt = _react(fb, monkeypatch, 7)
    assert [x["username"] for x in opt["helmsman"]] == ["vesper"]


def test_a_deactivated_member_may_not_opt_in(fb, state, monkeypatch):
    """The finding: `_resolve_my_username` matches any row carrying the id and
    asks neither question the file's own `_is_authorized_user` asks."""
    fb.save_state(fb.TRIBE_ROSTER, {"vesper": {
        "name": "Vesper Lynd", "telegram_user_id": 7, "active": False}})
    assert _react(fb, monkeypatch, 7)["helmsman"] == []


def test_an_excluded_member_may_not_opt_in(fb, state, monkeypatch):
    fb.save_state(fb.TRIBE_ROSTER, {"vesper": {
        "name": "Vesper Lynd", "telegram_user_id": 7, "active": True,
        "excluded_from_fireside": True}})
    assert _react(fb, monkeypatch, 7)["helmsman"] == []


def test_a_member_who_lost_authorization_can_still_remove_their_opt_in(
        fb, state, monkeypatch):
    """Removal is deliberately keyed on user_id alone; the comment says so."""
    fb.save_state(fb.TRIBE_ROSTER, {"vesper": {
        "name": "Vesper Lynd", "telegram_user_id": 7, "active": False}})
    fb.save_state(fb.OPT_INS, {"helmsman": [{"user_id": 7, "username": "vesper"}],
                               "wildcard": []})
    monkeypatch.setenv("FIRESIDE_LAUNCH_ANNOUNCEMENT_MSG_ID", "555")
    monkeypatch.setattr(fb, "_log_event", lambda *a, **k: None)
    fb._handle_message_reaction({
        "message_id": 555,
        "user": {"id": 7, "username": "vesper"},
        "new_reaction": [],
    })
    assert fb.load_state(fb.OPT_INS)["helmsman"] == []


# ============================================================
# 11. the monitor that went quiet where the evidence was worst
# ============================================================

# `_health` used to carry a fourth patch:
#
#     monkeypatch.setattr(fb, "_webhook_status_line", lambda *a, **k: "",
#                         raising=False)
#
# `_webhook_status_line` exists NOWHERE in this repository -- `grep -rn` over
# every .py and .md returned that one line and nothing else. `cmd_health_check`
# reads its two webhook fields inline off `last_tick`; there is no helper to
# neutralise. `raising=False` is precisely what kept that invisible: it turns
# "the name you aimed at does not exist" into a silent new attribute nobody
# reads. MEASURED 2026-08-31: deleting the two lines changed no result (3
# passed, identical to baseline), which is the definition of a patch that binds
# a stranger. It is gone rather than repointed, and the webhook fields it was
# gesturing at are asserted for real in
# `test_a_fresh_tick_reports_the_webhook_evidence_it_has` below.
#
# `hc_ping` stays. Unlike the above it IS a real module attribute (imported at
# the top of fireside-bot.py) and neutralising it is what stops a unit test
# reaching the network if `cmd_health_check` ever gains a ping.

def _health(fb, monkeypatch):
    """Run cmd_health_check and return the (chat_id, message) tuples it SENT.

    Returning the arguments, not a count. The previous version appended the
    argument tuple and every caller then asserted bare list truthiness, so the
    tests measured only that SOME message left the process. MEASURED 2026-08-31:
    replacing the whole alert string with `XXXX MUTATED XXXX` in a scratch copy
    of scripts/fireside-bot.py left all three tests passing. A monitor whose
    test cannot tell "the daemon is down" from arbitrary text is not testing the
    monitor.
    """
    alerts = []
    monkeypatch.setattr(fb, "misha_user_id", lambda: 42)
    monkeypatch.setattr(fb, "get_bot", lambda: SimpleNamespace(
        send_message=lambda *a, **k: alerts.append(a)))
    monkeypatch.setattr(fb, "hc_ping", lambda *a, **k: None)
    fb.cmd_health_check(SimpleNamespace())
    return alerts


def _sole_alert(alerts):
    """The one alert, unpacked. Asserts the count before reading the content."""
    assert len(alerts) == 1, (
        f"expected exactly one alert DM, got {len(alerts)}: {alerts!r}")
    chat_id, message = alerts[0][0], alerts[0][1]
    assert chat_id == 42, f"the alert went to {chat_id!r}, not to Misha"
    return message


def test_an_absent_dm_log_alerts(fb, state, monkeypatch):
    """The strongest evidence that nothing is alive used to print a line to
    stdout and return, while the weaker case - a file with no ticks - alerted."""
    assert not (state / fb.DM_LOG).exists()

    msg = _sole_alert(_health(fb, monkeypatch))

    assert "no liveness tick ever recorded" in msg, (
        f"the alert does not say the daemon may be down: {msg!r}")
    assert "Bot may not be running" in msg


def test_a_dm_log_with_no_tick_still_alerts(fb, state, monkeypatch):
    (state / fb.DM_LOG).write_text(
        json.dumps({"dm_type": "2wk", "ts": "2026-08-01T10:00:00+04:00"}) + "\n",
        encoding="utf-8")

    msg = _sole_alert(_health(fb, monkeypatch))

    assert "no liveness tick ever recorded" in msg, (
        f"a dm-log carrying only non-tick rows must read as no tick: {msg!r}")


def test_a_fresh_tick_does_not_alert(fb, state, monkeypatch):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    (state / fb.DM_LOG).write_text(
        json.dumps({"dm_type": "poll-tick", "ts": now}) + "\n",
        encoding="utf-8")
    assert _health(fb, monkeypatch) == []


def _tick(state, fb, when, **extra):
    (state / fb.DM_LOG).write_text(
        json.dumps({"dm_type": "poll-tick", "ts": when.isoformat(), **extra}) + "\n",
        encoding="utf-8")


def test_the_thirty_minute_threshold_is_over_not_at(fb, state, monkeypatch):
    """The case ON the line. `cmd_health_check` computes `age > 30 min`, so a
    tick exactly 30 minutes old is healthy and one second older is not.

    The clock is frozen for this: reading the host clock would make the boundary
    a race, and a boundary that can only be approached from one side is not a
    boundary test.
    """
    from datetime import timedelta

    frozen = fb.local_now()
    monkeypatch.setattr(fb, "local_now", lambda: frozen)

    _tick(state, fb, frozen - timedelta(minutes=30))
    assert _health(fb, monkeypatch) == [], (
        "a tick exactly at the 30-minute threshold alerted; the check is "
        "`age > 30 min`, so the boundary value itself is healthy")

    _tick(state, fb, frozen - timedelta(minutes=30, seconds=1))
    msg = _sole_alert(_health(fb, monkeypatch))
    assert "threshold 30 min" in msg, (
        f"one second past the threshold must alert and name it: {msg!r}")
    assert "30 min ago" in msg


def test_an_unreadable_timestamp_alerts_as_unknown_not_healthy(
        fb, state, monkeypatch):
    """The third branch. A tick whose `ts` cannot be parsed means liveness is
    UNKNOWN; reporting that as healthy is the same silence this shard is about.
    """
    (state / fb.DM_LOG).write_text(
        json.dumps({"dm_type": "poll-tick", "ts": "not-a-timestamp"}) + "\n",
        encoding="utf-8")

    msg = _sole_alert(_health(fb, monkeypatch))

    assert "unreadable timestamp" in msg, (
        f"an unparseable tick must be reported, not swallowed: {msg!r}")
    assert "not the same as healthy" in msg


def test_a_fresh_tick_reports_the_webhook_evidence_it_has(
        fb, state, monkeypatch, capsys):
    """What the deleted `_webhook_status_line` patch was gesturing at.

    A fresh tick proves a process reached Telegram and nothing more; in webhook
    mode the handler is a DIFFERENT process. The command therefore prints the
    pending count and last webhook error rather than implying it checked the
    handler. Asserting the values, not merely that something printed.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    _tick(state, fb, now, pending_update_count=17,
          webhook_last_error="Wrong response from the webhook: 502")

    assert _health(fb, monkeypatch) == [], "a fresh tick must not alert"

    out = capsys.readouterr().out
    assert "pending_update_count=17" in out, (
        f"the pending count is the only signal that the webhook handler died "
        f"under a healthy heartbeat, and it was not printed: {out!r}")
    assert "Wrong response from the webhook: 502" in out, (
        f"the last webhook error was not printed: {out!r}")
    assert "does not prove the webhook handler is" in out, (
        f"the caveat that a tick is not handler liveness was not printed: {out!r}")
