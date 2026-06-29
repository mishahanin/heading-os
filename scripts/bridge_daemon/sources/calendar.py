"""Real-data source for the /day endpoint.

Reads outputs/_sync/calendar/YYYY-MM-DD.md (local TZ per workspace
convention) and returns the full today's agenda as a sorted list of
events with time, subject, location (zoom link if present).

The existing sources/pulse.py uses a similar regex to extract only the
NEXT upcoming event. This module returns ALL events for today plus
a 'next_index' marker so the browser can highlight the next one.
"""
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from scripts.utils.paths import get_data_root
from scripts.utils.workspace import get_default_tz, get_default_tz_name


# Calendar table row format:
# | 10:45 | Morning Sync | https://us02web.zoom.us/j/3131313013 | 15m |
# The location column may contain a zoom URL, a dash, or other text.
# We capture time, subject, location (best effort).
_CAL_ROW_RE = re.compile(
    r"^\|\s*(?P<time>\d{2}:\d{2})\s*\|\s*(?P<subject>[^|]+?)\s*\|\s*(?P<location>[^|]*?)\s*\|"
)


def _clean_location(loc: str) -> str:
    """Trim, treat '-' as empty."""
    loc = loc.strip()
    return "" if loc in ("-", "—") else loc


def today_agenda(workspace_root: Path, now: datetime | None = None,
                 data_root: "Path | None" = None) -> dict:
    """Return today's calendar in local time.

    Returns:
        {
            "date": "YYYY-MM-DD" (local),
            "events": [
                {"time": "HH:MM", "subject": str, "location": str, "is_next": bool, "is_past": bool},
                ...
            ],
            "data_time": ISO 8601 UTC of the file mtime (None if file absent),
        }

    HEADING OS engine/data split: the calendar file is DATA, so it resolves
    under ``data_root``. Back-compat: when ``data_root`` is not supplied it
    falls back to ``workspace_root`` (identical on transitional ceo-main).
    """
    if data_root is None:
        data_root = get_data_root()
    if now is None:
        now = datetime.now(timezone.utc)
    now_local = now.astimezone(get_default_tz())
    date_str = now_local.strftime("%Y-%m-%d")
    cal = data_root / "outputs" / "_sync" / "calendar" / f"{date_str}.md"
    if not cal.exists():
        return {"date": date_str, "events": [], "data_time": None}
    try:
        text = cal.read_text(encoding="utf-8")
        mtime = cal.stat().st_mtime
    except OSError:
        return {"date": date_str, "events": [], "data_time": None}

    events: list[dict] = []
    for line in text.splitlines():
        m = _CAL_ROW_RE.match(line)
        if not m:
            continue
        # Defensive: a well-formed row has exactly 4 column separators + 1
        # trailing = 5 pipes. More pipes mean an unescaped pipe in a cell
        # corrupted the column boundaries. Skip rather than emit garbage.
        if line.count("|") > 5:
            continue
        time_str = m.group("time")
        try:
            hh, mm = (int(x) for x in time_str.split(":"))
        except ValueError:
            continue
        if not (0 <= hh < 24 and 0 <= mm < 60):
            continue
        events.append({
            "time": time_str,
            "subject": m.group("subject").strip(),
            "location": _clean_location(m.group("location")),
            "is_next": False,  # populated below
            "is_past": False,
            "minutes_until": 0,    # populated below
            "minutes_to_next": None,  # gap to next event in the day, or None
        })

    # Sort by time string (HH:MM lexicographic sort == chronological within a day).
    events.sort(key=lambda e: e["time"])

    # Mark is_past + is_next, compute minutes_until + gap-to-next.
    next_marked = False
    for idx, e in enumerate(events):
        hh, mm = (int(x) for x in e["time"].split(":"))
        event_dt = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        e["minutes_until"] = int((event_dt - now_local).total_seconds() // 60)
        if event_dt < now_local:
            e["is_past"] = True
        elif not next_marked:
            e["is_next"] = True
            next_marked = True
        if idx + 1 < len(events):
            next_hh, next_mm = (int(x) for x in events[idx + 1]["time"].split(":"))
            next_dt = now_local.replace(hour=next_hh, minute=next_mm, second=0, microsecond=0)
            e["minutes_to_next"] = int((next_dt - event_dt).total_seconds() // 60)

    return {
        "date": date_str,
        "events": events,
        "data_time": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
    }
