"""Unit tests for ops sources (telemetry summary + log tail)."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.bridge_daemon.sources.ops import (
    LOG_TAIL_LINES,
    read_log_tail,
    read_telemetry_summary,
)


def _write_usage(workspace_root, events):
    """events: list of dicts with at least ts + event."""
    p = workspace_root / ".daemon-state" / "usage.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e) for e in events]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_telemetry_empty_when_no_usage_file(tmp_path):
    """No usage.jsonl -> ok with zero counts."""
    result = read_telemetry_summary(tmp_path)
    assert result["ok"] is True
    assert result["today_total"] == 0
    assert result["last_7d_total"] == 0
    assert result["last_event_ts"] is None
    assert result["file_size_bytes"] is None


def test_telemetry_counts_by_event_type(tmp_path):
    """Events are counted per event_type in today and last_7d windows."""
    now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    _write_usage(tmp_path, [
        {"ts": "2026-05-18T08:00:00+00:00", "event": "page_view", "page": "pulse"},
        {"ts": "2026-05-18T09:00:00+00:00", "event": "page_view", "page": "inbox"},
        {"ts": "2026-05-18T10:00:00+00:00", "event": "launch", "action": "email"},
        {"ts": "2026-05-15T10:00:00+00:00", "event": "page_view", "page": "day"},  # 3 days ago
        {"ts": "2026-05-09T10:00:00+00:00", "event": "page_view", "page": "tribe"},  # 9 days ago - outside 7d
    ])
    result = read_telemetry_summary(tmp_path, now=now)
    assert result["ok"] is True
    assert result["today"]["page_view"] == 2
    assert result["today"]["launch"] == 1
    assert result["today_total"] == 3
    # Last 7d includes today (3) plus the 3-days-ago event (1) = 4.
    assert result["last_7d"]["page_view"] == 3
    assert result["last_7d"]["launch"] == 1
    assert result["last_7d_total"] == 4
    # 9-days-ago event is excluded.
    assert result["last_7d"]["page_view"] != 4


def test_telemetry_malformed_lines_skipped(tmp_path):
    """Malformed JSON lines are silently skipped."""
    p = tmp_path / ".daemon-state" / "usage.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(
        '{"ts": "2026-05-18T08:00:00+00:00", "event": "page_view"}\n'
        'not-json{garbage\n'
        '{"ts": "2026-05-18T09:00:00+00:00", "event": "launch"}\n',
        encoding="utf-8",
    )
    now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    result = read_telemetry_summary(tmp_path, now=now)
    assert result["today_total"] == 2


def test_telemetry_missing_required_fields_skipped(tmp_path):
    """Lines missing ts or event are silently skipped."""
    p = tmp_path / ".daemon-state" / "usage.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(
        '{"event": "page_view"}\n'  # no ts
        '{"ts": "2026-05-18T08:00:00+00:00"}\n'  # no event
        '{"ts": "2026-05-18T08:00:00+00:00", "event": "page_view"}\n',  # good
        encoding="utf-8",
    )
    now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    result = read_telemetry_summary(tmp_path, now=now)
    assert result["today_total"] == 1


def test_telemetry_last_event_ts(tmp_path):
    """last_event_ts is the ts of the last record in the file (append-only)."""
    _write_usage(tmp_path, [
        {"ts": "2026-05-18T08:00:00+00:00", "event": "page_view"},
        {"ts": "2026-05-18T09:00:00+00:00", "event": "launch"},
    ])
    result = read_telemetry_summary(tmp_path)
    assert result["last_event_ts"] == "2026-05-18T09:00:00+00:00"


def test_log_tail_empty_when_no_file(tmp_path):
    """No bridge.log -> ok=True with empty lines."""
    result = read_log_tail(tmp_path)
    assert result["ok"] is True
    assert result["lines"] == []
    assert result["size_bytes"] is None


def test_log_tail_returns_last_n_lines(tmp_path):
    """Returns the last n_lines of the file."""
    p = tmp_path / ".daemon-state" / "bridge.log"
    p.parent.mkdir(parents=True)
    content = "\n".join(f"line-{i}" for i in range(100)) + "\n"
    p.write_text(content, encoding="utf-8")
    result = read_log_tail(tmp_path, n_lines=10)
    assert result["ok"] is True
    assert len(result["lines"]) == 10
    assert result["lines"][-1] == "line-99"
    assert result["lines"][0] == "line-90"


def test_log_tail_default_50_lines(tmp_path):
    """Default n_lines is LOG_TAIL_LINES = 50."""
    p = tmp_path / ".daemon-state" / "bridge.log"
    p.parent.mkdir(parents=True)
    content = "\n".join(f"line-{i}" for i in range(200)) + "\n"
    p.write_text(content, encoding="utf-8")
    result = read_log_tail(tmp_path)
    assert len(result["lines"]) == LOG_TAIL_LINES
