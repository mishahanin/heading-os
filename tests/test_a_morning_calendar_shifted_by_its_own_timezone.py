"""Shard 06-p4: the morning dashboard, and the newsletter renderer.

k3 read `scripts/generate-dashboard.py` and `scripts/generate-newsletter-html.py`
and returned seven findings. All seven were confirmed against source. Pulling on
the smallest of them -- finding 5, "a non-zero-padded time is silently dropped",
severity LOW, hedged with "if the Exchange sync always emits HH:MM 24-hour, this
is unreachable" -- found the one it was standing next to.

THE CALENDAR WAS CONVERTED FROM A TIMEZONE IT WAS NEVER IN.

`sync-exchange.sync_calendar` groups events by `_to_local(...).date()` and writes
every Time cell through `_event_time_str`, which is
`event.start.astimezone(local_tz).strftime("%H:%M")`. Both have been local since
the engine's initial import; `upcoming.md` has never held UTC.

`collect_calendar` believed it did, from that same initial import. It began by
adding a constant `CALENDAR_UTC_OFFSET_HOURS = 4` under the comment "Convert
meeting times from UTC to the configured local timezone". On 2026-08-23 an
earlier night of THIS audit replaced the constant with a tz-aware `astimezone`
and filtered on the converted date -- removing the hardcoding, keeping the false
premise, and making the symptom worse. Measured on Asia/Dubai, against the
fixture below:

    file says          09:00, 21:00
    dashboard rendered 13:00          <- and the 21:00 meeting was GONE

The 21:00 became 01:00 tomorrow and failed the "is it today" filter. A meeting
the CEO is never shown is the worst thing this page can do, and the page did it
every evening. The fix is to delete the conversion, not to improve it.

The other six, and one the report did not raise:

* "All Clear" was reachable from an ERROR. `collect_crm_health` returns an empty
  skeleton when the scan raises, and `build_urgent` cannot tell an empty
  skeleton from a quiet morning. One contact dict missing one key was enough:
  every field in the normaliser was a bare index, so the first bad contact
  raised before ANY bucket was filled. `health_bucket` had been hardened against
  precisely this outcome; the loop above it had not.
* `days_since` reached `>= 7` unguarded, one line below the sibling field that
  gets `_as_int_or_count` under a docstring saying the producer never promised
  a shape.
* `completion_rate` reached `{rate:.0f}%` unvalidated: a string killed the run
  at format time, and a fraction rendered 0.87 as "1%".
* `_recent` read as a fallback chain and behaved as first-hit-wins, so a note
  ingested this week with an old `date` was missing from "Signals Captured (7d)".
* The newsletter's hero title and `signal_watch` items were the two text paths
  in that file NOT coerced with `str()`, in a file that already carries that
  guard twice with comments explaining why.
* NOT IN THE REPORT: `generate-dashboard.py` prints the literal words "the
  configured timezone" in its cover and its datebar -- the same placeholder
  found in `generate-crm-dashboard.py` during shard 06-p3, in a second file.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gd():
    return _load("dashboard_k3_p4", "scripts/generate-dashboard.py")


@pytest.fixture(scope="module")
def nl():
    return _load("newsletter_k3_p4", "scripts/generate-newsletter-html.py")


def _calendar(gd, tmp_path, rows, day=None):
    """Write an upcoming.md exactly as sync-exchange writes one."""
    when = day or gd.TODAY
    lines = [f"# Calendar - Next 7 Days", "", "> Synced: x (Asia/Dubai)", "",
             f"## {when}", "",
             "| Time | Subject | Location | Duration |",
             "|------|---------|----------|----------|"]
    for t, subject in rows:
        lines.append(f"| {t} | {subject} | Room | 30m |")
    f = tmp_path / "upcoming.md"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


# ============================================================
# 1. The calendar converted from a timezone it was never in
# ============================================================

def test_a_morning_meeting_shows_the_time_the_file_says(gd, tmp_path, monkeypatch):
    """The whole finding, in one assertion.

    On Asia/Dubai this rendered 13:00 for a 09:00 meeting, every day, since
    the engine's first commit.
    """
    cal = _calendar(gd, tmp_path, [("09:00", "Board sync")])
    monkeypatch.setattr(gd, "calendar_file", lambda p=cal: p)
    assert [m["Time"] for m in gd.collect_calendar()["meetings"]] == ["09:00"]


def test_an_evening_meeting_does_not_vanish(gd, tmp_path, monkeypatch):
    """The half that was worse than a wrong number.

    Converted forward, 21:00 became tomorrow, and the "is it today" filter
    then removed it. The CEO's page simply had no evening.
    """
    cal = _calendar(gd, tmp_path, [("21:00", "Late call")])
    monkeypatch.setattr(gd, "calendar_file", lambda p=cal: p)
    got = gd.collect_calendar()["meetings"]
    assert [m["Time"] for m in got] == ["21:00"]


def test_a_full_day_survives_end_to_end(gd, tmp_path, monkeypatch):
    cal = _calendar(gd, tmp_path, [("08:15", "Standup"), ("13:30", "Partner"),
                                   ("22:45", "US call")])
    monkeypatch.setattr(gd, "calendar_file", lambda p=cal: p)
    assert [m["Time"] for m in gd.collect_calendar()["meetings"]] == [
        "08:15", "13:30", "22:45"]


def test_another_days_section_is_not_shown_as_today(gd, tmp_path, monkeypatch):
    tomorrow = gd.TODAY + timedelta(days=1)
    cal = _calendar(gd, tmp_path, [("09:00", "Tomorrow")], day=tomorrow)
    monkeypatch.setattr(gd, "calendar_file", lambda p=cal: p)
    assert gd.collect_calendar()["meetings"] == []


def test_midnight_is_not_lost(gd, tmp_path, monkeypatch):
    cal = _calendar(gd, tmp_path, [("00:05", "Overnight")])
    monkeypatch.setattr(gd, "calendar_file", lambda p=cal: p)
    assert [m["Time"] for m in gd.collect_calendar()["meetings"]] == ["00:05"]


def _dashboard_ast():
    src = (ROOT / "scripts" / "generate-dashboard.py").read_text(encoding="utf-8")
    return ast.parse(src)


def _function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _docstrings(tree):
    """Every docstring node, so a prose mention is not read as behaviour."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


def test_the_source_no_longer_converts_the_calendar():
    """A future edit that reintroduces the conversion fails here.

    Checked on the SYNTAX TREE, not on the text. The comment above the fixed
    loop names `astimezone` in order to explain why it is gone, and a
    substring search over the source called that a regression -- a test that
    fails on its own documentation is a test nobody keeps.
    """
    fn = _function(_dashboard_ast(), "collect_calendar")
    attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert "astimezone" not in attrs
    assert "utc" not in attrs
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "CALENDAR_UTC_OFFSET_HOURS" not in names


# ============================================================
# 2. The clock the regex admitted and the parser refused
# ============================================================

@pytest.mark.parametrize("raw,expected", [
    ("09:00", "09:00"),
    ("9:00", "09:00"),
    ("9:05", "09:05"),
    ("23:59", "23:59"),
    ("00:00", "00:00"),
    ("9:30 AM", "09:30"),
    ("9:30 PM", "21:30"),
    ("12:15 AM", "00:15"),
    ("12:15 PM", "12:15"),
    ("  8:00  ", "08:00"),
])
def test_a_time_the_regex_admits_is_a_time_the_parser_accepts(gd, raw, expected):
    """The guard allowed `\\d{1,2}` and `time.fromisoformat` did not.

    "9:30" passed the regex, failed the parse, and hit a bare `continue`.
    """
    assert gd._parse_clock(raw).strftime("%H:%M") == expected


@pytest.mark.parametrize("raw", ["", "   ", "TBC", "all day", "25:00", "10:99"])
def test_a_non_time_is_not_invented(gd, raw):
    assert gd._parse_clock(raw) is None


def test_a_missing_time_cell_is_not_a_time(gd):
    """`None`, not just `""`. This is what the falsy guard is FOR.

    An empty string already fails the regex, so the guard reads as
    redundant against the one caller. It is not: `re.match` raises
    TypeError on None, and a defensive parser that raises on absent input
    is a parser that takes the dashboard down instead of skipping a row.
    """
    assert gd._parse_clock(None) is None


def test_an_unparsed_row_is_kept_and_flagged_not_dropped(gd, tmp_path,
                                                          monkeypatch, capsys):
    """On this panel, a silent drop is the worst available outcome."""
    cal = _calendar(gd, tmp_path, [("TBC", "Unscheduled")])
    monkeypatch.setattr(gd, "calendar_file", lambda p=cal: p)
    got = gd.collect_calendar()["meetings"]
    assert len(got) == 1
    assert got[0]["Time"] == "TBC"
    assert "unparsed Time" in capsys.readouterr().err


def test_a_row_with_no_time_at_all_is_not_kept(gd, tmp_path, monkeypatch):
    cal = _calendar(gd, tmp_path, [("", "Blank")])
    monkeypatch.setattr(gd, "calendar_file", lambda p=cal: p)
    assert gd.collect_calendar()["meetings"] == []


# ============================================================
# 3. "All Clear" reachable from an error
# ============================================================

def _empty_crm():
    return {"contacts": [], "red": [], "yellow": [], "green": [], "gray": [],
            "commitments_due": [], "total": 0, "failed": ""}


def test_a_quiet_morning_still_reads_all_clear(gd):
    assert "All Clear" in gd.build_urgent(_empty_crm())


def test_a_failed_scan_never_reads_all_clear(gd):
    """The finding. An empty panel from a FAILURE is not a clear panel."""
    crm = _empty_crm()
    crm["failed"] = "KeyError: 'company'"
    html = gd.build_urgent(crm)
    assert "All Clear" not in html
    assert "Unavailable" in html


def test_the_failure_panel_names_the_error(gd):
    crm = _empty_crm()
    crm["failed"] = "KeyError: 'company'"
    assert "company" in gd.build_urgent(crm)


def test_the_failure_panel_says_empty_is_not_clear(gd):
    crm = _empty_crm()
    crm["failed"] = "boom"
    assert "not clear" in gd.build_urgent(crm).lower()


def test_a_real_alert_still_wins_over_the_failure_card(gd):
    crm = _empty_crm()
    crm["red"] = [{"name": "Vesper Lynd", "company": "Universal",
                   "type": "partner", "days_since": 40, "cadence": 30}]
    html = gd.build_urgent(crm)
    assert "Vesper Lynd" in html
    assert "All Clear" not in html


def test_one_malformed_contact_does_not_empty_the_scan(gd, monkeypatch, capsys):
    """A bad contact no longer takes everyone else down with it.

    Every field was a bare index, so the first bad dict raised before a
    single bucket was filled and the whole panel went quiet.
    """
    good = {"name": "Vesper Lynd", "company": "Universal", "type": "partner",
            "last_touch": "2026-01-01", "cadence": 30, "health": "red",
            "days_since": 40, "commitments": [], "file": "v.md"}
    bad = {"name": "Broken"}          # every other key missing
    monkeypatch.setattr(gd, "_crm_parse_config", lambda p: {})
    monkeypatch.setattr(gd, "_crm_scan_contacts",
                        lambda cfg, today=None: ([bad, good], [], [], [], []))
    result = gd.collect_crm_health()
    assert result["failed"] == ""
    assert any(c["name"] == "Vesper Lynd" for c in result["contacts"])


def test_a_contact_missing_optional_fields_is_kept_not_dropped(gd, monkeypatch):
    """Two guards, and this is the one the other was hiding.

    The per-contact `try` alone would satisfy "one bad contact does not
    empty the scan" -- it would drop that contact and keep the rest, and a
    test asserting only the survivor's presence passes either way. The
    `.get` defaults do something stricter: a contact whose Company cell is
    simply absent STAYS ON THE PAGE, with an empty company, instead of
    disappearing from the CEO's radar over a missing optional field. A
    relationship silently absent is the failure mode this whole panel is
    being hardened against.
    """
    monkeypatch.setattr(gd, "_crm_parse_config", lambda p: {})
    monkeypatch.setattr(gd, "_crm_scan_contacts",
                        lambda cfg, today=None: ([{"name": "Broken"}], [], [], [], []))
    result = gd.collect_crm_health()
    names = [c["name"] for c in result["contacts"]]
    assert names == ["Broken"], "a contact missing optional keys must survive"
    assert result["contacts"][0]["company"] == ""
    assert result["total"] == 1


def test_a_commitment_missing_its_text_drops_only_that_contact(gd, monkeypatch,
                                                                capsys):
    good = {"name": "Vesper Lynd", "company": "U", "type": "p",
            "last_touch": "2026-01-01", "cadence": 30, "health": "red",
            "days_since": 40, "commitments": [], "file": "v.md"}
    bad = dict(good, name="Felix Leiter", commitments=[{"due": None}])
    monkeypatch.setattr(gd, "_crm_parse_config", lambda p: {})
    monkeypatch.setattr(gd, "_crm_scan_contacts",
                        lambda cfg, today=None: ([bad, good], [], [], [], []))
    result = gd.collect_crm_health()
    names = [c["name"] for c in result["contacts"]]
    assert "Vesper Lynd" in names
    assert "Felix Leiter" not in names
    assert "malformed contact" in capsys.readouterr().err


def test_a_scan_that_raises_sets_the_failed_flag(gd, monkeypatch, capsys):
    def _boom(path):
        raise RuntimeError("config unreadable")
    monkeypatch.setattr(gd, "_crm_parse_config", _boom)
    result = gd.collect_crm_health()
    assert "RuntimeError" in result["failed"]
    assert "config unreadable" in result["failed"]


def test_the_failure_reaches_the_page_not_only_stderr(gd, monkeypatch):
    """stderr scrolls past; the page is what gets read."""
    def _boom(path):
        raise RuntimeError("config unreadable")
    monkeypatch.setattr(gd, "_crm_parse_config", _boom)
    assert "All Clear" not in gd.build_urgent(gd.collect_crm_health())


# ============================================================
# 4. Producer fields that were never validated
# ============================================================

@pytest.mark.parametrize("value,expected", [
    (0, 0.0), (75, 75.0), (100, 100.0), (75.4, 75.4),
    ("75", 75.0), ("75%", 75.0), (" 75 ", 75.0),
    (0.87, 87.0),           # a fraction, not "1%"
    (1, 100.0),             # ambiguous; the likelier reading wins
    (None, 0.0), (True, 0.0), ("abc", 0.0), ([], 0.0), ({}, 0.0),
    (-5, 0.0), (150, 100.0),
])
def test_a_completion_rate_is_a_percentage_whatever_arrives(gd, value, expected):
    assert gd._as_percent(value) == pytest.approx(expected)


def test_a_string_completion_rate_no_longer_kills_the_build(gd, capsys):
    """It raised at FORMAT time, after every collector had succeeded."""
    rate = gd._as_percent("not a number")
    assert f"{rate:.0f}%" == "0%"
    assert "not a number" in capsys.readouterr().err


def test_a_fractional_rate_is_not_shown_as_one_percent(gd):
    assert f"{gd._as_percent(0.87):.0f}%" == "87%"


def test_the_viraid_read_coerces_at_read_time(gd):
    src = (ROOT / "scripts" / "generate-dashboard.py").read_text(encoding="utf-8")
    assert '_as_percent(stats.get("completion_rate"))' in src
    assert 'stats.get("completion_rate", 0.0)' not in src


def test_days_since_gets_the_guard_its_sibling_already_had(gd):
    """Asked of the AST, not of a substring.

    This read `'_as_int_or_count(data.get("days_since"))' in src` until
    2026-08-29, when the cadence dict was renamed from `data` to `cadence` and
    the guard reported a missing coercion that was sitting right in front of
    it. A control that spells the code it checks fails on a rename and passes
    on a rewrite, which is the wrong way round. It now walks every assignment
    to `days_since` and asks what is on the right-hand side.
    """
    tree = ast.parse((ROOT / "scripts" / "generate-dashboard.py").read_text(encoding="utf-8"))
    collector = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "collect_capture_payoff"),
        None)
    assert collector is not None, "collect_capture_payoff is gone from the dashboard"

    # Scoped to the collector. `build_capture_payoff` legitimately does
    # `payoff.get("days_since")`, reading the value this guard is about AFTER
    # it has been coerced, and a whole-module scan flags it.
    rhs = []
    for node in ast.walk(collector):
        if not isinstance(node, ast.Assign):
            continue
        if "days_since" not in [t.id for t in node.targets if isinstance(t, ast.Name)]:
            continue
        rhs.append(node.value)

    assert any(
        isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
        and v.func.id == "_as_int_or_count"
        for v in rhs
    ), "no assignment to days_since runs through _as_int_or_count"

    # And nothing assigns it a raw `<mapping>.get("days_since")`, which is the
    # shape the coercion replaced.
    for v in rhs:
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == "get":
            arg = v.args[0] if v.args else None
            assert not (isinstance(arg, ast.Constant) and arg.value == "days_since"), \
                "days_since is assigned straight from the child's JSON again"


@pytest.mark.parametrize("value,expected", [
    ("3", None), (3, 3), (3.7, 3), (True, None), ([1, 2], 2), (None, None),
])
def test_the_guard_answers_a_number_or_nothing(gd, value, expected):
    assert gd._as_int_or_count(value) == expected


def test_a_string_days_since_no_longer_reaches_a_comparison(gd):
    """`days_since >= 7` in the BUILD phase is what a string used to hit."""
    guarded = gd._as_int_or_count("3")
    assert (guarded is not None and guarded >= 7) is False


# ============================================================
# 5. The fallback chain that stopped at the first answer
# ============================================================

def _note(tmp_path, name, **fm):
    body = "---\n" + "\n".join(f"{k}: {v}" for k, v in fm.items()) + "\n---\n\ntext\n"
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_a_note_ingested_this_week_counts_however_old_its_date(gd, tmp_path,
                                                                monkeypatch):
    """The finding: an old `date` returned False and `ingested` never ran."""
    monkeypatch.setattr(gd, "knowledge_dir", lambda p=tmp_path: p)
    monkeypatch.setattr(gd, "odin_brain_dir", lambda p=tmp_path / "odin-brain": p)
    (tmp_path / "odin-brain").mkdir()
    _note(tmp_path, "n.md", title="Old source", date="2020-01-01",
          ingested=str(gd.TODAY))
    payoff = gd.collect_capture_payoff()
    assert payoff["signals_week"] >= 1


def test_a_note_old_in_every_field_does_not_count(gd, tmp_path, monkeypatch):
    monkeypatch.setattr(gd, "knowledge_dir", lambda p=tmp_path: p)
    monkeypatch.setattr(gd, "odin_brain_dir", lambda p=tmp_path / "odin-brain": p)
    (tmp_path / "odin-brain").mkdir()
    _note(tmp_path, "n.md", title="Old", date="2020-01-01", ingested="2020-01-02")
    assert gd.collect_capture_payoff()["signals_week"] == 0


def test_a_note_recent_in_the_first_field_still_counts(gd, tmp_path, monkeypatch):
    monkeypatch.setattr(gd, "knowledge_dir", lambda p=tmp_path: p)
    monkeypatch.setattr(gd, "odin_brain_dir", lambda p=tmp_path / "odin-brain": p)
    (tmp_path / "odin-brain").mkdir()
    _note(tmp_path, "n.md", title="Fresh", updated=str(gd.TODAY))
    assert gd.collect_capture_payoff()["signals_week"] >= 1


def test_an_unparseable_field_does_not_end_the_search(gd, tmp_path, monkeypatch):
    monkeypatch.setattr(gd, "knowledge_dir", lambda p=tmp_path: p)
    monkeypatch.setattr(gd, "odin_brain_dir", lambda p=tmp_path / "odin-brain": p)
    (tmp_path / "odin-brain").mkdir()
    _note(tmp_path, "n.md", title="Fresh", updated="sometime",
          ingested=str(gd.TODAY))
    assert gd.collect_capture_payoff()["signals_week"] >= 1


# ============================================================
# 6. The placeholder the report did not raise
# ============================================================

def test_the_dashboard_does_not_print_the_placeholder_words():
    """No STRING the module can emit still carries the placeholder.

    Comments and docstrings are excluded deliberately: `_zone_suffix`'s
    docstring quotes the phrase to record what it replaced, and that is
    documentation, not output.
    """
    tree = _dashboard_ast()
    docs = _docstrings(tree)
    emitted = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and id(n) not in docs and "the configured timezone" in n.value]
    assert emitted == [], emitted


def test_the_zone_suffix_names_the_real_zone(gd):
    zone = gd.NOW.tzname()
    if zone:
        assert gd._zone_suffix() == f" ({zone})"


def test_a_nameless_zone_leaves_no_empty_brackets(gd, monkeypatch):
    monkeypatch.setattr(gd, "NOW", datetime(2026, 8, 24, 14, 32))  # noqa: DTZ001
    assert gd._zone_suffix() == ""


# ============================================================
# 7. The newsletter's two uncoerced text paths
# ============================================================

def test_a_numeric_hero_title_does_not_kill_the_render(nl):
    html = nl.build_hero({"title": 42, "date": "2026-08-24"})
    assert "42" in html


def test_a_null_hero_title_renders_empty(nl):
    assert nl.build_hero({"title": None, "date": "2026-08-24"}) is not None


def test_a_numeric_accent_word_does_not_raise(nl):
    assert nl.build_hero({"title": "A line", "accent_word": 7,
                          "date": "2026-08-24"}) is not None


def test_a_real_accent_word_is_still_highlighted(nl):
    html = nl.build_hero({"title": "The signal is clear", "accent_word": "signal",
                          "date": "2026-08-24"})
    assert 'class="accent"' in html


def test_a_numeric_signal_item_does_not_kill_the_render(nl):
    """`html.escape` calls `.replace`; a bare number has none."""
    html = nl.build_signal_watch(["Ceasefire talks stall", 2026], 5)
    assert "2026" in html


def test_signal_items_are_still_escaped(nl):
    html = nl.build_signal_watch(["<script>alert(1)</script>"], 5)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_signal_bold_markup_still_renders(nl):
    html = nl.build_signal_watch(["A **strong** signal"], 5)
    assert "<strong>strong</strong>" in html
