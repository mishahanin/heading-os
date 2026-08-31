"""Shard 15-p2: `scripts/sync-exchange.py`, five findings, all five measured.

THE CALENDAR LANE DIED ON THE FIRST LINE THAT TOUCHED THE EVENTS.

`sync_calendar` opened with `sorted(events, key=lambda e: e.start)`. All-day
events arrive as an `EWSDate` and timed meetings as an `EWSDateTime`, which the
module's own `_to_local` docstring states as fact, and Python refuses to order
one against the other. Measured 2026-08-29 on exchangelib 5.6.0, against a range
holding one all-day event and one 09:00 meeting:

    TypeError: can't compare EWSDateTime to EWSDate
      File "scripts/sync-exchange.py", line 252, in sync_calendar

Everything careful below that line -- `_to_local`, the per-day grouping, the
duration guard, the stale-file prune -- was written for precisely these inputs
and none of it ever ran. `main` caught the TypeError, set `results["calendar"]`
to -1 and exited 1, so a week containing one all-day event had no calendar at
all.

The crash even depended on arrival order, which is why it survived so long:
`date.__lt__` compares against a datetime without complaint, `datetime.__lt__`
refuses, so `[all-day, timed]` raised and `[timed, all-day]` quietly sorted by
year, month and day with the clock ignored. A calendar view returns the all-day
item first.

The other four:

* `create_meeting` re-introduced the exact timezone bug `sync_calendar` carries a
  comment about having fixed: the calendar day came from `get_default_tz()` and
  the timestamp was stamped with the Exchange zone. Measured with
  HEADING_OS_TZ=UTC and EXCHANGE_TIMEZONE=Asia/Dubai at 2026-09-01T20:30Z, where
  the Dubai wall clock reads 2026-09-02 00:30: `--time 02:00` booked 2026-09-01
  02:00 +04:00, twenty two hours in the past.
* An empty mailbox returned before the write, so the file from the previous run
  stayed on disk. Measured: a second sync over an emptied inbox left
  `inbox-latest.md` still saying "Count: 1 emails" and still listing the message,
  with only the timestamp inside it to give the staleness away. The calendar lane
  writes its file for a zero-event range; the email lane did not.
* `--delete ""` parses to the empty string, which is falsy, so the branch was
  skipped and a destructive request ran an ordinary calendar-plus-email sync and
  exited 0. `delete_emails` raises ValueError on a blank query for exactly this
  reason and the blank value never reached it. Same for `--create-meeting ""`.
* `str(None)[:10]` is the four characters "None", so a malformed item produced a
  `## None` heading and a per-day file named `None.md`. `DAY_FILE_RE` never
  matches that name, so nothing could ever prune it.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("exchangelib")
from exchangelib import EWSDate, EWSDateTime, EWSTimeZone  # noqa: E402

DUBAI = ZoneInfo("Asia/Dubai")
EWS_DUBAI = EWSTimeZone.from_timezone(DUBAI)


def _load():
    spec = importlib.util.spec_from_file_location(
        "sync_exchange_shard15", str(ROOT / "scripts" / "sync-exchange.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sync_exchange_shard15"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sx():
    return _load()


@pytest.fixture
def cal_dir(sx, tmp_path, monkeypatch):
    """Every calendar write goes to tmp_path, never the operator's overlay."""
    target = tmp_path / "calendar"
    monkeypatch.setattr(sx, "calendar_dir", lambda p=target: p)
    return target


class Event:
    """The attribute surface `sync_calendar` reads, and nothing else."""

    def __init__(self, start, end=None, subject="Untitled", location=None):
        self.start = start
        self.end = end
        self.subject = subject
        self.location = location
        self.body = None
        self.required_attendees = None
        self.optional_attendees = None


def _account(events):
    return types.SimpleNamespace(
        calendar=types.SimpleNamespace(view=lambda start, end: list(events)))


def _run_calendar(sx, events, days=7):
    return sx.sync_calendar(_account(events), days=days, timezone_str="Asia/Dubai")


def _today():
    return datetime.now(DUBAI).date()


def _all_day(day=None, subject="Team offsite"):
    d = day or _today()
    return Event(EWSDate(d.year, d.month, d.day), subject=subject)


def _timed(hour, minute=0, day=None, subject="Acme Telecom call"):
    d = day or _today()
    start = EWSDateTime(d.year, d.month, d.day, hour, minute, 0, tzinfo=EWS_DUBAI)
    return Event(start, end=start + timedelta(minutes=30), subject=subject)


def _times_in(text):
    """The Time column of every row in the combined file, in written order."""
    return [line.split("|")[1].strip() for line in text.splitlines()
            if line.startswith("| ") and not line.startswith("| Time |")
            and not line.startswith("|---")]


# ============================================================
# 1. The sort that could not compare a date to a time
# ============================================================

def test_the_two_start_types_still_refuse_to_be_ordered():
    """The hazard is real and lives in exchangelib, not in our imagination.

    If a future exchangelib makes these comparable, this test says so and the
    helper below can be reconsidered. Both directions are asserted because the
    asymmetry is the reason the crash looked intermittent.
    """
    day = EWSDate(2026, 9, 1)
    at_nine = EWSDateTime(2026, 9, 1, 9, 0, 0, tzinfo=EWS_DUBAI)
    with pytest.raises(TypeError):
        sorted([day, at_nine])
    assert sorted([at_nine, day]) == [at_nine, day], (
        "the reverse order used to sort silently, on the day alone")


def test_an_all_day_event_beside_a_meeting_no_longer_kills_the_lane(sx, cal_dir):
    """The finding, in one call. This raised TypeError before the fix."""
    total = _run_calendar(sx, [_all_day(), _timed(9)])
    assert total == 2
    assert (cal_dir / "upcoming.md").exists()


def test_both_events_reach_the_file(sx, cal_dir):
    _run_calendar(sx, [_all_day(subject="Team offsite"), _timed(9)])
    text = (cal_dir / "upcoming.md").read_text(encoding="utf-8")
    assert "Team offsite" in text
    assert "Acme Telecom call" in text


def test_the_all_day_event_leads_its_own_day(sx, cal_dir):
    _run_calendar(sx, [_timed(9), _timed(14), _all_day()])
    assert _times_in((cal_dir / "upcoming.md").read_text(encoding="utf-8")) == [
        "All day", "09:00", "14:00"]


def test_the_order_events_arrive_in_does_not_change_the_file(sx, tmp_path,
                                                             monkeypatch):
    """The half that did not crash was silently sorting on the day alone.

    `[timed, all-day]` never raised, so a range in that order produced a file
    whose rows were ordered by year/month/day with every clock ignored. Two
    directories, one per arrival order, compared byte for byte apart from the
    minute-resolution Synced stamp.
    """
    def render(label, order):
        target = tmp_path / f"cal-{label}"
        monkeypatch.setattr(sx, "calendar_dir", lambda p=target: p)
        _run_calendar(sx, order)
        return _times_in((target / "upcoming.md").read_text(encoding="utf-8"))

    late, early, day = _timed(21), _timed(8), _all_day()
    assert render("a", [day, late, early]) == render("b", [late, early, day]) == [
        "All day", "08:00", "21:00"]


def test_a_missing_start_does_not_take_the_others_down(sx, cal_dir):
    """Two malformed items used to raise on `None < None`'s neighbours."""
    total = _run_calendar(sx, [_timed(9), Event(None, subject="Broken"),
                               Event(None, subject="Also broken")])
    assert total == 3


def test_a_missing_start_sorts_last_not_first(sx, cal_dir):
    _run_calendar(sx, [Event(None, subject="Broken"), _timed(9)])
    text = (cal_dir / "upcoming.md").read_text(encoding="utf-8")
    assert text.index("Acme Telecom call") < text.index("Broken")


@pytest.mark.parametrize("value", [None, "not a date", 42, object()])
def test_the_sort_key_never_raises_on_a_start_it_cannot_read(sx, value):
    """Whatever the server sends, the key is a tuple of ints."""
    key = sx._start_sort_key(value, DUBAI)
    assert all(isinstance(part, int) for part in key)


def test_an_unreadable_start_sorts_after_every_real_one(sx):
    real = sx._start_sort_key(
        EWSDateTime(9999, 12, 31, 23, 59, 0, tzinfo=EWS_DUBAI), DUBAI)
    assert real < sx._start_sort_key(None, DUBAI)


def test_a_naive_datetime_start_still_sorts_by_its_clock(sx):
    """`_to_local` handles a naive datetime, so the key must use its result."""
    early = sx._start_sort_key(datetime(2026, 9, 1, 8, 0), DUBAI)  # noqa: DTZ001
    late = sx._start_sort_key(datetime(2026, 9, 1, 20, 0), DUBAI)  # noqa: DTZ001
    assert early < late


# ============================================================
# 2. The meeting stamped with one zone and dated from another
# ============================================================

# 2026-09-01T20:30Z is 2026-09-02 00:30 in Asia/Dubai. Half an hour past
# midnight on one side of the world and still yesterday evening on the other is
# what makes the two readings of "today" visibly different.
STRADDLES_MIDNIGHT = datetime(2026, 9, 1, 20, 30, tzinfo=timezone.utc)


@pytest.fixture
def booked(sx, monkeypatch):
    """Freeze the clock, fake exchangelib, and capture what got saved."""
    created = []

    class FakeCalendarItem:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(self)

        def save(self, send_meeting_invitations=None):
            pass

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return STRADDLES_MIDNIGHT.astimezone(tz) if tz else STRADDLES_MIDNIGHT

    # `Account` first: `_ensure_exchangelib` returns early only when it is set,
    # and otherwise imports the real library over every fake below.
    monkeypatch.setattr(sx, "Account", object)
    monkeypatch.setattr(sx, "CalendarItem", FakeCalendarItem)
    monkeypatch.setattr(sx, "datetime", FrozenDatetime)
    monkeypatch.setattr(sx, "EWSTimeZone",
                        types.SimpleNamespace(from_timezone=lambda z: z))
    monkeypatch.setattr(sx, "EWSDateTime",
                        lambda *a, **k: datetime(*a[:6], tzinfo=k.get("tzinfo")))
    monkeypatch.setenv("HEADING_OS_TZ", "UTC")
    return created


def _create(sx, subject="Standup", start_time="02:00", zone="Asia/Dubai"):
    sx.create_meeting(types.SimpleNamespace(calendar=object()),
                      subject=subject, start_time=start_time, timezone_str=zone)


def test_a_bare_clock_time_books_the_day_of_the_exchange_zone(sx, booked):
    """The finding. This booked 2026-09-01, twenty two hours in the past."""
    _create(sx)
    assert booked[0].kwargs["start"].date().isoformat() == "2026-09-02"


def test_the_booked_meeting_is_not_in_the_past(sx, booked):
    """The consequence, stated the way the operator met it."""
    _create(sx)
    assert booked[0].kwargs["start"] > STRADDLES_MIDNIGHT


def test_the_clock_the_operator_typed_is_the_clock_that_is_booked(sx, booked):
    _create(sx, start_time="02:00")
    assert booked[0].kwargs["start"].strftime("%H:%M") == "02:00"


def test_an_explicit_date_is_still_taken_verbatim(sx, booked):
    """The other branch is unchanged and must stay that way."""
    _create(sx, start_time="2026-07-16 11:00")
    assert booked[0].kwargs["start"].strftime("%Y-%m-%d %H:%M") == "2026-07-16 11:00"


def test_the_two_zones_agreeing_is_still_the_same_answer(sx, booked, monkeypatch):
    """A control: the fix must not move the day when nothing straddles."""
    monkeypatch.setenv("HEADING_OS_TZ", "Asia/Dubai")
    _create(sx)
    assert booked[0].kwargs["start"].date().isoformat() == "2026-09-02"


# ============================================================
# 3. The mailbox that emptied and the file that did not
# ============================================================

class Mail:
    def __init__(self, subject, sender="q.branch@example.com"):
        self.subject = subject
        self.sender = types.SimpleNamespace(email_address=sender, name="Q Branch")
        self.datetime_received = datetime(2026, 8, 1, 9, 0, tzinfo=DUBAI)
        self.is_read = True
        self.to_recipients = None
        self.cc_recipients = None
        self.has_attachments = False
        self.attachments = None
        self.text_body = "Body text."
        self.body = "Body text."


class FakeFolder(list):
    def order_by(self, *_a):
        return self

    def all(self):
        return self

    def filter(self, **_k):
        return self


@pytest.fixture
def mail_dir(sx, tmp_path, monkeypatch):
    """tmp_path for the write, and a stub for the CRM bump.

    `sync_emails` imports `bump_inbound` inside its own loop body, so patching a
    module attribute here binds nothing. The stub goes into `sys.modules` where
    that import will find it, which is also what keeps a test mailbox from
    touching the operator's real contact files.
    """
    bumped = []
    monkeypatch.setattr(sx, "email_dir", lambda p=tmp_path / "emails": p)
    monkeypatch.setitem(
        sys.modules, "scripts.utils.crm_autolog",
        types.SimpleNamespace(bump_inbound=lambda **kw: bumped.append(kw)))
    return tmp_path / "emails"


def _run_emails(sx, mails):
    return sx.sync_emails(types.SimpleNamespace(inbox=FakeFolder(mails)), count=30)


def test_an_emptied_mailbox_rewrites_the_file(sx, mail_dir):
    """The finding. The first run's file used to survive the second, intact."""
    _run_emails(sx, [Mail("Acme Telecom quote")])
    first = (mail_dir / "inbox-latest.md").read_text(encoding="utf-8")
    _run_emails(sx, [])
    second = (mail_dir / "inbox-latest.md").read_text(encoding="utf-8")
    assert first != second


def test_the_rewritten_file_does_not_still_list_last_run_s_mail(sx, mail_dir):
    _run_emails(sx, [Mail("Acme Telecom quote")])
    _run_emails(sx, [])
    text = (mail_dir / "inbox-latest.md").read_text(encoding="utf-8")
    assert "Acme Telecom quote" not in text
    assert "> Count: 0 emails" in text


def test_an_empty_first_run_still_writes_the_file(sx, mail_dir):
    """No previous run to be stale: the directory must not be simply empty."""
    _run_emails(sx, [])
    assert (mail_dir / "inbox-latest.md").exists()


def test_an_empty_sync_still_reports_zero(sx, mail_dir):
    assert _run_emails(sx, []) == 0


def test_a_non_empty_sync_is_unchanged(sx, mail_dir):
    assert _run_emails(sx, [Mail("Acme Telecom quote"), Mail("Q4 renewal")]) == 2
    text = (mail_dir / "inbox-latest.md").read_text(encoding="utf-8")
    assert "> Count: 2 emails" in text
    assert "Q4 renewal" in text


# ============================================================
# 4. The blank destructive flag that ran an unrelated sync
# ============================================================

@pytest.fixture
def cli(sx, monkeypatch):
    """Record which lane `main` chose, with every lane stubbed."""
    lanes = []

    def _delete(_account, subject_query, folder_name="Inbox", confirm=True):
        lanes.append("delete")
        return 0

    monkeypatch.setattr(sx, "load_config",
                        lambda: {"EXCHANGE_TIMEZONE": "Asia/Dubai"})
    monkeypatch.setattr(sx, "connect", lambda _cfg: object())
    monkeypatch.setattr(sx, "delete_emails", _delete)
    monkeypatch.setattr(sx, "create_meeting",
                        lambda *a, **k: lanes.append("create"))
    monkeypatch.setattr(sx, "sync_calendar",
                        lambda *a, **k: (lanes.append("calendar"), 0)[1])
    monkeypatch.setattr(sx, "sync_emails",
                        lambda *a, **k: (lanes.append("emails"), 0)[1])
    return lanes


def _main(sx, monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["sync-exchange.py", *argv])
    try:
        return sx.main()
    except SystemExit as exc:
        return exc.code


def test_a_blank_delete_never_reaches_the_sync_lanes(sx, cli, monkeypatch):
    """The finding: `--delete ""` ran a full calendar and email sync, exit 0."""
    _main(sx, monkeypatch, ["--delete", ""])
    assert "calendar" not in cli
    assert "emails" not in cli


def test_a_blank_delete_reaches_the_validator_that_exists_for_it(sx, cli,
                                                                 monkeypatch):
    """It must arrive at `delete_emails`, which is where the refusal lives."""
    _main(sx, monkeypatch, ["--delete", ""])
    assert cli == ["delete"]


def test_the_real_delete_guard_refuses_a_blank_query(sx):
    """The guard the falsy check was routing around, asserted directly."""
    with pytest.raises(ValueError, match="blank"):
        sx.delete_emails(object(), subject_query="", folder_name="Inbox")


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_create_meeting_never_reaches_the_sync_lanes(sx, cli, monkeypatch,
                                                             blank):
    code = _main(sx, monkeypatch, ["--create-meeting", blank, "--time", "14:30"])
    assert code == 1
    assert cli == []


def test_a_real_delete_query_still_deletes(sx, cli, monkeypatch):
    assert _main(sx, monkeypatch, ["--delete", "Acme Telecom quote"]) == 0
    assert cli == ["delete"]


def test_a_real_meeting_subject_still_creates(sx, cli, monkeypatch):
    assert _main(sx, monkeypatch,
                 ["--create-meeting", "Standup", "--time", "14:30"]) == 0
    assert cli == ["create"]


def test_no_flag_at_all_still_syncs_both_lanes(sx, cli, monkeypatch):
    assert _main(sx, monkeypatch, []) == 0
    assert cli == ["calendar", "emails"]


# ============================================================
# 5. The day file named None that nothing could prune
# ============================================================

def test_a_missing_start_writes_no_none_day_file(sx, cal_dir):
    """The finding: `None.md` appeared and no prune pass could ever remove it."""
    _run_calendar(sx, [Event(None, subject="Broken")])
    assert not (cal_dir / "None.md").exists()


def test_the_undated_bucket_gets_no_day_file_of_its_own(sx, cal_dir):
    """A renamed key is only a fix if the writer also stops writing it."""
    _run_calendar(sx, [Event(None, subject="Broken"), _timed(9)])
    written = sorted(p.name for p in cal_dir.glob("*.md"))
    assert written, "the corpus is empty; this test would pass over nothing"
    assert written == sorted(["upcoming.md", f"{_today()}.md"])


def test_every_day_file_written_is_one_the_prune_pass_can_match(sx, cal_dir):
    """The property the `None.md` name broke, asserted over the real output."""
    _run_calendar(sx, [Event(None, subject="Broken"), _all_day(), _timed(9)])
    days = [p.name for p in cal_dir.glob("*.md") if p.name != "upcoming.md"]
    assert days, "no per-day file was written; the assertion below is vacuous"
    assert all(sx.DAY_FILE_RE.match(name) for name in days)


def test_the_undated_event_is_still_listed_for_the_operator(sx, cal_dir):
    """Dropping it silently would be the worse fix."""
    _run_calendar(sx, [Event(None, subject="Broken")])
    text = (cal_dir / "upcoming.md").read_text(encoding="utf-8")
    assert "Broken" in text
    assert "## None" not in text
    assert f"## {sx.UNDATED_KEY}" in text


def test_a_real_day_still_gets_its_per_day_file(sx, cal_dir):
    _run_calendar(sx, [_timed(9)])
    assert (cal_dir / f"{_today()}.md").exists()
