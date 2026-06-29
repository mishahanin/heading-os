"""Real-data sources for the /pulse endpoint.

Phase 1.5: parse existing workspace files (no new producers required).
Phase 2 will swap to a refresh_prime cache for performance.
"""
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.bridge_daemon.sources.pipeline import list_pipeline
from scripts.bridge_daemon.sources.investors import list_investors
from scripts.utils.paths import get_data_root
from scripts.utils.workspace import get_default_tz, get_default_tz_name


# ODIN-5 target date locked per spec ("ODIN-5 end-2026").
# Override via corporate/daemon/config.yaml -> kpi.odin_5_target_date.
ODIN_5_TARGET_DEFAULT = "2026-12-31"

# Workspace dirs scanned for "in-flight" file count.
# Must stay in sync with sources/studio.IN_FLIGHT_DIRS (path components).
# Both surfaces (Pulse count + Studio item list) need to agree so the CEO
# sees consistent numbers across pages.
IN_FLIGHT_DIRS = (
    "outputs/operations/email-intelligence",  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
    "outputs/content/linkedin",  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
    "outputs/intel",  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
    "outputs/negotiations",  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
    "outputs/documents",  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
    "outputs/content/tribe",  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
    "outputs/operations/fundraising",  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
)
IN_FLIGHT_WINDOW_DAYS = 7


def days_to_odin_5(target_iso: str | None = None) -> int:
    """Days remaining until the ODIN-5 target date. Negative if already past."""
    target = date.fromisoformat(target_iso or ODIN_5_TARGET_DEFAULT)
    return (target - date.today()).days


def pipeline_value_and_deals(workspace_root: Path, data_root: "Path | None" = None) -> tuple[int, int]:
    """Parse context/pipeline.md for total pipeline value and active deal count.

    Returns (value_usd, active_deal_count). Both 0 if the file is missing
    or unparseable - silent degradation so the dashboard never throws on
    a corrupt pipeline file.

    HEADING OS engine/data split: context/pipeline.md is DATA, resolved
    under ``data_root`` (falls back to ``workspace_root``).
    """
    if data_root is None:
        data_root = get_data_root()
    p = data_root / "context" / "pipeline.md"
    if not p.exists():
        return 0, 0
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return 0, 0
    # Match lines like: | Total pipeline value (priced deals only) | $11,000,000 |
    value = 0
    m = re.search(r"\|\s*Total pipeline value[^|]*\|\s*\$([\d,]+)\s*\|", text)
    if m:
        try:
            value = int(m.group(1).replace(",", ""))
        except ValueError:
            value = 0
    # Active deals count.
    deals = 0
    m = re.search(r"\|\s*Total active deals\s*\|\s*(\d+)\s*\|", text)
    if m:
        try:
            deals = int(m.group(1))
        except ValueError:
            deals = 0
    return value, deals


# in_flight_count removed 2026-05-24: its tree-walk was a duplicate of the
# one in studio.recent_inflight_items. pulse_data() now derives both the
# count and the recent-items list from a single call (see total_count in
# the recent_inflight_items return). The shared scan also gained scandir +
# directory pruning, dropping refresher cost from ~8 s to <1 s on WSL.


# Calendar parsing: outputs/_sync/calendar/YYYY-MM-DD.md has a markdown table.
# Rows look like: | 10:45 | Morning Sync | https://zoom.us/... | 15m |
# Duration column is optional in the regex - trailing `\|?` accepts rows that
# stop after the location column.
_CAL_ROW_RE = re.compile(
    r"^\|\s*(?P<time>\d{2}:\d{2})\s*\|\s*(?P<subject>[^|]+?)\s*\|\s*(?P<location>[^|]*?)\s*(?:\|\s*(?P<duration>[^|]*?)\s*)?\|?\s*$"
)


def _parse_duration_minutes(s: str) -> int:
    """Parse 'Nm', 'Nh', 'NhMm', 'Nh Mm' into total minutes.

    Returns 30 on unparseable input (sensible default for an unmarked
    meeting).
    """
    if not s:
        return 30
    s = s.strip().lower().replace(" ", "")
    total = 0
    matched_any = False
    h_match = re.search(r"(\d+)h", s)
    if h_match:
        total += int(h_match.group(1)) * 60
        matched_any = True
    m_match = re.search(r"(\d+)m", s)
    if m_match:
        total += int(m_match.group(1))
        matched_any = True
    return total if matched_any else 30


def next_meeting(workspace_root: Path, now: datetime | None = None,
                 data_root: "Path | None" = None) -> dict | None:
    """Return the next upcoming meeting from today's calendar markdown file.

    Returns a dict with time (HH:MM), subject (str), location (str, may be
    empty or a URL), and minutes_until (int from now to event, can be 0).
    Returns None if there's no calendar file or no future events today.

    Calendar files use the configured timezone per workspace convention; we resolve
    'today' and 'now' in that timezone so the date selection and event
    comparison are consistent regardless of the daemon machine's local
    timezone.

    HEADING OS engine/data split: the calendar file is DATA, resolved under
    ``data_root`` (falls back to ``workspace_root`` when not supplied).
    """
    if data_root is None:
        data_root = get_data_root()
    if now is None:
        now = datetime.now(timezone.utc)
    # Always evaluate the calendar in the configured local timezone, even if the daemon runs
    # in a different timezone.
    now_local = now.astimezone(get_default_tz())
    today = now_local.strftime("%Y-%m-%d")
    cal = data_root / "outputs" / "_sync" / "calendar" / f"{today}.md"
    if not cal.exists():
        return None
    try:
        text = cal.read_text(encoding="utf-8")
    except OSError:
        return None
    candidates = []
    for line in text.splitlines():
        m = _CAL_ROW_RE.match(line)
        if not m:
            continue
        # Defensive: skip rows with too many pipes (unescaped | in a cell).
        if line.count("|") > 5:
            continue
        time_str = m.group("time")
        try:
            hh, mm = (int(x) for x in time_str.split(":"))
        except ValueError:
            continue
        if not (0 <= hh < 24 and 0 <= mm < 60):
            continue
        subject = m.group("subject").strip()
        location_raw = m.group("location").strip()
        # Normalize "-" / em-dash to empty string.
        location = "" if location_raw in ("-", "—") else location_raw
        # Build the event datetime in the configured local timezone (matches the file's tagging).
        event_dt = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if event_dt >= now_local:
            candidates.append((event_dt, time_str, subject, location))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    event_dt, time_str, subject, location = candidates[0]
    minutes_until = int((event_dt - now_local).total_seconds() // 60)
    return {
        "time": time_str,
        "subject": subject,
        "location": location,
        "minutes_until": minutes_until,
        "event_utc_iso": event_dt.astimezone(timezone.utc).isoformat(),
    }


def current_meeting(workspace_root: Path, now: datetime | None = None,
                    data_root: "Path | None" = None) -> dict | None:
    """Return the calendar event in progress right now, or None.

    A meeting is "in progress" when it started at or before now and ends
    after now (start + duration). Returns
    {focus, until, minutes_remaining} when active.

    HEADING OS engine/data split: the calendar file is DATA, resolved under
    ``data_root`` (falls back to ``workspace_root`` when not supplied).
    """
    if data_root is None:
        data_root = get_data_root()
    if now is None:
        now = datetime.now(timezone.utc)
    now_local = now.astimezone(get_default_tz())
    today = now_local.strftime("%Y-%m-%d")
    cal = data_root / "outputs" / "_sync" / "calendar" / f"{today}.md"
    if not cal.exists():
        return None
    try:
        text = cal.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        m = _CAL_ROW_RE.match(line)
        if not m:
            continue
        # Defensive: too many pipes -> skip this row.
        if line.count("|") > 6:
            continue
        time_str = m.group("time")
        try:
            hh, mm = (int(x) for x in time_str.split(":"))
        except ValueError:
            continue
        if not (0 <= hh < 24 and 0 <= mm < 60):
            continue
        subject = m.group("subject").strip()
        dur_min = _parse_duration_minutes(m.group("duration") or "")
        start_dt = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        end_dt = start_dt + timedelta(minutes=dur_min)
        if start_dt <= now_local < end_dt:
            minutes_remaining = int((end_dt - now_local).total_seconds() // 60)
            return {
                "focus": subject,
                "until": end_dt.strftime("%H:%M"),
                "minutes_remaining": minutes_remaining,
            }
    return None


WATCH_STALE_DRAFT_HOURS = 24
WATCH_LARGE_INBOX_THRESHOLD = 25


def watch_items(workspace_root: Path, data_root: "Path | None" = None) -> list:
    """Aggregate cross-source watchpoints for the Pulse top-of-mind block.

    Each item: {label, count, severity ('red'|'yellow'), link}.
    Each source is wrapped in try/except so a broken parser never poisons
    the whole list.

    Surfaces:
      - overdue tasks (red)            -> #/tasks
      - overdue pipeline deals (red)   -> #/pipeline
      - drafts pending >24h (yellow)   -> #/approvals
      - large unread inbox (>=25, yellow) -> #/inbox

    HEADING OS engine/data split: all watched sources are DATA, resolved
    under ``data_root`` (falls back to ``workspace_root``).
    """
    if data_root is None:
        data_root = get_data_root()
    from .tasks import list_active_tasks
    items: list = []
    try:
        tasks_data = list_active_tasks(data_root)
        overdue = tasks_data.get("overdue_count", 0)
        if overdue > 0:
            items.append({
                "label": f"overdue task{'s' if overdue != 1 else ''}",
                "count": overdue,
                "severity": "red",
                "link": "#/tasks",
            })
    except Exception as e:
        logging.warning("bridge.pulse.watch.tasks: source unavailable, skipping: %s", e)

    # Phase 1.74: overdue pipeline deals.
    try:
        from .pipeline import list_pipeline
        pipe = list_pipeline(data_root)
        overdue_deals = pipe.get("overdue_count", 0)
        if overdue_deals > 0:
            items.append({
                "label": f"overdue deal{'s' if overdue_deals != 1 else ''}",
                "count": overdue_deals,
                "severity": "red",
                "link": "#/pipeline",
            })
    except Exception as e:
        logging.warning("bridge.pulse.watch.pipeline: source unavailable, skipping: %s", e)

    # Phase 1.74: drafts waiting >24h. Uses mtime from list_approvals.items
    # (which already filters out marked-sent paths).
    try:
        from .approvals import list_approvals
        approvals = list_approvals(data_root)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=WATCH_STALE_DRAFT_HOURS)
        stale = 0
        for it in approvals.get("items", []) or []:
            mtime_iso = it.get("mtime")
            if not mtime_iso:
                continue
            try:
                mt = datetime.fromisoformat(mtime_iso)
            except ValueError:
                continue
            if mt < cutoff:
                stale += 1
        if stale > 0:
            items.append({
                "label": f"draft{'s' if stale != 1 else ''} >24h",
                "count": stale,
                "severity": "yellow",
                "link": "#/approvals",
            })
    except Exception as e:
        logging.warning("bridge.pulse.watch.approvals: source unavailable, skipping: %s", e)

    # Phase 1.74: large inbox backlog.
    # Phase 1.32: the Inbox is banded now; the 'needs you' count (P1/P2
    # conversations) is the backlog signal the old unread_count carried.
    try:
        from .inbox import read_inbox
        ib = read_inbox(data_root)
        needs_you = ib.get("counts", {}).get("needs-you", 0)
        if needs_you >= WATCH_LARGE_INBOX_THRESHOLD:
            items.append({
                "label": "inbox needs you",
                "count": needs_you,
                "severity": "yellow",
                "link": "#/inbox",
            })
    except Exception as e:
        logging.warning("bridge.pulse.watch.inbox: source unavailable, skipping: %s", e)

    return items


def next_items(workspace_root: Path, now: datetime | None = None, limit: int = 5,
               data_root: "Path | None" = None) -> list:
    """Return upcoming items across sources for today.

    Combines:
      - Today's remaining calendar events (future, local TZ)
      - Active tasks due today (not in past, not in future)

    Sorted by time-of-day (events with explicit time first, tasks with
    only a due-date go at the end), capped at `limit`.

    Phase 1.17 scope: today only. Phase 2 will extend to "next 6 hours"
    spanning into tomorrow if relevant.

    HEADING OS engine/data split: the calendar files + tasks are DATA,
    resolved under ``data_root`` (falls back to ``workspace_root``).
    """
    if data_root is None:
        data_root = get_data_root()
    if now is None:
        now = datetime.now(timezone.utc)
    now_local = now.astimezone(get_default_tz())
    today_str = now_local.strftime("%Y-%m-%d")

    items = []

    # Today's remaining calendar events.
    cal = data_root / "outputs" / "_sync" / "calendar" / f"{today_str}.md"
    if cal.exists():
        try:
            text = cal.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for line in text.splitlines():
            m = _CAL_ROW_RE.match(line)
            if not m:
                continue
            if line.count("|") > 6:
                continue
            time_str = m.group("time")
            try:
                hh, mm = (int(x) for x in time_str.split(":"))
            except ValueError:
                continue
            if not (0 <= hh < 24 and 0 <= mm < 60):
                continue
            event_dt = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if event_dt >= now_local:
                subject = m.group("subject").strip()
                location_raw = (m.group("location") or "").strip()
                location = "" if location_raw in ("-", "—") else location_raw
                items.append({
                    "kind": "meeting",
                    "time": time_str,
                    "label": subject,
                    "location": location,
                    "_sort": event_dt.isoformat(),
                })

    # Today's active tasks due today (overdue go to Watch, not Next).
    try:
        from .tasks import list_active_tasks
        # Phase 1.80: pass the caller's notion of "today" through so
        # is_overdue is evaluated consistently with `now`. Without this,
        # list_active_tasks would use date.today() and disagree with the
        # caller across the UTC midnight boundary.
        tasks = list_active_tasks(workspace_root, today=now_local.date(), data_root=data_root)
        for t in tasks.get("tasks", []):
            if t.get("due") == today_str and not t.get("is_overdue"):
                items.append({
                    "kind": "task",
                    "time": "",  # tasks have only a date, not a time
                    "label": t["description"],
                    "priority": t.get("priority", ""),
                    "_sort": f"99:99 {t.get('priority', 'P9')}",  # sort after timed events
                })
    except Exception as e:
        logging.warning("bridge.pulse.next.tasks: source unavailable, skipping: %s", e)

    items.sort(key=lambda x: x.pop("_sort"))

    # Phase 1.121: when today's list is empty, surface tomorrow's earliest
    # calendar event so the Next panel always has a useful pointer. CEO
    # observed (2026-05-19) that an empty Next section feels broken;
    # 'next day' fallback is preferred over a bare empty state.
    if not items:
        tomorrow = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
        cal_tom = data_root / "outputs" / "_sync" / "calendar" / f"{tomorrow}.md"
        if cal_tom.exists():
            try:
                text = cal_tom.read_text(encoding="utf-8")
            except OSError:
                text = ""
            tom_items = []
            for line in text.splitlines():
                m = _CAL_ROW_RE.match(line)
                if not m or line.count("|") > 6:
                    continue
                time_str = m.group("time")
                try:
                    hh, mm = (int(x) for x in time_str.split(":"))
                except ValueError:
                    continue
                if not (0 <= hh < 24 and 0 <= mm < 60):
                    continue
                subject = m.group("subject").strip()
                location_raw = (m.group("location") or "").strip()
                location = "" if location_raw in ("-", "—") else location_raw
                tom_items.append({
                    "kind": "meeting",
                    "time": time_str,
                    "label": subject,
                    "location": location,
                    "is_next_day": True,
                    "day_label": "Tomorrow",
                    "_sort": time_str,
                })
            tom_items.sort(key=lambda x: x.pop("_sort"))
            # Show only the first tomorrow event - the panel is meant as a
            # 'what's after this clear stretch?' pointer, not a tomorrow agenda.
            return tom_items[:1]

    return items[:limit]


_SENDABLE_STATUSES = {"first-5", "parallel-week-1-2", "wave-2"}


def raise_progress(workspace_root: Path, data_root: "Path | None" = None) -> dict | None:
    """Summarise the active fundraising raise.

    Returns None when no active raise program exists (silent degradation,
    Pulse omits the line). Otherwise returns:
        {
            "target": str | None,       # "$25-40M" anchor from the brief
            "total": int,               # all firms on the shortlist
            "sendable_total": int,      # firms in first-5 + parallel + wave-2
            "sendable_drafts": int,     # sendable firms with a first-touch draft on disk
            "first_5_total": int,
            "first_5_drafts": int,
        }

    HEADING OS engine/data split: the fundraising program is DATA, resolved
    under ``data_root`` (falls back to ``workspace_root``).
    """
    if data_root is None:
        data_root = get_data_root()
    try:
        d = list_investors(data_root)
    except Exception:
        return None
    if not d or d.get("total", 0) == 0:
        return None
    sendable_total = 0
    sendable_drafts = 0
    sendable_sent = 0
    first_5_total = 0
    first_5_drafts = 0
    first_5_sent = 0
    for f in d.get("firms", []):
        status = f.get("status", "")
        has_draft = bool(f.get("message_path"))
        has_sent = bool(f.get("sent_date"))
        if status in _SENDABLE_STATUSES:
            sendable_total += 1
            if has_draft:
                sendable_drafts += 1
            if has_sent:
                sendable_sent += 1
        if status == "first-5":
            first_5_total += 1
            if has_draft:
                first_5_drafts += 1
            if has_sent:
                first_5_sent += 1
    return {
        "target": d.get("raise_target"),
        "total": d["total"],
        "sendable_total": sendable_total,
        "sendable_drafts": sendable_drafts,
        "sendable_sent": sendable_sent,
        "first_5_total": first_5_total,
        "first_5_drafts": first_5_drafts,
        "first_5_sent": first_5_sent,
    }


TRIBE_PREVIEW_ROW_CAP = 6
TRIBE_ON_WATCH_DAYS = 7

THREADS_PREVIEW_ROW_CAP = 6
THREADS_BUSINESS_DIR = "threads/business"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)
# Status values we treat as active. Spec uses 'active'; 'open' is a common
# variant. Anything else (closed, held, abandoned) is excluded.
THREADS_ACTIVE_STATUSES = {"active", "open"}

# Frontmatter parser - same shape as library.py uses.
_THREAD_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_THREAD_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")


def tribe_state_preview(workspace_root: Path, data_root: "Path | None" = None) -> dict:
    """Compact tribe-state snapshot for the Pulse footer card.

    'on watch' is defined as days_since_touch <= TRIBE_ON_WATCH_DAYS;
    that's the CEO's natural-cadence horizon. Members are ordered by
    days_since_touch ASC (most-recently-touched first) and capped at
    TRIBE_PREVIEW_ROW_CAP rows.

    Returns:
        {
            "members": [
                {"slug": str, "name": str, "role": str,
                 "days_since": int, "presence": "on" | "off"},
                ...
            ],
            "total": int,
            "on_watch": int,
        }
        or None when the tribe source is unavailable.

    HEADING OS engine/data split: the tribe (crm/contacts + roster xlsx) is
    DATA, resolved under ``data_root`` (falls back to ``workspace_root``).
    """
    if data_root is None:
        data_root = get_data_root()
    try:
        from .tribe import list_tribe
        d = list_tribe(data_root)
    except Exception:
        return None
    members_in = d.get("members") or []
    if not members_in:
        return None
    enriched = []
    for m in members_in:
        days = m.get("days_since_touch")
        presence = "on" if (isinstance(days, int) and days <= TRIBE_ON_WATCH_DAYS) else "off"
        enriched.append({
            "slug": m.get("slug", ""),
            "name": m.get("name", ""),
            "role": m.get("role", ""),
            "days_since": days if isinstance(days, int) else None,
            "presence": presence,
        })
    # Sort by days_since ASC; unknown days go to the bottom.
    def key(x):
        d = x["days_since"]
        return (d is None, d if d is not None else 0)
    enriched.sort(key=key)
    on_watch = sum(1 for x in enriched if x["presence"] == "on")
    return {
        "members": enriched[:TRIBE_PREVIEW_ROW_CAP],
        "total": len(enriched),
        "on_watch": on_watch,
    }


# ============================================================
# Signals - operational anomalies that need CEO attention
# ============================================================
# v1 scope: pipeline-only. Tribe cadence breaches are NOT signalled per
# CEO directive (memory: feedback_crm_cadence_exceptions - CEO talks
# to Tribe daily; cadence alerts would be noise).

SIGNALS_FORWARD_STAGES = {"Negotiation", "Proposal", "Demo/POC"}
# Stages where TIME-IN-STAGE is itself a drift signal. Demo/POC is
# deliberately excluded (recalibrated 2026-06-22, CEO): a sovereign-DPI
# proof-of-value legitimately runs 50-130 days - "Proof of Value over PoC"
# is a 31C core principle - so a long Demo/POC is expected, not drift.
# Flagging every long POC red made the whole Critical Signals section noise.
# Demo/POC still signals on an OVERDUE next-action (a genuine "do this now"),
# just not on elapsed time in stage.
SIGNALS_TIME_DRIFT_STAGES = {"Negotiation", "Proposal"}
SIGNALS_STALLED_DAYS = 14   # days_at_stage threshold for 'stalled'
SIGNALS_DRIFTING_DAYS = 30  # days_at_stage threshold for 'drifting' (more severe)
SIGNALS_CAP = 6             # max signals returned to pulse_data (Pulse UI scans top 3)
SIGNALS_CAP_FULL = 50       # max signals returned by the dedicated /signals page
# Phase 1.55: deals touched within the last N days don't fire stalled/
# drifting signals. The CEO's explicit acknowledgement counts as activity.
SIGNALS_TOUCH_SUPPRESS_DAYS = 7
# Prefixes in next_action that mark a deal as intentionally paused.
# These deals should NOT generate stalled/drifting signals - the CEO
# already knows and has flagged them. Case-insensitive prefix match.
SIGNALS_PAUSED_PREFIXES = ("DEPRIORITIZED", "DEPRIORITISED", "ON HOLD", "PAUSED",
                          "PARKED", "WAITING", "ABANDONED", "DROPPED")


def _is_paused_deal(next_action: str) -> bool:
    if not next_action:
        return False
    norm = next_action.strip().upper()
    return any(norm.startswith(p) for p in SIGNALS_PAUSED_PREFIXES)


def _days_at_stage(stage_date_iso: str, today: date | None = None) -> int | None:
    if not stage_date_iso:
        return None
    try:
        d = date.fromisoformat(stage_date_iso[:10])
    except ValueError:
        return None
    today = today or date.today()
    delta = (today - d).days
    return delta if delta >= 0 else None


def signals(workspace_root: Path, today: date | None = None, cap: int | None = None,
            data_root: "Path | None" = None) -> list[dict]:
    """Derive operational signals from pipeline data.

    Each signal: {kind, severity, title, context, link}
    - kind: 'pipeline-stalled' | 'pipeline-overdue-action' | 'pipeline-drifting'
    - severity: 'yellow' (worth noting) | 'red' (needs action today)
    - title:    short label e.g. 'ExampleTelco - Negotiation 18 days'
    - context:  one-line explainer e.g. 'next: Send NDA · due 2026-05-10'
    - link:     '#/pipeline' for click-through

    Sorted by severity (red first), then days_at_stage DESC. Capped at
    `cap` (defaults to SIGNALS_CAP for the Pulse-embedded view; the
    dedicated /signals page passes SIGNALS_CAP_FULL).

    Returns [] (never None) so callers don't need to nil-check.

    HEADING OS engine/data split: pipeline data is DATA, resolved under
    ``data_root`` (falls back to ``workspace_root``).
    """
    if data_root is None:
        data_root = get_data_root()
    today = today or date.today()
    try:
        pipe = list_pipeline(data_root, today=today)
    except Exception:
        return []
    out: list[dict] = []
    for deal in pipe.get("deals", []):
        stage = deal.get("stage", "")
        if stage not in SIGNALS_FORWARD_STAGES:
            continue
        next_action = deal.get("next_action", "")
        # Respect CEO-flagged pauses - never signal a deal that's been
        # explicitly deprioritised, parked, or marked on-hold.
        if _is_paused_deal(next_action):
            continue
        # Phase 1.55: recent CEO touch suppresses stalled/drifting signals.
        # Overdue-action signals (due_date breach) are separate and still fire.
        days_since_touched = deal.get("days_since_touched")
        recently_touched = (
            isinstance(days_since_touched, int)
            and days_since_touched <= SIGNALS_TOUCH_SUPPRESS_DAYS
        )
        days_at_stage = _days_at_stage(deal.get("stage_date", ""), today=today)
        company = deal.get("company", "(unknown)")
        # Time-in-stage drift/stall fires only for stages expected to move
        # quickly. Demo/POC is excluded (see SIGNALS_TIME_DRIFT_STAGES) - a
        # long POC is normal, not drift. Demo/POC falls through to the
        # overdue-next-action check below, which IS a real signal.
        if stage in SIGNALS_TIME_DRIFT_STAGES:
            # 1. Drifting: stage > 30 days. Red. Suppressed by a recent touch.
            if days_at_stage is not None and days_at_stage >= SIGNALS_DRIFTING_DAYS and not recently_touched:
                out.append({
                    "kind": "pipeline-drifting",
                    "severity": "red",
                    "title": f"{company} - {stage} {days_at_stage} days",
                    "context": next_action or "no recorded next action",
                    "_sort": (0, -days_at_stage),  # red first, then longest stall first
                    "link": "#/pipeline",
                    "ref": company,  # Phase 1.75: deep-link to specific deal
                })
                continue
            # 2. Stalled: stage 14-29 days. Yellow. Suppressed by a recent touch.
            if days_at_stage is not None and days_at_stage >= SIGNALS_STALLED_DAYS and not recently_touched:
                out.append({
                    "kind": "pipeline-stalled",
                    "severity": "yellow",
                    "title": f"{company} - {stage} {days_at_stage} days",
                    "context": next_action or "no recorded next action",
                    "_sort": (1, -days_at_stage),
                    "link": "#/pipeline",
                    "ref": company,
                })
                continue
        # 3. Overdue next-action: due_date passed for a forward-stage deal.
        if deal.get("is_overdue") and deal.get("days_until_due") is not None:
            days_late = abs(deal["days_until_due"])
            out.append({
                "kind": "pipeline-overdue-action",
                "severity": "red" if days_late >= 7 else "yellow",
                "title": f"{company} - {days_late}d late on next action",
                "context": next_action or "no recorded next action",
                "_sort": (0 if days_late >= 7 else 1, -days_late),
                "link": "#/pipeline",
                "ref": company,
            })
    out.sort(key=lambda s: s.pop("_sort"))
    return out[: (cap if cap is not None else SIGNALS_CAP)]


def _parse_thread_frontmatter(text: str) -> dict:
    """Lightweight YAML frontmatter parser for thread files.

    Captures top-level scalar keys only (id, title, status, last_touched, type).
    Nested keys + list items are ignored - we don't need them here.
    """
    m = _THREAD_FM_RE.match(text)
    if not m:
        return {}
    result: dict = {}
    for line in m.group(1).splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        # Skip continuation/indented lines (nested keys, list items).
        if line.startswith(" ") or line.startswith("\t"):
            continue
        km = _THREAD_KEY_RE.match(line)
        if not km:
            continue
        key = km.group(1).strip()
        val = km.group(2).strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        result[key] = val
    return result


def threads_state_preview(workspace_root: Path, data_root: "Path | None" = None) -> dict | None:
    """Compact threads snapshot for the Pulse footer card.

    Walks THREADS_BUSINESS_DIR (threads/business/) for *.md files, parses
    YAML frontmatter, keeps only threads with status in
    THREADS_ACTIVE_STATUSES. Sorted by last_touched DESC, capped at
    THREADS_PREVIEW_ROW_CAP.

    Returns:
        {
            "threads": [
                {"id": str, "title": str, "last_touched": "YYYY-MM-DD",
                 "days_since": int | None},
                ...
            ],
            "active_total": int,
        }
        or None when the threads directory is missing or empty.

    The companion CEO-only thread subtree is intentionally NOT walked
    even though the daemon runs on the CEO's machine; this keeps the
    bridge sources portable to any future per-exec workspace.

    HEADING OS engine/data split: threads/business/ is DATA, resolved under
    ``data_root`` (falls back to ``workspace_root``).
    """
    if data_root is None:
        data_root = get_data_root()
    biz_dir = data_root / THREADS_BUSINESS_DIR
    if not biz_dir.is_dir():
        return None
    threads: list[dict] = []
    today = date.today()
    for p in biz_dir.glob("*.md"):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _parse_thread_frontmatter(text)
        if not fm:
            continue
        status = (fm.get("status") or "").lower()
        if status not in THREADS_ACTIVE_STATUSES:
            continue
        last_touched_raw = fm.get("last_touched") or fm.get("opened")
        last_touched_date = None
        if last_touched_raw:
            try:
                last_touched_date = date.fromisoformat(last_touched_raw[:10])
            except ValueError:
                last_touched_date = None
        days_since = (today - last_touched_date).days if last_touched_date else None
        threads.append({
            "id": fm.get("id", p.stem),
            "title": fm.get("title") or p.stem,
            "last_touched": last_touched_raw or "",
            "days_since": days_since,
        })
    if not threads:
        return None
    # Sort by days_since ASC (most recently touched first). None entries
    # go to the bottom.
    def key(x):
        d = x["days_since"]
        return (d is None, d if d is not None else 999_999)
    threads.sort(key=key)
    return {
        "threads": threads[:THREADS_PREVIEW_ROW_CAP],
        "active_total": len(threads),
    }


def _today_event_count(workspace_root: Path, data_root: "Path | None" = None) -> int:
    """Total calendar events on today's agenda (local TZ). Returns 0 on any read failure."""
    if data_root is None:
        data_root = get_data_root()
    try:
        from .calendar import today_agenda
        agenda = today_agenda(data_root)
        return len(agenda.get("events") or [])
    except Exception:
        return 0


def _derive_mood(event_count: int) -> str:
    """Mood from today's calendar density.

    Thresholds chosen so a typical CEO day (3-4 meetings) reads 'focused',
    a packed back-to-back day reads 'split', and a clear day reads 'open'.
    """
    if event_count <= 0:
        return "open"
    if event_count <= 3:
        return "focused"
    if event_count <= 6:
        return "split"
    return "packed"


def sea_state(workspace_root: Path, data_root: "Path | None" = None) -> dict:
    """Derive operational state + mood for the topbar pill.

    The 31C operational vocabulary uses sea-state metaphors. The pill
    shows TWO independent signals:
      - state: external pressure (overdue items)
      - mood: cognitive load (today's calendar density)

    State heuristic (low blast radius, easy to recalibrate):
      - rough    when overdue_total >= 10
      - moderate when overdue_total >= 3
      - calm     otherwise

    Mood heuristic (today's calendar local TZ):
      - open    when 0 events
      - focused when 1-3 events
      - split   when 4-6 events
      - packed  when >= 7 events

    Returns:
        {
            "state": "calm" | "moderate" | "rough",
            "mood": "open" | "focused" | "split" | "packed",
            "pipeline_overdue": int,
            "tasks_overdue": int,
            "overdue_total": int,
            "events_today": int,
            "label": str,            # e.g. "Sea calm"
            "mood_label": str,       # e.g. "mood focused"
        }

    HEADING OS engine/data split: pipeline, tasks and calendar are DATA,
    resolved under ``data_root`` (falls back to ``workspace_root``).
    """
    if data_root is None:
        data_root = get_data_root()
    pipe_overdue = 0
    tasks_overdue = 0
    try:
        pipe_overdue = list_pipeline(data_root).get("overdue_count", 0) or 0
    except Exception as e:
        logging.warning("bridge.pulse.sea_state.pipeline: source unavailable, skipping: %s", e)
    try:
        from .tasks import list_active_tasks
        tasks_overdue = list_active_tasks(data_root).get("overdue_count", 0) or 0
    except Exception as e:
        logging.warning("bridge.pulse.sea_state.tasks: source unavailable, skipping: %s", e)
    overdue_total = int(pipe_overdue) + int(tasks_overdue)
    if overdue_total >= 10:
        state = "rough"
    elif overdue_total >= 3:
        state = "moderate"
    else:
        state = "calm"
    events_today = _today_event_count(data_root)
    mood = _derive_mood(events_today)
    return {
        "state": state,
        "mood": mood,
        "pipeline_overdue": int(pipe_overdue),
        "tasks_overdue": int(tasks_overdue),
        "overdue_total": overdue_total,
        "events_today": events_today,
        "label": f"Sea {state}",
        "mood_label": f"mood {mood}",
    }


TODAY_ACTIVITY_ENTRY_CAP = 20  # cap entries per kind to bound payload


def _iter_jsonl(log_path: Path):
    """Yield parsed JSON objects from a .jsonl file. Skips blank/corrupt lines."""
    if not log_path.exists():
        return
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            yield entry


def today_activity(workspace_root: Path, today: date | None = None,
                   data_root: "Path | None" = None) -> dict:
    """Count today's CEO actions across the five mutating workflow logs.

    Aggregates entries dated today from:
      - investors send-log (mark-sent entries; tombstones ignored)
      - pipeline touch-log (mark-touched entries)
      - inbox dismiss-log (mark-dismissed entries; tombstones ignored)
      - approvals sent-log (mark-sent entries; tombstones ignored)
      - tasks done-log (mark-done entries; tombstones ignored)

    Returns counts + per-kind entries (capped at TODAY_ACTIVITY_ENTRY_CAP)
    so the browser can render an expandable recap. Each entry shape:
      {kind, target, ref, ts, note}

    HEADING OS engine/data split: the five workflow logs are DATA, resolved
    under ``data_root`` (falls back to ``workspace_root``).
    """
    if data_root is None:
        data_root = get_data_root()
    today_iso = (today or date.today()).isoformat()
    inv_entries: list[dict] = []
    pipe_entries: list[dict] = []
    inbox_entries: list[dict] = []
    approval_entries: list[dict] = []
    task_entries: list[dict] = []

    # Investors send-log.
    try:
        from .investors import PROGRAM_DIR as _INV_DIR, SEND_LOG_FILE as _SEND_LOG
        log_path = data_root / _INV_DIR / _SEND_LOG
        for entry in _iter_jsonl(log_path):
            if entry.get("undo") is True:
                continue
            if entry.get("date") != today_iso:
                continue
            firm_num = entry.get("firm_num")
            target = f"firm #{firm_num}" if firm_num is not None else "firm"
            inv_entries.append({
                "kind": "investor_sent",
                "target": target,
                "ref": firm_num if firm_num is not None else "",  # for /investors?focus
                "ts": entry.get("ts", ""),
                "note": entry.get("note", ""),
            })
    except Exception as e:
        logging.warning("bridge.pulse.today_activity.investors: source unavailable, skipping: %s", e)

    # Pipeline touch-log.
    try:
        from .pipeline import TOUCH_LOG_FILE as _TOUCH_LOG
        log_path = data_root / _TOUCH_LOG
        for entry in _iter_jsonl(log_path):
            if entry.get("date") != today_iso:
                continue
            company = entry.get("company", "(unknown)")
            pipe_entries.append({
                "kind": "pipeline_touched",
                "target": company,
                "ref": company,  # for /pipeline?focus
                "ts": entry.get("ts", ""),
                "note": entry.get("note", ""),
            })
    except Exception as e:
        logging.warning("bridge.pulse.today_activity.pipeline: source unavailable, skipping: %s", e)

    # Inbox dismiss-log.
    try:
        from .inbox import DISMISS_LOG_FILE as _DISMISS_LOG
        log_path = data_root / _DISMISS_LOG
        for entry in _iter_jsonl(log_path):
            if entry.get("undo") is True:
                continue
            # Phase 1.80: prefer the explicit 'date' field (local CEO day);
            # fall back to ts.startswith for legacy entries written before
            # the date field was added.
            entry_date = entry.get("date")
            if entry_date:
                if entry_date != today_iso:
                    continue
            else:
                ts = entry.get("ts", "")
                if not (isinstance(ts, str) and ts.startswith(today_iso)):
                    continue
            conv_id = entry.get("conv_id", "(unknown)")
            inbox_entries.append({
                "kind": "inbox_dismissed",
                "target": conv_id[:80],
                # No ref: dismissed conversations are filtered out of /inbox,
                # so linking back has nothing to land on. Field present for shape parity.
                "ref": "",
                "ts": entry.get("ts", ""),
                "note": entry.get("note", ""),
            })
    except Exception as e:
        logging.warning("bridge.pulse.today_activity.inbox: source unavailable, skipping: %s", e)

    # Approvals sent-log.
    try:
        from .approvals import SENT_LOG_FILE as _APPROVAL_SENT_LOG
        log_path = data_root / _APPROVAL_SENT_LOG
        for entry in _iter_jsonl(log_path):
            if entry.get("undo") is True:
                continue
            if entry.get("date") != today_iso:
                continue
            path = entry.get("path", "")
            # Show the filename only as target (full path is noisy in the recap).
            target = path.rsplit("/", 1)[-1] if path else "(draft)"
            approval_entries.append({
                "kind": "approval_sent",
                "target": target,
                # No ref: filtered drafts are no longer in the queue, so
                # deep-linking back has nothing to land on. Shape parity only.
                "ref": "",
                "ts": entry.get("ts", ""),
                "note": entry.get("note", ""),
            })
    except Exception as e:
        logging.warning("bridge.pulse.today_activity.approvals: source unavailable, skipping: %s", e)

    # Tasks done-log.
    try:
        from .tasks import DONE_LOG_FILE as _TASKS_DONE_LOG
        log_path = data_root / _TASKS_DONE_LOG
        for entry in _iter_jsonl(log_path):
            if entry.get("undo") is True:
                continue
            if entry.get("date") != today_iso:
                continue
            task_key = entry.get("task_key", "")
            # The key is "captured|priority|desc-prefix" - surface the
            # description prefix as the target line for readability.
            target = task_key.split("|", 2)[-1] if task_key else "(task)"
            task_entries.append({
                "kind": "task_done",
                "target": target[:80],
                # No ref: closed tasks are filtered out of /tasks so there's
                # nothing to deep-link back to. Shape parity only.
                "ref": "",
                "ts": entry.get("ts", ""),
                "note": entry.get("note", ""),
            })
    except Exception as e:
        logging.warning("bridge.pulse.today_activity.tasks: source unavailable, skipping: %s", e)

    # Cap each list (most-recent last in the log, so keep tail).
    inv_entries  = inv_entries[-TODAY_ACTIVITY_ENTRY_CAP:]
    pipe_entries = pipe_entries[-TODAY_ACTIVITY_ENTRY_CAP:]
    inbox_entries = inbox_entries[-TODAY_ACTIVITY_ENTRY_CAP:]
    approval_entries = approval_entries[-TODAY_ACTIVITY_ENTRY_CAP:]
    task_entries = task_entries[-TODAY_ACTIVITY_ENTRY_CAP:]

    return {
        "investors_sent": len(inv_entries),
        "pipeline_touched": len(pipe_entries),
        "inbox_dismissed": len(inbox_entries),
        "approvals_sent": len(approval_entries),
        "tasks_done": len(task_entries),
        "total": (
            len(inv_entries) + len(pipe_entries)
            + len(inbox_entries) + len(approval_entries) + len(task_entries)
        ),
        "entries": {
            "investors_sent": inv_entries,
            "pipeline_touched": pipe_entries,
            "inbox_dismissed": inbox_entries,
            "approvals_sent": approval_entries,
            "tasks_done": task_entries,
        },
    }


SUGGESTIONS_CAP = 5  # max suggestions surfaced on Pulse


def suggestions(workspace_root: Path, today: date | None = None,
                data_root: "Path | None" = None) -> list[dict]:
    """Rule-based 'Suggested for now' list for the Pulse r2 panel.

    Maps the workspace's current state to actionable next steps. No ML;
    each rule is explicit and easy to audit. Returns at most SUGGESTIONS_CAP
    items, ordered by section then severity. Each suggestion:
        {agent, reason, action, link}
    - agent: short skill chip label, e.g. '/follow-up'
    - reason: one-line context grounded in actual data
    - action: short verb-phrase rendered as the row's arrow target text
    - link:   the hash-route the row navigates to on click

    Empty list returned when no rules fire (clean state).

    HEADING OS engine/data split: every source consulted is DATA, resolved
    under ``data_root`` (falls back to ``workspace_root``).
    """
    if data_root is None:
        data_root = get_data_root()
    out: list[dict] = []

    # Stalled / drifting deals from the signals analyser - highest leverage,
    # surface up to two with /follow-up suggestion.
    try:
        sigs = signals(data_root, today=today)
    except Exception:
        sigs = []
    stalled = [s for s in sigs if s.get("kind") in ("pipeline-stalled", "pipeline-drifting")]
    for s in stalled[:2]:
        ref = s.get("ref") or ""
        out.append({
            "agent": "/follow-up",
            "reason": s.get("title") or "stalled deal",
            "action": "draft a check-in",
            "link": f"#/pipeline?focus={ref}" if ref else "#/pipeline",
        })

    # Drafts waiting for approval - high signal that something is queued.
    try:
        from .approvals import list_approvals
        appr = list_approvals(data_root)
        total = appr.get("total", 0)
    except Exception:
        total = 0
    if total > 0:
        out.append({
            "agent": "/email-respond",
            "reason": f"{total} draft{'s' if total != 1 else ''} pending in approvals",
            "action": "review and send",
            "link": "#/approvals",
        })

    # Overdue tasks - the CEO has unfinished commitments.
    try:
        from .tasks import list_active_tasks
        tasks_data = list_active_tasks(data_root, today=today)
        overdue = tasks_data.get("overdue_count", 0)
    except Exception:
        overdue = 0
    if overdue > 0:
        out.append({
            "agent": "/tasks",
            "reason": f"{overdue} overdue task{'s' if overdue != 1 else ''}",
            "action": "clear the queue",
            "link": "#/tasks",
        })

    # Today is Monday in local time - tribe-monday is the standing weekly post.
    today_local = today or date.today()
    if today_local.weekday() == 0:  # Monday
        out.append({
            "agent": "/tribe-monday",
            "reason": "Monday - tribe weekly post due",
            "action": "draft the message",
            "link": "#/studio",
        })

    # Upcoming named meeting today - suggest /meeting-prep.
    try:
        nm = next_meeting(data_root)
        if nm and nm.get("subject") and nm.get("minutes_until", 0) > 30:
            subj = nm["subject"]
            out.append({
                "agent": "/meeting-prep",
                "reason": f"{subj} in {nm['minutes_until']}m",
                "action": "brief + Voss prep",
                "link": "#/day",
            })
    except Exception as e:
        logging.warning("bridge.pulse.suggestions.next_meeting: source unavailable, skipping: %s", e)

    return out[:SUGGESTIONS_CAP]


def pulse_data(workspace_root: Path, odin_5_target: str | None = None,
               data_root: "Path | None" = None) -> dict:
    """Top-level: assemble the /pulse payload from real workspace data.

    HEADING OS engine/data split: every sub-source here reads DATA, so the
    whole assembly runs against ``data_root`` (falls back to
    ``workspace_root`` when not supplied; identical on transitional ceo-main).
    """
    if data_root is None:
        data_root = get_data_root()
    value, deals = pipeline_value_and_deals(data_root)
    # Phase 1.29: pull overdue count + stage rollup from /pipeline source.
    # Silent degradation - if pipeline.md is missing or malformed,
    # list_pipeline returns zero counts.
    pipe = list_pipeline(data_root)
    now_block = current_meeting(data_root)
    raise_block = raise_progress(data_root)
    tribe_block = tribe_state_preview(data_root)
    threads_block = threads_state_preview(data_root)
    sea_block = sea_state(data_root)
    # Phase 1.56: surface the approvals count on Pulse subhead.
    try:
        from .approvals import list_approvals
        approvals_total = list_approvals(data_root).get("total", 0)
    except Exception:
        approvals_total = 0
    # Phase 1.93: surface the 5 most-recent in-flight outputs for the
    # Pulse 'Recent outputs' panel (v8 r2 row).
    # 2026-05-24: also derive kpi.in_flight from the same scan to avoid
    # walking the outputs/ tree twice per refresher tick.
    try:
        from .studio import recent_inflight_items
        inflight_block = recent_inflight_items(data_root)
        recent_outputs = inflight_block.get("items", [])[:5]
        in_flight_total = inflight_block.get("total_count", 0)
    except Exception:
        recent_outputs = []
        in_flight_total = 0
    # Phase 1.94: rule-based 'Suggested for now' panel.
    try:
        suggested = suggestions(data_root)
    except Exception:
        suggested = []
    return {
        "kpi": {
            "days_to_odin_5": days_to_odin_5(odin_5_target),
            "pipeline_value": value,
            "active_deals": deals,
            "pipeline_overdue": pipe["overdue_count"],
            "pipeline_stages": pipe["counts"],
            "in_flight": in_flight_total,
            "next_meeting": next_meeting(data_root),
            "raise_progress": raise_block,
            "tribe_state": tribe_block,
            "threads_state": threads_block,
            "sea_state": sea_block,
            "approvals_total": approvals_total,
            "today_activity": today_activity(data_root),
        },
        "now": now_block or {"focus": None, "until": None, "minutes_remaining": None},
        "next": next_items(data_root),
        "watch": watch_items(data_root),
        "signals": signals(data_root),
        "recent_outputs": recent_outputs,
        "suggested": suggested,
    }
