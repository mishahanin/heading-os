"""A backup that overwrote a backup, two caps nobody was told about, and a day
that belonged to another clock.

* ``merge-contacts.py`` moved the source record aside to a single fixed name,
  ``.md.merged``. ``Path.rename`` on POSIX SILENTLY replaces its destination, so
  merging the same contact twice destroyed the first backup while still printing
  "Source backed up:". The sibling ``transfer-contact.py`` had this exact fix,
  comment and all, since July - and the same four lines here never got it. The
  logic now lives once, in ``scripts.utils.crm.stamped_backup_path``.

* ``email-intelligence.load_pipeline_context`` returned the first 80 lines of
  ``context/pipeline.md``. It has TWO consumers and only one is the LLM:
  ``enrich_conversation`` scans that string for the contact's company and writes
  ``pipeline_context = None`` on no match, so a deal at line 81 or later read as
  a company with no deal at all. The cap bought nothing even for the prompt,
  which applies its own ``[:1500]``.

* ``sentinel._build_evening_digest`` printed the first five medium items in
  ARRIVAL order under a heading making no top-N claim, with no count anywhere.
  On a busy day the CEO read five routine morning messages as the whole band.

* ``crm-backfill-exchange`` called ``.date()`` on a UTC-aware ``EWSDateTime``,
  so a mail sent at 01:30 local was filed under yesterday - straight into
  ``last_touch:``, which the whole staleness stack reads as a local date. Every
  test in that script's suite stubs the fetch, so the conversion had never been
  measured at all.

Run: python3 -m pytest tests/test_four_bounds_that_nobody_was_told_about.py
"""
from __future__ import annotations

import importlib.util
import sys
import zoneinfo
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.crm import stamped_backup_path  # noqa: E402

DUBAI = zoneinfo.ZoneInfo("Asia/Dubai")


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


backfill = _load("backfill_local_day", "scripts/crm-backfill-exchange.py")
intel = _load("intel_pipeline_ctx", "scripts/email-intelligence.py")
sen = _load("sentinel_digest_bound", "scripts/sentinel.py")


# ============================================================
# The backup that overwrote a backup
# ============================================================

def test_a_second_backup_does_not_overwrite_the_first(tmp_path):
    """THE case. Two merges of one contact used to leave one file."""
    source = tmp_path / "quillon-marsh.md"
    source.write_text("second source\n", encoding="utf-8")
    first = stamped_backup_path(source, "merged")
    first.write_text("the first merge's source\n", encoding="utf-8")

    second = stamped_backup_path(source, "merged")

    assert second != first
    assert first.read_text(encoding="utf-8") == "the first merge's source\n"


def test_a_third_backup_finds_a_third_name(tmp_path):
    """The suffix loop must keep counting, not stop at 2."""
    source = tmp_path / "quillon-marsh.md"
    source.write_text("x\n", encoding="utf-8")
    names = []
    for _ in range(3):
        p = stamped_backup_path(source, "merged")
        p.write_text("x\n", encoding="utf-8")
        names.append(p.name)

    assert len(set(names)) == 3, names


def test_the_first_backup_carries_the_date_not_a_counter(tmp_path):
    """A name that only counts tells nobody WHEN. The stamp is what makes an
    old backup identifiable months later."""
    source = tmp_path / "quillon-marsh.md"
    source.write_text("x\n", encoding="utf-8")

    p = stamped_backup_path(source, "merged", today=date(2026, 8, 27))

    assert p.name == "quillon-marsh.md.merged-20260827"


def test_the_kind_marker_keeps_the_two_tools_apart(tmp_path):
    """A merge backup and a transfer backup of one contact are different
    events, so they must not collide into one name."""
    source = tmp_path / "quillon-marsh.md"
    source.write_text("x\n", encoding="utf-8")

    merged = stamped_backup_path(source, "merged", today=date(2026, 8, 27))
    transferred = stamped_backup_path(source, "transferred", today=date(2026, 8, 27))

    assert merged != transferred
    assert "merged" in merged.name and "transferred" in transferred.name


def test_the_stamp_is_the_operator_day_not_utc(monkeypatch, tmp_path):
    """A backup filed under yesterday is the small version of the same
    confusion this repo keeps finding in timestamps."""
    monkeypatch.setattr("scripts.utils.crm.get_default_tz", lambda: DUBAI)
    source = tmp_path / "quillon-marsh.md"
    source.write_text("x\n", encoding="utf-8")

    # 2026-08-27 22:00 UTC is 2026-08-28 02:00 in Dubai.
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 27, 22, 0, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr("scripts.utils.crm.datetime", _Clock)

    assert stamped_backup_path(source, "merged").name.endswith("-20260828")


# ============================================================
# The pipeline head that hid a deal
# ============================================================

def _pipeline(tmp_path: Path, rows: int) -> Path:
    lines = ["# Pipeline", ""]
    lines += [f"| Filler {i} | Lead | $1 | note |" for i in range(rows)]
    lines.append("| Vantooren Systems | Negotiation | $347,850 | live |")
    p = tmp_path / "pipeline.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_the_deal_row_really_sits_past_the_old_cap(tmp_path, monkeypatch):
    """Pins the fixture. If the row landed inside the first 80 lines, the
    lookup test below would pass against the old truncating version too."""
    src = _pipeline(tmp_path, 100)
    monkeypatch.setattr(intel, "pipeline_file", lambda p=src: p)
    lines = intel.load_pipeline_context().splitlines()

    assert len(lines) > 80
    assert "Vantooren Systems" in lines[-1]
    assert not any("Vantooren" in ln for ln in lines[:80])


def test_the_whole_file_is_returned(tmp_path, monkeypatch):
    src = _pipeline(tmp_path, 100)
    monkeypatch.setattr(intel, "pipeline_file", lambda p=src: p)

    assert intel.load_pipeline_context() == src.read_text(encoding="utf-8")


def test_a_short_pipeline_is_unchanged(tmp_path, monkeypatch):
    """The negative case: a file under the old cap must read exactly the same as
    before, so this change moves nothing for the common corpus."""
    src = _pipeline(tmp_path, 3)
    monkeypatch.setattr(intel, "pipeline_file", lambda p=src: p)

    assert intel.load_pipeline_context() == src.read_text(encoding="utf-8")


def test_an_absent_pipeline_is_still_the_empty_string(tmp_path, monkeypatch):
    monkeypatch.setattr(intel, "pipeline_file", lambda p=tmp_path / "nope.md": p)

    assert intel.load_pipeline_context() == ""


def test_the_prompt_is_still_bounded():
    """The cap was named for the LLM, and the LLM path already had its own. This
    holds that one, because removing the 80-line cap is only safe while the
    prompt bound stays.

    Read from the AST. The first version of this test grepped the file text for
    `pipeline_text[:1500]` and a mutation that DELETED the slice still passed,
    because the docstring three hundred lines up quotes the same characters
    while executing nothing.
    """
    import ast

    tree = ast.parse((ROOT / "scripts" / "email-intelligence.py").read_text(encoding="utf-8"))
    bounded = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
        and n.value.id == "pipeline_text" and isinstance(n.slice, ast.Slice)
        and isinstance(n.slice.upper, ast.Constant)
    ]

    assert len(bounded) == 1, "the prompt's pipeline slice is gone or duplicated"
    assert bounded[0].slice.upper.value == 1500


def test_the_deal_lookup_reads_every_line_it_was_given(tmp_path, monkeypatch):
    """Drives `enrich_conversation` for real over a contact whose deal row is
    the last line of a long file."""
    src = _pipeline(tmp_path, 100)
    monkeypatch.setattr(intel, "pipeline_file", lambda p=src: p)
    text = intel.load_pipeline_context()
    crm_map = {"lena@vantooren.example": {"name": "Lena Voss",
                                          "company": "Vantooren Systems"}}
    conv = {"topic": "renewal",
            "participants": [{"email": "lena@vantooren.example"}],
            "messages": [{"sender_email": "lena@vantooren.example"}]}

    out = intel.enrich_conversation(conv, crm_map, text, {})

    assert out["pipeline_context"] is not None, "the deal past line 80 was missed"
    assert out["pipeline_context"]["stage"] == "Negotiation"


def test_a_company_with_no_row_is_still_none(tmp_path, monkeypatch):
    """The negative case. A lookup that matched anything would pass the test
    above while telling the CEO every thread has a deal."""
    src = _pipeline(tmp_path, 100)
    monkeypatch.setattr(intel, "pipeline_file", lambda p=src: p)
    text = intel.load_pipeline_context()
    crm_map = {"x@absent.example": {"name": "X", "company": "Absent Holdings"}}
    conv = {"topic": "t", "participants": [{"email": "x@absent.example"}],
            "messages": [{"sender_email": "x@absent.example"}]}

    out = intel.enrich_conversation(conv, crm_map, text, {})

    assert out["pipeline_context"] is None


# ============================================================
# The evening digest that showed five of twenty-one
# ============================================================

class _Sentinel:
    """Just enough of Sentinel to call the digest builder unbound."""

    def __init__(self, items):
        self.state = type("S", (), {"data": {"digest": {
            "items_by_urgency": items, "emails_checked": 0,
            "tg_messages_checked": 0, "urgent_sent": 0}}})()


def _item(urgency: int, sender: str, when: str) -> dict:
    return {"urgency": urgency, "source": "email", "sender": sender,
            "subject": f"from {sender}", "time": when}


def _digest(items) -> str:
    stub = _Sentinel(items)
    return sen.Sentinel._build_evening_digest.__get__(stub)(
        datetime(2026, 8, 27, 19, 0, tzinfo=DUBAI))


def test_a_cut_medium_list_says_how_many_it_dropped():
    items = [_item(5, f"s{i}", f"0{i}:00") for i in range(21)]

    out = _digest(items)

    assert "21 at 5-6/10" in out
    assert "and 16 more at 5-6/10, not shown" in out


def test_the_five_shown_are_the_most_urgent_not_the_earliest():
    """The slice was taken in ARRIVAL order, so five routine 5/10 messages from
    before 09:00 hid every 6/10 that came later."""
    items = [_item(5, f"early{i}", f"0{i}:00") for i in range(5)]
    items.append(_item(6, "partner", "17:40"))

    # Scoped to the medium block. Every sender also appears in the "Top senders"
    # roll-up below it, so a whole-message assertion reads the wrong section and
    # can never fail.
    medium = _digest(items).split("Medium-priority")[1].split("Top senders")[0]

    assert "partner" in medium
    assert "early0" not in medium, "the oldest 5/10 item was still shown"


def test_an_uncut_list_gains_no_drop_note():
    items = [_item(5, f"s{i}", f"0{i}:00") for i in range(3)]

    out = _digest(items)

    assert "more at 5-6/10" not in out
    assert "3 at 5-6/10" in out


def test_exactly_five_is_not_a_truncation():
    """The row ON the bound."""
    items = [_item(5, f"s{i}", f"0{i}:00") for i in range(sen.MEDIUM_DIGEST_ROWS)]

    out = _digest(items)

    assert "more at 5-6/10" not in out


def test_no_medium_items_still_says_none():
    out = _digest([_item(9, "urgent", "10:00")])

    assert "None" in out
    assert "0 at 5-6/10" in out


def test_the_band_is_still_five_to_six():
    """A widened band would make the count right and the contents wrong."""
    out = _digest([_item(4, "belowband", "10:00"), _item(7, "aboveband", "11:00")])
    medium = out.split("Medium-priority")[1].split("Top senders")[0]

    assert "belowband" not in medium and "aboveband" not in medium
    assert "0 at 5-6/10" in out
    # Both DID reach the sender roll-up, so the split above is reading the right
    # block rather than an empty string.
    assert "belowband" in out and "aboveband" in out


def test_the_heading_number_is_the_constant_the_slice_uses():
    """The heading names the bound. A literal beside a constant is how the two
    come apart."""
    items = [_item(5, f"s{i}", f"0{i}:00") for i in range(20)]

    out = _digest(items)

    assert f"top {sen.MEDIUM_DIGEST_ROWS} shown" in out


# ============================================================
# The day that belonged to another clock
# ============================================================

def test_a_late_night_send_keeps_the_operator_day(monkeypatch):
    """01:30 Dubai on the 28th is 21:30 UTC on the 27th, and `last_touch` is
    read as a local date everywhere downstream."""
    monkeypatch.setattr(backfill, "get_default_tz", lambda: DUBAI)

    out = backfill.local_day(datetime(2026, 8, 27, 21, 30, tzinfo=timezone.utc))

    assert out.isoformat() == "2026-08-28"


def test_a_daytime_send_is_unchanged(monkeypatch):
    """The negative case, and why this was invisible: most of the day it was
    already right."""
    monkeypatch.setattr(backfill, "get_default_tz", lambda: DUBAI)

    out = backfill.local_day(datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc))

    assert out.isoformat() == "2026-08-27"


def test_a_utc_operator_sees_no_shift(monkeypatch):
    monkeypatch.setattr(backfill, "get_default_tz", lambda: timezone.utc)

    out = backfill.local_day(datetime(2026, 8, 27, 21, 30, tzinfo=timezone.utc))

    assert out.isoformat() == "2026-08-27"


def test_a_naive_timestamp_is_read_as_utc(monkeypatch):
    monkeypatch.setattr(backfill, "get_default_tz", lambda: DUBAI)

    out = backfill.local_day(datetime(2026, 8, 27, 21, 30))  # noqa: DTZ001

    assert out.isoformat() == "2026-08-28"


class _Recipient:
    def __init__(self, email):
        self.email_address = email


class _Message:
    def __init__(self, sent, recipients):
        self.datetime_sent = sent
        self.to_recipients = [_Recipient(r) for r in recipients]


class _Folder(list):
    def filter(self, **_k):
        return self

    def order_by(self, *_a):
        return self


class _ExchangeAccount:
    def __init__(self, messages):
        self.sent = _Folder(messages)


def test_the_fetch_loop_files_the_send_on_the_operator_day(monkeypatch):
    """Drives `fetch_sent_items_recent`, not the helper it calls.

    Every test in tests/test_a_backfill_that_walked_the_date_backwards.py stubs
    this function and feeds ready-made date strings, so the conversion inside it
    had never been measured. Testing `local_day` alone left the same hole: a
    mutation that reverted the CALL SITE to `msg.datetime_sent.date()` survived
    a full green run.
    """
    import exchangelib

    msg = _Message(datetime(2026, 8, 27, 21, 30, tzinfo=timezone.utc),
                   ["lena@vantooren.example"])
    monkeypatch.setattr(backfill, "get_default_tz", lambda: DUBAI)
    monkeypatch.setattr(backfill, "_get_exchange_config",
                        lambda: {"EXCHANGE_USERNAME": "u", "EXCHANGE_PASSWORD": "p",
                                 "EXCHANGE_SERVER": "s", "EXCHANGE_EMAIL": "e"})
    monkeypatch.setattr(exchangelib, "Credentials", lambda *a, **k: None)
    monkeypatch.setattr(exchangelib, "Configuration", lambda *a, **k: None)
    monkeypatch.setattr(exchangelib, "Account",
                        lambda *a, **k: _ExchangeAccount([msg]))

    items = backfill.fetch_sent_items_recent(7)

    assert items == [("lena@vantooren.example", "2026-08-28")]


def test_the_fetch_loop_leaves_a_daytime_send_alone(monkeypatch):
    """The negative case for the call site, so a conversion that always adds a
    day cannot pass the test above."""
    import exchangelib

    msg = _Message(datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc),
                   ["lena@vantooren.example"])
    monkeypatch.setattr(backfill, "get_default_tz", lambda: DUBAI)
    monkeypatch.setattr(backfill, "_get_exchange_config",
                        lambda: {"EXCHANGE_USERNAME": "u", "EXCHANGE_PASSWORD": "p",
                                 "EXCHANGE_SERVER": "s", "EXCHANGE_EMAIL": "e"})
    monkeypatch.setattr(exchangelib, "Credentials", lambda *a, **k: None)
    monkeypatch.setattr(exchangelib, "Configuration", lambda *a, **k: None)
    monkeypatch.setattr(exchangelib, "Account",
                        lambda *a, **k: _ExchangeAccount([msg]))

    items = backfill.fetch_sent_items_recent(7)

    assert items == [("lena@vantooren.example", "2026-08-27")]


@pytest.mark.parametrize("hour,expected", [(19, "2026-08-27"), (20, "2026-08-28")])
def test_the_rollover_is_exactly_at_twenty_hundred_utc(monkeypatch, hour, expected):
    """The boundary itself. Dubai is +04 with no DST, so the operator's day
    turns over at 20:00 UTC and the two cases ON either side of that are what
    separate a real conversion from a constant."""
    monkeypatch.setattr(backfill, "get_default_tz", lambda: DUBAI)

    out = backfill.local_day(datetime(2026, 8, 27, hour, 0, tzinfo=timezone.utc))

    assert out.isoformat() == expected
