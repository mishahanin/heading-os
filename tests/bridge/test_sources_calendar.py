"""Unit tests for /day calendar source - full today's agenda parsing."""
from datetime import datetime, timezone

from scripts.bridge_daemon.sources.calendar import today_agenda


def _write_today(workspace_root, content, date_str):
    cal_dir = workspace_root / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True)
    (cal_dir / f"{date_str}.md").write_text(content, encoding="utf-8")


def test_empty_when_file_missing(tmp_path):
    """No calendar file -> empty events, None data_time."""
    fake_now = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    result = today_agenda(tmp_path, now=fake_now)
    assert result["events"] == []
    assert result["data_time"] is None
    # date is still set (local (UTC+4) today).
    assert result["date"] == "2026-05-18"


def test_returns_all_events_in_order(tmp_path):
    """All today's events are returned, sorted by time."""
    _write_today(tmp_path,
        "| Time | Subject | Location |\n"
        "|------|---------|----------|\n"
        "| 18:30 | Tribe Fireside | https://zoom.us/j/abc |\n"
        "| 09:00 | Morning sync | - |\n"
        "| 13:00 | Customer call | https://zoom.us/j/def |\n",
        "2026-05-18",
    )
    # 10:00 UTC = 14:00 local (UTC+4) - between 13:00 and 18:30.
    fake_now = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    result = today_agenda(tmp_path, now=fake_now)
    assert [e["time"] for e in result["events"]] == ["09:00", "13:00", "18:30"]


def test_marks_past_and_next(tmp_path):
    """Events before now are is_past; first future event is is_next."""
    _write_today(tmp_path,
        "| 09:00 | A | - |\n"
        "| 13:00 | B | - |\n"
        "| 18:30 | C | - |\n",
        "2026-05-18",
    )
    # 10:00 UTC = 14:00 local (UTC+4). 09:00 and 13:00 are past; 18:30 is next.
    fake_now = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    result = today_agenda(tmp_path, now=fake_now)
    by_time = {e["time"]: e for e in result["events"]}
    assert by_time["09:00"]["is_past"] is True
    assert by_time["13:00"]["is_past"] is True
    assert by_time["18:30"]["is_past"] is False
    assert by_time["18:30"]["is_next"] is True
    # No other event should be is_next.
    next_count = sum(1 for e in result["events"] if e["is_next"])
    assert next_count == 1


def test_no_next_when_all_past(tmp_path):
    """When all events are past, no event is is_next."""
    _write_today(tmp_path,
        "| 06:00 | Early bird | - |\n",
        "2026-05-18",
    )
    # 22:00 UTC on 2026-05-17 = 02:00 local (UTC+4) on 2026-05-18 (wait - need to flip).
    # 18:00 UTC = 22:00 local (UTC+4) - well past 06:00 local (UTC+4).
    fake_now = datetime(2026, 5, 18, 18, 0, tzinfo=timezone.utc)
    result = today_agenda(tmp_path, now=fake_now)
    assert len(result["events"]) == 1
    assert result["events"][0]["is_past"] is True
    assert result["events"][0]["is_next"] is False


def test_location_dash_stripped(tmp_path):
    """A '-' in the location column is normalized to empty string."""
    _write_today(tmp_path,
        "| 10:00 | Meeting | - |\n",
        "2026-05-18",
    )
    fake_now = datetime(2026, 5, 18, 5, 0, tzinfo=timezone.utc)  # 09:00 local (UTC+4)
    result = today_agenda(tmp_path, now=fake_now)
    assert result["events"][0]["location"] == ""


def test_local_tz_resolves_correct_date(tmp_path):
    """UTC 22:00 on 2026-05-17 = local (UTC+4) 02:00 on 2026-05-18 - the local (UTC+4) date wins."""
    _write_today(tmp_path,
        "| 14:00 | Today's meeting | - |\n",
        "2026-05-18",  # local (UTC+4) date
    )
    fake_now = datetime(2026, 5, 17, 22, 0, tzinfo=timezone.utc)
    result = today_agenda(tmp_path, now=fake_now)
    assert result["date"] == "2026-05-18"
    assert len(result["events"]) == 1
    assert result["events"][0]["subject"] == "Today's meeting"


def test_data_time_is_file_mtime_iso(tmp_path):
    """data_time is the calendar file's mtime in ISO 8601 UTC."""
    _write_today(tmp_path,
        "| 10:00 | M | - |\n",
        "2026-05-18",
    )
    fake_now = datetime(2026, 5, 18, 5, 0, tzinfo=timezone.utc)
    result = today_agenda(tmp_path, now=fake_now)
    assert result["data_time"] is not None
    # ISO 8601 UTC.
    parsed = datetime.fromisoformat(result["data_time"])
    assert parsed.tzinfo is not None


def test_malformed_time_is_skipped(tmp_path):
    """A row with hour > 23 or minute > 59 is skipped without crashing."""
    _write_today(tmp_path,
        "| 25:99 | Garbage row | - |\n"
        "| 10:00 | Real meeting | - |\n",
        "2026-05-18",
    )
    fake_now = datetime(2026, 5, 18, 5, 0, tzinfo=timezone.utc)  # 09:00 local (UTC+4)
    result = today_agenda(tmp_path, now=fake_now)
    # Garbage row dropped, real row kept.
    assert len(result["events"]) == 1
    assert result["events"][0]["subject"] == "Real meeting"


def test_zoom_url_passthrough(tmp_path):
    """A zoom URL in the location column is preserved verbatim (not normalized)."""
    zoom = "https://us02web.zoom.us/j/3131313013"
    _write_today(tmp_path,
        f"| 10:00 | Meeting | {zoom} |\n",
        "2026-05-18",
    )
    fake_now = datetime(2026, 5, 18, 5, 0, tzinfo=timezone.utc)
    result = today_agenda(tmp_path, now=fake_now)
    assert result["events"][0]["location"] == zoom


def test_pipe_in_subject_row_is_skipped(tmp_path):
    """A row with an unescaped pipe in subject (>5 pipes total) is skipped."""
    _write_today(tmp_path,
        "| 14:00 | TLS | TLS Setup | https://zoom.us/j/abc | 30m |\n"  # 6 pipes
        "| 15:00 | Clean meeting | - |\n",  # 4 pipes - well formed
        "2026-05-18",
    )
    fake_now = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)  # 13:00 local (UTC+4)
    result = today_agenda(tmp_path, now=fake_now)
    assert len(result["events"]) == 1
    assert result["events"][0]["subject"] == "Clean meeting"


# ============================================================
# Phase 1.33: minutes_until + minutes_to_next for Day drill-down
# ============================================================
def test_minutes_until_positive_for_future_event(tmp_path):
    """A future event reports positive minutes_until."""
    _write_today(tmp_path,
        "| 18:30 | Tribe | - |\n",
        "2026-05-18",
    )
    # 10:00 UTC = 14:00 local (UTC+4). 18:30 - 14:00 = 4h 30m = 270m.
    fake_now = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    result = today_agenda(tmp_path, now=fake_now)
    assert result["events"][0]["minutes_until"] == 270


def test_minutes_until_negative_for_past_event(tmp_path):
    """A past event reports negative minutes_until."""
    _write_today(tmp_path,
        "| 09:00 | Standup | - |\n",
        "2026-05-18",
    )
    # 10:00 UTC = 14:00 local (UTC+4). 14:00 - 09:00 = 5h = 300m. So minutes_until = -300.
    fake_now = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    result = today_agenda(tmp_path, now=fake_now)
    assert result["events"][0]["minutes_until"] == -300


def test_minutes_to_next_gap_between_events(tmp_path):
    """Each event reports the gap (in minutes) to the next one."""
    _write_today(tmp_path,
        "| 09:00 | A | - |\n"
        "| 09:30 | B | - |\n"
        "| 11:00 | C | - |\n",
        "2026-05-18",
    )
    fake_now = datetime(2026, 5, 18, 4, 0, tzinfo=timezone.utc)  # 08:00 local (UTC+4)
    result = today_agenda(tmp_path, now=fake_now)
    by_time = {e["time"]: e for e in result["events"]}
    assert by_time["09:00"]["minutes_to_next"] == 30   # 09:00 -> 09:30
    assert by_time["09:30"]["minutes_to_next"] == 90   # 09:30 -> 11:00
    assert by_time["11:00"]["minutes_to_next"] is None  # last event
