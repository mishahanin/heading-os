#!/usr/bin/env python3
"""Seven defects the 2026-08-24 audit found in `scripts/fireside-bot.py`.

Five of the seven are the same shape: the file already knew the answer and had
written it down at one call site, and a sibling site was added later without it.
That shape is why the tests here are structural as often as behavioural. A test
that only checks the site fixed today leaves the next copy free to be written
without the guard, which is exactly how these arrived.

  1. `cmd_helmsman_brief` derived the greeting with
     `entry.get("name", "Helmsman").split()[0]` while three sibling send loops
     each carried `raw.split()[0] if raw else <fallback>` above a comment saying
     a blank name had already crashed a live send. A `.get` default does not
     apply to a key that is present and empty, so `""` reached `[0]`. That job
     runs daily and raises BEFORE the entry is stamped `briefed`, so it picks
     the same candidate again tomorrow, forever, and its healthcheck goes red by
     silence.

  2. `cmd_email_backup` looked the speaker up with a bare `roster.get(username)`
     while the other two lookups fell back to a case-insensitive match. Roster
     keys keep the xlsx's case and schedule rows keep the cycle config's, and
     nothing normalises either, so a case mismatch classified the member
     `not-in-roster`. The one person that command exists to reach, an
     unresponsive speaker, was named in a summary line instead of emailed.

  3. `_UNREADABLE_SHEET` exists because a truncated or HTML-overwritten
     `31C_Tribe.xlsx` raises `zipfile.BadZipFile` or openpyxl's
     `InvalidFileException`, neither of which is an OSError or a ValueError.
     Only `ensure_state_dir` used the tuple. The three other entry points that
     call `load_tribe_metadata` caught `(FileNotFoundError, ValueError)` and gave
     a traceback where they had a friendly message ready.

  4. The nine bare trigrams in `VP_TITLE_FRAGMENTS` were matched as substrings,
     so "cio" matched "precious", "clo" matched "clothing", and a member titled
     "Clothing and Apparel Lead" came back is_vp.

  5. `cycle-end-invite` overwrote `pending_cycle_invite` when a new cycle came
     round, and Telegram keeps an old inline keyboard tappable forever. The
     cycle-1 approval card stayed in the CEO's history showing cycle-1 text over
     a pending draft that had become cycle 2. Tapping "Send to Tribe" on it
     posted the cycle-2 invite: the text on the screen and the text sent were
     different, on the one flow whose entire purpose is approving exact wording.

  6. The module docstring called itself the current subcommand inventory and
     omitted nine of the twenty six commands `main()` registers.

  7. `cmd_log_session`'s worked example used 2026-05-12, a Tuesday. A schedule
     holds only Mon and Wed rows, so the documented invocation could never match
     one and exited 1 whatever names were passed.

Nothing here reaches Telegram, the network, or the operator's state directory:
`state_dir` is redirected to tmp_path and the bot is a recorder.

Run: .venv/bin/python -m pytest \
     tests/test_a_bot_that_carried_its_guards_everywhere_but_the_last_call_site.py -q
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BOT = ROOT / "scripts" / "fireside-bot.py"


@pytest.fixture(scope="module")
def fb():
    """Load fireside-bot.py as a module (hyphen in the filename)."""
    spec = importlib.util.spec_from_file_location("fireside_bot", str(BOT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tree():
    return ast.parse(BOT.read_text(encoding="utf-8"))


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone from fireside-bot.py; this guard lost its subject")


# ============================================================
# 1 - the greeting that crashed a daily job
# ============================================================

@pytest.mark.parametrize("raw", ["", "   ", None, 0, []])
def test_a_name_with_nothing_in_it_yields_the_fallback_rather_than_raising(fb, raw):
    assert fb._first_name(raw, "@handle") == "@handle"


def test_a_real_name_still_greets_by_its_first_word(fb):
    assert fb._first_name("Vesper Lynd", "@handle") == "Vesper"
    assert fb._first_name("  Felix   Leiter ", "@handle") == "Felix"


def test_the_helmsman_brief_no_longer_derives_a_name_of_its_own(tree):
    """The structural half. The behaviour above is satisfied by `_first_name`
    existing; this is what says the fourth call site actually uses it.

    Asked of every function in the file rather than of `cmd_helmsman_brief`
    alone, because the defect was a NEW site written without the guard, and a
    test pinned to today's four sites would not see the fifth.
    """
    helper = _func(tree, "_first_name")
    inside_helper = {id(n) for n in ast.walk(helper)}
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript) or id(node) in inside_helper:
            continue
        value = node.value
        # `raw.split()[0]`, and only that: a `split(":", 1)[1]` on a callback
        # payload is not a name and an empty one cannot strand anybody.
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                and value.func.attr == "split" and not value.args and not value.keywords
                and isinstance(node.slice, ast.Constant) and node.slice.value == 0):
            offenders.append(ast.unparse(node))
    assert offenders == [], (
        "a greeting is still derived by indexing straight into `.split()`, which "
        "raises IndexError on a name that is present and empty. Route it through "
        f"`_first_name(raw, fallback)` like the four send loops do: {offenders}")


# ============================================================
# 2 - the roster lookup that was case-sensitive in one place
# ============================================================

def test_a_roster_key_written_in_another_case_still_resolves(fb):
    roster = {"AliceW": {"telegram_user_id": 7, "email": "a@example.invalid"}}
    assert fb._roster_entry(roster, "alicew") == roster["AliceW"]
    assert fb._roster_entry(roster, "ALICEW") == roster["AliceW"]
    assert fb._roster_entry(roster, "AliceW") == roster["AliceW"]


def test_a_username_nobody_holds_still_resolves_to_nothing(fb):
    roster = {"AliceW": {"telegram_user_id": 7}}
    assert fb._roster_entry(roster, "bond") is None
    assert fb._roster_entry(roster, "") is None
    assert fb._roster_entry(roster, None) is None


def test_the_speaker_resolver_reads_the_same_lookup(fb):
    roster = {"AliceW": {"telegram_user_id": 7}}
    assert fb._resolve_speaker_user_id(roster, "alicew") == 7


def test_no_call_site_looks_a_username_up_in_the_roster_by_hand(tree):
    """`roster.get(<name>)` outside `_roster_entry` is the defect returning.

    The exact-match-only lookup is invisible until a member's case diverges, and
    the divergence is silent on both sides, so a behavioural test on today's
    three call sites would not catch a fourth written next month.
    """
    helper = _func(tree, "_roster_entry")
    inside_helper = {id(n) for n in ast.walk(helper)}
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in inside_helper:
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "get"
                and isinstance(func.value, ast.Name) and func.value.id == "roster"
                and node.args and not isinstance(node.args[0], ast.Constant)):
            offenders.append(ast.unparse(node))
    assert offenders == [], (
        "a roster lookup bypasses `_roster_entry`, so it matches only on exact "
        f"case and reports a real member as not-in-roster: {offenders}")


# ============================================================
# 3 - the corrupt-workbook tuple that one handler honoured
# ============================================================

def test_the_unreadable_sheet_tuple_still_covers_a_truncated_workbook(fb):
    assert zipfile.BadZipFile in fb._UNREADABLE_SHEET
    assert ValueError in fb._UNREADABLE_SHEET


def test_every_reader_of_the_workbook_handles_a_corrupt_one(tree):
    """A `try` that calls `load_tribe_metadata` must name `_UNREADABLE_SHEET`.

    Four call sites, and the tuple was written for all four. Three of them
    caught `(FileNotFoundError, ValueError)`, which walks straight past
    `BadZipFile` and `InvalidFileException`, so an operator with a half-synced
    sheet got a traceback out of a command that had a hint prepared for exactly
    that state.
    """
    def calls_loader(node) -> bool:
        return any(isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                   and sub.func.id == "load_tribe_metadata"
                   for sub in ast.walk(node))

    tries = [n for n in ast.walk(tree)
             if isinstance(n, ast.Try) and any(calls_loader(s) for s in n.body)]
    assert len(tries) >= 4, (
        f"{len(tries)} guarded readers of the workbook; this floor models the "
        "four that exist, so a drop below it means a call site lost its handler "
        "or the loader was renamed and this guard went blind")

    unguarded = []
    for node in tries:
        names = {sub.id for h in node.handlers for sub in ast.walk(h)
                 if isinstance(sub, ast.Name)}
        if "_UNREADABLE_SHEET" not in names:
            unguarded.append(ast.unparse(node.handlers[0]) if node.handlers else "<no handler>")
    assert unguarded == [], (
        "a reader of 31C_Tribe.xlsx does not catch _UNREADABLE_SHEET, so a "
        "truncated or HTML-overwritten workbook is a traceback rather than the "
        f"message this handler exists to print: {unguarded}")


# ============================================================
# 4 - the VP trigrams that matched inside other words
# ============================================================

@pytest.mark.parametrize("title", [
    "Clothing and Apparel Lead",     # clo
    "Precious Metals Analyst",       # cio
    "Member ex officio",             # cio
    "Picsom Integration Engineer",   # cso
    "Ceomorph Data Curator",         # ceo
    "Senior Engineer",
    "",
])
def test_a_title_that_merely_contains_the_letters_is_not_a_senior_leader(fb, title):
    assert fb.is_vp_title(title) is False, f"{title!r} was read as a VP title"


@pytest.mark.parametrize("title", [
    "CEO", "Chief Executive Officer", "CTO, Platform", "cfo",
    "VP of Engineering", "SVP, Revenue", "Vice President of Sales",
    "Senior Vice President", "Founder", "Co-Founder", "co-founder and CTO",
])
def test_a_real_senior_title_is_still_matched(fb, title):
    assert fb.is_vp_title(title) is True, f"{title!r} stopped being a VP title"


def test_the_xlsx_loader_asks_the_shared_predicate(tree):
    """Without this, the boundary fix could sit in a helper nothing calls."""
    loader = _func(tree, "load_tribe_metadata")
    called = {n.func.id for n in ast.walk(loader)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "is_vp_title" in called, (
        "load_tribe_metadata no longer routes the title through is_vp_title, so "
        "the word-boundary handling is dead code")


# ============================================================
# 5 - the stale approval card that sent the wrong cycle's text
# ============================================================

class _RecordingBot:
    """Records instead of reaching Telegram. Same shape as the FakeBot in
    tests/test_fireside_topic_handlers.py, kept local so a change there cannot
    quietly alter what this file measures."""

    def __init__(self):
        self.sent = []
        self.edits = []
        self.markup_edits = []
        self.answered = []
        self.pins = []
        self._next_msg_id = 8000

    def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        self._next_msg_id += 1
        return {"message_id": self._next_msg_id}

    send_dm = send_message

    def edit_message_text(self, chat_id, msg_id, text, **kwargs):
        self.edits.append((chat_id, msg_id, text))
        return {"message_id": msg_id}

    def edit_message_reply_markup(self, chat_id, msg_id, markup):
        self.markup_edits.append((chat_id, msg_id, markup))
        return {"message_id": msg_id}

    def answer_callback_query(self, cq_id, text=None):
        self.answered.append((cq_id, text))

    def pin_chat_message(self, chat_id, msg_id, **kwargs):
        self.pins.append((chat_id, msg_id))


def _seed_pending(tmp_path, *, approval_msg_id, cycle, text):
    from scripts import fireside_topics as ft
    state = ft.load_topic_state(tmp_path)
    state["pending_cycle_invite"] = {
        "text": text, "approval_msg_id": approval_msg_id,
        "drafted_at": "2026-07-05T11:00:00+04:00", "cycle": cycle,
    }
    ft.save_topic_state(tmp_path, state)


@pytest.fixture(autouse=True)
def _state_root(fb, tmp_path, monkeypatch):
    """Autouse, and that is the point.

    An opt-in redirect leaves every test that does not request it resolving
    `state_dir` at the operator's live overlay, where the first error path that
    writes anything writes it for real.
    `tests/test_a_state_redirect_that_covered_some_of_a_modules_tests.py` fails a
    module that redirects for only some of its tests, and it failed this one.
    """
    monkeypatch.setattr(fb, "state_dir", lambda p=tmp_path: p)
    return tmp_path


@pytest.fixture
def ceo(fb, monkeypatch):
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "999")
    monkeypatch.setenv("FIRESIDE_TRIBE_CHAT_ID", "-100123")
    return fb


def test_a_tap_on_a_superseded_card_sends_nothing(ceo, tmp_path):
    """The measured defect. The card on screen showed cycle 1; the pending draft
    underneath it was cycle 2. The tap posted the cycle-2 text."""
    from scripts import fireside_topics as ft
    bot = _RecordingBot()
    _seed_pending(tmp_path, approval_msg_id=7777, cycle=2, text="CYCLE TWO INVITE")

    ceo._handle_cycle_invite_tap(bot, "cq1", "cycle_invite:send", 999,
                                 msg_chat_id=999, msg_id=4242)

    assert all(chat != -100123 for chat, _text, _kw in bot.sent), (
        "the stale card posted to the Tribe; the CEO approved cycle-1 wording "
        f"and cycle-2 wording went out: {bot.sent}")
    assert ft.load_topic_state(tmp_path)["pending_cycle_invite"] is not None, (
        "the live cycle-2 draft was consumed by a tap on the retired card")
    assert bot.answered and "replaced" in (bot.answered[-1][1] or ""), (
        f"the CEO was not told why nothing happened: {bot.answered}")
    assert bot.markup_edits, "the dead keyboard was left tappable"


def test_a_tap_on_a_superseded_card_cannot_cancel_the_live_draft(ceo, tmp_path):
    """The other direction of the same defect, and the reason the check sits
    above the choice split rather than inside the send branch."""
    from scripts import fireside_topics as ft
    bot = _RecordingBot()
    _seed_pending(tmp_path, approval_msg_id=7777, cycle=2, text="CYCLE TWO INVITE")

    ceo._handle_cycle_invite_tap(bot, "cq1", "cycle_invite:cancel", 999,
                                 msg_chat_id=999, msg_id=4242)

    assert ft.load_topic_state(tmp_path)["pending_cycle_invite"] is not None, (
        "tapping Cancel on a retired card cancelled the draft that replaced it")


def test_the_card_the_draft_was_drafted_onto_still_works(ceo, tmp_path):
    """Without this, a handler that refused every tap would pass the two above."""
    from scripts import fireside_topics as ft
    bot = _RecordingBot()
    _seed_pending(tmp_path, approval_msg_id=4242, cycle=2, text="CYCLE TWO INVITE")

    ceo._handle_cycle_invite_tap(bot, "cq1", "cycle_invite:send", 999,
                                 msg_chat_id=999, msg_id=4242)

    assert any(chat == -100123 and text == "CYCLE TWO INVITE"
               for chat, text, _kw in bot.sent), bot.sent
    assert ft.load_topic_state(tmp_path)["pending_cycle_invite"] is None


def test_a_draft_that_predates_the_message_id_convention_is_not_refused(ceo, tmp_path):
    """A pending record written before `approval_msg_id` was stamped carries
    None. Refusing on that would strand a live draft the CEO can still see, so
    the check requires both ids to be known."""
    from scripts import fireside_topics as ft
    bot = _RecordingBot()
    _seed_pending(tmp_path, approval_msg_id=None, cycle=2, text="CYCLE TWO INVITE")

    ceo._handle_cycle_invite_tap(bot, "cq1", "cycle_invite:send", 999,
                                 msg_chat_id=999, msg_id=4242)

    assert any(chat == -100123 for chat, _text, _kw in bot.sent), bot.sent
    assert ft.load_topic_state(tmp_path)["pending_cycle_invite"] is None


# ============================================================
# 6 - the docstring inventory that omitted nine commands
# ============================================================

def _documented_subcommands() -> set:
    doc = ast.get_docstring(ast.parse(BOT.read_text(encoding="utf-8"))) or ""
    names = set()
    started = False
    for line in doc.splitlines():
        if line.startswith("Subcommands"):
            started = True
            continue
        if not started:
            continue
        if line.startswith("Usage:"):
            break
        stripped = line.strip()
        if not stripped or " - " not in stripped:
            continue
        head = stripped.split(" - ", 1)[0].strip()
        # `helmsman set|list|gaps` documents the single `helmsman` command.
        names.add(head.split()[0])
    return names


def _registered_subcommands(tree) -> set:
    main = _func(tree, "main")
    for node in ast.walk(main):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                and any(isinstance(t, ast.Name) and t.id == "handlers" for t in node.targets)):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError("main() no longer binds a `handlers` dict literal")


def test_the_docstring_inventory_is_the_set_of_registered_subcommands(tree):
    documented = _documented_subcommands()
    registered = _registered_subcommands(tree)
    assert registered, "no subcommands were read out of main(); this guard went blind"
    assert documented == registered, (
        "the module docstring calls itself the subcommand inventory and no "
        f"longer is. Missing from the docstring: {sorted(registered - documented)}. "
        f"Documented but not registered: {sorted(documented - registered)}.")


# ============================================================
# 7 - the worked example on a day the schedule never holds
# ============================================================

def test_every_log_session_example_date_falls_on_a_session_day(tree):
    """A schedule holds Mon and Wed rows only, so an example on any other
    weekday cannot match one and exits 1 however correct the rest of it is."""
    import re

    doc = ast.get_docstring(_func(tree, "cmd_log_session")) or ""
    dates = re.findall(r"--date (\d{4}-\d{2}-\d{2})", doc)
    assert dates, "the log-session docstring lost its worked example"
    wrong = [d for d in dates if date.fromisoformat(d).weekday() not in (0, 2)]
    assert wrong == [], (
        "a documented --date falls on a day no fireside is ever scheduled, so "
        f"following the example verbatim exits 1: {wrong}")


# ============================================================
# 8 - a docstring that named the wrong origin for a value
# ============================================================
#
# Added 2026-09-02, after a fix landed beside a false provenance. The
# `_format_dm_date` docstring said its `day` argument "comes straight out of
# `config/fireside-schedule.json`, where `"day": null` is legal". Neither half
# held: that config carries week/theme/mon/wed and no `day` key at all, and
# every `day` in a schedule row is the literal "Mon" or "Wed" that
# `build_schedule` writes. A false claim about where a value comes from is the
# expensive kind, because the next reader takes it as the measurement they do
# not have to repeat. Both halves are now asked of the tree.

def test_the_schedule_config_carries_no_day_key():
    """The shipped example is the schema; `day` is derived, never configured."""
    import json

    weeks = json.loads(
        (ROOT / "scripts" / "fireside-schedule.example.json").read_text(encoding="utf-8")
    )["weeks"]
    assert weeks, "the example schedule lost its weeks, so this guard went blind"
    with_day = [w["week"] for w in weeks if "day" in w]
    assert with_day == [], (
        "a week entry now configures `day`, so `_format_dm_date`'s docstring "
        f"no longer describes where the value comes from: weeks {with_day}")


def test_build_schedule_writes_the_day_label_as_a_literal(tree):
    """`day` originates here, as one of exactly two constants."""
    labels = set()
    for node in ast.walk(_func(tree, "build_schedule")):
        if not (isinstance(node, ast.Dict)):
            continue
        # `strict` is real here rather than decorative: `ast.Dict` pairs keys
        # with values one-to-one, and a `**expansion` shows up as a None key
        # rather than as a missing one, so a length mismatch would mean the
        # node shape changed under this guard.
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "day":
                labels.add(value.id if isinstance(value, ast.Name) else None)
    assert labels == {"day_label"}, (
        "build_schedule no longer writes `day` from the `day_label` loop "
        f"variable: {labels}")


def test_the_candidate_builder_defaults_a_missing_day_to_a_string(tree):
    """A missing key yields ""; only a key present-and-null yields None, which
    is the case the annotation declares and the `or` absorbs."""
    defaults = []
    for node in ast.walk(_func(tree, "find_swap_candidates")):
        func = getattr(node, "func", None)
        if (isinstance(node, ast.Call) and isinstance(func, ast.Attribute)
                and func.attr == "get" and len(node.args) == 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "day"):
            defaults.append(node.args[1])
    assert len(defaults) == 1, (
        f"expected one `.get(\"day\", ...)` in find_swap_candidates, found {len(defaults)}")
    fallback = defaults[0]
    assert isinstance(fallback, ast.Constant) and fallback.value == "", (
        "the missing-key fallback is no longer the empty string, so the "
        "docstring's account of how None arrives here is now wrong")
