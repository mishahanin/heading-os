"""Two tools that showed the operator a UTC wall clock, and one default no test read.

* ``sentinel.py`` stamped every item it raises with a bare UTC wall clock and
  deleted the offset on the way. The email path did it by slicing
  (``str(dt)[:19]`` cuts ``+00:00`` off the end); the two Telegram paths did it
  explicitly (``datetime.now(timezone.utc).isoformat()[:19]``). That string goes
  straight into the Telegram alert card and into the urgency model's ``DATE:``
  line, with nothing naming a zone. On a +04 mailbox it is four hours early, and
  before 04:00 local it is the WRONG DAY. Recency is how a person judges
  urgency, so the one field an urgency monitor must get right was the field it
  silently shifted - while the daemon sending the alert already scheduled on the
  configured zone.

* ``exchange-task.py --list`` rendered ``due_date`` and ``reminder_due_by``,
  both UTC-aware ``DateTimeField`` values, with a bare ``strftime``. The create
  path in the same file labels its confirmation with ``EXCHANGE_TIMEZONE``, so
  the two commands of one script reported the same reminder on two clocks and
  only one said which.

* ``email-intelligence.filter_noise``'s ``check_processed=True`` is the switch
  that drops mail handled on a previous run. No test in the repo called
  ``filter_noise`` at all: flipping the default to False in a scratch tree and
  running all 13.5k tests produced zero failures. The digest would re-report
  conversations closed days ago and re-propose actions already taken, with the
  suite green. Nothing is wrong with the code; what was missing is the test.

Run: python3 -m pytest tests/test_two_clocks_and_a_default_nothing_read.py
"""
from __future__ import annotations

import importlib.util
import sys
import zoneinfo
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sen = _load("sentinel_clock", "scripts/sentinel.py")
task = _load("exchange_task_clock", "scripts/exchange-task.py")
intel = _load("email_intel_default", "scripts/email-intelligence.py")

DUBAI = zoneinfo.ZoneInfo("Asia/Dubai")          # +04, no DST
CHATHAM = zoneinfo.ZoneInfo("Pacific/Chatham")   # +12:45, a non-hour offset


# ============================================================
# The alert clock
# ============================================================

def test_a_late_night_arrival_keeps_its_own_day(monkeypatch):
    """The failure exactly as the operator would meet it.

    A VIP mail landing at 01:34 Dubai used to be announced as 21:34 the day
    before: wrong hour AND wrong date, on the field a person reads for urgency.
    """
    monkeypatch.setattr(sen, "get_default_tz", lambda: DUBAI)
    arrived = datetime(2026, 8, 26, 21, 34, 12, tzinfo=timezone.utc)

    out = sen.local_stamp(arrived)

    assert out.startswith("2026-08-27 01:34:12")


def test_the_stamp_carries_a_zone_label(monkeypatch):
    """A converted time with no label is a second unlabelled clock, not a fix."""
    monkeypatch.setattr(sen, "get_default_tz", lambda: DUBAI)

    out = sen.local_stamp(datetime(2026, 8, 26, 21, 34, 12, tzinfo=timezone.utc))

    assert out.strip() != "2026-08-27 01:34:12", "the zone label is missing"
    assert out.split()[-1] not in ("", None)


def test_a_utc_operator_still_sees_utc(monkeypatch):
    """The negative case. A converter that always shifts would pass the test
    above while breaking every UTC deployment."""
    monkeypatch.setattr(sen, "get_default_tz", lambda: timezone.utc)

    out = sen.local_stamp(datetime(2026, 8, 26, 21, 34, 12, tzinfo=timezone.utc))

    assert out.startswith("2026-08-26 21:34:12")


def test_a_fractional_offset_zone_is_handled(monkeypatch):
    """Not every zone is a whole hour from UTC, and an implementation that
    formats an offset by hand gets +12:45 wrong."""
    monkeypatch.setattr(sen, "get_default_tz", lambda: CHATHAM)

    out = sen.local_stamp(datetime(2026, 8, 26, 21, 34, 12, tzinfo=timezone.utc))

    assert out.startswith("2026-08-27 10:19:12")


def test_a_naive_datetime_is_read_as_utc_not_as_local(monkeypatch):
    """Every producer here is UTC-aware, so a naive value means something
    upstream dropped the zone. Treating it as local would invent a four-hour
    correction that nothing asked for."""
    monkeypatch.setattr(sen, "get_default_tz", lambda: DUBAI)

    out = sen.local_stamp(datetime(2026, 8, 26, 21, 34, 12))  # noqa: DTZ001

    assert out.startswith("2026-08-27 01:34:12")


def test_a_value_that_is_not_a_datetime_does_not_kill_the_cycle(monkeypatch):
    """A fetch loop that raises on one odd field drops the whole cycle, and a
    monitor that goes quiet is the worst failure it has.

    The old `str(dt)[:19]` swallowed any type by accident; the converted version
    would have raised AttributeError. Found by an existing mailbox fake that
    hands `datetime_received` an int, so this is not a hypothetical shape.
    """
    monkeypatch.setattr(sen, "get_default_tz", lambda: DUBAI)

    assert sen.local_stamp(7) == "7"
    assert sen.local_stamp("2026-08-26") == "2026-08-26"


def test_no_datetime_gives_an_empty_string(monkeypatch):
    """The email path reaches this with None when Exchange returns no
    datetime_received; it must not raise inside a fetch loop."""
    monkeypatch.setattr(sen, "get_default_tz", lambda: DUBAI)

    assert sen.local_stamp(None) == ""


def _sentinel_tree():
    import ast

    return ast.parse((ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8"))


def test_no_producer_slices_the_offset_off_a_timestamp_any_more():
    """The three sites, asked of the AST.

    `[:19]` on an ISO string or a `str(dt)` is exactly long enough to keep the
    seconds and drop the offset, so it is the shape this defect takes. Read from
    the parse tree rather than the text, because the prose in this file and in
    `local_stamp`'s own docstring both quote the slice while executing nothing -
    a text scan counts those and calls a fixed file broken.

    One use survives: a log line about the cycle start, which is the daemon's
    own record and is never shown as an item time.
    """
    import ast

    lines = sorted(
        node.lineno for node in ast.walk(_sentinel_tree())
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)
        and isinstance(node.slice.upper, ast.Constant) and node.slice.upper.value == 19
    )
    src_lines = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8").splitlines()

    assert len(lines) == 1, [src_lines[n - 1].strip() for n in lines]
    assert "Cycle starting at" in src_lines[lines[0] - 1]


def test_the_ast_probe_can_actually_find_a_slice():
    """Pins the check above. A probe that matches nothing passes any file,
    including one where every producer went back to slicing."""
    import ast

    found = [n for n in ast.walk(ast.parse("x = s.isoformat()[:19]\n"))
             if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice)
             and isinstance(n.slice.upper, ast.Constant) and n.slice.upper.value == 19]

    assert len(found) == 1


def test_every_item_date_goes_through_the_helper():
    """Every `"date"` an item carries traces back to `local_stamp`.

    The email path assigns through `date_str`, the two Telegram paths inline the
    call, so both shapes are accepted and anything else is not: a new source
    that stamps its own clock puts the alert card back on two of them.
    """
    src = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")
    date_lines = [ln.strip() for ln in src.splitlines()
                  if ln.strip().startswith('"date":')]

    assert date_lines, "the extractor found no date producers"
    for line in date_lines:
        assert "local_stamp(" in line or line == '"date": date_str,', line
    assert "date_str = local_stamp(email_item.datetime_received)" in src


# ============================================================
# The reminder clock
# ============================================================

def test_a_reminder_is_listed_on_the_mailbox_clock():
    """The create path prints 09:00 (Asia/Dubai); --list used to print 05:00."""
    utc_reminder = datetime(2026, 8, 28, 5, 0, tzinfo=timezone.utc)

    out = task.in_mailbox_zone(utc_reminder, "Asia/Dubai")

    assert out.strftime("%Y-%m-%d %H:%M") == "2026-08-28 09:00"


def test_a_pre_dawn_reminder_keeps_its_own_date():
    """A 02:00 Dubai reminder is 22:00 the PREVIOUS day in UTC, so the listing
    showed the wrong calendar day, which is the harder error to notice."""
    utc_reminder = datetime(2026, 8, 27, 22, 0, tzinfo=timezone.utc)

    out = task.in_mailbox_zone(utc_reminder, "Asia/Dubai")

    assert out.strftime("%Y-%m-%d %H:%M") == "2026-08-28 02:00"


def test_a_utc_mailbox_is_unchanged():
    """The negative case, and the reason the old code looked correct: on a UTC
    mailbox it WAS correct."""
    utc_reminder = datetime(2026, 8, 28, 5, 0, tzinfo=timezone.utc)

    out = task.in_mailbox_zone(utc_reminder, "UTC")

    assert out.strftime("%Y-%m-%d %H:%M") == "2026-08-28 05:00"


def test_no_reminder_stays_none():
    assert task.in_mailbox_zone(None, "Asia/Dubai") is None


def test_an_unknown_zone_does_not_take_the_listing_down():
    """A misconfigured EXCHANGE_TIMEZONE must degrade, not crash: --list is how
    an operator finds out what is set."""
    utc_reminder = datetime(2026, 8, 28, 5, 0, tzinfo=timezone.utc)

    out = task.in_mailbox_zone(utc_reminder, "Mars/Olympus_Mons")

    assert out == utc_reminder


class _Task:
    """One Exchange task, with the four fields `list_tasks` reads."""

    def __init__(self, due, remind):
        self.due_date = due
        self.reminder_due_by = remind
        self.reminder_is_set = remind is not None
        self.status = "NotStarted"
        # Invented. The engine repo is public and the content gate flags any
        # real place or person that reaches a fixture, which is what it did to
        # the first draft of this line.
        self.subject = "Call the Vantooren office"
        self.body = ""


class _Query(list):
    """`account.tasks.all().order_by(...).filter(...)` in three methods."""

    def all(self):
        return self

    def order_by(self, *_a, **_k):
        return self

    def filter(self, **_k):
        return self


class _Account:
    def __init__(self, tasks):
        self.tasks = _Query(tasks)


class _Args:
    all_statuses = True
    status = "NotStarted"


def _listing(capsys, due, remind, tz_name="Asia/Dubai") -> str:
    """Run the real `list_tasks` and return its plain output.

    Source greps stood here first, and two mutations walked past them: one
    replaced the converted reminder with the raw UTC value, the other pinned the
    zone name to "UTC". Both left the greppable lines intact. Only running the
    function catches either.
    """
    import re

    task.list_tasks(_Account([_Task(due, remind)]), _Args(),
                    {"EXCHANGE_TIMEZONE": tz_name})
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def test_the_ansi_stripper_actually_removes_the_escapes(capsys):
    """`GREEN` is "\\033[92m" and contains a 9 and a 2, so an unstripped line
    can satisfy an assertion about a time by accident."""
    print(f"{task.GREEN}x{task.RESET}")
    import re

    assert re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out).strip() == "x"


def test_the_listing_shows_the_reminder_on_the_mailbox_clock(capsys):
    """End to end, through the function the operator actually runs.

    Create prints `Reminder: ... at 09:00 (Asia/Dubai)`; the listing used to
    answer 05:00 with no zone.
    """
    out = _listing(capsys,
                   due=datetime(2026, 8, 28, 5, 0, tzinfo=timezone.utc),
                   remind=datetime(2026, 8, 28, 5, 0, tzinfo=timezone.utc))

    assert "remind 2026-08-28 09:00 (Asia/Dubai)" in out
    assert "05:00" not in out


def test_the_listing_uses_the_configured_zone_not_a_hardcoded_one(capsys):
    """Pins `tz_name` to the config. A literal "UTC" in its place leaves every
    greppable line unchanged and every conversion wrong."""
    out = _listing(capsys,
                   due=datetime(2026, 8, 28, 5, 0, tzinfo=timezone.utc),
                   remind=datetime(2026, 8, 28, 5, 0, tzinfo=timezone.utc),
                   tz_name="Pacific/Chatham")

    assert "(Pacific/Chatham)" in out
    assert "2026-08-28 17:45" in out


def test_the_listed_due_date_is_converted_too(capsys):
    """`due_date` is the same DateTimeField and had the same defect. At 22:00
    UTC the Dubai date is the NEXT day, so a row fixed on one line only would
    show its two halves a day apart."""
    out = _listing(capsys,
                   due=datetime(2026, 8, 27, 22, 0, tzinfo=timezone.utc),
                   remind=datetime(2026, 8, 27, 22, 0, tzinfo=timezone.utc))

    assert "due 2026-08-28" in out
    assert "remind 2026-08-28 02:00" in out


def test_a_task_with_no_reminder_prints_no_reminder(capsys):
    """The negative case: the reminder branch must stay behind its flag."""
    out = _listing(capsys, due=datetime(2026, 8, 28, 5, 0, tzinfo=timezone.utc),
                   remind=None)

    assert "remind" not in out
    assert "due 2026-08-28" in out


def test_a_task_with_no_due_date_says_so(capsys):
    out = _listing(capsys, due=None, remind=None)

    assert "no due date" in out


# ============================================================
# The dedupe default nothing read
# ============================================================

class _State:
    """The slice of StateManager `filter_noise` touches, and no more."""

    def __init__(self, processed=(), learned=()):
        self._processed = set(processed)
        self.data = {"learned_ignore_senders": list(learned)}
        self.asked: list[str] = []

    def is_processed(self, message_id: str) -> bool:
        # Records the argument instead of discarding it: a stub that answers
        # without looking cannot tell "asked about the right message" from
        # "asked about nothing".
        self.asked.append(message_id)
        return message_id in self._processed


def _msg(mid: str, subject: str = "Quarterly numbers",
         sender: str = "someone@example.com") -> dict:
    return {"item_class": "IPM.Note", "subject": subject,
            "sender_email": sender, "message_id": mid}


def test_the_default_drops_mail_already_handled():
    """The behaviour the untested default carries. Nothing in 13.5k tests called
    this function, so flipping the default to False was green."""
    state = _State(processed={"seen-1"})

    clean, filtered = intel.filter_noise([_msg("seen-1"), _msg("new-1")], state, [])

    assert [m["message_id"] for m in clean] == ["new-1"]
    assert filtered == 1


def test_the_default_really_consulted_the_state():
    """A `filtered == 1` can also come from a subject or sender pattern. This
    asserts the dedupe layer was the one that ran."""
    state = _State(processed={"seen-1"})

    intel.filter_noise([_msg("seen-1"), _msg("new-1")], state, [])

    assert state.asked == ["seen-1", "new-1"]


def test_the_unread_feed_deliberately_keeps_seen_mail():
    """The one explicit call site. An email can stay unread for days, so an
    already-seen unread message must still reach the dashboard."""
    state = _State(processed={"seen-1"})

    clean, filtered = intel.filter_noise([_msg("seen-1")], state, [],
                                         check_processed=False)

    assert [m["message_id"] for m in clean] == ["seen-1"]
    assert filtered == 0
    assert state.asked == [], "the state must not even be consulted"


def test_nothing_is_dropped_when_nothing_was_processed():
    """The negative case for the dedupe layer itself."""
    state = _State()

    clean, filtered = intel.filter_noise([_msg("a"), _msg("b")], state, [])

    assert len(clean) == 2 and filtered == 0


def test_mirror_mode_keeps_pattern_matched_mail_but_still_dedupes():
    """`mirror` gates layers 2-4 and must NOT gate layer 5. The two flags are
    independent and a single boolean covering both would pass every test above.
    """
    state = _State(processed={"seen-1"}, learned={"noisy@example.com"})
    msgs = [_msg("seen-1"), _msg("kept-1", sender="noisy@example.com")]

    clean, _filtered = intel.filter_noise(msgs, state, [], mirror=True)

    assert [m["message_id"] for m in clean] == ["kept-1"]


def test_the_two_flags_are_still_separate_parameters():
    """Pins the pair. Collapsing them into one is the plausible next edit, and
    the behavioural test above would survive it only by luck."""
    import inspect

    params = inspect.signature(intel.filter_noise).parameters

    assert params["check_processed"].default is True
    assert params["mirror"].default is False


@pytest.mark.parametrize("delta_days", [0, 1, 400])
def test_dedupe_does_not_depend_on_how_old_the_message_is(delta_days):
    """`is_processed` is an id-set membership test with no clock in it, and this
    holds it that way: a time-based expiry added later would re-admit old mail
    silently, which is the same digest-repeats-itself outcome by another route.
    """
    state = _State(processed={"seen-1"})
    msg = _msg("seen-1")
    msg["datetime"] = datetime.now(timezone.utc) - timedelta(days=delta_days)

    clean, filtered = intel.filter_noise([msg], state, [])

    assert clean == [] and filtered == 1
