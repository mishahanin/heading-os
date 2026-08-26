"""Shard scripts-14-p3: the Exchange sync lane, its pulse, and the STE gate.

* `sync_calendar` and `sync_emails` printed their success line through
  `output_file.relative_to(get_data_root())`. On an exec workspace the outputs
  tree resolves under `../.heading-os-data-{slug}` while `get_data_root()`
  resolves under `../.heading-os-data`, so `relative_to` raised ValueError right
  after upcoming.md was written. `main()` caught it, printed "Calendar sync
  failed", and exited 1 for a sync that had SUCCEEDED. Worse, the raise aborted
  the function mid-way: the per-day `YYYY-MM-DD.md` files were never written and
  the stale-file prune never ran, so old day files accumulated forever.

* The detail section heading sliced characters 11:16 out of `str(event.start)`,
  which is the UTC wall clock, while the table above it used
  `_event_time_str(..., local_tz)`. One meeting, two different times in one
  file, four hours apart in Asia/Dubai. The wrong one titled the section that
  carries the agenda and the attendees.

* The "Synced:" header stamped `datetime.now(get_default_tz())` and labelled it
  `EXCHANGE_TIMEZONE`, which the operator may set independently. The same
  mismatch chose the window's start date.

* `scripts/sync-exchange-pulse.py` printed "started pid N" and returned 0 on the
  pid `Popen` hands back the instant it forks. A daemon that died on startup
  read as healthy forever, which is the exact failure the comment three lines
  above says was fixed for the spawn-failed lane.

* `scripts/ste-check.py` split a wrapped numbered step into a step unit plus a
  loose prose unit, so the step's sentence was measured in halves and never
  against the 20-word limit. Identical prose passed or failed purely on whether
  the author hard wrapped it, and the gate then called the file clean.

Nothing here contacts Exchange. Every account object is a stand-in.

Run: python3 -m pytest tests/test_a_sync_that_reported_failure_after_it_succeeded.py
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sx():
    return _load("scripts/sync-exchange.py", "sx_under_test")


@pytest.fixture(scope="module")
def pulse():
    return _load("scripts/sync-exchange-pulse.py", "pulse_under_test")


@pytest.fixture(scope="module")
def ste():
    return _load("scripts/ste-check.py", "ste_under_test")


# ============================================================
# The path that aborted a successful sync
# ============================================================

def _roots(sx, monkeypatch, personal: Path, data: Path, engine: Path):
    monkeypatch.setattr(sx, "get_personal_root", lambda: personal)
    monkeypatch.setattr(sx, "get_data_root", lambda: data)
    monkeypatch.setattr(sx, "get_workspace_root", lambda: engine)


def test_an_exec_outputs_path_is_shortened_not_refused(sx, monkeypatch, tmp_path):
    """The finding. These are different trees, so `relative_to(get_data_root())`
    raised and took the rest of the function with it."""
    _roots(sx, monkeypatch, tmp_path / "data-bond", tmp_path / "data",
           tmp_path / "engine")
    target = tmp_path / "data-bond" / "outputs" / "_sync" / "calendar" / "upcoming.md"

    assert sx._display_path(target) == "outputs/_sync/calendar/upcoming.md"


def test_the_ceo_path_is_unchanged(sx, monkeypatch, tmp_path):
    """On the CEO workspace both roots are the same directory, which is why the
    defect never showed here."""
    same = tmp_path / "data"
    _roots(sx, monkeypatch, same, same, tmp_path / "engine")
    target = same / "outputs" / "_sync" / "emails" / "inbox-recent.md"

    assert sx._display_path(target) == "outputs/_sync/emails/inbox-recent.md"


def test_a_path_under_the_engine_is_shortened_too(sx, monkeypatch, tmp_path):
    _roots(sx, monkeypatch, tmp_path / "data", tmp_path / "data",
           tmp_path / "engine")

    assert sx._display_path(tmp_path / "engine" / "a" / "b.md") == "a/b.md"


@pytest.mark.parametrize("odd", [
    "/elsewhere/x.md",
    "/",
    "relative/path.md",
])
def test_an_unrelatable_path_degrades_instead_of_raising(sx, monkeypatch,
                                                          tmp_path, odd):
    """A cosmetic path in a log line must never be able to abort its caller."""
    _roots(sx, monkeypatch, tmp_path / "data", tmp_path / "data",
           tmp_path / "engine")

    assert sx._display_path(Path(odd)) == odd


# ============================================================
# The meeting shown at two times
# ============================================================

class _Body:
    def __init__(self, text):
        self.text = text

    def __str__(self):
        return self.text

    def __bool__(self):
        return bool(self.text)


class _Event:
    def __init__(self, start, end, subject, location=None, body="", attendees=None):
        self.start = start
        self.end = end
        self.subject = subject
        self.location = location
        self.body = _Body(body)
        self.required_attendees = attendees or []
        self.optional_attendees = []


class _Calendar:
    def __init__(self, events):
        self._events = events

    def view(self, start, end):
        return list(self._events)


class _Account:
    def __init__(self, events):
        self.calendar = _Calendar(events)


def _utc_event(sx, hour, subject="Synthetic standup", body="Agenda body text"):
    sx._ensure_exchangelib()
    from exchangelib import EWSDateTime, EWSTimeZone
    utc = EWSTimeZone.from_timezone(ZoneInfo("UTC"))
    from datetime import datetime as _dt
    today = _dt.now(ZoneInfo("Asia/Dubai")).date()
    start = EWSDateTime(today.year, today.month, today.day, hour, 0, 0, tzinfo=utc)
    end = EWSDateTime(today.year, today.month, today.day, hour, 30, 0, tzinfo=utc)
    return _Event(start, end, subject, location="Room A", body=body)


def _run_calendar(sx, monkeypatch, tmp_path, events, tz="Asia/Dubai", days=2):
    cal = tmp_path / "calendar"
    monkeypatch.setattr(sx, "CALENDAR_DIR", cal)
    _roots(sx, monkeypatch, tmp_path, tmp_path, tmp_path)
    total = sx.sync_calendar(_Account(events), days=days, timezone_str=tz)
    return total, (cal / "upcoming.md").read_text(encoding="utf-8"), cal


def test_the_detail_heading_matches_the_table(sx, monkeypatch, tmp_path):
    """A 09:00 UTC meeting in Asia/Dubai is 13:00. The table said 13:00 and the
    heading below it said 09:00."""
    _, text, _ = _run_calendar(sx, monkeypatch, tmp_path, [_utc_event(sx, 9)])

    assert "| 13:00 | Synthetic standup" in text
    assert "### 13:00 - Synthetic standup" in text
    assert "### 09:00" not in text


def test_the_two_clocks_agree_for_every_event(sx, monkeypatch, tmp_path):
    """Not one hour: any event whose local and UTC clocks differ."""
    events = [_utc_event(sx, h, subject=f"Meeting {h}") for h in (6, 9, 15, 21)]

    _, text, _ = _run_calendar(sx, monkeypatch, tmp_path, events)

    for line in text.splitlines():
        if line.startswith("### "):
            clock = line[4:9]
            assert f"| {clock} |" in text, f"detail {clock} has no table row"


def test_a_sync_on_an_exec_workspace_writes_the_per_day_files(sx, monkeypatch,
                                                              tmp_path):
    """The real harm behind the ValueError: the raise sat BEFORE these writes."""
    _roots(sx, monkeypatch, tmp_path / "data-bond", tmp_path / "data",
           tmp_path / "engine")
    cal = tmp_path / "data-bond" / "outputs" / "_sync" / "calendar"
    monkeypatch.setattr(sx, "CALENDAR_DIR", cal)

    sx.sync_calendar(_Account([_utc_event(sx, 9)]), days=2,
                     timezone_str="Asia/Dubai")

    day_files = [p.name for p in cal.glob("*.md") if p.name != "upcoming.md"]
    assert day_files, "the per-day files were never written"


def test_a_sync_on_an_exec_workspace_prunes_stale_day_files(sx, monkeypatch,
                                                            tmp_path):
    """The prune is the last statement in the function, so it died first."""
    from datetime import datetime as _dt
    _roots(sx, monkeypatch, tmp_path / "data-bond", tmp_path / "data",
           tmp_path / "engine")
    cal = tmp_path / "data-bond" / "outputs" / "_sync" / "calendar"
    cal.mkdir(parents=True)
    today = _dt.now(ZoneInfo("Asia/Dubai")).date()
    stale = cal / f"{today.isoformat()}.md"
    stale.write_text("# cancelled meeting from an earlier run\n", encoding="utf-8")
    monkeypatch.setattr(sx, "CALENDAR_DIR", cal)

    sx.sync_calendar(_Account([]), days=2, timezone_str="Asia/Dubai")

    assert not stale.exists(), "the stale day file survived the sync"


def _freeze(sx, monkeypatch, default_zone="Asia/Dubai"):
    """Pin the clock to one instant on which three zones hold three dates.

    2026-08-26 20:30 UTC is 00:30 on the 27th in Asia/Dubai, and 20:30 on the
    26th in UTC itself. Without a pin the two zones under test usually share a
    date, and a test that cannot tell them apart proves nothing.
    """
    from datetime import datetime as _dt
    instant = _dt(2026, 8, 26, 20, 30, tzinfo=ZoneInfo("UTC"))

    class _Frozen(_dt):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant

    monkeypatch.setattr(sx, "datetime", _Frozen)
    monkeypatch.setattr(sx, "get_default_tz", lambda: ZoneInfo(default_zone))
    return instant


def test_the_synced_stamp_is_in_the_zone_it_names(sx, monkeypatch, tmp_path):
    """It printed the default-zone clock beside the Exchange-zone label."""
    _freeze(sx, monkeypatch)

    _, text, _ = _run_calendar(sx, monkeypatch, tmp_path, [], tz="UTC")

    stamp = [ln for ln in text.splitlines() if ln.startswith("> Synced:")][0]
    assert "(UTC)" in stamp
    assert "2026-08-26 20:30" in stamp, f"stamped in the wrong zone: {stamp}"


def test_the_per_day_header_states_its_zone_too(sx, monkeypatch, tmp_path):
    _, _, cal = _run_calendar(sx, monkeypatch, tmp_path, [_utc_event(sx, 9)])

    day = [p for p in cal.glob("*.md") if p.name != "upcoming.md"][0]

    assert "(Asia/Dubai)" in day.read_text(encoding="utf-8")


def test_the_window_starts_on_todays_date_in_the_named_zone(sx, monkeypatch,
                                                            tmp_path):
    """`now` used to come from the default zone and then be stamped with the
    Exchange zone, so the window could open on the wrong calendar day. At the
    pinned instant Asia/Dubai is already the 27th while UTC is still the 26th,
    so the two answers are a day apart."""
    _freeze(sx, monkeypatch, default_zone="Asia/Dubai")

    _, text, _ = _run_calendar(sx, monkeypatch, tmp_path, [], tz="UTC")

    assert "> Range: 2026-08-26 to 2026-08-28" in text
    assert "> Range: 2026-08-27" not in text


# ============================================================
# The email lane, which carries the identical line
# ============================================================

class _Mailbox:
    def __init__(self, addr, name=None):
        self.email_address = addr
        self.name = name


class _Email:
    def __init__(self, subject):
        self.subject = subject
        self.datetime_received = "2026-08-26 09:00:00+00:00"
        self.sender = _Mailbox("nobody@example.invalid", "Nobody")
        self.is_read = True
        self.to_recipients = []
        self.cc_recipients = []
        self.has_attachments = False
        self.attachments = []
        self.text_body = "Body text."
        self.body = "Body text."


class _Query:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self

    def filter(self, **kwargs):
        return self

    def order_by(self, key):
        return self

    def __getitem__(self, sl):
        return self._items[sl]


class _MailAccount:
    def __init__(self, emails):
        self.inbox = _Query(emails)
        self.sent = self.inbox
        self.drafts = self.inbox


def test_the_email_lane_survives_an_exec_workspace(sx, monkeypatch, tmp_path):
    """`sync_emails` carries the identical `relative_to` line, so it had the
    identical defect. CRM auto-bump is stubbed: nothing writes to a contact."""
    import types as _types
    stub = _types.ModuleType("scripts.utils.crm_autolog")
    bumped = []
    stub.bump_inbound = lambda *a, **k: bumped.append(a)
    monkeypatch.setitem(sys.modules, "scripts.utils.crm_autolog", stub)
    _roots(sx, monkeypatch, tmp_path / "data-bond", tmp_path / "data",
           tmp_path / "engine")
    mail = tmp_path / "data-bond" / "outputs" / "_sync" / "emails"
    monkeypatch.setattr(sx, "EMAIL_DIR", mail)

    count = sx.sync_emails(_MailAccount([_Email("Hello")]), count=5)

    assert count == 1
    assert (mail / "inbox-latest.md").exists()


def test_the_email_lane_prints_the_shortened_path(sx, monkeypatch, tmp_path,
                                                   capsys):
    import types as _types
    stub = _types.ModuleType("scripts.utils.crm_autolog")
    stub.bump_inbound = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "scripts.utils.crm_autolog", stub)
    _roots(sx, monkeypatch, tmp_path / "data-bond", tmp_path / "data",
           tmp_path / "engine")
    monkeypatch.setattr(sx, "EMAIL_DIR",
                        tmp_path / "data-bond" / "outputs" / "_sync" / "emails")

    sx.sync_emails(_MailAccount([_Email("Hello")]), count=5)

    out = capsys.readouterr().out
    assert "[OK] Emails: 1 saved to outputs/_sync/emails/inbox-latest.md" in out


# ============================================================
# The daemon that was started and never checked
# ============================================================

def _fake_daemon(pulse, monkeypatch, tmp_path, source: str):
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "sync-exchange-daemon.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(pulse, "WORKSPACE", tmp_path)
    monkeypatch.setattr(pulse, "_resolve_pythonw", lambda: Path(sys.executable))
    monkeypatch.setattr(pulse, "STARTUP_SETTLE_SECONDS", 1.0)


def test_a_daemon_that_dies_at_once_is_not_called_started(pulse, monkeypatch,
                                                          tmp_path):
    """The finding: Popen returns a pid the instant it forks."""
    _fake_daemon(pulse, monkeypatch, tmp_path, "import sys\nsys.exit(1)\n")

    assert pulse._spawn_detached_daemon() is None


def test_a_daemon_that_dies_with_code_zero_is_also_not_started(pulse, monkeypatch,
                                                               tmp_path):
    """Its own 'another instance is starting' path can exit cleanly."""
    _fake_daemon(pulse, monkeypatch, tmp_path, "import sys\nsys.exit(0)\n")

    assert pulse._spawn_detached_daemon() is None


def test_a_daemon_that_survives_reports_its_pid(pulse, monkeypatch, tmp_path):
    """The guard must not report a healthy start as a failure."""
    _fake_daemon(pulse, monkeypatch, tmp_path, "import time\ntime.sleep(30)\n")

    pid = pulse._spawn_detached_daemon()
    try:
        assert isinstance(pid, int) and pid > 0
    finally:
        import os
        import signal
        with __import__("contextlib").suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def test_a_missing_daemon_script_is_still_a_spawn_failure(pulse, monkeypatch,
                                                          tmp_path):
    monkeypatch.setattr(pulse, "WORKSPACE", tmp_path)
    monkeypatch.setattr(pulse, "_resolve_pythonw", lambda: Path(sys.executable))

    assert pulse._spawn_detached_daemon() is None


def test_the_failure_message_names_both_ways_it_can_fail(pulse):
    src = (ROOT / "scripts" / "sync-exchange-pulse.py").read_text(encoding="utf-8")

    assert "exited straight after starting" in src


def test_the_settle_window_is_a_named_constant(pulse):
    """A literal here would be invisible to a test and to an operator."""
    assert pulse.STARTUP_SETTLE_SECONDS > 0


# ============================================================
# The step measured in halves
# ============================================================

def test_a_wrapped_step_is_one_unit(ste):
    """The finding. Two lines, one step."""
    text = "1. Open the file and\n   read the header.\n"

    units = ste.parse_units(text)

    assert len(units) == 1
    assert units[0]["kind"] == "step"
    assert units[0]["text"] == "Open the file and read the header."


def test_a_wrapped_step_is_measured_whole(ste):
    """Twenty-three words in a step, hard wrapped. It used to pass."""
    words = " ".join(f"word{i}" for i in range(1, 23))
    text = f"1. {words}\n   and one more word.\n"

    units = ste.parse_units(text)

    assert len(units) == 1
    assert ste.word_count(units[0]["text"]) > 20


def test_the_same_step_on_one_line_gives_the_same_unit(ste):
    """The whole defect: identical prose passed or failed on line breaks."""
    one = ste.parse_units("1. Open the file and read the header.\n")
    two = ste.parse_units("1. Open the file and\n   read the header.\n")

    assert one[0]["text"] == two[0]["text"]


def test_a_wrapped_bullet_is_one_unit_too(ste):
    units = ste.parse_units("- Open the file and\n  read the header.\n")

    assert len(units) == 1
    assert units[0]["kind"] == "prose"


def test_a_blank_line_still_ends_the_item(ste):
    """A paragraph after a blank line is its own unit, not a continuation."""
    units = ste.parse_units("1. Open the file.\n\nThis is separate prose.\n")

    assert len(units) == 2
    assert units[1]["kind"] == "prose"
    assert units[1]["text"] == "This is separate prose."


def test_a_second_step_ends_the_first(ste):
    units = ste.parse_units("1. Open the file.\n2. Read the header.\n")

    assert [u["text"] for u in units] == ["Open the file.", "Read the header."]


def test_a_heading_ends_the_item(ste):
    units = ste.parse_units("1. Open the file.\n## Next section\nProse here.\n")

    assert units[0]["text"] == "Open the file."
    assert any(u["text"] == "Prose here." for u in units)


def test_a_table_row_ends_the_item(ste):
    units = ste.parse_units("- A bullet.\n| a | b |\n")

    assert len(units) == 1
    assert units[0]["text"] == "A bullet."


def test_leading_prose_is_untouched(ste):
    """A paragraph before any list item must not be folded anywhere."""
    units = ste.parse_units("Plain prose line one.\nPlain prose line two.\n")

    assert len(units) == 1
    assert units[0]["text"] == "Plain prose line one. Plain prose line two."


def test_a_line_number_points_at_the_item_not_the_continuation(ste):
    """The reported line has to be the one the author edits."""
    units = ste.parse_units("intro\n\n1. Open the file and\n   read the header.\n")

    step = [u for u in units if u["kind"] == "step"][0]
    assert step["line"] == 3


def test_a_sentence_opening_with_a_slash_command_is_split(ste):
    """Found while fixing the corpus: this engine starts sentences with a slash
    command, and the opener class did not accept `/`. Two sentences measured as
    one 22-word sentence, and a skill had to be reworded around the tool."""
    text = "Working tree has changes. /calibrate's auto-commit will include them."

    assert len(ste.split_sentences(text)) == 2


def test_a_version_number_still_does_not_split_before_a_path(ste):
    """The other direction. `(?<![A-Z0-9])` is what keeps this whole."""
    assert len(ste.split_sentences("Upgrade to v1.2 /opt is the target.")) == 1


def test_an_ordinary_capital_opener_still_splits(ste):
    assert len(ste.split_sentences("First one. Second one.")) == 2


# ============================================================
# The gates the parser fix has to leave green
# ============================================================

@pytest.mark.parametrize("scope", ["--all", "--skills"])
def test_the_shipped_corpus_passes_the_gate(scope):
    """The parser fix exposed 39 sentences that were never measured. This holds
    the corpus at zero errors so they cannot creep back."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ste-check.py"), scope, "--quiet"],
        cwd=str(ROOT), capture_output=True, text=True)

    assert proc.returncode == 0, proc.stdout[-4000:]


# ============================================================
# The status subcommand that could not do what it promised
# ============================================================

def test_the_daemon_docstring_does_not_promise_a_next_run():
    """cmd_status reads a PID file from another process. It has no scheduler."""
    src = (ROOT / "scripts" / "sync-exchange-daemon.py").read_text(encoding="utf-8")
    head = src.split('"""')[1]

    assert "next scheduled run" not in head
    assert "cannot report a next fire time" in head


# ============================================================
# The lazy names two functions read without ever binding them
# ============================================================

_COLD_PROBE = '''
import importlib.util, sys, tempfile
from pathlib import Path
ROOT = Path({root!r})
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("sx", ROOT / "scripts" / "sync-exchange.py")
sx = importlib.util.module_from_spec(spec)
sys.modules["sx"] = sx
spec.loader.exec_module(sx)
assert sx.EWSTimeZone is None, "the module bound it at import time; probe is void"

class _Cal:
    @staticmethod
    def view(*a, **k):
        return []

class _Account:
    calendar = _Cal()

with tempfile.TemporaryDirectory() as td:
    sx.CALENDAR_DIR = Path(td) / "cal"
    sx.sync_calendar(_Account(), days=1, timezone_str="UTC")
print("COLD_OK")
'''


def test_sync_calendar_binds_exchangelib_before_it_reads_it():
    """`EWSTimeZone` is a module global that only `_ensure_exchangelib` sets,
    and `sync_calendar` read it without calling that. It worked only because
    `main()` connects first, so the gap was invisible in production and showed
    up as an order-dependent suite failure instead: on 2026-08-26 this file
    failed under xdist with `AttributeError: 'NoneType' object has no attribute
    'from_timezone'` after an unrelated test moved it to another worker.

    Run in a SUBPROCESS on purpose. Inside the suite some earlier test may
    already have bound the globals, and then this proves nothing at all; a
    fresh interpreter is the only place the cold path exists.
    """
    proc = subprocess.run(
        [sys.executable, "-c", _COLD_PROBE.format(root=str(ROOT))],
        capture_output=True, text=True, timeout=120)

    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "COLD_OK" in proc.stdout, proc.stdout[-2000:]


@pytest.mark.parametrize("name", ["sync_calendar", "create_meeting"])
def test_every_function_reading_a_lazy_name_binds_it_first(name):
    """The class, not the instance. Both functions read the same globals."""
    import ast

    tree = ast.parse((ROOT / "scripts" / "sync-exchange.py").read_text(encoding="utf-8"))
    lazy = {"Account", "CalendarItem", "Configuration", "Credentials",
            "DELEGATE", "EWSDateTime", "EWSTimeZone"}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name != name:
            continue
        used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} & lazy
        assert used, f"{name} no longer reads a lazy name; drop it from this test"
        binds = any(isinstance(c, ast.Call)
                    and getattr(c.func, "id", "") == "_ensure_exchangelib"
                    for c in ast.walk(fn))
        assert binds, f"{name} reads {sorted(used)} without calling _ensure_exchangelib()"
        return
    raise AssertionError(f"{name} not found in sync-exchange.py")
