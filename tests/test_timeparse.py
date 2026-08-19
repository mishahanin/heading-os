"""Unit tests for the one shared ISO-8601 parser, plus the regression it fixes.

The guard that matters is `test_ops_telemetry_survives_offsetless_ts`: before
2026-08-20 `bridge_daemon/sources/ops.py` had its own `_parse_iso` that returned
a NAIVE datetime, and one `usage.jsonl` line with ts `2026-08-19T10:00:00` made
`read_telemetry_summary()` raise `TypeError: can't compare offset-naive and
offset-aware datetimes` — 500ing the Settings endpoint, whose caller has no
guard. The band-sort in `sources/inbox.py` carried the identical shape against
an aware `_epoch`.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.bridge_daemon.sources.inbox import read_inbox
from scripts.bridge_daemon.sources.ops import read_telemetry_summary
from scripts.utils.timeparse import parse_iso


def test_offsetless_timestamp_reads_as_utc():
    """A serialized timestamp with no offset is UTC per the DTZ convention."""
    dt = parse_iso("2026-08-19T10:00:00")
    assert dt == datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)
    assert dt.tzinfo is not None


def test_utc_offset_timestamp_preserved():
    dt = parse_iso("2026-08-19T10:00:00+00:00")
    assert dt == datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)


def test_non_utc_offset_preserved_not_reinterpreted():
    """A +04:00 stamp keeps its own offset; it is never re-stamped as UTC."""
    dt = parse_iso("2026-08-19T10:00:00+04:00")
    assert dt.utcoffset() == timedelta(hours=4)
    assert dt == datetime(2026, 8, 19, 6, 0, tzinfo=timezone.utc)


def test_z_suffix_parses_as_utc():
    """Callers (promote-corporate reading `git --format=%cI`) may see a Z."""
    assert parse_iso("2026-08-19T10:00:00Z") == datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", ["", "not-a-timestamp", "2026-13-45", "   ", 17, 3.5, [], None])
def test_garbage_and_none_return_none_never_raise(value):
    """Every caller reads append-only state it does not control: skip, never die."""
    assert parse_iso(value) is None


def test_every_return_is_aware():
    """The whole point: no return value can start a naive/aware comparison graph."""
    for s in ("2026-08-19T10:00:00", "2026-08-19T10:00:00+00:00",
              "2026-08-19T10:00:00-05:00", "2026-08-19"):
        assert parse_iso(s).tzinfo is not None


# ============================================================
# Regression: the endpoint that 500'd
# ============================================================
def test_ops_telemetry_survives_offsetless_ts(tmp_path):
    """One offset-less usage.jsonl line must not TypeError the Settings page."""
    usage = tmp_path / ".daemon-state" / "usage.jsonl"
    usage.parent.mkdir(parents=True)
    usage.write_text(
        json.dumps({"ts": "2026-08-19T10:00:00", "event": "page_view"}) + "\n",
        encoding="utf-8",
    )
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

    result = read_telemetry_summary(tmp_path, now=now)

    assert isinstance(result, dict)
    assert result["ok"] is True
    assert result["today"]["page_view"] == 1
    assert result["last_7d_total"] == 1


def test_ops_telemetry_mixes_offsetless_and_aware_rows(tmp_path):
    """A file that gained a hand-edited row still aggregates both shapes."""
    usage = tmp_path / ".daemon-state" / "usage.jsonl"
    usage.parent.mkdir(parents=True)
    usage.write_text("\n".join([
        json.dumps({"ts": "2026-08-19T08:00:00+00:00", "event": "page_view"}),
        json.dumps({"ts": "2026-08-19T09:00:00", "event": "launch"}),      # hand-edited
        json.dumps({"ts": "2026-08-01T09:00:00", "event": "page_view"}),   # outside 7d
    ]) + "\n", encoding="utf-8")
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

    result = read_telemetry_summary(tmp_path, now=now)

    assert result["today_total"] == 2
    assert result["last_7d_total"] == 2


def test_inbox_band_sort_survives_offsetless_ts(tmp_path):
    """inbox's band sort compares parsed stamps against an aware `_epoch`.

    A mixed naive/aware key set raises TypeError mid-sort, so this exercises the
    same defect as ops.py from the other side.
    """
    fetch = tmp_path / "outputs" / "operations" / "email-intelligence" / "_latest-fetch.json"
    fetch.parent.mkdir(parents=True)
    fetch.write_text(json.dumps({"conversations": [
        {"id": "c1", "topic": "offset-less", "latest_datetime": "2026-08-19T10:00:00",
         "priority": "P1"},
        {"id": "c2", "topic": "aware", "latest_datetime": "2026-08-19T11:00:00+00:00",
         "priority": "P1"},
        {"id": "c3", "topic": "garbled", "latest_datetime": "nonsense", "priority": "P1"},
    ]}), encoding="utf-8")

    result = read_inbox(tmp_path, now=datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc),
                        data_root=tmp_path)

    subjects = [r["subject"] for r in result["bands"]["needs-you"]]
    assert subjects == ["aware", "offset-less", "garbled"]
