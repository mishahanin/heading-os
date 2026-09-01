"""Real-data source for the /day endpoint.

Reads outputs/_sync/calendar/YYYY-MM-DD.md (local TZ per workspace
convention) and returns the full today's agenda as a sorted list of
events with time, subject, location (zoom link if present).

The existing sources/pulse.py uses a similar regex to extract only the
NEXT upcoming event. This module returns ALL events for today plus
a 'next_index' marker so the browser can highlight the next one.
"""
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from scripts.utils.paths import get_data_root
from scripts.utils.workspace import get_default_tz

logger = logging.getLogger(__name__)


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
            "data_time": ISO 8601 UTC of the file mtime, else None,
        }

    `data_time` is None when the calendar file is absent AND when it is present
    but unreadable, which the older "None if file absent" did not cover. The
    two are told apart in the daemon log, not in the return: an unreadable file
    is NAMED at warning level, an absent one is silent.

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
    # `UnicodeDecodeError` is a `ValueError`, not an `OSError`, and the decode
    # happens INSIDE `read_text` -- so the handler that exists to turn an
    # unreadable calendar into an empty agenda could not see the one failure
    # mode a sync-written markdown file actually produces. MEASURED 2026-09-01
    # with one 0xe9 byte in a subject cell: `UnicodeDecodeError: invalid
    # continuation byte` raised out of /day rather than returning the empty
    # agenda the Returns block above promises.
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("calendar file %s is unreadable (%s); the agenda for %s "
                       "is reported EMPTY, which is not the same as no events",
                       cal, exc, date_str)
        return {"date": date_str, "events": [], "data_time": None}

    events: list[dict] = []
    for line in text.splitlines():
        m = _CAL_ROW_RE.match(line)
        if not m:
            continue
        # A well-formed row has exactly 4 column separators + 1 trailing = 5
        # pipes. More pipes mean a pipe inside a cell corrupted the column
        # boundaries. Skipping is right; skipping SILENTLY was not. MEASURED
        # 2026-08-31 against the four-column form this module's own header
        # comment documents (Time, Subject, Location, Duration): a 09:00 row
        # reading "Board review \| Q3 close" with a zoom link and a 60m
        # duration reached six pipes, 3 rows in produced 2 events out, and no
        # log line was emitted at any level. A meeting that leaves the CEO's
        # day silently is worse than one shown with a mangled subject, because
        # nothing on the page says a row existed.
        if line.count("|") > 5:
            # The remedy is "reword", not "escape". MEASURED 2026-08-31: a
            # markdown-escaped `\|` is counted by `line.count("|")` exactly
            # like a raw one, so the escaped row is dropped with this same
            # message, and telling the operator to escape it sends them to do
            # a thing that does not work. Honouring `\|` here would be worse
            # than dropping: `_CAL_ROW_RE`'s cells are `[^|]`, so a five-pipe
            # escaped row already parses with the columns shifted (subject
            # "Board review \", location "Q3 close", the zoom link lost).
            logger.warning(
                "agenda: dropping a calendar row with %d pipes (max 5) from "
                "today's events; a pipe inside a cell shifts the columns. "
                "Reword the cell to remove it (a markdown-escaped \\| is "
                "dropped too). Row: %s",
                line.count("|"), line.strip())
            continue
        # No try/except around the int(): the regex already matched
        # `\d{2}:\d{2}`, so the parse cannot raise. The handler advertised a
        # robustness this parse does not need, while the range check below is
        # the one that actually rejects "99:99".
        time_str = m.group("time")
        hh, mm = (int(x) for x in time_str.split(":"))
        if not (0 <= hh < 24 and 0 <= mm < 60):
            logger.warning("agenda: dropping a calendar row whose time %r is "
                           "not a real clock time. Row: %s",
                           time_str, line.strip())
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
