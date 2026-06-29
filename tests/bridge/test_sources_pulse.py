"""Unit tests for /pulse real-data sources."""
from datetime import datetime, timezone

from scripts.bridge_daemon.sources.pulse import (
    days_to_odin_5,
    next_meeting,
    pipeline_value_and_deals,
    pulse_data,
    raise_progress,
    sea_state,
    signals,
    threads_state_preview,
    today_activity,
    tribe_state_preview,
)
from scripts.bridge_daemon.sources.investors import PROGRAM_DIR as _INV_PROGRAM_DIR


def test_days_to_odin_5_default_returns_int():
    """Default target date computes to a real int."""
    d = days_to_odin_5()
    assert isinstance(d, int)


def test_days_to_odin_5_custom_target():
    """Explicit target date is honored."""
    # Far future to keep test stable.
    assert days_to_odin_5("2099-01-01") > 0


def test_pipeline_value_parses_real_format(tmp_path):
    """Parses the canonical pipeline.md table row format."""
    (tmp_path / "context").mkdir()
    (tmp_path / "context" / "pipeline.md").write_text(
        "# Pipeline\n\n"
        "| Total active deals | 25 |\n"
        "| Total pipeline value (priced deals only) | $11,000,000 |\n",
        encoding="utf-8",
    )
    value, deals = pipeline_value_and_deals(tmp_path)
    assert value == 11_000_000
    assert deals == 25


def test_pipeline_returns_zero_on_missing_file(tmp_path):
    """Missing pipeline.md -> (0, 0), no exception."""
    value, deals = pipeline_value_and_deals(tmp_path)
    assert value == 0 and deals == 0


def test_next_meeting_returns_earliest_future_event(tmp_path):
    """Parses the calendar markdown table and returns the next event."""
    cal_dir = tmp_path / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True)
    today = "2026-05-18"
    (cal_dir / f"{today}.md").write_text(
        "# Calendar\n\n"
        "| Time | Subject | Location |\n"
        "|------|---------|----------|\n"
        "| 09:00 | Morning Sync | - |\n"
        "| 13:00 | Customer Call | - |\n"
        "| 18:00 | Tribe Fireside | - |\n",
        encoding="utf-8",
    )
    # "Now" = 08:00 UTC = 12:00 local (UTC+4) -> next event is 13:00 local (UTC+4).
    fake_now = datetime(2026, 5, 18, 8, 0, tzinfo=timezone.utc)
    result = next_meeting(tmp_path, now=fake_now)
    assert result is not None
    assert result["time"] == "13:00"
    assert result["subject"] == "Customer Call"
    assert "location" in result
    assert "minutes_until" in result
    assert isinstance(result["minutes_until"], int)
    assert result["minutes_until"] >= 0


def test_next_meeting_returns_none_when_all_past(tmp_path):
    """When all events for today are in the past, returns None."""
    cal_dir = tmp_path / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True)
    (cal_dir / "2026-05-18.md").write_text(
        "| 06:00 | Early meeting | - |\n", encoding="utf-8",
    )
    # 06:00 local (UTC+4) = 02:00 UTC. Pick a "now" that's past 06:00 in local (UTC+4)
    # (use 20:00 UTC = 00:00 local (UTC+4) on next day; same-day check needs
    # an in-day past time). Use 12:00 UTC = 16:00 local (UTC+4) (well past 06:00).
    fake_now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    assert next_meeting(tmp_path, now=fake_now) is None


def test_next_meeting_uses_local_tz(tmp_path):
    """Calendar files are tagged in local (UTC+4); resolution must use local (UTC+4) date,
    not the daemon's local timezone. A UTC 'now' that's still on the
    previous calendar day in local (UTC+4) must NOT yet flip to the next day."""
    cal_dir = tmp_path / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True)
    # Build today's calendar for 2026-05-18 local (UTC+4) date.
    (cal_dir / "2026-05-18.md").write_text(
        "| 14:00 | Today's meeting | - |\n", encoding="utf-8",
    )
    # UTC 22:00 on 2026-05-17 = local (UTC+4) 02:00 on 2026-05-18. The local (UTC+4) date
    # is already 2026-05-18, so we should find today's meeting (14:00).
    fake_now = datetime(2026, 5, 17, 22, 0, tzinfo=timezone.utc)
    result = next_meeting(tmp_path, now=fake_now)
    assert result is not None
    assert result["subject"] == "Today's meeting"
    assert result["time"] == "14:00"


def test_next_meeting_captures_location_url(tmp_path):
    """Zoom URL in the location column is preserved verbatim."""
    cal_dir = tmp_path / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True)
    zoom = "https://us02web.zoom.us/j/3131313013"
    (cal_dir / "2026-05-18.md").write_text(
        f"| 14:00 | Customer call | {zoom} |\n",
        encoding="utf-8",
    )
    fake_now = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)  # 13:00 local (UTC+4)
    result = next_meeting(tmp_path, now=fake_now)
    assert result["location"] == zoom


def test_next_meeting_location_dash_normalized_to_empty(tmp_path):
    """'-' in location column becomes empty string."""
    cal_dir = tmp_path / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True)
    (cal_dir / "2026-05-18.md").write_text(
        "| 14:00 | In-person meeting | - |\n",
        encoding="utf-8",
    )
    fake_now = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
    result = next_meeting(tmp_path, now=fake_now)
    assert result["location"] == ""


def test_next_meeting_minutes_until_is_positive_int(tmp_path):
    """minutes_until reflects local (UTC+4)-local time difference."""
    cal_dir = tmp_path / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True)
    (cal_dir / "2026-05-18.md").write_text(
        "| 14:00 | Meeting | - |\n",
        encoding="utf-8",
    )
    # 09:00 UTC = 13:00 local (UTC+4). Event at 14:00 local (UTC+4) is 60 minutes away.
    fake_now = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
    result = next_meeting(tmp_path, now=fake_now)
    assert result["minutes_until"] == 60


def test_pulse_data_assembles_shape(tmp_path):
    """pulse_data returns the full payload shape."""
    p = pulse_data(tmp_path)
    assert "kpi" in p
    assert "days_to_odin_5" in p["kpi"]
    assert "pipeline_value" in p["kpi"]
    assert "in_flight" in p["kpi"]
    assert "next_meeting" in p["kpi"]
    assert "active_deals" in p["kpi"]
    # When no meeting is active, now block has all three keys set to None.
    assert p["now"] == {"focus": None, "until": None, "minutes_remaining": None}
    assert p["next"] == []
    # watch may be populated if real workspace has overdue tasks; in tmp it's [].
    assert isinstance(p["watch"], list)


def test_current_meeting_returns_active_event(tmp_path):
    """If now falls between an event's start and start+duration, return it."""
    from scripts.bridge_daemon.sources.pulse import current_meeting
    from datetime import datetime, timezone
    cal_dir = tmp_path / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True)
    (cal_dir / "2026-05-18.md").write_text(
        "| 13:00 | Customer call | - | 1h |\n", encoding="utf-8",
    )
    # 09:30 UTC = 13:30 local (UTC+4). Event 13:00 + 1h = 14:00 local (UTC+4). 13:30 is in window.
    fake_now = datetime(2026, 5, 18, 9, 30, tzinfo=timezone.utc)
    result = current_meeting(tmp_path, now=fake_now)
    assert result is not None
    assert result["focus"] == "Customer call"
    assert result["until"] == "14:00"
    assert result["minutes_remaining"] == 30


def test_current_meeting_none_when_no_active(tmp_path):
    """No active meeting -> None."""
    from scripts.bridge_daemon.sources.pulse import current_meeting
    from datetime import datetime, timezone
    cal_dir = tmp_path / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True)
    (cal_dir / "2026-05-18.md").write_text(
        "| 09:00 | Past meeting | - | 30m |\n"
        "| 18:00 | Future meeting | - | 30m |\n",
        encoding="utf-8",
    )
    # 09:00 UTC = 13:00 local (UTC+4). 09:00 past, 18:00 future. Nothing active.
    fake_now = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
    result = current_meeting(tmp_path, now=fake_now)
    assert result is None


def test_duration_parse_short_form():
    """_parse_duration_minutes handles short forms."""
    from scripts.bridge_daemon.sources.pulse import _parse_duration_minutes
    assert _parse_duration_minutes("15m") == 15
    assert _parse_duration_minutes("1h") == 60
    assert _parse_duration_minutes("1h30m") == 90
    assert _parse_duration_minutes("1h 30m") == 90
    assert _parse_duration_minutes("") == 30  # fallback
    assert _parse_duration_minutes("garbage") == 30  # fallback


def test_watch_items_includes_overdue_tasks(tmp_path):
    """watch_items surfaces overdue task count."""
    from scripts.bridge_daemon.sources.pulse import watch_items
    tasks_md = tmp_path / "outputs" / "operations" / "viraid" / "tasks.md"
    tasks_md.parent.mkdir(parents=True)
    tasks_md.write_text(
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | Old task | *Task* | Due: 2026-05-05\n"
        "- [ ] **2026-05-01** | `P1` | Another old | *Task* | Due: 2026-05-06\n",
        encoding="utf-8",
    )
    items = watch_items(tmp_path)
    assert len(items) == 1
    assert items[0]["count"] == 2
    assert "overdue" in items[0]["label"]
    assert items[0]["severity"] == "red"


def test_watch_items_empty_when_no_overdue(tmp_path):
    """No overdue tasks -> empty watch list."""
    from scripts.bridge_daemon.sources.pulse import watch_items
    tasks_md = tmp_path / "outputs" / "operations" / "viraid" / "tasks.md"
    tasks_md.parent.mkdir(parents=True)
    # Empty Active section.
    tasks_md.write_text("## Active\n\n", encoding="utf-8")
    items = watch_items(tmp_path)
    assert items == []


# ============================================================
# Phase 1.74: expanded watchpoints
# ============================================================
def test_watch_items_includes_overdue_deals(tmp_path):
    """An overdue pipeline deal surfaces as a red watch item."""
    from scripts.bridge_daemon.sources.pulse import watch_items
    pipeline_md = tmp_path / "context" / "pipeline.md"
    pipeline_md.parent.mkdir(parents=True)
    pipeline_md.write_text(
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---------|---------|-------|-----------|-----------|------|-------------|---------|\n"
        "| Acme | UAE | Proposal | $1M | 2026-01-01 | Misha | Send proposal | 2026-02-01 |\n",
        encoding="utf-8",
    )
    items = watch_items(tmp_path)
    deal_items = [i for i in items if "deal" in i["label"]]
    assert len(deal_items) == 1
    assert deal_items[0]["count"] >= 1
    assert deal_items[0]["severity"] == "red"
    assert deal_items[0]["link"] == "#/pipeline"


def test_watch_items_includes_stale_drafts(tmp_path):
    """An approval draft older than 24h surfaces as a yellow watch item."""
    import os, time
    from scripts.bridge_daemon.sources.pulse import watch_items
    from scripts.bridge_daemon.sources.approvals import EMAIL_DRAFTS_DIR
    drafts = tmp_path / EMAIL_DRAFTS_DIR
    drafts.mkdir(parents=True)
    target = drafts / "stale.md"
    target.write_text(
        "# Stale draft\n**To:** x@y.com\n**Subject:** S\n\n---\n\nbody",
        encoding="utf-8",
    )
    # Backdate mtime 30 hours.
    old = time.time() - 30 * 3600
    os.utime(target, (old, old))
    items = watch_items(tmp_path)
    draft_items = [i for i in items if "draft" in i["label"]]
    assert len(draft_items) == 1
    assert draft_items[0]["count"] == 1
    assert draft_items[0]["severity"] == "yellow"
    assert draft_items[0]["link"] == "#/approvals"


def test_watch_items_skips_fresh_drafts(tmp_path):
    """A fresh draft (mtime within 24h) does not surface."""
    from scripts.bridge_daemon.sources.pulse import watch_items
    from scripts.bridge_daemon.sources.approvals import EMAIL_DRAFTS_DIR
    drafts = tmp_path / EMAIL_DRAFTS_DIR
    drafts.mkdir(parents=True)
    (drafts / "fresh.md").write_text(
        "# Fresh\n**To:** x@y.com\n**Subject:** S\n\n---\n\nbody",
        encoding="utf-8",
    )
    items = watch_items(tmp_path)
    assert not any("draft" in i["label"] for i in items)


def test_watch_items_includes_large_inbox(tmp_path):
    """A needs-you backlog >= WATCH_LARGE_INBOX_THRESHOLD surfaces as yellow."""
    import json
    from scripts.bridge_daemon.sources.pulse import watch_items, WATCH_LARGE_INBOX_THRESHOLD
    fetch_file = tmp_path / "outputs" / "operations" / "email-intelligence" / "_latest-fetch.json"
    fetch_file.parent.mkdir(parents=True)
    # Build enough P1 conversations to all land in the 'needs-you' band.
    convs = [
        {"id": f"conv-{i}", "topic": f"Topic {i}", "priority": "P1",
         "latest_datetime": "2026-05-20T09:00:00+00:00", "analysis": {"priority": "P1"}}
        for i in range(WATCH_LARGE_INBOX_THRESHOLD + 5)
    ]
    fetch_file.write_text(
        json.dumps({"run_info": {"timestamp": "2026-05-20T10:00:00+00:00"},
                    "conversations": convs}),
        encoding="utf-8",
    )
    items = watch_items(tmp_path)
    inbox_items = [i for i in items if "inbox" in i["label"]]
    assert len(inbox_items) == 1
    assert inbox_items[0]["count"] >= WATCH_LARGE_INBOX_THRESHOLD
    assert inbox_items[0]["severity"] == "yellow"
    assert inbox_items[0]["link"] == "#/inbox"


def test_pulse_data_includes_now_and_watch(tmp_path):
    """pulse_data top-level dict has 'now' and 'watch' keys."""
    from scripts.bridge_daemon.sources.pulse import pulse_data
    result = pulse_data(tmp_path)
    assert "now" in result
    assert "watch" in result
    assert isinstance(result["watch"], list)


def test_next_items_returns_future_meetings_today(tmp_path):
    """Today's future meetings appear in next_items."""
    from scripts.bridge_daemon.sources.pulse import next_items
    from datetime import datetime, timezone
    cal_dir = tmp_path / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True)
    (cal_dir / "2026-05-18.md").write_text(
        "| 09:00 | Past meeting | - |\n"
        "| 14:00 | Future meeting | https://zoom.us/j/x |\n"
        "| 18:00 | Late meeting | - |\n",
        encoding="utf-8",
    )
    # 09:00 UTC = 13:00 local (UTC+4). Past < now < 14:00, 18:00.
    fake_now = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
    items = next_items(tmp_path, now=fake_now)
    labels = [it["label"] for it in items]
    assert "Past meeting" not in labels
    assert "Future meeting" in labels
    assert "Late meeting" in labels


def test_next_items_excludes_overdue_tasks(tmp_path):
    """Overdue tasks go to Watch, not Next."""
    from scripts.bridge_daemon.sources.pulse import next_items
    from datetime import datetime, timezone
    tasks_md = tmp_path / "outputs" / "operations" / "viraid" / "tasks.md"
    tasks_md.parent.mkdir(parents=True)
    tasks_md.write_text(
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | Overdue thing | *Task* | Due: 2026-05-10\n"
        "- [ ] **2026-05-01** | `P1` | Due today | *Task* | Due: 2026-05-18\n"
        "- [ ] **2026-05-01** | `P1` | Future thing | *Task* | Due: 2026-05-25\n",
        encoding="utf-8",
    )
    fake_now = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
    items = next_items(tmp_path, now=fake_now)
    labels = [it["label"] for it in items]
    assert "Overdue thing" not in labels
    assert "Future thing" not in labels
    assert "Due today" in labels


def test_next_items_capped_at_limit(tmp_path):
    """Returns at most `limit` items."""
    from scripts.bridge_daemon.sources.pulse import next_items
    from datetime import datetime, timezone
    cal_dir = tmp_path / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True)
    rows = [f"| {h:02d}:00 | Meeting {h} | - |\n" for h in range(14, 22)]
    (cal_dir / "2026-05-18.md").write_text("".join(rows), encoding="utf-8")
    fake_now = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)  # 13:00 local (UTC+4)
    items = next_items(tmp_path, now=fake_now, limit=3)
    assert len(items) == 3


def test_next_items_sorted_by_time(tmp_path):
    """Returned items are sorted by time-of-day."""
    from scripts.bridge_daemon.sources.pulse import next_items
    from datetime import datetime, timezone
    cal_dir = tmp_path / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True)
    (cal_dir / "2026-05-18.md").write_text(
        "| 18:00 | Late | - |\n"
        "| 14:00 | Mid | - |\n"
        "| 16:00 | Later | - |\n",
        encoding="utf-8",
    )
    fake_now = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
    items = next_items(tmp_path, now=fake_now)
    times = [it["time"] for it in items]
    assert times == ["14:00", "16:00", "18:00"]


def test_next_items_empty_when_nothing_scheduled(tmp_path):
    """No calendar + no tasks -> empty list."""
    from scripts.bridge_daemon.sources.pulse import next_items
    items = next_items(tmp_path)
    assert items == []


def test_next_meeting_includes_event_utc_iso(tmp_path):
    """The next_meeting payload now includes event_utc_iso for client-side tick."""
    cal_dir = tmp_path / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True)
    (cal_dir / "2026-05-18.md").write_text(
        "| 14:00 | Meeting | - |\n",
        encoding="utf-8",
    )
    # 09:00 UTC = 13:00 local (UTC+4). Event at 14:00 local (UTC+4) is 60 minutes away.
    fake_now = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
    result = next_meeting(tmp_path, now=fake_now)
    assert result is not None
    assert "event_utc_iso" in result
    # Event is at 14:00 local (UTC+4) = 10:00 UTC.
    assert result["event_utc_iso"].startswith("2026-05-18T10:00:00")
    # Sanity: still tz-aware UTC.
    from datetime import datetime as dt
    parsed = dt.fromisoformat(result["event_utc_iso"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_pulse_data_includes_pipeline_overdue_and_stages(tmp_path):
    """pulse_data exposes pipeline_overdue + pipeline_stages from list_pipeline()."""
    # Build a pipeline.md with mixed stages + an overdue deal.
    p = tmp_path / "context" / "pipeline.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---------|---------|-------|------------|------------|-------|-------------|----------|\n"
        "| A | X | Negotiation | $1,000,000 | 2026-03-01 | M | a | 2026-04-01 |\n"  # overdue
        "| B | X | Negotiation | $2,500,000 | 2026-03-01 | M | b | - |\n"
        "| C | X | Lead | TBD | 2026-03-01 | M | c | - |\n",
        encoding="utf-8",
    )
    result = pulse_data(tmp_path)
    assert "pipeline_overdue" in result["kpi"]
    assert "pipeline_stages" in result["kpi"]
    # Stages dict has both entries.
    assert result["kpi"]["pipeline_stages"]["Negotiation"] == 2
    assert result["kpi"]["pipeline_stages"]["Lead"] == 1
    # At least one overdue (Deal A's 2026-04-01 due date is before any plausible test "now").
    # The list_pipeline default uses date.today(), so this assertion is robust.
    assert result["kpi"]["pipeline_overdue"] >= 1


def test_pulse_data_pipeline_overdue_zero_when_no_pipeline_file(tmp_path):
    """No pipeline.md -> overdue=0, stages={} (silent degradation)."""
    result = pulse_data(tmp_path)
    assert result["kpi"]["pipeline_overdue"] == 0
    assert result["kpi"]["pipeline_stages"] == {}


# ============================================================
# Phase 1.32: raise_progress widget
# ============================================================
def _write_investor_shortlist(tmp_path, content: str) -> None:
    p = tmp_path / _INV_PROGRAM_DIR / "00-master-shortlist-v1.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _write_investor_message(tmp_path, filename: str) -> None:
    p = tmp_path / _INV_PROGRAM_DIR / "messages" / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# draft", encoding="utf-8")


def test_raise_progress_none_when_no_program(tmp_path):
    """No fundraising program dir -> None (silent degradation)."""
    assert raise_progress(tmp_path) is None


def test_raise_progress_counts_status_tiers(tmp_path):
    _write_investor_shortlist(tmp_path,
        "raise posture: $25-40M anchor\n\n"
        "## Europe (3)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 1 | A | VC | X | 10M | HIGH | x |\n"
        "| 2 | B | VC | X | 10M | HIGH | x |\n"
        "| 3 | C | VC | X | 10M | MED | x |\n\n"
        "# Decisions locked\n\n"
        "| Slot | Firm | Wave | Notes |\n"
        "|------|------|------|-------|\n"
        "| First 5 (this week) | A | Week 1 | x |\n"
        "| Parallel-track Week 1-2 | B | Week 1-2 | x |\n"
        "| Wave 2 (warm-intro-first) | C | Week 2-3 | x |\n"
    )
    r = raise_progress(tmp_path)
    assert r is not None
    assert r["target"] == "$25-40M"
    assert r["total"] == 3
    assert r["sendable_total"] == 3   # all three statuses are sendable
    assert r["sendable_drafts"] == 0  # no message files yet
    assert r["first_5_total"] == 1
    assert r["first_5_drafts"] == 0


def test_raise_progress_counts_drafts(tmp_path):
    _write_investor_shortlist(tmp_path,
        "## Europe (2)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 1 | A | VC | X | 10M | HIGH | x |\n"
        "| 2 | B | VC | X | 10M | HIGH | x |\n\n"
        "# Decisions locked\n\n"
        "| Slot | Firm | Wave | Notes |\n"
        "|------|------|------|-------|\n"
        "| First 5 (this week) | A | Week 1 | x |\n"
        "| First 5 (this week) | B | Week 1 | x |\n"
    )
    _write_investor_message(tmp_path, "01-a-first-touch.md")
    # Firm 2 (B) has NO draft.
    r = raise_progress(tmp_path)
    assert r["first_5_total"] == 2
    assert r["first_5_drafts"] == 1
    assert r["sendable_drafts"] == 1


def test_raise_progress_excludes_wave_3_and_out_of_scope(tmp_path):
    """wave-3 and out-of-scope are not 'sendable' (deferred / dropped)."""
    _write_investor_shortlist(tmp_path,
        "## GCC / MENA (2)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 1 | MGX | SWF | AD | 50M | HIGH | x |\n\n"
        "## UK / Israel (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 19 | Glilot Capital (Glilot+ growth platform) | VC | TLV | 10M | MED | x |\n\n"
        "# Decisions locked\n\n"
        "| Slot | Firm | Wave | Notes |\n"
        "|------|------|------|-------|\n"
        "| Wave 3 (deferred) | MGX | TBD | x |\n\n"
        "## Out-of-scope this round\n\n"
        "- **Glilot+** -- dropped this round.\n"
    )
    r = raise_progress(tmp_path)
    assert r["total"] == 2
    assert r["sendable_total"] == 0  # neither status is sendable
    assert r["first_5_total"] == 0


def test_pulse_data_includes_raise_progress(tmp_path):
    """pulse_data() surfaces raise_progress under kpi."""
    _write_investor_shortlist(tmp_path,
        "raise posture: $25-40M anchor\n\n"
        "## Europe (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 1 | A | VC | X | 10M | HIGH | x |\n\n"
        "# Decisions locked\n\n"
        "| Slot | Firm | Wave | Notes |\n"
        "|------|------|------|-------|\n"
        "| First 5 (this week) | A | Week 1 | x |\n"
    )
    result = pulse_data(tmp_path)
    assert "raise_progress" in result["kpi"]
    rp = result["kpi"]["raise_progress"]
    assert rp is not None
    assert rp["first_5_total"] == 1
    assert rp["target"] == "$25-40M"


def test_pulse_data_raise_progress_none_when_no_program(tmp_path):
    """When no fundraising program exists, raise_progress is None (Pulse omits the line)."""
    result = pulse_data(tmp_path)
    assert result["kpi"]["raise_progress"] is None


# ============================================================
# Phase 1.46: sea_state heuristic
# ============================================================
def _write_pipeline_with_overdue(tmp_path, overdue_count):
    """Build a pipeline.md with `overdue_count` overdue deals."""
    p = tmp_path / "context" / "pipeline.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(overdue_count):
        rows.append(f"| Co{i} | X | Negotiation | $1,000,000 | 2026-03-01 | M | a | 2026-04-01 |")
    body = (
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---------|---------|-------|------------|------------|-------|-------------|----------|\n"
        + "\n".join(rows)
    )
    p.write_text(body, encoding="utf-8")


def test_sea_state_calm_when_no_overdue(tmp_path):
    """No pipeline / tasks files -> calm."""
    r = sea_state(tmp_path)
    assert r["state"] == "calm"
    assert r["overdue_total"] == 0
    assert r["label"] == "Sea calm"


def test_sea_state_moderate_threshold(tmp_path):
    """3-9 overdue -> moderate."""
    _write_pipeline_with_overdue(tmp_path, 4)
    r = sea_state(tmp_path)
    assert r["state"] == "moderate"
    assert r["pipeline_overdue"] == 4
    assert r["overdue_total"] == 4


def test_sea_state_rough_threshold(tmp_path):
    """10+ overdue -> rough."""
    _write_pipeline_with_overdue(tmp_path, 12)
    r = sea_state(tmp_path)
    assert r["state"] == "rough"
    assert r["overdue_total"] == 12
    assert r["label"] == "Sea rough"


def _write_pipeline_for_signals(tmp_path, rows):
    """rows: list of dicts with company/stage/stage_date/due_date/next_action."""
    body = [
        "## Active Deals\n",
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |",
        "|---------|---------|-------|------------|------------|-------|-------------|----------|",
    ]
    for r in rows:
        body.append(
            f"| {r['company']} | X | {r['stage']} | $1,000,000 | {r['stage_date']} | M | "
            f"{r.get('next_action', 'a')} | {r.get('due_date', '-')} |"
        )
    p = tmp_path / "context" / "pipeline.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(body), encoding="utf-8")


def test_signals_empty_when_no_pipeline(tmp_path):
    """No pipeline.md -> empty list (not None)."""
    assert signals(tmp_path) == []


def test_signals_skip_non_forward_stages(tmp_path):
    """Lead / Qualified / Won do not generate stalled signals."""
    from datetime import date
    _write_pipeline_for_signals(tmp_path, [
        {"company": "Lead Co", "stage": "Lead", "stage_date": "2026-01-01"},
        {"company": "Qual Co", "stage": "Qualified", "stage_date": "2026-01-01"},
        {"company": "Won Co", "stage": "Won", "stage_date": "2026-01-01"},
    ])
    assert signals(tmp_path, today=date(2026, 5, 18)) == []


def test_signals_drifting_severity_red(tmp_path):
    """Forward-stage deal stalled >= 30 days -> red drifting signal."""
    from datetime import date
    _write_pipeline_for_signals(tmp_path, [
        {"company": "Acme", "stage": "Negotiation",
         "stage_date": "2026-03-01", "next_action": "Send NDA"},
    ])
    sigs = signals(tmp_path, today=date(2026, 5, 18))  # 78 days
    assert len(sigs) == 1
    assert sigs[0]["severity"] == "red"
    assert sigs[0]["kind"] == "pipeline-drifting"
    assert "Acme" in sigs[0]["title"]
    # Phase 1.75: ref carries the company name for deep-link to /pipeline?focus
    assert sigs[0]["ref"] == "Acme"


def test_signals_stalled_severity_yellow(tmp_path):
    """Forward-stage deal stalled 14-29 days -> yellow stalled signal."""
    from datetime import date
    _write_pipeline_for_signals(tmp_path, [
        {"company": "Beta", "stage": "Proposal",
         "stage_date": "2026-05-01", "next_action": "Follow up"},
    ])
    sigs = signals(tmp_path, today=date(2026, 5, 18))  # 17 days
    assert len(sigs) == 1
    assert sigs[0]["severity"] == "yellow"
    assert sigs[0]["kind"] == "pipeline-stalled"
    assert sigs[0]["ref"] == "Beta"


def test_signals_demo_poc_excluded_from_time_drift(tmp_path):
    """Recalibration 2026-06-22: a long Demo/POC is NOT time-drift (POCs run
    50-130 days legitimately). A 130-day Demo/POC with no overdue action fires
    no signal; the same stage in Negotiation would fire red."""
    from datetime import date
    _write_pipeline_for_signals(tmp_path, [
        {"company": "PocCo", "stage": "Demo/POC",
         "stage_date": "2026-01-08", "next_action": "Workshop after Ramadan"},
    ])
    assert signals(tmp_path, today=date(2026, 5, 18)) == []  # 130 days, no signal


def test_signals_demo_poc_still_fires_on_overdue_action(tmp_path):
    """Demo/POC is exempt from time-drift but NOT from an overdue next-action -
    that is a genuine 'do this now' regardless of stage age."""
    from datetime import date
    _write_pipeline_for_signals(tmp_path, [
        {"company": "PocLate", "stage": "Demo/POC", "stage_date": "2026-01-08",
         "next_action": "Send revised SOW", "due_date": "2026-05-10"},
    ])
    sigs = signals(tmp_path, today=date(2026, 5, 18))  # 8 days late
    assert len(sigs) == 1
    assert sigs[0]["kind"] == "pipeline-overdue-action"
    assert sigs[0]["ref"] == "PocLate"


def test_signals_paused_deals_excluded(tmp_path):
    """DEPRIORITIZED / ON HOLD / PAUSED prefixes suppress the signal."""
    from datetime import date
    _write_pipeline_for_signals(tmp_path, [
        {"company": "Skip-D", "stage": "Negotiation", "stage_date": "2026-01-01",
         "next_action": "DEPRIORITIZED per CEO"},
        {"company": "Skip-H", "stage": "Negotiation", "stage_date": "2026-01-01",
         "next_action": "ON HOLD pending review"},
        {"company": "Skip-P", "stage": "Proposal", "stage_date": "2026-01-01",
         "next_action": "PARKED until Q3"},
        {"company": "RealStall", "stage": "Negotiation", "stage_date": "2026-01-01",
         "next_action": "Send pricing"},
    ])
    sigs = signals(tmp_path, today=date(2026, 5, 18))
    # Only RealStall should signal.
    assert len(sigs) == 1
    assert "RealStall" in sigs[0]["title"]


def test_signals_recent_touch_suppresses_drift(tmp_path):
    """A deal touched within SIGNALS_TOUCH_SUPPRESS_DAYS skips stalled/drifting."""
    from datetime import date
    from scripts.bridge_daemon.sources.pipeline import mark_touched
    _write_pipeline_for_signals(tmp_path, [
        {"company": "OldDeal", "stage": "Negotiation", "stage_date": "2026-01-01",
         "next_action": "x"},
    ])
    # Without touch -> drifting red signal.
    sigs = signals(tmp_path, today=date(2026, 5, 18))
    assert len(sigs) == 1 and sigs[0]["kind"] == "pipeline-drifting"
    # Touch the deal -> signal suppressed.
    mark_touched(tmp_path, "OldDeal")
    sigs = signals(tmp_path, today=date(2026, 5, 18))
    assert sigs == []


def test_signals_capped_at_six(tmp_path):
    """No more than SIGNALS_CAP signals returned."""
    from datetime import date
    rows = [
        {"company": f"Co{i}", "stage": "Negotiation",
         "stage_date": "2026-01-01", "next_action": "x"}
        for i in range(12)
    ]
    _write_pipeline_for_signals(tmp_path, rows)
    sigs = signals(tmp_path, today=date(2026, 5, 18))
    assert len(sigs) == 6


def _write_thread(tmp_path, slug, title, last_touched, status="active"):
    biz_dir = tmp_path / "threads" / "business"
    biz_dir.mkdir(parents=True, exist_ok=True)
    (biz_dir / f"{slug}.md").write_text(
        f"---\nid: {slug}\ntitle: {title}\nstatus: {status}\nlast_touched: '{last_touched}'\ntype: business\n---\n\n# {title}\n\nBody.\n",
        encoding="utf-8",
    )


def test_threads_preview_none_when_dir_missing(tmp_path):
    assert threads_state_preview(tmp_path) is None


def test_threads_preview_none_when_no_active(tmp_path):
    """Only closed/held threads -> None."""
    _write_thread(tmp_path, "old", "Old thing", "2026-04-01", status="closed")
    _write_thread(tmp_path, "held", "Held thing", "2026-04-15", status="held")
    assert threads_state_preview(tmp_path) is None


def test_threads_preview_returns_active_sorted_by_recency(tmp_path):
    """Active threads sorted by days_since ASC (most recent first), capped at 6."""
    from datetime import date, timedelta
    today = date.today()
    for i in range(8):
        _write_thread(tmp_path, f"t{i}", f"Thread {i}",
                      (today - timedelta(days=i)).isoformat())
    r = threads_state_preview(tmp_path)
    assert r["active_total"] == 8
    assert len(r["threads"]) == 6
    days = [t["days_since"] for t in r["threads"]]
    assert days == sorted(days)


def test_threads_preview_excludes_closed(tmp_path):
    """Closed threads do not count toward active_total."""
    _write_thread(tmp_path, "a", "Active A", "2026-05-15", status="active")
    _write_thread(tmp_path, "b", "Closed B", "2026-05-10", status="closed")
    _write_thread(tmp_path, "c", "Held C", "2026-05-12", status="held")
    r = threads_state_preview(tmp_path)
    assert r["active_total"] == 1
    assert r["threads"][0]["title"] == "Active A"


def test_pulse_data_includes_threads_state(tmp_path):
    """pulse_data() exposes threads_state under kpi."""
    _write_thread(tmp_path, "x", "Demo thread", "2026-05-18")
    result = pulse_data(tmp_path)
    ts = result["kpi"].get("threads_state")
    assert ts is not None
    assert ts["active_total"] == 1
    assert ts["threads"][0]["title"] == "Demo thread"


def _write_tribe_member(tmp_path, slug, name, last_touch, relationship_type="tribe"):
    crm_dir = tmp_path / "crm" / "contacts"
    crm_dir.mkdir(parents=True, exist_ok=True)
    (crm_dir / f"{slug}.md").write_text(
        f"---\nrelationship_type: {relationship_type}\nlast_touch: {last_touch}\n---\n\n# {name} (misha-hanin)\n\nBody.\n",
        encoding="utf-8",
    )


def test_tribe_preview_none_when_no_members(tmp_path):
    """Empty CRM -> None (Pulse omits the card)."""
    assert tribe_state_preview(tmp_path) is None


def test_tribe_preview_returns_top_n_sorted_by_recency(tmp_path):
    """Members ordered by days_since ASC; cap at 6 rows."""
    from datetime import date, timedelta
    today = date.today()
    for i in range(8):
        _write_tribe_member(
            tmp_path,
            f"m{i}",
            f"Member {i}",
            (today - timedelta(days=i)).isoformat(),
        )
    r = tribe_state_preview(tmp_path)
    assert r["total"] == 8
    assert len(r["members"]) == 6  # cap
    days = [m["days_since"] for m in r["members"]]
    assert days == sorted(days)  # ASC sort


def test_tribe_preview_presence_threshold(tmp_path):
    """days_since <= 7 -> 'on'; > 7 -> 'off'."""
    from datetime import date, timedelta
    today = date.today()
    _write_tribe_member(tmp_path, "fresh", "Fresh One", (today - timedelta(days=1)).isoformat())
    _write_tribe_member(tmp_path, "edge", "Edge One",  (today - timedelta(days=7)).isoformat())
    _write_tribe_member(tmp_path, "stale", "Stale One", (today - timedelta(days=30)).isoformat())
    r = tribe_state_preview(tmp_path)
    by_name = {m["name"]: m for m in r["members"]}
    assert by_name["Fresh One"]["presence"] == "on"
    assert by_name["Edge One"]["presence"] == "on"
    assert by_name["Stale One"]["presence"] == "off"
    assert r["on_watch"] == 2


def test_pulse_data_includes_tribe_state(tmp_path):
    """pulse_data() surfaces tribe_state under kpi."""
    from datetime import date
    _write_tribe_member(tmp_path, "victor", "Victor H", date.today().isoformat())
    result = pulse_data(tmp_path)
    ts = result["kpi"].get("tribe_state")
    assert ts is not None
    assert ts["total"] == 1
    assert ts["on_watch"] == 1
    assert ts["members"][0]["name"] == "Victor H"


def _write_today_calendar(tmp_path, lines):
    """Write a calendar file for local (UTC+4)-today with the given event rows."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    today_local = datetime.now(timezone.utc).astimezone(ZoneInfo("Etc/GMT-4")).strftime("%Y-%m-%d")
    cal_dir = tmp_path / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True, exist_ok=True)
    (cal_dir / f"{today_local}.md").write_text("\n".join(lines), encoding="utf-8")


def test_sea_state_mood_open_when_no_events(tmp_path):
    r = sea_state(tmp_path)
    assert r["mood"] == "open"
    assert r["events_today"] == 0
    assert r["mood_label"] == "mood open"


def test_sea_state_mood_focused_for_few_events(tmp_path):
    """1-3 events -> focused."""
    _write_today_calendar(tmp_path, [
        "| 09:00 | A | - |",
        "| 13:00 | B | - |",
    ])
    r = sea_state(tmp_path)
    assert r["events_today"] == 2
    assert r["mood"] == "focused"


def test_sea_state_mood_split_for_busy_day(tmp_path):
    """4-6 events -> split."""
    _write_today_calendar(tmp_path, [
        f"| 0{i}:00 | E{i} | - |" for i in range(5)
    ])
    r = sea_state(tmp_path)
    assert r["events_today"] == 5
    assert r["mood"] == "split"


def test_sea_state_mood_packed_for_dense_day(tmp_path):
    """>= 7 events -> packed."""
    _write_today_calendar(tmp_path, [
        f"| {i:02d}:00 | E{i} | - |" for i in range(8, 16)
    ])
    r = sea_state(tmp_path)
    assert r["events_today"] >= 7
    assert r["mood"] == "packed"


def test_sea_state_state_and_mood_independent(tmp_path):
    """State (overdue) and mood (calendar) are orthogonal signals."""
    # Pipeline rough (12 overdue) + busy calendar (5 events) -> rough + split.
    _write_pipeline_with_overdue(tmp_path, 12)
    _write_today_calendar(tmp_path, [
        f"| 0{i}:00 | E{i} | - |" for i in range(5)
    ])
    r = sea_state(tmp_path)
    assert r["state"] == "rough"
    assert r["mood"] == "split"


# ============================================================
# Phase 1.64: today_activity recap across mutation logs
# ============================================================
def test_today_activity_empty_by_default(tmp_path):
    r = today_activity(tmp_path)
    assert r["investors_sent"] == 0
    assert r["pipeline_touched"] == 0
    assert r["inbox_dismissed"] == 0
    assert r["total"] == 0
    # Phase 1.69: entries dict is always present, with empty lists per kind.
    # Phase 1.71: approvals_sent joins as the fourth kind.
    # Phase 1.90: tasks_done joins as the fifth kind.
    assert r["entries"] == {
        "investors_sent": [],
        "pipeline_touched": [],
        "inbox_dismissed": [],
        "approvals_sent": [],
        "tasks_done": [],
    }


def test_today_activity_counts_investors_sent(tmp_path):
    from scripts.bridge_daemon.sources.investors import mark_sent
    mark_sent(tmp_path, 5)
    mark_sent(tmp_path, 8, note="via Outlook")
    r = today_activity(tmp_path)
    assert r["investors_sent"] == 2
    assert r["total"] == 2


def test_today_activity_excludes_investor_tombstones(tmp_path):
    from scripts.bridge_daemon.sources.investors import mark_sent, undo_sent
    mark_sent(tmp_path, 5)
    undo_sent(tmp_path, 5)
    r = today_activity(tmp_path)
    # The mark counts; the tombstone doesn't add to it.
    assert r["investors_sent"] == 1


def test_today_activity_counts_pipeline_touched(tmp_path):
    from scripts.bridge_daemon.sources.pipeline import mark_touched
    mark_touched(tmp_path, "Acme")
    mark_touched(tmp_path, "Beta")
    r = today_activity(tmp_path)
    assert r["pipeline_touched"] == 2


def test_today_activity_counts_inbox_dismissed(tmp_path):
    from scripts.bridge_daemon.sources.inbox import mark_dismissed, undo_dismissed
    mark_dismissed(tmp_path, "conv-a")
    mark_dismissed(tmp_path, "conv-b")
    undo_dismissed(tmp_path, "conv-a")
    r = today_activity(tmp_path)
    # Both dismisses count; the undo doesn't add or subtract from the total.
    assert r["inbox_dismissed"] == 2


def test_today_activity_combines_all_workflows(tmp_path):
    from scripts.bridge_daemon.sources.investors import mark_sent
    from scripts.bridge_daemon.sources.pipeline import mark_touched
    from scripts.bridge_daemon.sources.inbox import mark_dismissed
    mark_sent(tmp_path, 1)
    mark_touched(tmp_path, "Co")
    mark_dismissed(tmp_path, "c1")
    r = today_activity(tmp_path)
    assert r["investors_sent"] == 1
    assert r["pipeline_touched"] == 1
    assert r["inbox_dismissed"] == 1
    assert r["total"] == 3


# ============================================================
# Phase 1.69: today_activity entries (expandable recap)
# ============================================================
def test_today_activity_entries_shape_per_kind(tmp_path):
    """Each entry carries {kind, target, ts, note}."""
    from scripts.bridge_daemon.sources.investors import mark_sent
    from scripts.bridge_daemon.sources.pipeline import mark_touched
    from scripts.bridge_daemon.sources.inbox import mark_dismissed
    mark_sent(tmp_path, 5, note="via Outlook")
    mark_touched(tmp_path, "Acme", note="Demo scheduled")
    mark_dismissed(tmp_path, "conv-a", note="not relevant")
    r = today_activity(tmp_path)
    inv = r["entries"]["investors_sent"]
    pipe = r["entries"]["pipeline_touched"]
    inbox = r["entries"]["inbox_dismissed"]
    assert len(inv) == 1
    assert inv[0]["kind"] == "investor_sent"
    assert inv[0]["target"] == "firm #5"
    assert inv[0]["note"] == "via Outlook"
    assert inv[0]["ts"]  # populated by the source
    assert len(pipe) == 1
    assert pipe[0]["kind"] == "pipeline_touched"
    assert pipe[0]["target"] == "Acme"
    assert pipe[0]["note"] == "Demo scheduled"
    assert len(inbox) == 1
    assert inbox[0]["kind"] == "inbox_dismissed"
    assert inbox[0]["target"] == "conv-a"
    assert inbox[0]["note"] == "not relevant"


def test_today_activity_entries_exclude_tombstones(tmp_path):
    """Undo tombstones do not surface as entries, matching the counts contract."""
    from scripts.bridge_daemon.sources.investors import mark_sent, undo_sent
    from scripts.bridge_daemon.sources.inbox import mark_dismissed, undo_dismissed
    mark_sent(tmp_path, 9)
    undo_sent(tmp_path, 9)
    mark_dismissed(tmp_path, "conv-x")
    undo_dismissed(tmp_path, "conv-x")
    r = today_activity(tmp_path)
    # The mark stays in entries; the tombstone never renders as an entry.
    assert len(r["entries"]["investors_sent"]) == 1
    assert len(r["entries"]["inbox_dismissed"]) == 1
    for e in r["entries"]["investors_sent"] + r["entries"]["inbox_dismissed"]:
        assert e.get("kind") in ("investor_sent", "inbox_dismissed")


def test_today_activity_entries_capped_per_kind(tmp_path):
    """Each kind is capped at TODAY_ACTIVITY_ENTRY_CAP entries (tail-kept)."""
    from scripts.bridge_daemon.sources.pulse import TODAY_ACTIVITY_ENTRY_CAP
    from scripts.bridge_daemon.sources.pipeline import mark_touched
    for i in range(TODAY_ACTIVITY_ENTRY_CAP + 5):
        mark_touched(tmp_path, f"Co-{i}")
    r = today_activity(tmp_path)
    pipe = r["entries"]["pipeline_touched"]
    assert len(pipe) == TODAY_ACTIVITY_ENTRY_CAP
    # Cap retains the tail (most-recent entries), so the last one is in.
    assert any(e["target"] == f"Co-{TODAY_ACTIVITY_ENTRY_CAP + 4}" for e in pipe)
    # And the very first (oldest) entry has fallen off.
    assert not any(e["target"] == "Co-0" for e in pipe)


# ============================================================
# Phase 1.70: ref field on entries (deep-link from Pulse to source page)
# ============================================================
def test_today_activity_investor_entry_has_firm_num_ref(tmp_path):
    """investor_sent entries carry ref=firm_num so the UI can deep-link to /investors?focus=N."""
    from scripts.bridge_daemon.sources.investors import mark_sent
    mark_sent(tmp_path, 7)
    r = today_activity(tmp_path)
    inv = r["entries"]["investors_sent"]
    assert len(inv) == 1
    assert inv[0]["ref"] == 7


def test_today_activity_pipeline_entry_has_company_ref(tmp_path):
    """pipeline_touched entries carry ref=company so the UI can deep-link to /pipeline?focus=Company."""
    from scripts.bridge_daemon.sources.pipeline import mark_touched
    mark_touched(tmp_path, "Acme Corp")
    r = today_activity(tmp_path)
    pipe = r["entries"]["pipeline_touched"]
    assert len(pipe) == 1
    assert pipe[0]["ref"] == "Acme Corp"


def test_today_activity_inbox_entry_has_empty_ref(tmp_path):
    """inbox_dismissed entries carry empty ref (dismissed rows are filtered out
    of /inbox, so deep-linking has nothing to land on). Shape parity only."""
    from scripts.bridge_daemon.sources.inbox import mark_dismissed
    mark_dismissed(tmp_path, "conv-z")
    r = today_activity(tmp_path)
    inbox = r["entries"]["inbox_dismissed"]
    assert len(inbox) == 1
    assert inbox[0]["ref"] == ""


# ============================================================
# Phase 1.71: approval_sent as a fourth activity kind
# ============================================================
def test_today_activity_empty_includes_approvals_sent_key(tmp_path):
    """Empty payload exposes approvals_sent count + entries key for shape parity."""
    r = today_activity(tmp_path)
    assert r["approvals_sent"] == 0
    assert r["entries"]["approvals_sent"] == []


def test_today_activity_counts_approvals_sent(tmp_path):
    from scripts.bridge_daemon.sources.approvals import mark_sent, EMAIL_DRAFTS_DIR
    # mark_sent only validates the path prefix - file existence not required.
    mark_sent(tmp_path, f"{EMAIL_DRAFTS_DIR}/draft-a.md")
    mark_sent(tmp_path, f"{EMAIL_DRAFTS_DIR}/draft-b.md", note="to acme")
    r = today_activity(tmp_path)
    assert r["approvals_sent"] == 2


def test_today_activity_approval_entry_shape(tmp_path):
    """approval_sent entries carry filename as target, empty ref, note + ts."""
    from scripts.bridge_daemon.sources.approvals import mark_sent, EMAIL_DRAFTS_DIR
    mark_sent(tmp_path, f"{EMAIL_DRAFTS_DIR}/2026-05-18_acme.md", note="via Outlook")
    r = today_activity(tmp_path)
    a = r["entries"]["approvals_sent"]
    assert len(a) == 1
    assert a[0]["kind"] == "approval_sent"
    # Target is filename only (full paths are noisy in the recap).
    assert a[0]["target"] == "2026-05-18_acme.md"
    assert a[0]["ref"] == ""  # filtered out of /approvals, no nav target
    assert a[0]["note"] == "via Outlook"
    assert a[0]["ts"]


def test_today_activity_excludes_approval_tombstones(tmp_path):
    from scripts.bridge_daemon.sources.approvals import mark_sent, undo_sent, EMAIL_DRAFTS_DIR
    rel = f"{EMAIL_DRAFTS_DIR}/x.md"
    mark_sent(tmp_path, rel)
    undo_sent(tmp_path, rel)
    r = today_activity(tmp_path)
    # The mark counts; the tombstone doesn't add to it.
    assert r["approvals_sent"] == 1
    # The tombstone never surfaces as an entry.
    for e in r["entries"]["approvals_sent"]:
        assert e["kind"] == "approval_sent"


def test_today_activity_total_includes_approvals(tmp_path):
    """The top-level total sums all four kinds."""
    from scripts.bridge_daemon.sources.investors import mark_sent as inv_mark
    from scripts.bridge_daemon.sources.pipeline import mark_touched
    from scripts.bridge_daemon.sources.inbox import mark_dismissed
    from scripts.bridge_daemon.sources.approvals import mark_sent as app_mark, EMAIL_DRAFTS_DIR
    inv_mark(tmp_path, 1)
    mark_touched(tmp_path, "Co")
    mark_dismissed(tmp_path, "conv-1")
    app_mark(tmp_path, f"{EMAIL_DRAFTS_DIR}/x.md")
    r = today_activity(tmp_path)
    assert r["total"] == 4


def test_pulse_data_includes_today_activity(tmp_path):
    result = pulse_data(tmp_path)
    a = result["kpi"].get("today_activity")
    assert a is not None
    assert "total" in a


def test_pulse_data_includes_sea_state(tmp_path):
    """pulse_data() exposes sea_state under kpi for the subhead."""
    result = pulse_data(tmp_path)
    ss = result["kpi"].get("sea_state")
    assert ss is not None
    assert ss["state"] in ("calm", "moderate", "rough")
    assert ss["mood"] in ("open", "focused", "split", "packed")


# ============================================================
# Phase 1.93: recent_outputs panel for Pulse 'Recent outputs'
# ============================================================
def test_pulse_data_includes_recent_outputs_field(tmp_path):
    """pulse_data() exposes recent_outputs as a top-level list."""
    result = pulse_data(tmp_path)
    assert "recent_outputs" in result
    assert isinstance(result["recent_outputs"], list)


def test_pulse_data_recent_outputs_top_5(tmp_path):
    """When the in-flight dir has more than 5 items, only top-5 surface."""
    import time
    inflight = tmp_path / "outputs" / "content" / "linkedin"
    inflight.mkdir(parents=True)
    for i in range(8):
        p = inflight / f"draft-{i}.md"
        p.write_text(f"# Draft {i}\nbody", encoding="utf-8")
        # Stagger mtimes so sort order is deterministic.
        ts = time.time() - (8 - i) * 60
        import os
        os.utime(p, (ts, ts))
    result = pulse_data(tmp_path)
    assert len(result["recent_outputs"]) == 5
    # Top-5 are the newest. Mid-bunch must not appear.
    names = [it["name"] for it in result["recent_outputs"]]
    assert "draft-7.md" in names  # newest
    assert "draft-0.md" not in names  # oldest


def test_pulse_data_includes_suggested_field(tmp_path):
    """pulse_data() exposes a top-level 'suggested' list (possibly empty)."""
    result = pulse_data(tmp_path)
    assert "suggested" in result
    assert isinstance(result["suggested"], list)


def test_suggestions_empty_on_clean_state(tmp_path):
    """No data anywhere -> no suggestions."""
    from datetime import date
    from scripts.bridge_daemon.sources.pulse import suggestions
    # Use a non-Monday so the tribe-monday rule doesn't fire.
    sugs = suggestions(tmp_path, today=date(2026, 5, 20))  # Wednesday
    assert sugs == []


def test_suggestions_monday_triggers_tribe_monday(tmp_path):
    """Mondays add the /tribe-monday suggestion."""
    from datetime import date
    from scripts.bridge_daemon.sources.pulse import suggestions
    monday = date(2026, 5, 18)  # Monday
    sugs = suggestions(tmp_path, today=monday)
    agents = [s["agent"] for s in sugs]
    assert "/tribe-monday" in agents


def test_suggestions_overdue_tasks_trigger_tasks_link(tmp_path):
    """An overdue task surfaces a /tasks suggestion."""
    from datetime import date
    from scripts.bridge_daemon.sources.pulse import suggestions
    tasks_md = tmp_path / "outputs" / "operations" / "viraid" / "tasks.md"
    tasks_md.parent.mkdir(parents=True)
    tasks_md.write_text(
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | Stale | *Task* | Due: 2026-05-05\n",
        encoding="utf-8",
    )
    sugs = suggestions(tmp_path, today=date(2026, 5, 20))  # Wednesday
    agents = [s["agent"] for s in sugs]
    assert "/tasks" in agents


def test_suggestions_capped_at_cap(tmp_path):
    """No matter how many rules fire, at most SUGGESTIONS_CAP rows return."""
    from datetime import date
    from scripts.bridge_daemon.sources.pulse import suggestions, SUGGESTIONS_CAP
    # Build a state that fires many rules: stalled deals + drafts + overdue
    # tasks + Monday. Stalled needs a pipeline.md with at-stage > 14d.
    pipeline_md = tmp_path / "context" / "pipeline.md"
    pipeline_md.parent.mkdir(parents=True)
    pipeline_md.write_text(
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---------|---------|-------|-----------|-----------|------|-------------|---------|\n"
        "| Acme    | UAE     | Proposal | $1M    | 2026-01-01 | Misha| Send NDA    |          |\n"
        "| Beta    | UAE     | Proposal | $1M    | 2026-01-01 | Misha| Send draft  |          |\n"
        "| Gamma   | UAE     | Proposal | $1M    | 2026-01-01 | Misha| Send pricing|          |\n",
        encoding="utf-8",
    )
    tasks_md = tmp_path / "outputs" / "operations" / "viraid" / "tasks.md"
    tasks_md.parent.mkdir(parents=True)
    tasks_md.write_text(
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | x | *Task* | Due: 2026-05-05\n",
        encoding="utf-8",
    )
    sugs = suggestions(tmp_path, today=date(2026, 5, 18))  # Monday
    assert len(sugs) <= SUGGESTIONS_CAP


def test_signals_default_cap_six(tmp_path):
    """signals() defaults to SIGNALS_CAP (6) for the Pulse-embedded view."""
    from datetime import date
    from scripts.bridge_daemon.sources.pulse import signals, SIGNALS_CAP
    # Build 10 stalled deals.
    deals = "\n".join(
        f"| Co-{i} | UAE | Proposal | $1M | 2026-01-01 | Misha | Send pricing |  |"
        for i in range(10)
    )
    pipeline_md = tmp_path / "context" / "pipeline.md"
    pipeline_md.parent.mkdir(parents=True)
    pipeline_md.write_text(
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---|---|---|---|---|---|---|---|\n"
        f"{deals}\n",
        encoding="utf-8",
    )
    sigs = signals(tmp_path, today=date(2026, 5, 20))
    assert len(sigs) == SIGNALS_CAP


def test_signals_accepts_explicit_cap(tmp_path):
    """The /signals page passes a wider cap to see the full list."""
    from datetime import date
    from scripts.bridge_daemon.sources.pulse import signals
    deals = "\n".join(
        f"| Co-{i} | UAE | Proposal | $1M | 2026-01-01 | Misha | Send pricing |  |"
        for i in range(10)
    )
    pipeline_md = tmp_path / "context" / "pipeline.md"
    pipeline_md.parent.mkdir(parents=True)
    pipeline_md.write_text(
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---|---|---|---|---|---|---|---|\n"
        f"{deals}\n",
        encoding="utf-8",
    )
    sigs = signals(tmp_path, today=date(2026, 5, 20), cap=50)
    assert len(sigs) == 10  # all 10 surface when cap is wider


def test_suggestions_shape_carries_required_fields(tmp_path):
    """Each suggestion has agent / reason / action / link."""
    from datetime import date
    from scripts.bridge_daemon.sources.pulse import suggestions
    sugs = suggestions(tmp_path, today=date(2026, 5, 18))  # Monday -> one row
    assert len(sugs) >= 1
    for s in sugs:
        assert "agent" in s
        assert "reason" in s
        assert "action" in s
        assert "link" in s


def test_pulse_data_recent_outputs_carries_category_and_mtime(tmp_path):
    """Each recent_outputs entry has the v8-needed fields."""
    inflight = tmp_path / "outputs" / "content" / "linkedin"
    inflight.mkdir(parents=True)
    (inflight / "post.md").write_text("# X\n", encoding="utf-8")
    result = pulse_data(tmp_path)
    if result["recent_outputs"]:
        it = result["recent_outputs"][0]
        assert "category" in it
        assert "mtime" in it
        assert "name" in it
        assert "path" in it


def test_sea_state_combines_pipeline_and_tasks(tmp_path):
    """overdue_total sums both sources."""
    _write_pipeline_with_overdue(tmp_path, 2)
    # Add 2 overdue tasks via the viraid tasks.md format.
    viraid_dir = tmp_path / "outputs" / "operations" / "viraid"
    viraid_dir.mkdir(parents=True, exist_ok=True)
    (viraid_dir / "tasks.md").write_text(
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | Late task A | *Task* | Due: 2026-05-10\n"
        "- [ ] **2026-05-01** | `P1` | Late task B | *Task* | Due: 2026-05-11\n",
        encoding="utf-8",
    )
    r = sea_state(tmp_path)
    # 2 pipeline + 2 task = 4 total -> moderate
    assert r["pipeline_overdue"] == 2
    assert r["tasks_overdue"] >= 2
    assert r["overdue_total"] >= 4
    assert r["state"] == "moderate"
