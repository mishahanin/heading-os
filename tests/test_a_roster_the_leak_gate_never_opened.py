"""Shard scripts-06-p1: the fireside bot, and the guard that never read its roster.

Nine findings, one theme. Each of these tools printed a confident sentence over
something it had not actually established.

The header said "Five findings" until 2026-09-02 and the file already carried
nine, in seven section groups: the five enumerated below, then the log-session
contract the `/fireside` help got wrong, then three the Kimi k3 read of the same
file turned up (a duplicate-handle sheet swallowed into an empty roster, the
spelled-out vice-president titles the seniority test missed, and a routine
membership event filed as an error). A count is a claim about coverage, so a
reader reconciling this shard against the audit record was told four of its
findings were not pinned here when every one of them is.

1. THE LEAK GATE HARVESTED NO TRIBE MEMBER AT ALL. `content_denylist` exists
   because the six structural layers check WHERE a file routes and never WHAT is
   inside it. It harvested people from `crm/contacts/` and `admin/executives.json`,
   and the block meant to cover the Tribe read `config/fireside-schedule.json`
   looking for member dicts -- a shape that file has never had, because its
   speakers are plain strings under `weeks[].mon` / `weeks[].wed`. Measured on the
   live overlay 2026-08-25: the `handle`, `handle-name` and `telegram-id`
   categories held ZERO tokens against a 58-member roster. Two real handles and
   a real full name sat in a tracked engine test, in a public repo, and
   `content-guard --all --strict` reported the surface clean.

2. THE SWAP DM OPENED ON A COMMA. `_format_dm_date(date, day)` interpolates `day`
   straight into "{day}, {d} {mon}". Three call sites pass no day: `_handle_a_tap`
   reads `ctx["a_day"]`, which `_swap_kickoff_for_a` never writes, and
   `_handle_b_tap` passes "" twice outright. B was asked to trade slots on the
   strength of ", 8 Jun".

3. THE GAP DETECTOR CREDITED THE WRONG MEMBER. `speaker_gaps` matched on display
   name, so two active members sharing one reopened the exact hole the function
   exists to close: both read as covered while one held no slot all cycle.
   `build_roster_by_name`, in the same file, refuses to guess between them.

4. EMAIL-BACKUP DROPPED THE PEOPLE IT IS FOR. A speaker with no e-mail in the
   sheet, or one whose schedule row names a handle the roster lacks, fell out of
   the loop counted nowhere, under a summary line reading `sent=N skipped=M`.

5. A MALFORMED ROSTER RECORD KILLED A SEND LOOP MID-FLIGHT. `roster_entry["name"]
   .split()[0]` raised on a missing or blank Name, after earlier addressees had
   their mail and before the summary line was ever reached.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.content_denylist import build_denylist  # noqa: E402


@pytest.fixture(scope="module")
def fb():
    """Load fireside-bot.py as a module (hyphen in filename)."""
    path = ROOT / "scripts" / "fireside-bot.py"
    spec = importlib.util.spec_from_file_location("fireside_bot", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _state_in_tmp(fb, tmp_path, monkeypatch):
    """Point `state_dir` at a tmp_path for every test in the module.

    Nine tests below redirect it by hand and twenty-seven do not, which is the
    defect this file's own title is about, applied to its own fixtures.
    Redirecting once here is what a per-test line cannot be: a default the next
    test inherits without knowing it needs one. Tests that set the constant
    themselves still win, because monkeypatch inside the body runs after this.
    """
    state = tmp_path / "fireside-state"
    state.mkdir(exist_ok=True)
    monkeypatch.setattr(fb, "state_dir", lambda p=state: p)
    return state


# ============================================================
# 1. The leak gate and the roster it never opened
# ============================================================

def _overlay(tmp_path, *, roster=None, weeks=None, roster_file=True):
    """A minimal DATA overlay carrying a fireside roster and a cycle config."""
    data = tmp_path / ".heading-os-data"
    (data / "config").mkdir(parents=True, exist_ok=True)
    cfg = {"cycle": 3, "cycle_1_start_monday": "2026-09-21",
           "weeks": weeks if weeks is not None
           else [{"week": 1, "theme": "Origins",
                  "mon": ["Qorvath Lune"], "wed": ["Sethra Vaig"]}]}
    (data / "config" / "fireside-schedule.json").write_text(
        json.dumps(cfg), encoding="utf-8")
    if roster_file:
        state = data / "datastore" / "operations" / "tribe" / "fireside-state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "tribe-roster.json").write_text(
            json.dumps(roster if roster is not None else {
                "zaltrix": {"name": "Ordo Zaltrix", "telegram_user_id": 774119280,
                            "active": True},
            }), encoding="utf-8")
    return data


def _cats(dl):
    out = {}
    for cat in dl.tokens.values():
        out[cat] = out.get(cat, 0) + 1
    return out


def test_a_tribe_handle_is_a_denylist_token(tmp_path):
    """The finding: this category held zero tokens against a 58-member roster."""
    dl = build_denylist(_overlay(tmp_path))
    assert "zaltrix" in dl.tokens
    assert dl.tokens["zaltrix"] == "handle"


def test_a_tribe_members_full_name_is_a_denylist_token(tmp_path):
    dl = build_denylist(_overlay(tmp_path))
    assert dl.tokens.get("ordo zaltrix") == "handle-name"


def test_a_tribe_members_telegram_id_is_a_denylist_token(tmp_path):
    """Ids bypass the length/alpha gate, exactly as the config harvest does."""
    dl = build_denylist(_overlay(tmp_path))
    assert dl.tokens.get("774119280") == "telegram-id"


def test_the_handle_categories_are_not_empty_for_a_populated_roster(tmp_path):
    """A category that exists and counts zero is the shape of the whole defect."""
    cats = _cats(build_denylist(_overlay(tmp_path)))
    assert cats.get("handle", 0) >= 1
    assert cats.get("handle-name", 0) >= 1
    assert cats.get("telegram-id", 0) >= 1


def test_a_cycle_speaker_named_as_a_plain_string_is_harvested(tmp_path):
    """The real fireside-schedule.json shape: speakers are strings, not dicts."""
    dl = build_denylist(_overlay(tmp_path))
    assert dl.tokens.get("qorvath lune") == "schedule-speaker"
    assert dl.tokens.get("sethra vaig") == "schedule-speaker"


def test_both_weekdays_of_the_cycle_config_are_read(tmp_path):
    """Harvesting only `mon` would leave every Wednesday speaker unguarded."""
    dl = build_denylist(_overlay(tmp_path, weeks=[
        {"week": 1, "theme": "t", "mon": ["Mona Vex"], "wed": ["Wenda Kroll"]}]))
    assert "mona vex" in dl.tokens
    assert "wenda kroll" in dl.tokens


def test_a_cycle_speakers_bare_given_name_stays_out_of_the_default_gate(tmp_path):
    """58 members is far past the six-name exec roster promoted into the default.

    Their given names collide with ordinary English, so the bare form stays
    behind `strict`, matching the policy this harvest replaces.
    """
    dl = build_denylist(_overlay(tmp_path))
    assert "qorvath" not in dl.tokens
    assert "lune" not in dl.tokens


def test_strict_mode_does_add_the_bare_name_words(tmp_path):
    """Presence, not category: a name-word may re-label a token `_add` already
    holds (here "zaltrix" is both a handle and a family name), and which of the
    two labels survives is last-writer detail the gate never reads."""
    roster = {"vandrek": {"name": "Merrick Vandrel", "telegram_user_id": 5,
                          "active": True}}
    default = build_denylist(_overlay(tmp_path, roster=roster))
    strict = build_denylist(_overlay(tmp_path, roster=roster), strict=True)
    for word in ("merrick", "vandrel", "qorvath"):
        assert word not in default.tokens, word
        assert word in strict.tokens, word


def test_the_gate_flags_a_tribe_handle_inside_engine_text(tmp_path):
    dl = build_denylist(_overlay(tmp_path))
    hits = dl.scan_text('roster = {"zaltrix": {"name": "Ordo Zaltrix"}}')
    matched = {m.lower() for _, m, _ in hits}
    assert "zaltrix" in matched
    assert "ordo zaltrix" in matched


def test_an_inline_suppression_still_silences_a_tribe_hit(tmp_path):
    """The `content-guard: ok` convention must reach the new categories too."""
    dl = build_denylist(_overlay(tmp_path))
    assert dl.scan_text('x = "zaltrix"  # content-guard: ok reason') == []


def test_a_published_contributor_handle_is_never_flagged(tmp_path):
    """Both HANDLES are credited in CHANGELOG.md and are keys in the private roster.

    Without the allowlist the roster harvest reads the repo's own published
    attribution as a leak: measured, one matches 61 tracked files.

    The handles are load-bearing, because they are the exact keys the allowlist
    holds: swap them for invented ones and this test asserts nothing. The human
    NAMES beside them are not, so the second one is a placeholder here. The
    operator's law, restated 2026-08-26, is that only his own name and 31C may
    appear in the engine, and a test fixture is the easiest place to honour it.
    """
    dl = build_denylist(_overlay(tmp_path, roster={
        "mishahanin": {"name": "Misha Hanin", "telegram_user_id": 1, "active": True},
        "mmaatuq": {"name": "Marlow Carter", "telegram_user_id": 2, "active": True},
    }))
    assert "mishahanin" not in dl.tokens
    assert "mmaatuq" not in dl.tokens
    assert dl.scan_text("see github.com/mishahanin and github.com/mmaatuq") == []


def test_a_missing_roster_file_degrades_quietly(tmp_path):
    """A public clone has no overlay roster; absence is normal, not a failure."""
    dl = build_denylist(_overlay(tmp_path, roster_file=False))
    assert not dl.degraded
    assert "zaltrix" not in dl.tokens


def test_an_unreadable_roster_marks_the_denylist_degraded(tmp_path):
    """Corrected 2026-08-27. This asserted `not dl.degraded` on a stated reason
    that does not hold: "a degraded list makes the whole gate no-op".

    It is true of ONE consumer and false of the one that matters. The advisory
    CLI `scripts/content-guard.py` does skip and exit 0 on a degraded list, but
    it names the cause while doing so. The UNBYPASSABLE push wall,
    `engine_content_scan` in `scripts/push-all.py`, reads the same flag the
    opposite way: overlay present AND degraded means REFUSE TO PUSH, exit 2,
    logged as a denial. `egress_proof.classify` likewise returns
    EGRESS_UNVERIFIABLE.

    So the old assertion pinned the gate OPEN. A corrupt tribe-roster.json meant
    the Tribe's handles, names and Telegram IDs silently left the token set, and
    a push carrying one of them in an engine-routed file passed the wall clean.
    That is the operator law's exact failure mode, so the harvester now lets the
    error reach `build_denylist`, which prints the cause and degrades.
    """
    data = _overlay(tmp_path)
    p = data / "datastore" / "operations" / "tribe" / "fireside-state" / "tribe-roster.json"
    p.write_text("{not json", encoding="utf-8")
    dl = build_denylist(data)
    assert "zaltrix" not in dl.tokens
    # The cycle config is a separate source and must survive its neighbour:
    # `degraded` is the signal, never the token count.
    assert "qorvath lune" in dl.tokens
    assert dl.degraded


def test_a_roster_record_without_a_telegram_id_still_yields_its_handle(tmp_path):
    dl = build_denylist(_overlay(tmp_path, roster={
        "unbound": {"name": "Unbound Person", "telegram_user_id": None, "active": True}}))
    assert "unbound" in dl.tokens
    assert dl.tokens.get("unbound person") == "handle-name"


def test_a_boolean_is_never_recorded_as_a_telegram_id(tmp_path):
    """`isinstance(True, int)` is True in Python, so the bool check comes first.

    The id path writes `tokens[str(uid)]` directly, bypassing `_add` -- so the key
    a bool would leave is "True", capitalised and unnormalised. Asserting on "1"
    or "true" tested nothing, which is how this survived its own mutation.
    """
    dl = build_denylist(_overlay(tmp_path, roster={
        "flagged": {"name": "Flagged Person", "telegram_user_id": True, "active": True}}))
    assert str(True) not in dl.tokens
    assert "True" not in dl.tokens
    assert not any(v == "telegram-id" for v in dl.tokens.values())


def test_the_live_engine_tree_carries_no_tribe_identity():
    """The sweep this guard was written to make possible, run against the tree.

    Skipped on a clone with no DATA overlay: the denylist is built in memory from
    the private overlay and is never persisted into the engine, so a public CI
    runner has nothing to compare against.
    """
    from scripts.utils.workspace import get_data_root
    dl = build_denylist(get_data_root())
    if dl.degraded or not dl.tokens:
        pytest.skip("no DATA overlay on this host; the gate no-ops by design")
    tribe = {"handle", "handle-name", "telegram-id", "schedule-speaker"}
    if not any(c in tribe for c in dl.tokens.values()):
        pytest.skip("overlay carries no fireside roster")
    offenders = {}
    for rel in ("scripts/fireside-bot.py", "tests/test_fireside_speaker_gaps.py",
                "tests/test_fireside_email_backup.py",
                "tests/test_a_roster_the_leak_gate_never_opened.py"):
        hits = [h for h in dl.scan_text((ROOT / rel).read_text(encoding="utf-8"))
                if h[2] in tribe]
        if hits:
            offenders[rel] = [(n, cat) for n, _, cat in hits]
    assert offenders == {}, f"real Tribe identities in engine files: {offenders}"


# ============================================================
# 2. The swap DM that opened on a comma
# ============================================================

def test_a_date_with_no_weekday_still_names_one(fb):
    assert fb._format_dm_date("2026-06-08", "") == "Mon, 8 Jun"


def test_a_date_label_never_opens_on_a_comma(fb):
    """Both spellings of "no day", each handed over as it stands.

    The call was `_format_dm_date("2026-06-08", day or "")`, which turned the
    `None` case into the `""` case one character before the boundary: the loop
    read as two cases and ran one. A formatter narrowed to `day.strip() or ...`
    would raise `AttributeError` on a real `None` and this test would still have
    passed. `None` is not hypothetical here - `session_date`/`day` come out of
    `config/fireside-schedule.json`, where `"day": null` is a legal value that
    arrives at the call site as `c["day"]`, and `_handle_a_tap` spells its own
    read `ctx.get("a_day") or ""` precisely because the key can be absent.
    """
    for day in ("", None):
        assert not fb._format_dm_date("2026-06-08", day).startswith(",")


def test_a_supplied_weekday_still_wins(fb):
    """The schedule's own day label stays authoritative when it is present.

    2026-06-08 is a Monday, so a supplied "Wed" DISAGREES with the date. That is
    the only shape that can tell "the label wins" from "the date is read": an
    agreeing pair passes either way, which is how the first version of this test
    proved nothing. Only the BLANK case falls back to the date.
    """
    assert fb._format_dm_date("2026-06-08", "Wed") == "Wed, 8 Jun"
    assert fb._format_dm_date("2026-06-10", "Wed") == "Wed, 10 Jun"


def test_the_derived_weekday_matches_the_date(fb):
    assert fb._format_dm_date("2026-06-10", "") == "Wed, 10 Jun"


def test_the_day_of_month_is_not_zero_padded(fb):
    assert fb._format_dm_date("2026-06-08", "") == "Mon, 8 Jun"


def test_the_no_candidate_path_logs_the_outcome_it_reached(fb, monkeypatch):
    """What this path actually promises, asserted instead of asserted-at.

    Renamed and rewritten 2026-09-02. It was
    `test_the_swap_kickoff_records_a_days_own_slot_day` over the single
    assertion `assert logged`, which is true of ANY log line and so measured
    nothing its name promised. The name was wrong twice over: nothing writes
    `a_day` anywhere in the module (see the test below), and a bare
    truthiness check on a list could not have seen it if something did.

    The contract this path really carries is the one the CEO acts on: an
    outcome the operator can filter for, the member's handle, and a manual
    hand-off DM naming the exact slot to move. All three are asserted here.
    """
    today = fb._today_local_date()
    a_date = (today + timedelta(days=7)).isoformat()
    schedule = [{"cycle": 1, "week": 1, "session_date": a_date, "day": "Mon",
                 "slot": 1, "theme": "t", "speaker_name": "A Person",
                 "speaker_username": "aperson"}]
    monkeypatch.setattr(fb, "load_state",
                        lambda n: schedule if n == fb.SCHEDULE else {})
    monkeypatch.setattr(fb, "find_swap_candidates", lambda *a, **k: [])
    monkeypatch.setattr(fb, "misha_user_id", lambda: 77)
    logged = []
    monkeypatch.setattr(fb, "_log_event", lambda *a, **k: logged.append((a, k)))

    sent = []

    class _Bot:
        def send_message(self, uid, text, **k):
            sent.append((uid, text))
            return {"message_id": 1}

    fb._swap_kickoff_for_a(_Bot(), 55, "aperson")

    assert logged, "the no-candidate path must still record the request"
    (event, *_), kwargs = logged[0]
    assert event == "swap_requested"
    assert kwargs["outcome"] == "no_candidates", (
        "the outcome is what separates this path from a served swap in the "
        f"event log; got {kwargs.get('outcome')!r}")
    assert kwargs["username"] == "aperson"

    to_misha = [text for uid, text in sent if uid == 77]
    assert to_misha, "nobody told the CEO a manual swap is waiting"
    assert a_date in to_misha[0] and "#1" in to_misha[0], (
        "the hand-off DM must name the slot to move, or it is a notification "
        f"with nothing to act on: {to_misha[0]!r}")


def test_nothing_in_the_module_writes_the_a_day_key(fb):
    """`_handle_a_tap` reads `ctx.get("a_day")`; no writer exists.

    The read is harmless because `_format_dm_date` derives the weekday from
    the date when the day is blank, and the label is correct either way. It is
    pinned rather than deleted because `.get("a_day") or ""` READS as a
    default with a value behind it, and that appearance is what kept the
    absence invisible through one audit. If a writer ever lands, this test
    fails and the reader stops being phantom; until then the source says so.
    """
    src = (Path(fb.__file__).read_text(encoding="utf-8"))
    tree = ast.parse(src)
    writes = []
    for node in ast.walk(tree):
        # ctx["a_day"] = ... and {"a_day": ...} are the two shapes a writer
        # would take in this module; both are asked for.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value == "a_day"):
                    writes.append(node.lineno)
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value == "a_day":
                    writes.append(node.lineno)
    assert writes == [], (
        "something now writes `a_day`; the reader at `_handle_a_tap` is no "
        f"longer phantom, so update its comment and this test: lines {writes}")
    assert "a_day" in src, "the phantom read is gone; delete this guard with it"


# ============================================================
# 3. The gap detector that credited the wrong member
# ============================================================

def _sched(*rows):
    """rows: (speaker_name, speaker_username_or_None)."""
    return [{"session_date": "2026-09-21", "day": "Mon", "slot": i,
             "speaker_name": n, "speaker_username": u}
            for i, (n, u) in enumerate(rows, start=1)]


def _two_alexes():
    return {"alexk": {"name": "Alex Kim", "active": True},
            "akim": {"name": "Alex Kim", "active": True}}


def test_a_shared_display_name_no_longer_covers_both_members(fb):
    gaps = fb.speaker_gaps(_two_alexes(), _sched(("Alex Kim", "alexk")))
    assert gaps == ["Alex Kim (@akim)"]


def test_the_member_who_holds_the_slot_is_not_a_gap(fb):
    gaps = fb.speaker_gaps(_two_alexes(), _sched(("Alex Kim", "alexk")))
    assert "Alex Kim (@alexk)" not in gaps


def test_both_are_covered_when_both_hold_a_slot(fb):
    assert fb.speaker_gaps(
        _two_alexes(), _sched(("Alex Kim", "alexk"), ("Alex Kim", "akim"))) == []


def test_the_handle_match_ignores_case_on_the_roster_side(fb):
    """Roster keys keep the sheet's casing; schedule rows may not."""
    roster = {"AlexK": {"name": "Alex Kim", "active": True}}
    assert fb.speaker_gaps(roster, _sched(("Alex Kim", "alexk"))) == []


def test_the_handle_match_ignores_case_on_the_schedule_side(fb):
    """Both sides need lowering, and one fixture can only ever prove one of them.

    A roster-uppercase case passes even when the SCHEDULE side stops lowering,
    because the fixture's schedule handle was already lowercase.
    """
    roster = {"alexk": {"name": "Alex Kim", "active": True}}
    assert fb.speaker_gaps(roster, _sched(("Alex Kim", "AlexK"))) == []


def test_the_handle_match_ignores_case_on_both_sides_at_once(fb):
    roster = {"AlexK": {"name": "Alex Kim", "active": True}}
    assert fb.speaker_gaps(roster, _sched(("Alex Kim", "ALEXK"))) == []


def test_an_unbound_row_still_falls_back_to_the_name(fb):
    """`build_schedule` writes speaker_username=None when a roster lookup misses.

    That row carries nothing but a name, so a name is all it can be matched on.
    """
    roster = {"solo": {"name": "Solo Speaker", "active": True}}
    assert fb.speaker_gaps(roster, _sched(("Solo Speaker", None))) == []


def test_an_unbound_row_does_not_cover_a_differently_named_member(fb):
    roster = {"other": {"name": "Other Person", "active": True}}
    assert fb.speaker_gaps(roster, _sched(("Solo Speaker", None))) == \
        ["Other Person (@other)"]


def test_a_bound_row_does_not_cover_by_name_alone(fb):
    """The bound row names @alexk; @akim must not ride on the display name."""
    roster = {"akim": {"name": "Alex Kim", "active": True}}
    assert fb.speaker_gaps(roster, _sched(("Alex Kim", "alexk"))) == \
        ["Alex Kim (@akim)"]


def test_an_empty_schedule_still_makes_everyone_a_gap(fb):
    assert fb.speaker_gaps({"a": {"name": "A Person", "active": True}}, []) == \
        ["A Person (@a)"]


def test_an_excluded_member_is_still_never_a_gap(fb):
    roster = {"out": {"name": "Excluded Person", "active": True,
                      "excluded_from_fireside": True}}
    assert fb.speaker_gaps(roster, _sched(("Someone", "someone"))) == []


def test_an_inactive_member_is_still_never_a_gap(fb):
    roster = {"gone": {"name": "Departed Person", "active": False}}
    assert fb.speaker_gaps(roster, _sched(("Someone", "someone"))) == []


def test_surrounding_whitespace_on_a_schedule_handle_is_stripped(fb):
    roster = {"spaced": {"name": "Spaced Person", "active": True}}
    assert fb.speaker_gaps(roster, _sched(("Spaced Person", " spaced "))) == []


def test_surrounding_whitespace_on_a_roster_key_is_stripped(fb):
    """`bootstrap` writes stripped keys, but this file is hand-edited over SSH.

    `cmd_helmsman` was written because assignment used to be exactly that, so a
    roster carrying a stray space in a key is a real shape, not a hypothetical.
    Putting the space only on the schedule side leaves the roster-side strip
    unexercised.
    """
    roster = {" spaced ": {"name": "Spaced Person", "active": True}}
    assert fb.speaker_gaps(roster, _sched(("Spaced Person", "spaced"))) == []


# ============================================================
# 4 + 5. email-backup: what it dropped, and what killed it
# ============================================================

class _Ok:
    returncode = 0
    stderr = ""


def _email_run(fb, tmp_path, monkeypatch, roster, *, days=3, extra_rows=()):
    """Drive cmd_email_backup over one session `days` out. Returns (spawned, out)."""
    today = fb._today_local_date()
    session_date = (today + timedelta(days=days)).isoformat()
    schedule = [{"week": 1, "session_date": session_date, "day": "Mon", "slot": i,
                 "theme": "A theme", "speaker_name": n, "speaker_username": u}
                for i, (n, u) in enumerate(
                    [(r["name"], h) for h, r in roster.items()] + list(extra_rows),
                    start=1)]
    monkeypatch.setattr(fb, "state_path", lambda name: tmp_path / name)
    monkeypatch.setattr(fb, "load_state",
                        lambda n: {fb.SCHEDULE: schedule, fb.TRIBE_ROSTER: roster}.get(n))
    monkeypatch.setattr(fb, "_log_dm", lambda *a, **k: None)
    spawned = []
    monkeypatch.setattr("subprocess.run",
                        lambda cmd, **k: (spawned.append(cmd), _Ok())[1])
    return schedule, spawned


def test_a_speaker_with_no_email_is_counted_not_dropped(fb, tmp_path, monkeypatch, capsys):
    roster = {"noemail": {"name": "No Mail", "email": "", "telegram_user_id": 7,
                          "active": True}}
    _email_run(fb, tmp_path, monkeypatch, roster)
    fb.cmd_email_backup(fb.argparse.Namespace())
    out = capsys.readouterr().out
    assert "no-email=1" in out
    assert "@noemail" in out


def test_a_speaker_with_no_email_does_not_read_as_sent(fb, tmp_path, monkeypatch, capsys):
    roster = {"noemail": {"name": "No Mail", "email": "", "telegram_user_id": 7,
                          "active": True}}
    _, spawned = _email_run(fb, tmp_path, monkeypatch, roster)
    fb.cmd_email_backup(fb.argparse.Namespace())
    assert spawned == []
    assert "sent=0" in capsys.readouterr().out


def test_a_schedule_row_naming_no_roster_member_is_counted(fb, tmp_path, monkeypatch, capsys):
    roster = {"known": {"name": "Known Person", "email": "k@example.test",
                        "telegram_user_id": 8, "active": True}}
    _email_run(fb, tmp_path, monkeypatch, roster,
               extra_rows=[("Ghost Speaker", "ghost")])
    fb.cmd_email_backup(fb.argparse.Namespace())
    out = capsys.readouterr().out
    assert "not-in-roster=1" in out
    assert "@ghost" in out


def test_a_clean_run_says_nothing_about_drops(fb, tmp_path, monkeypatch, capsys):
    """A quiet line is the signal that nothing fell out; it must stay quiet."""
    roster = {"fine": {"name": "Fine Person", "email": "f@example.test",
                       "telegram_user_id": 9, "active": True}}
    _email_run(fb, tmp_path, monkeypatch, roster)
    fb.cmd_email_backup(fb.argparse.Namespace())
    out = capsys.readouterr().out
    assert "sent=1" in out
    assert "no-email" not in out
    assert "not-in-roster" not in out


def test_a_blank_name_does_not_kill_the_send_loop(fb, tmp_path, monkeypatch, capsys):
    roster = {"blank": {"name": "", "email": "b@example.test",
                        "telegram_user_id": 10, "active": True}}
    _, spawned = _email_run(fb, tmp_path, monkeypatch, roster)
    fb.cmd_email_backup(fb.argparse.Namespace())
    assert len(spawned) == 1
    assert "sent=1" in capsys.readouterr().out


def test_a_missing_name_key_does_not_kill_the_send_loop(fb, tmp_path, monkeypatch):
    roster = {"nokey": {"email": "n@example.test", "telegram_user_id": 11,
                        "active": True}}
    # The helper reads r["name"] to build the schedule, so build it by hand.
    today = fb._today_local_date()
    session_date = (today + timedelta(days=3)).isoformat()
    schedule = [{"week": 1, "session_date": session_date, "day": "Mon", "slot": 1,
                 "theme": "t", "speaker_name": "Whoever", "speaker_username": "nokey"}]
    monkeypatch.setattr(fb, "state_path", lambda name: tmp_path / name)
    monkeypatch.setattr(fb, "load_state",
                        lambda n: {fb.SCHEDULE: schedule, fb.TRIBE_ROSTER: roster}.get(n))
    monkeypatch.setattr(fb, "_log_dm", lambda *a, **k: None)
    spawned = []
    monkeypatch.setattr("subprocess.run",
                        lambda cmd, **k: (spawned.append(cmd), _Ok())[1])
    fb.cmd_email_backup(fb.argparse.Namespace())
    assert len(spawned) == 1


def test_a_nameless_record_is_greeted_by_handle(fb, tmp_path, monkeypatch):
    roster = {"blank": {"name": "  ", "email": "b@example.test",
                        "telegram_user_id": 12, "active": True}}
    _, spawned = _email_run(fb, tmp_path, monkeypatch, roster)
    fb.cmd_email_backup(fb.argparse.Namespace())
    body = spawned[0][spawned[0].index("--body") + 1]
    assert "@blank" in body


def test_a_named_record_is_still_greeted_by_first_name(fb, tmp_path, monkeypatch):
    roster = {"named": {"name": "Given Family", "email": "g@example.test",
                        "telegram_user_id": 13, "active": True}}
    _, spawned = _email_run(fb, tmp_path, monkeypatch, roster)
    fb.cmd_email_backup(fb.argparse.Namespace())
    body = spawned[0][spawned[0].index("--body") + 1]
    assert "Hi Given," in body


def test_a_speaker_outside_the_window_is_not_counted_as_a_drop(fb, tmp_path,
                                                               monkeypatch, capsys):
    """The 1..14 day window is a scope, not a failure to mail."""
    roster = {"far": {"name": "Far Off", "email": "", "telegram_user_id": 14,
                      "active": True}}
    _email_run(fb, tmp_path, monkeypatch, roster, days=40)
    fb.cmd_email_backup(fb.argparse.Namespace())
    out = capsys.readouterr().out
    assert "no-email" not in out
    assert "sent=0" in out


def test_a_row_with_no_speaker_at_all_is_not_counted_as_a_drop(fb, tmp_path,
                                                               monkeypatch, capsys):
    """An empty slot names nobody, so nobody was missed."""
    today = fb._today_local_date()
    session_date = (today + timedelta(days=3)).isoformat()
    schedule = [{"week": 1, "session_date": session_date, "day": "Mon", "slot": 1,
                 "theme": "t", "speaker_name": "", "speaker_username": None}]
    monkeypatch.setattr(fb, "state_path", lambda name: tmp_path / name)
    monkeypatch.setattr(fb, "load_state",
                        lambda n: {fb.SCHEDULE: schedule, fb.TRIBE_ROSTER: {}}.get(n))
    monkeypatch.setattr(fb, "_log_dm", lambda *a, **k: None)
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: _Ok())
    fb.cmd_email_backup(fb.argparse.Namespace())
    out = capsys.readouterr().out
    assert "not-in-roster" not in out
    assert "no-email" not in out


def test_a_member_who_answered_the_bot_is_still_skipped_not_flagged(fb, tmp_path,
                                                                    monkeypatch, capsys):
    roster = {"chatty": {"name": "Chatty Person", "email": "c@example.test",
                         "telegram_user_id": 99, "active": True}}
    _email_run(fb, tmp_path, monkeypatch, roster)
    (tmp_path / fb.SESSIONS_LOG).write_text(
        json.dumps({"event_type": "start_received", "user_id": 99}) + "\n",
        encoding="utf-8")
    fb.cmd_email_backup(fb.argparse.Namespace())
    out = capsys.readouterr().out
    assert "skipped=1" in out
    assert "no-email" not in out


# ============================================================
# The log-session contract the docstring got wrong
# ============================================================

def test_log_session_matches_display_names_not_handles(fb, tmp_path, monkeypatch):
    """The documented example passed handles, which match no schedule row."""
    schedule = [{"session_date": "2026-05-12", "day": "Mon", "slot": 1,
                 "speaker_name": "Vesper Lynd", "speaker_username": "vlynd",
                 "no_show": False, "completed": False}]
    saved = {}
    # The lock this command now takes resolves `state_dir` for real, so patching
    # load_state and save_state alone is no longer enough to keep the test off
    # disk. Unpatched it reads the ambient data root, which is why this passed on
    # a workstation with an overlay and refused under CI without one.
    monkeypatch.setattr(fb, "state_dir", lambda p=tmp_path: p)
    monkeypatch.setattr(fb, "load_state", lambda n: schedule)
    monkeypatch.setattr(fb, "save_state", lambda n, d: saved.update({n: d}))
    monkeypatch.setattr(fb, "_log_event", lambda *a, **k: None)

    args = fb.argparse.Namespace(date="2026-05-12", shared="Vesper Lynd",
                                 no_shows="", swaps="")
    fb.cmd_log_session(args)
    assert schedule[0]["completed"] is True


def test_log_session_refuses_a_handle_where_a_name_belongs(fb, tmp_path, monkeypatch,
                                                           capsys):
    schedule = [{"session_date": "2026-05-12", "day": "Mon", "slot": 1,
                 "speaker_name": "Vesper Lynd", "speaker_username": "vlynd",
                 "no_show": False, "completed": False}]
    monkeypatch.setattr(fb, "state_dir", lambda p=tmp_path: p)
    monkeypatch.setattr(fb, "load_state", lambda n: schedule)
    monkeypatch.setattr(fb, "save_state", lambda n, d: None)
    monkeypatch.setattr(fb, "_log_event", lambda *a, **k: None)
    args = fb.argparse.Namespace(date="2026-05-12", shared="vlynd",
                                 no_shows="", swaps="")
    with pytest.raises(SystemExit) as exc:
        fb.cmd_log_session(args)
    assert exc.value.code == 1
    assert schedule[0]["completed"] is False


def test_the_log_session_help_names_the_display_name_contract(fb):
    """A CLI whose only guidance was a wrong example is how the defect persisted."""
    doc = fb.cmd_log_session.__doc__ or ""
    assert "DISPLAY NAMES" in doc


# ============================================================
# Three more, found by the Kimi k3 read of the same file
# ============================================================

def test_a_duplicate_handle_in_the_sheet_is_not_swallowed(fb, tmp_path, monkeypatch,
                                                          capsys):
    """`load_tribe_metadata` raises ValueError for a sheet the operator must fix.

    `ensure_state_dir` caught it beside FileNotFoundError and wrote `{}` in
    silence -- and an empty roster is the state the self-heal exists to prevent,
    because the bot then refuses every DM as an outsider.
    """
    monkeypatch.setattr(fb, "state_dir", lambda p=tmp_path: p)
    monkeypatch.setattr(fb, "state_path", lambda name: tmp_path / name)

    def _boom():
        raise ValueError("Telegram username @dup appears twice")

    monkeypatch.setattr(fb, "load_tribe_metadata", _boom)
    monkeypatch.setattr(fb, "load_state", lambda name: None)
    saved = {}
    monkeypatch.setattr(fb, "save_state", lambda n, d: saved.update({n: d}))
    errors = []
    monkeypatch.setattr(fb, "log_error", lambda m, e=None: errors.append(m))

    fb.ensure_state_dir()
    err = capsys.readouterr().err
    assert saved.get(fb.TRIBE_ROSTER) == {}
    assert "appears twice" in err
    assert "outsider" in err
    assert any("EMPTY" in m for m in errors)


def test_a_missing_sheet_also_says_the_roster_came_back_empty(fb, tmp_path,
                                                              monkeypatch, capsys):
    monkeypatch.setattr(fb, "state_dir", lambda p=tmp_path: p)
    monkeypatch.setattr(fb, "state_path", lambda name: tmp_path / name)

    def _missing():
        raise FileNotFoundError("no xlsx")

    monkeypatch.setattr(fb, "load_tribe_metadata", _missing)
    monkeypatch.setattr(fb, "load_state", lambda name: None)
    saved = {}
    monkeypatch.setattr(fb, "save_state", lambda n, d: saved.update({n: d}))
    monkeypatch.setattr(fb, "log_error", lambda m, e=None: None)

    fb.ensure_state_dir()
    assert saved.get(fb.TRIBE_ROSTER) == {}
    assert "EMPTY" in capsys.readouterr().err


def _is_vp(fb, title):
    return any(f in title.lower() for f in fb.VP_TITLE_FRAGMENTS)


def test_a_spelled_out_vice_president_is_a_senior_leader(fb):
    assert _is_vp(fb, "Vice President of Engineering")


def test_a_senior_vice_president_is_a_senior_leader(fb):
    assert _is_vp(fb, "Senior Vice President, Sales")


def test_the_abbreviated_forms_still_match(fb):
    for title in ("VP of Engineering", "SVP, Sales", "Chief Technology Officer"):
        assert _is_vp(fb, title), title


def test_an_ordinary_title_is_still_not_a_senior_leader(fb):
    for title in ("Head of Delivery", "Software Engineer", "Presenter"):
        assert not _is_vp(fb, title), title


def test_a_routine_membership_event_is_not_filed_as_an_error(fb, monkeypatch):
    """errors.log is what an operator opens to find what broke."""
    errors, events = [], []
    monkeypatch.setattr(fb, "log_error", lambda m, e=None: errors.append(m))
    monkeypatch.setattr(fb, "_log_event", lambda t, **k: events.append((t, k)))
    monkeypatch.setattr(fb, "_sweep_expired_swap_requests", lambda bot: None)

    fb._handle_update(object(), {"my_chat_member": {"chat": {"id": 1}}})
    assert errors == []
    assert events and events[0][0] == "my_chat_member"


def test_the_membership_event_still_records_what_happened(fb, monkeypatch):
    events = []
    monkeypatch.setattr(fb, "log_error", lambda m, e=None: None)
    monkeypatch.setattr(fb, "_log_event", lambda t, **k: events.append((t, k)))
    monkeypatch.setattr(fb, "_sweep_expired_swap_requests", lambda bot: None)

    fb._handle_update(object(), {"my_chat_member": {"status": "kicked"}})
    assert "kicked" in events[0][1]["detail"]
