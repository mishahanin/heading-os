"""Six fireside JSONL walks that died on the very line they promised to skip.

Every one of them is spelled the same way: open the file as text, iterate it,
and wrap only the `json.loads` in `except json.JSONDecodeError`.

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

The decode does not happen where the handler is. It happens inside `for line in
f`, one frame further out, and `UnicodeDecodeError` is a `ValueError` -- a
SIBLING of `json.JSONDecodeError`, not a member of it. So a single non-UTF-8
byte anywhere in the file raises straight past a handler written to skip exactly
that kind of damage. These are append-only ledgers that nothing prunes, so one
torn append is permanent: every later run dies the same way until somebody edits
the file by hand.

MEASURED 2026-09-01, driving each real function with a file holding one intact
record and one line of `b"\\xff\\xfe torn"`:

    site                                        before              after
    ---------------------------------------------------------------------------
    fireside_topics.load_ideas                  UnicodeDecodeError  the intact row
    fireside-bot._dm_already_sent               UnicodeDecodeError  True
    fireside-bot._load_swap_requests            UnicodeDecodeError  the intact rid
    fireside-bot.cmd_stats                      UnicodeDecodeError  prints its report
    fireside-bot.cmd_health_check               UnicodeDecodeError  prints its verdict

`cmd_health_check` is the worst of the five. It is a scheduled daemon job whose
whole purpose is to notice that the bot has stopped writing ticks, and it died
on the one corruption it exists to report.

The sixth site is the same expression's other half, and it is not a crash.
`fireside-bot._read_jsonl_rows` was widened to `except (OSError, UnicodeError)`
on 2026-09-01, which stops the raise, but it splits the decoded text with
`str.splitlines()`. That method breaks on eight characters a JSONL record does
not end at: U+000B, U+000C, U+001C, U+001D, U+001E, U+0085, U+2028 and U+2029.
`append_jsonl` writes with `json.dumps(..., ensure_ascii=False)`, which escapes
none of the last three, so a record carrying one of them was written as a single
valid line and then shredded into two halves that no longer parsed -- and the
`JSONDecodeError` clause dropped both halves in silence.

That one is reachable end to end and costs a real person a wrong email.
`cmd_email_backup` builds its `responded_user_ids` set from
`_read_jsonl_rows(sessions.jsonl)`, and `idea_submitted` is in its
ENGAGEMENT_EVENTS. U+2028 is what a browser paste produces for a line
separator, so a member who pasted their `/idea` from a web page had their
engagement row written, then made invisible, then was classed unresponsive and
mailed "I've sent you a few Telegram DMs ... but haven't seen a response". The
comment above that set records the same class of miss being fixed once already.

The fix in all six is `scripts/utils/jsonl_lines.jsonl_lines`, which already
existed for two council readers with these two defects: it reads bytes, splits
with `bytes.splitlines()` (which breaks on `\\n` and `\\r` only), and decodes each
line on its own, yielding None for one that will not decode so the caller can
say what it dropped. Strict decoding, not `errors="replace"`: these rows hold
Tribe members' names and their own words, and they are rendered back to the
Tribe, so mojibake would be shipped rather than caught.

Run: .venv/bin/python -m pytest \
    tests/test_six_fireside_jsonl_walks_that_died_on_the_line_they_skip.py
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import fireside_topics as ft  # noqa: E402

# One intact record, then a line that is not UTF-8 at all. 0xff is never a legal
# UTF-8 start byte, so this is undecodable rather than merely unparseable.
TORN = b"\xff\xfe torn\n"

# The separators `json.dumps(..., ensure_ascii=False)` leaves raw and
# `str.splitlines()` then cuts on. Written as code points so no source file in
# this repository has to carry one literally.
SPLITLINES_ONLY = {
    "U+2028 LINE SEPARATOR": chr(0x2028),
    "U+2029 PARAGRAPH SEPARATOR": chr(0x2029),
    "U+0085 NEXT LINE": chr(0x0085),
}


@pytest.fixture(scope="module")
def fb():
    """fireside-bot.py, loaded by path (the filename is hyphenated)."""
    spec = importlib.util.spec_from_file_location(
        "fireside_bot_jsonl_decode", str(ROOT / "scripts" / "fireside-bot.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def state(fb, tmp_path, monkeypatch):
    """Every state path under tmp_path.

    Both `state_dir` and `state_path` are redirected. The module-level constants
    these functions inherit resolve at the operator's live overlay, so pinning
    one and not the other is how a fireside test starts writing real data.
    """
    monkeypatch.setattr(fb, "state_dir", lambda p=tmp_path: p)
    monkeypatch.setattr(fb, "state_path", lambda name: tmp_path / name)
    monkeypatch.setattr(fb, "hc_ping", lambda *a, **k: None)
    monkeypatch.setattr(fb, "log_error", lambda *a, **k: None)
    return tmp_path


def _write(path: Path, first: dict, tail: bytes = TORN) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        json.dumps(first, ensure_ascii=False).encode("utf-8") + b"\n" + tail)


# ---------------------------------------------------------------------------
# 1. fireside_topics.load_ideas
# ---------------------------------------------------------------------------

def test_load_ideas_keeps_the_intact_ideas_beside_an_undecodable_line(tmp_path):
    """Its docstring says "Corrupt lines are skipped". One bad byte raised."""
    ft.append_idea(tmp_path, now_iso="2026-06-25T10:00:00+04:00", user_id=1,
                   username="vlynd", name="Vesper Lynd", text="a first idea",
                   cycle=1)
    with open(ft._ideas_path(tmp_path), "ab") as f:
        f.write(TORN)
    ft.append_idea(tmp_path, now_iso="2026-06-26T10:00:00+04:00", user_id=2,
                   username="fleiter", name="Felix Leiter", text="a second idea",
                   cycle=1)

    got = ft.load_ideas(tmp_path)
    assert [i["text"] for i in got] == ["a first idea", "a second idea"], (
        "the line after the torn one is intact and must survive with it")


def test_load_ideas_keeps_a_record_carrying_a_line_separator(tmp_path):
    """`str.splitlines()` shreds these; `bytes.splitlines()` does not."""
    for label, ch in SPLITLINES_ONLY.items():
        d = tmp_path / label.split()[0]
        d.mkdir()
        ft.append_idea(d, now_iso="2026-06-25T10:00:00+04:00", user_id=1,
                       username="vlynd", name="Vesper Lynd",
                       text=f"pasted{ch}from a page", cycle=1)
        got = ft.load_ideas(d)
        assert len(got) == 1, f"{label} shredded the record: {got}"
        assert got[0]["text"] == f"pasted{ch}from a page", label


def test_load_ideas_still_skips_a_line_that_decodes_but_is_not_json(tmp_path):
    """The anchor. The fix must not turn "skip corrupt" into "keep everything"."""
    ft.append_idea(tmp_path, now_iso="2026-06-25T10:00:00+04:00", user_id=1,
                   username="vlynd", name="Vesper Lynd", text="kept", cycle=1)
    with open(ft._ideas_path(tmp_path), "a", encoding="utf-8") as f:
        f.write("{not json\n")
    assert [i["text"] for i in ft.load_ideas(tmp_path)] == ["kept"]


def test_load_ideas_over_a_wholly_undecodable_file_is_empty_not_a_raise(tmp_path):
    ft._ideas_path(tmp_path).write_bytes(TORN)
    assert ft.load_ideas(tmp_path) == []


# ---------------------------------------------------------------------------
# 2. fireside-bot._dm_already_sent  (the email-backup duplicate guard)
# ---------------------------------------------------------------------------

def test_dm_already_sent_still_sees_the_row_beside_an_undecodable_line(fb, state):
    """A raise here takes the whole email-backup run down; a silent False mails
    a second identical letter to a real person. Both are worse than skipping."""
    path = state / fb.DM_LOG
    _write(path, {"dm_type": "email-backup", "speaker_username": "vlynd",
                  "session_date": "2026-09-21", "delivered": True,
                  "ts": "2026-09-14T10:00:00+04:00"})
    # Argument order is (path, speaker_username, dm_type, session_date).
    assert fb._dm_already_sent(path, "vlynd", "email-backup", "2026-09-21") is True


def test_dm_already_sent_is_false_when_the_only_readable_row_does_not_match(
        fb, state):
    """The negative direction, so the test above cannot be satisfied by a
    function that returns True unconditionally."""
    path = state / fb.DM_LOG
    _write(path, {"dm_type": "email-backup", "speaker_username": "someone-else",
                  "session_date": "2026-09-21", "delivered": True})
    assert fb._dm_already_sent(path, "vlynd", "email-backup", "2026-09-21") is False


# ---------------------------------------------------------------------------
# 3. fireside-bot._load_swap_requests
# ---------------------------------------------------------------------------

def test_load_swap_requests_keeps_the_intact_events(fb, state):
    path = state / fb.SWAP_REQUESTS_LOG
    _write(path, {"rid": "rid123", "status": "initiated"})
    got = fb._load_swap_requests()
    assert list(got) == ["rid123"], got


# ---------------------------------------------------------------------------
# 4. fireside-bot.cmd_stats
# ---------------------------------------------------------------------------

def test_cmd_stats_reports_over_a_torn_log_instead_of_dying(fb, state, capsys,
                                                            monkeypatch):
    # The report lands in `stats_dir()`, which resolves under the operator's
    # data overlay. `scripts/utils/overlay_write_guard.py` refuses that from a
    # test, correctly, so the destination is redirected rather than the guard
    # relaxed.
    out = state / "stats"
    out.mkdir()
    monkeypatch.setattr(fb, "require_writable_stats_dir", lambda: out)
    _write(state / fb.SESSIONS_LOG,
           {"event_type": "session_logged", "shared": "vlynd", "no_shows": ""})
    _write(state / fb.DM_LOG,
           {"dm_type": "2wk", "speaker_username": "vlynd",
            "session_date": "2026-09-21", "delivered": True})
    fb.load_state = lambda name: {fb.SCHEDULE: [], fb.TRIBE_ROSTER: {}}.get(name)
    fb.cmd_stats(argparse.Namespace())
    assert capsys.readouterr().out.strip(), "the report printed nothing at all"


# ---------------------------------------------------------------------------
# 5. fireside-bot.cmd_health_check
# ---------------------------------------------------------------------------

def test_cmd_health_check_still_reads_the_tick_beside_a_torn_line(fb, state,
                                                                  capsys):
    """The monitor died on the corruption it exists to notice."""
    from datetime import timedelta

    recent = (fb.local_now() - timedelta(minutes=5)).isoformat()
    _write(state / fb.DM_LOG, {"dm_type": "poll-tick", "ts": recent})
    fb.load_state = lambda name: {fb.SCHEDULE: [], fb.TRIBE_ROSTER: {}}.get(name)
    fb.cmd_health_check(argparse.Namespace())
    out = capsys.readouterr().out
    assert out.strip(), "the health check printed nothing at all"
    assert "missing" not in out, (
        "the tick is present and readable; reporting it missing is the "
        "monitor's other failure mode")


# ---------------------------------------------------------------------------
# 6. fireside-bot._read_jsonl_rows -- the shredding half, end to end
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", sorted(SPLITLINES_ONLY))
def test_read_jsonl_rows_keeps_a_record_carrying_a_line_separator(
        fb, state, label):
    ch = SPLITLINES_ONLY[label]
    path = state / fb.SESSIONS_LOG
    _write(path, {"event_type": "idea_submitted", "user_id": 4242,
                  "text": f"pasted{ch}from a page"}, tail=b"")
    rows = fb._read_jsonl_rows(path)
    assert [r.get("user_id") for r in rows] == [4242], (
        f"{label} shredded the row into halves that no longer parse: {rows}")


def test_read_jsonl_rows_keeps_the_intact_rows_beside_an_undecodable_line(
        fb, state):
    """The widening to `except UnicodeError` stopped the raise by returning the
    file EMPTY, which loses every intact row for one bad byte."""
    path = state / fb.SESSIONS_LOG
    _write(path, {"event_type": "idea_submitted", "user_id": 4242})
    assert [r.get("user_id") for r in fb._read_jsonl_rows(path)] == [4242]


def test_read_jsonl_rows_still_drops_a_non_dict_line(fb, state):
    """Anchor for the two above: the reader's own filters must still apply."""
    path = state / fb.SESSIONS_LOG
    path.write_text('["not", "an", "object"]\n{"event_type": "kept"}\n',
                    encoding="utf-8")
    assert [r.get("event_type") for r in fb._read_jsonl_rows(path)] == ["kept"]


def test_an_engaged_member_who_pasted_their_idea_is_not_mailed_as_silent(
        fb, state, monkeypatch, capsys):
    """The consequence, driven through the command that causes it.

    `cmd_email_backup` mails a speaker who "hasn't responded". A member whose
    `/idea` carried a browser-pasted U+2028 had their engagement row written and
    then made invisible, so the one member who HAD answered got the letter
    saying they had not.
    """
    from datetime import timedelta

    session_date = (fb._today_local_date() + timedelta(days=3)).isoformat()
    schedule = [{
        "week": 1, "session_date": session_date, "day": "Mon", "slot": 1,
        "theme": "A pasted idea", "speaker_name": "Vesper Lynd",
        "speaker_username": "vlynd", "swapped_with": None,
        "no_show": False, "completed": False,
    }]
    roster = {"vlynd": {"name": "Vesper Lynd", "email": "vlynd@example.invalid",
                        "telegram_user_id": 4242, "active": True}}
    _write(state / fb.SESSIONS_LOG,
           {"event_type": "idea_submitted", "user_id": 4242,
            "text": f"pasted{chr(0x2028)}from a page"}, tail=b"")

    monkeypatch.setattr(fb, "load_state", lambda name: {
        fb.SCHEDULE: schedule, fb.TRIBE_ROSTER: roster}.get(name))
    monkeypatch.setattr(fb, "_log_dm", lambda *a, **k: None)

    spawned = []

    class _Result:
        returncode = 0
        stderr = ""

    def _refuse_to_send(cmd, **kwargs):
        spawned.append(cmd)
        return _Result()

    monkeypatch.setattr("subprocess.run", _refuse_to_send)
    fb.cmd_email_backup(argparse.Namespace())

    assert spawned == [], (
        "the member's engagement row is on disk and readable, so no backup "
        f"letter may be sent; it tried to mail {spawned}")
    assert "sent=0" in capsys.readouterr().out
