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
from scripts.utils.paths import get_data_root
from scripts.utils.workspace import get_default_tz


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


def today_agenda(data_root: "Path | None" = None,
                 now: datetime | None = None) -> dict:
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
    under ``data_root``, which falls back to the ``get_data_root()`` seam when
    not supplied. The dead leading ``workspace_root`` went on 2026-08-24.
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
        # No try/except around the int(): the regex already matched
        # `\d{2}:\d{2}`, so the parse cannot raise. The handler advertised a
        # robustness this parse does not need, while the range check below is
        # the one that actually rejects "99:99".
        time_str = m.group("time")
        hh, mm = (int(x) for x in time_str.split(":"))
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
        # Truncate toward zero, not floor. `//` floors toward MINUS infinity, so
        # an event that began one second ago has total_seconds() in (-60, 0),
        # `// 60` gives -1, and the row rendered "1 minute ago" for the whole
        # first minute of every meeting. `is_past` is computed by comparison
        # below and was always right; only the magnitude lied.
        e["minutes_until"] = int((event_dt - now_local).total_seconds() / 60)
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
