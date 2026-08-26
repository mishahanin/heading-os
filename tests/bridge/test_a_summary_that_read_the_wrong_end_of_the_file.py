"""Shard 01-p4: a cap pointed the wrong way, a crash the guard could not catch,
and four Returns blocks that had stopped describing their own function.

* ``ops.read_telemetry_summary`` read ``usage.jsonl`` head-first and stopped at
  line 20,000. The file is append-only and nothing in this repository rotates
  it, so the cap discarded exactly the events the summary exists to report:
  past 20,000 lines ``today`` and ``last_7d`` froze at zero and
  ``last_event_ts`` reported line 20,000 as the last event. No flag, no error,
  no way for the Settings page to know it had stopped counting. The scan now
  reads the END, and says so with ``truncated``.

* ``pipeline.list_pipeline`` sliced ``entry["date"][:10]`` inside an
  ``except ValueError``. A touch-log line carrying ``"date": null`` made that
  ``None[:10]`` - a ``TypeError``, which ``ValueError`` does not catch - so one
  malformed line 500'd the whole /pipeline surface. ``read_touch_log`` is the
  single producer of that dict and now coerces every field to a string, which
  is what "corrupt lines are skipped silently" claimed all along.

* Four Returns blocks had drifted behind their code: ``list_pipeline`` omitted
  five deal keys (including ``days_since_touched``, the one ``pulse.signals()``
  reads), ``list_library`` omitted ``type_order``, ``next_meeting`` omitted
  ``event_utc_iso``, and ``list_library``'s empty-knowledge return omitted
  ``type_order`` entirely - a KeyError, not just a doc gap. The last test in
  this file is a standing guard against the next one.

Run: python3 -m pytest tests/bridge/test_a_summary_that_read_the_wrong_end_of_the_file.py
"""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import scripts.bridge_daemon.sources.contacts as contacts_src
import scripts.bridge_daemon.sources.ops as ops_src
from scripts.bridge_daemon.sources.contacts import list_contacts
from scripts.bridge_daemon.sources.conversations import list_conversations
from scripts.bridge_daemon.sources.inbox import read_inbox
from scripts.bridge_daemon.sources.library import list_library
from scripts.bridge_daemon.sources.ops import (
    USAGE_MAX_LINES,
    read_telemetry_summary,
)
from scripts.bridge_daemon.sources.pipeline import (
    TOUCH_LOG_FILE,
    list_pipeline,
    read_touch_log,
)
from scripts.bridge_daemon.sources.pulse import next_meeting
from scripts.utils.workspace import get_default_tz

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


# ============================================================
# The telemetry cap that discarded the newest events
# ============================================================

def _usage(root: Path, records: list[dict]) -> Path:
    p = root / ".daemon-state" / "usage.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return p


def _event(when: datetime, evt: str = "page_view") -> dict:
    return {"ts": when.isoformat(), "event": evt}


def test_an_event_today_is_counted_behind_twenty_thousand_old_ones(tmp_path):
    """The reported reproduction, verbatim.

    Head-first, `today_total` was 0 while a real event existed today.
    """
    old = NOW - timedelta(days=30)
    records = [_event(old) for _ in range(USAGE_MAX_LINES)]
    records.append(_event(NOW, "launch"))
    _usage(tmp_path, records)

    got = read_telemetry_summary(tmp_path, now=NOW)
    assert got["today_total"] == 1
    assert got["today"] == {"launch": 1}


def test_last_event_ts_is_the_last_event_not_the_last_line_read(tmp_path):
    old = NOW - timedelta(days=30)
    records = [_event(old) for _ in range(USAGE_MAX_LINES)]
    records.append(_event(NOW, "finalize"))
    _usage(tmp_path, records)

    got = read_telemetry_summary(tmp_path, now=NOW)
    assert got["last_event_ts"] == NOW.isoformat()


def test_the_seven_day_window_survives_the_cap(tmp_path):
    old = NOW - timedelta(days=30)
    recent = NOW - timedelta(days=2)
    records = [_event(old) for _ in range(USAGE_MAX_LINES)]
    records.extend(_event(recent, "return_to_browser") for _ in range(3))
    _usage(tmp_path, records)

    got = read_telemetry_summary(tmp_path, now=NOW)
    assert got["last_7d_total"] == 3


def test_a_capped_read_says_it_was_capped(tmp_path):
    records = [_event(NOW) for _ in range(USAGE_MAX_LINES + 1)]
    _usage(tmp_path, records)
    assert read_telemetry_summary(tmp_path, now=NOW)["truncated"] is True


def test_a_whole_file_read_is_not_reported_capped(tmp_path):
    _usage(tmp_path, [_event(NOW), _event(NOW, "launch")])
    got = read_telemetry_summary(tmp_path, now=NOW)
    assert got["truncated"] is False
    assert got["today_total"] == 2


def test_a_file_exactly_at_the_line_cap_is_not_reported_capped(tmp_path):
    _usage(tmp_path, [_event(NOW) for _ in range(USAGE_MAX_LINES)])
    got = read_telemetry_summary(tmp_path, now=NOW)
    assert got["truncated"] is False
    assert got["today_total"] == USAGE_MAX_LINES


def test_the_byte_cap_also_drops_from_the_front(tmp_path, monkeypatch):
    """The byte cap is the outer one; it must cut the same end as the line cap."""
    old = NOW - timedelta(days=30)
    records = [_event(old) for _ in range(200)]
    records.append(_event(NOW, "launch"))
    p = _usage(tmp_path, records)
    # Small enough that only the last few lines fit.
    monkeypatch.setattr(ops_src, "USAGE_MAX_BYTES", 300)
    assert p.stat().st_size > 300

    got = read_telemetry_summary(tmp_path, now=NOW)
    assert got["truncated"] is True
    assert got["today"] == {"launch": 1}


def test_a_record_split_by_the_byte_cap_is_not_half_parsed(tmp_path, monkeypatch):
    """The slice can land mid-record; that partial line must be dropped.

    Half a JSON object either fails to parse (harmless) or, worse, parses into
    something the counters accept. Dropping it is the only safe reading.
    """
    records = [_event(NOW, "page_view") for _ in range(50)]
    _usage(tmp_path, records)
    line_len = len(json.dumps(_event(NOW, "page_view"))) + 1
    # Land the boundary in the middle of a record, not on a newline.
    monkeypatch.setattr(ops_src, "USAGE_MAX_BYTES", line_len * 3 + line_len // 2)

    got = read_telemetry_summary(tmp_path, now=NOW)
    assert got["today_total"] == 3, "the partial record must not be counted"
    assert got["truncated"] is True


def test_a_partial_line_that_would_parse_is_still_dropped(tmp_path, monkeypatch):
    """The dangerous case: the tail of a cut line is itself valid JSON.

    A record whose remainder parses would be counted as a whole event that
    never happened. The boundary check is what prevents it, so this test cuts
    the file at exactly the byte where a well-formed object begins.
    """
    ghost = json.dumps({"ts": NOW.isoformat(), "event": "ghost"})
    prefix = "XXXXXX"
    line1 = prefix + ghost + "\n"          # one physical line, not valid JSON
    line2 = json.dumps(_event(NOW, "launch")) + "\n"
    p = tmp_path / ".daemon-state" / "usage.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(line1 + line2, encoding="utf-8")
    size = p.stat().st_size
    # Cut exactly where the ghost object starts, mid-line.
    monkeypatch.setattr(ops_src, "USAGE_MAX_BYTES", size - len(prefix))

    got = read_telemetry_summary(tmp_path, now=NOW)
    assert got["today"] == {"launch": 1}, "a half-line must never become an event"


def test_a_missing_file_carries_the_truncated_key(tmp_path):
    got = read_telemetry_summary(tmp_path, now=NOW)
    assert got["ok"] is True
    assert got["truncated"] is False


def test_a_read_failure_carries_the_truncated_key(tmp_path, monkeypatch):
    """A consumer reading payload["truncated"] must never hit a KeyError."""
    _usage(tmp_path, [_event(NOW)])

    # `open`, not `stat`: `Path.exists()` calls stat and SWALLOWS the OSError,
    # so patching stat lands in the missing-file branch instead of the failure
    # branch this test is about.
    def _boom(self, *a, **kw):
        raise OSError("permission denied")
    monkeypatch.setattr(Path, "open", _boom)

    got = read_telemetry_summary(tmp_path, now=NOW)
    assert got["ok"] is False
    assert got["truncated"] is False


def test_corrupt_and_blank_lines_are_still_skipped(tmp_path):
    p = tmp_path / ".daemon-state" / "usage.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(_event(NOW)) + "\n"
        "\n"
        "{not json\n"
        + json.dumps({"ts": NOW.isoformat()}) + "\n"      # no event
        + json.dumps({"event": "launch"}) + "\n"          # no ts
        + json.dumps({"ts": "not-a-date", "event": "launch"}) + "\n"
        + json.dumps(_event(NOW, "launch")) + "\n",
        encoding="utf-8")
    got = read_telemetry_summary(tmp_path, now=NOW)
    assert got["today"] == {"page_view": 1, "launch": 1}


# ============================================================
# The touch-log field that was not a string
# ============================================================

def _pipeline_with_acme(root: Path) -> None:
    p = root / "context" / "pipeline.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| Acme | USA | Proposal | $1,000 | 2026-05-01 | Misha | Send NDA | 2026-09-01 |\n",
        encoding="utf-8")


def _touch(root: Path, **over) -> None:
    entry = {"company": "Acme", "company_key": "acme",
             "date": "2026-08-20", "ts": NOW.isoformat(), "note": "called"}
    entry.update(over)
    p = root / TOUCH_LOG_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entry) + "\n", encoding="utf-8")


@pytest.mark.parametrize("bad_date", [None, 20260824, ["2026-08-24"], {"d": 1}, True])
def test_a_non_string_date_does_not_take_the_pipeline_down(tmp_path, bad_date):
    """`None[:10]` is a TypeError, which `except ValueError` never sees."""
    _pipeline_with_acme(tmp_path)
    _touch(tmp_path, date=bad_date)

    got = list_pipeline(tmp_path, today=date(2026, 8, 25))
    deal = got["deals"][0]
    assert deal["days_since_touched"] is None
    assert deal["touched_date"] == ""


def test_a_touched_deal_with_an_unreadable_date_still_counts_as_touched(tmp_path):
    """The entry exists; only its date is unusable."""
    _pipeline_with_acme(tmp_path)
    _touch(tmp_path, date=None)
    assert list_pipeline(tmp_path, today=date(2026, 8, 25))["touched_total"] == 1


def test_a_good_date_still_computes_the_gap(tmp_path):
    _pipeline_with_acme(tmp_path)
    _touch(tmp_path, date="2026-08-20")
    deal = list_pipeline(tmp_path, today=date(2026, 8, 25))["deals"][0]
    assert deal["days_since_touched"] == 5
    assert deal["touched_date"] == "2026-08-20"


@pytest.mark.parametrize("field", ["date", "ts", "note", "company"])
def test_every_touch_log_field_is_a_string(tmp_path, field):
    """The dict's contract is four strings; a null used to break it silently."""
    _touch(tmp_path, **{field: None})
    entry = read_touch_log(tmp_path)["acme"]
    assert entry[field] == ""
    assert all(isinstance(v, str) for v in entry.values())


def test_an_untouched_deal_is_unchanged(tmp_path):
    _pipeline_with_acme(tmp_path)
    deal = list_pipeline(tmp_path, today=date(2026, 8, 25))["deals"][0]
    assert deal["touched_date"] is None
    assert deal["touched_note"] == ""
    assert deal["days_since_touched"] is None


def test_touched_total_is_monotonic_and_the_docstring_says_so(tmp_path):
    """A year-old touch still counts. That is the code; the doc used to
    promise "inside the touch-log window", which nothing implements."""
    _pipeline_with_acme(tmp_path)
    _touch(tmp_path, date="2025-01-01")
    assert list_pipeline(tmp_path, today=date(2026, 8, 25))["touched_total"] == 1
    doc = list_pipeline.__doc__
    assert "at any age" in doc
    # The correction QUOTES the sentence it replaced, so a bare `not in` here
    # would fail on the fix itself. Pin the order instead: the old wording may
    # appear only after the clause that says it is the old wording.
    assert doc.index('said "deals touched') < doc.index("inside the touch-log window")


# ============================================================
# The library return that lost a key when knowledge/ was absent
# ============================================================

def test_an_absent_knowledge_tree_still_returns_type_order(tmp_path):
    got = list_library(tmp_path)
    assert got["type_order"] == []
    assert got["notes"] == [] and got["total"] == 0


def test_an_undated_note_ranks_below_every_dated_one(tmp_path):
    """Pins the sort the module docstring now describes.

    It used to say mtime was a "fallback", which reads as "ranked among the
    dated notes by mtime". It is not: undated notes go last as a group, and
    the 50-row cap is applied after.
    """
    d = tmp_path / "knowledge"
    d.mkdir()
    (d / "fresh-undated.md").write_text(
        "---\ntitle: Fresh\ntype: note\n---\n\nbody\n", encoding="utf-8")
    (d / "stale-dated.md").write_text(
        "---\ntitle: Stale\ntype: note\nupdated: 2020-01-01\n---\n\nbody\n",
        encoding="utf-8")
    # The undated note is the newest file on disk.
    import os
    old = (datetime(2020, 1, 1, tzinfo=timezone.utc)).timestamp()
    os.utime(d / "stale-dated.md", (old, old))

    titles = [n["title"] for n in list_library(tmp_path)["notes"]]
    assert titles == ["Stale", "Fresh"]


# ============================================================
# The standing guard: a Returns block that stopped describing its function
# ============================================================

def _returned_keys(payload) -> set[str]:
    return set(payload) if isinstance(payload, dict) else set()


def _undocumented(payload_keys, doc: str) -> list[str]:
    """Keys the docstring never names in its Returns block.

    The test is the QUOTED form, `"total"`, not the bare word. A word-boundary
    match was tried first and was measurably too weak: these docstrings also
    discuss their keys in prose ("``days_since_touched`` is the field
    ``pulse.signals()`` reads"), so deleting a key from the Returns block left
    the name elsewhere in the text and the guard passed. Five mutations
    survived that way. Requiring the quoted form means the Returns block is
    the only place that counts, which is the thing being guarded.

    The cost is that every covered function must document its return in the
    quoted style. Two did not and were converted, which is a small price for a
    guard that can actually fail.
    """
    return sorted(k for k in payload_keys if f'"{k}"' not in doc)


def _library_root(tmp_path: Path) -> Path:
    d = tmp_path / "knowledge"
    d.mkdir(parents=True, exist_ok=True)
    (d / "note.md").write_text(
        "---\ntitle: N\ntype: principle\nupdated: 2026-08-01\n---\n\nbody\n",
        encoding="utf-8")
    return tmp_path


def _calendar_root(tmp_path: Path) -> Path:
    # The OPERATOR's zone, which is the clock `next_meeting` reads the day on.
    # `NOW.astimezone()` with no argument uses the SYSTEM zone, so at UTC+14 the
    # fixture wrote 2026-08-26.md while the source looked for 2026-08-25.md and
    # found no meeting at all. Measured 2026-08-27.
    day = NOW.astimezone(get_default_tz()).strftime("%Y-%m-%d")
    p = tmp_path / "outputs" / "_sync" / "calendar" / f"{day}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("| 23:59 | Late sync | - | 15m |\n", encoding="utf-8")
    return tmp_path


def _inbox_root(tmp_path: Path) -> Path:
    p = tmp_path / "outputs" / "operations" / "email-intelligence" / "_latest-fetch.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"run_info": {"timestamp": "x"}, "conversations": []}),
                 encoding="utf-8")
    return tmp_path


@pytest.fixture
def _no_execs(monkeypatch):
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs", list)


def _pipeline_payload(tmp_path):
    _pipeline_with_acme(tmp_path)
    _touch(tmp_path)
    return list_pipeline(tmp_path, today=date(2026, 8, 25))


DOC_CASES = [
    ("list_pipeline", list_pipeline, _pipeline_payload),
    ("list_library", list_library, lambda p: list_library(_library_root(p))),
    ("read_telemetry_summary", read_telemetry_summary,
     lambda p: read_telemetry_summary(_usage(p, [_event(NOW)]).parent.parent, now=NOW)),
    ("next_meeting", next_meeting,
     lambda p: next_meeting(_calendar_root(p), now=NOW)),
    ("read_inbox", read_inbox, lambda p: read_inbox(_inbox_root(p))),
    ("list_conversations", list_conversations, lambda p: list_conversations(p)),
]


@pytest.mark.parametrize("name,fn,call", DOC_CASES, ids=[c[0] for c in DOC_CASES])
def test_every_returned_key_is_named_in_the_docstring(name, fn, call, tmp_path):
    """Docstring drift is this module family's recurring failure mode.

    Three of this shard's seven findings were one shape: a key added to the
    return and never to the Returns block, so a consumer written against the
    documentation could not see it. This asserts the cheap direction - every
    key the function actually returns appears somewhere in its docstring. It
    does not check the reverse, and it cannot check meaning; it exists so that
    ADDING a key without touching the doc fails here instead of six months
    later in an audit.
    """
    payload = call(tmp_path)
    doc = fn.__doc__ or ""
    missing = _undocumented(_returned_keys(payload), doc)
    assert not missing, f"{name} returns undocumented keys: {missing}"


def test_the_drift_guard_covers_the_nested_deal_records(tmp_path):
    """The deal dicts are the payload a consumer actually reads."""
    payload = _pipeline_payload(tmp_path)
    doc = list_pipeline.__doc__ or ""
    missing = _undocumented(payload["deals"][0], doc)
    assert not missing, f"list_pipeline deal records carry undocumented keys: {missing}"


def test_the_drift_guard_covers_the_library_note_records(tmp_path):
    payload = list_library(_library_root(tmp_path))
    doc = list_library.__doc__ or ""
    missing = _undocumented(payload["notes"][0], doc)
    assert not missing, f"list_library note records carry undocumented keys: {missing}"


def test_the_contacts_payload_is_covered_too(tmp_path, _no_execs):
    ws = tmp_path / "workspace"
    (ws / "crm" / "contacts").mkdir(parents=True)
    (ws / "crm" / "contacts" / "jane.md").write_text(
        "---\nrelationship_type: partner\n---\n\n# Jane\n", encoding="utf-8")
    payload = list_contacts(ws, data_root=ws)
    doc = list_contacts.__doc__ or ""
    missing = _undocumented(payload, doc)
    assert not missing, f"list_contacts returns undocumented keys: {missing}"


def test_the_guard_would_fail_on_an_undocumented_key():
    """The detector must be able to fail, or it pins nothing.

    A guard that passes whatever it is handed is the defect it was written to
    prevent, one level up.
    """
    doc = 'Returns: {"alpha": int, "beta": str}'
    payload = {"alpha": 1, "beta": "x", "gamma": True}
    assert _undocumented(payload, doc) == ["gamma"]


def test_next_meeting_returns_the_utc_instant(tmp_path):
    """The one field a consumer needs to render the meeting in another zone."""
    got = next_meeting(_calendar_root(tmp_path), now=NOW)
    assert got is not None
    assert "event_utc_iso" in got
    assert datetime.fromisoformat(got["event_utc_iso"]).tzinfo is not None
