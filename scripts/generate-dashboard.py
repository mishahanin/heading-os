#!/usr/bin/env python3
"""
31C CEO Morning Dashboard Generator

Aggregates CRM health, pipeline, calendar, email, strategy, metrics, and
data-freshness into a single-page HTML dashboard. Self-contained (inline CSS,
base64 logo, no external dependencies beyond Google Fonts).

Usage:
    python scripts/generate-dashboard.py                     # HTML only
    python scripts/generate-dashboard.py --pdf               # HTML + PDF
    python scripts/generate-dashboard.py --output-dir DIR    # custom output dir

Tests: tests/test_a_morning_calendar_shifted_by_its_own_timezone.py, tests/test_a_table_that_lost_a_deal_and_a_revert_that_froze_the_source.py
"""

import argparse
import base64
import html
import json
import re
import subprocess
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.html_templates import render_template
from scripts.utils.image import load_logo_base64
from scripts.utils.crm import parse_config as _crm_parse_config, scan_contacts as _crm_scan_contacts
from scripts.utils.workspace import (
    get_default_tz,
    get_crm_config_path as _get_crm_config_path,
    get_outputs_dir,
    get_knowledge_dir,
    get_datastore_dir,
    get_context_dir,
    get_people_file,
)
from scripts.utils.markdown import parse_frontmatter as _parse_fm
from scripts.utils.markdown import parse_md_table

# ============================================================
# Paths
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent

PIPELINE_FILE = get_context_dir() / "pipeline.md"
STRATEGY_FILE = get_context_dir() / "strategy.md"
METRICS_FILE = get_context_dir() / "current-data.md"
CALENDAR_FILE = get_outputs_dir() / "_sync" / "calendar" / "upcoming.md"
EMAIL_FILE = get_outputs_dir() / "_sync" / "emails" / "inbox-latest.md"
HTML_TO_PDF_SCRIPT = SCRIPT_DIR / "html-to-pdf.py"
CONTEXT_DIR = get_context_dir()
HIRING_FILE = get_context_dir() / "hiring-pipeline.md"
VIRAID_TASKS_FILE = get_outputs_dir() / "operations" / "viraid" / "tasks.md"
VIRAID_STATE_FILE = get_outputs_dir() / "operations" / "viraid" / "state.json"
NEWSLETTERS_DIR = get_outputs_dir() / "intel" / "newsletters"
LINKEDIN_DIR = get_outputs_dir() / "content" / "linkedin"
LINKEDIN_DRAFTS_DIR = get_outputs_dir() / "content" / "linkedin-drafts"
# R10 capture-payoff: Odin brain + zk captures, and the ceo-only cadence script.
KNOWLEDGE_DIR = get_knowledge_dir()
ODIN_BRAIN_DIR = get_knowledge_dir() / "odin-brain"
ODIN_CADENCE_SCRIPT = SCRIPT_DIR / "odin-cadence.py"

# Canonical brand assets (per reference/corporate-style-guide.md)
BRAND_DIR = get_datastore_dir() / "brand"
LOGO_BLUE_PATH = BRAND_DIR / "assets" / "logos" / "31C_Logo_Palantinate_Blue_Color.png"
LOGO_WHITE_PATH = BRAND_DIR / "assets" / "logos" / "31C_Logo_White_Color.png"
GT_LIGHT_FONT = BRAND_DIR / "fonts" / "GT Standard" / "GT-Standard-L-Standard-Light.woff2"
GT_MEDIUM_FONT = BRAND_DIR / "fonts" / "GT Standard" / "GT-Standard-L-Standard-Medium.woff2"


def load_font_b64(path):
    """Read a WOFF2 font file and return a base64-encoded data URI string.
    Returns empty string if missing - caller falls back to system fonts."""
    if not path.exists():
        return ""
    return base64.b64encode(path.read_bytes()).decode("ascii")

TODAY = datetime.now(get_default_tz()).date()
NOW = datetime.now(get_default_tz())

# Calendar times from Exchange are written to upcoming.md ALREADY LOCAL, by
# sync-exchange's `_event_time_str`. This comment used to assert they were
# stored in UTC, and `collect_calendar` converted them on that basis; see the
# note there for what that cost. `CALENDAR_UTC_OFFSET_HOURS = 4` lived here
# first -- a constant offset beside a comment claiming it equalled the
# configured zone -- and both it and the tz-aware conversion that replaced it
# were corrections to a problem that never existed.


def _zone_suffix():
    """" (ZONE)" for the page furniture, or "" when the zone has no name.

    The two headers below printed the literal words "the configured timezone"
    on a page marked "Internal - CEO Eyes Only". `NOW` is built with
    `get_default_tz()`, so the abbreviation was available the whole time.
    """
    zone = NOW.tzname()
    return f" ({esc(zone)})" if zone else ""


# ============================================================
# Utilities
# ============================================================
def esc(text):
    if not text:
        return ""
    return html.escape(str(text))


def read_file(path):
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


# parse_md_table used to live here, and an identical copy lived in
# generate-crm-dashboard.py. Both carried the same two defects: an empty cell
# was deleted instead of held in place, and a row that came out shorter than
# the header was DROPPED without a word. One empty Notes cell removed a whole
# deal from the count, the total, the weighted total and the top three. The
# shared implementation in scripts/utils/markdown.py pads and warns instead.


# ============================================================
# Data Collectors
# ============================================================
HEALTH_BUCKETS = ("red", "yellow", "green", "gray")


def health_bucket(contact):
    """The card a contact belongs on. Never raises, never silently vanishes.

    `result[c["health"]]` was a bare lookup, so one contact whose health
    scanned as "RED" or "unknown" raised KeyError, which the broad
    `except Exception` in collect_crm_health caught -- leaving the buckets
    HALF filled and commitments_due EMPTY. The Urgent Items panel then read
    "All Clear" while overdue commitments existed.
    """
    health = str(contact.get("health") or "gray").strip().lower()
    if health not in HEALTH_BUCKETS:
        print(f"  Warning: contact {contact.get('name', '?')!r} has health "
              f"{contact.get('health')!r}; counted as gray", file=sys.stderr)
        return "gray"
    return health



def collect_crm_health():
    """Scan CRM contacts via in-process import (no subprocess overhead).

    Replicates the JSON envelope crm-health.py --json produced, then bins
    contacts into red/yellow/green/gray and surfaces commitments due in the
    next 7 days. Behaviour-preserving refactor: previously this shelled out
    to crm-health.py and re-parsed its JSON output.
    """
    result = {"contacts": [], "red": [], "yellow": [], "green": [], "gray": [],
              "commitments_due": [], "total": 0, "failed": ""}
    try:
        config = _crm_parse_config(_get_crm_config_path())
        raw_contacts, _tribe_warnings, _dangling_refs, _stages, _aliases = _crm_scan_contacts(config, today=TODAY)

        # Normalise to the previous JSON-derived shape: due dates as ISO strings.
        #
        # `.get`, and one try per CONTACT. Every field here was a bare index, so
        # a single contact dict missing a single key raised before ANY bucket was
        # filled; the broad `except` below then returned the empty skeleton and
        # the Urgent Items panel showed "All Clear". `health_bucket` was hardened
        # against exactly that failure shape and the normaliser above it was not,
        # so the same outcome stayed reachable through a different trigger. A bad
        # contact is now dropped alone, and named.
        contacts = []
        for c in raw_contacts:
            try:
                commits = []
                for cm in c.get("commitments", []):
                    due_iso = cm["due"].strftime("%Y-%m-%d") if cm.get("due") else None
                    commits.append({"text": cm["text"], "due": due_iso})
                contacts.append({
                    "name": c.get("name", "(unnamed)"),
                    "company": c.get("company", ""),
                    "type": c.get("type", ""),
                    "last_touch": c.get("last_touch"),
                    "cadence": c.get("cadence"),
                    "health": c.get("health"),
                    "days_since": c.get("days_since"),
                    "commitments": commits,
                    "file": c.get("file", ""),
                })
            except (KeyError, AttributeError, TypeError, ValueError) as e:
                print(f"[generate-dashboard] skipping malformed contact "
                      f"{c.get('name', '?')!r}: {e}", file=sys.stderr)

        result["contacts"] = contacts
        result["total"] = len(contacts)
        for c in contacts:
            # `result[health]` was a bare lookup, so one contact whose health
            # scanned as "RED" or "unknown" raised KeyError, which the broad
            # `except Exception` below caught -- leaving the buckets HALF
            # filled and commitments_due EMPTY. The Urgent Items panel then
            # showed "All Clear" while overdue commitments existed. An
            # unrecognised value is grey now, and says so once.
            result[health_bucket(c)].append(c)
            for commit in c.get("commitments", []):
                due = commit.get("due")
                if due:
                    try:
                        due_date = date.fromisoformat(due)
                        if due_date <= TODAY + timedelta(days=7):
                            result["commitments_due"].append({
                                "name": c["name"],
                                "company": c.get("company", ""),
                                "text": commit["text"],
                                "due": due,
                                "overdue": due_date < TODAY,
                            })
                    except ValueError:
                        pass
    except Exception as e:
        print(f"Warning: CRM health collection failed: {e}", file=sys.stderr)
        # Recorded, not just printed. stderr scrolls past; the PAGE is what the
        # CEO reads, and until this flag existed the page could not tell a
        # failed scan from a quiet morning. `build_urgent` reads it.
        result["failed"] = f"{type(e).__name__}: {e}"
    return result


def collect_pipeline():
    """Parse pipeline.md for deals, investors, partnerships, won."""
    content = read_file(PIPELINE_FILE)
    result = {
        "deals": [], "investors": [], "partnerships": [], "won": [],
        "stages": {}, "total_deals": 0, "total_investors": 0,
        "total_partnerships": 0, "total_won": 0,
        "total_value": 0, "weighted_value": 0, "stale_count": 0,
        "top_deals": [],
    }
    if not content:
        return result

    # Stage probability mapping (canonical stages)
    stage_prob = {
        "Lead": 0.05, "Qualified": 0.15, "Demo/POC": 0.30,
        "Proposal": 0.50, "Negotiation": 0.75, "Won": 1.0,
    }

    # The Pipeline Summary table used to be parsed into a `summary` dict here
    # under a comment calling it a "fallback". Nothing read it. The totals
    # below are computed from the Active Deals rows, so either the fallback was
    # never written or it was removed and its parse left behind; parsing a
    # table to throw it away is not a fallback either way.

    # Active Deals table
    deals = parse_md_table(content, r"##\s*Active Deals", source=str(PIPELINE_FILE))
    result["deals"] = deals
    result["total_deals"] = len(deals)

    total_value = 0
    weighted_value = 0
    stale_count = 0
    deal_weighted = []

    for d in deals:
        stage = d.get("Stage", "Unknown").strip()
        result["stages"][stage] = result["stages"].get(stage, 0) + 1

        val = parse_money(d.get("Est. Value", ""),
                          where=f"{PIPELINE_FILE} deal {d.get('Company', '?')!r}")
        total_value += val

        prob = stage_prob.get(stage, 0.05)
        w_val = val * prob
        weighted_value += w_val
        deal_weighted.append((d, w_val))

        # Stale detection: stage date > 14 days old
        stage_date_str = d.get("Stage Date", "").strip()
        if stage_date_str:
            try:
                sd = date.fromisoformat(stage_date_str)
                if (TODAY - sd).days > 14:
                    stale_count += 1
            except ValueError:
                pass

    result["total_value"] = total_value
    result["weighted_value"] = weighted_value
    result["stale_count"] = stale_count

    # Top 3 deals by weighted value
    deal_weighted.sort(key=lambda x: -x[1])
    result["top_deals"] = [(d, w) for d, w in deal_weighted[:3]]

    # Investor Conversations
    investors = parse_md_table(content, r"##\s*Investor Conversations", source=str(PIPELINE_FILE))
    result["investors"] = investors
    result["total_investors"] = len(investors)

    # Partnership Discussions
    partnerships = parse_md_table(content, r"##\s*Partnership Discussions", source=str(PIPELINE_FILE))
    result["partnerships"] = partnerships
    result["total_partnerships"] = len(partnerships)

    # Won / Closed
    won = parse_md_table(content, r"##\s*Won\s*/\s*Closed", source=str(PIPELINE_FILE))
    result["won"] = won
    result["total_won"] = len(won)

    return result


_MONEY_RE = re.compile(r"^~?\$?\s*([0-9]+(?:\.[0-9]+)?)\s*([kmb])?$", re.IGNORECASE)
_MONEY_SCALE = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def parse_money(raw, where="pipeline"):
    """A deal value in whole units. Unparseable is 0 AND a warning.

    `int(val_str)` after stripping `$` and `,` was the whole parser, so
    `$1.5M`, `2.5m`, `~500000` and `500k` all became 0 in silence: the total
    and weighted pipeline understated by the size of the deal, and the
    Top 3 ranking put a real deal below a parsed-to-zero one. A value that
    cannot be read is still 0, because inventing a number is worse, but it
    no longer happens without saying so.
    """
    text = str(raw or "").replace(",", "").strip()
    if not text:
        return 0
    m = _MONEY_RE.match(text)
    if not m:
        print(f"  Warning: cannot read value {raw!r} in {where}; counted as 0",
              file=sys.stderr)
        return 0
    amount = float(m.group(1)) * _MONEY_SCALE.get((m.group(2) or "").lower(), 1)
    return int(amount)


def _as_int_or_count(value):
    """A count from a field whose shape the producer never promised.

    `reflect_clusters` arrives verbatim from `odin-cadence.py --json`. The
    dashboard compared it with `> 0` on two lines, so a list of clusters --
    a perfectly plausible shape for a field with that name -- raised
    TypeError and took the whole dashboard down. A list is counted, a number
    is used, anything else is None (rendered as "-").
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return None


def _parse_clock(raw_time):
    """A wall clock from a Time cell, or None when there isn't one.

    `time.fromisoformat` wants `HH:MM` zero-padded, and the guard in front of
    it admitted `\\d{1,2}:\\d{2}`. So "9:30" passed the regex, failed the parse,
    and hit a bare `continue` -- the meeting left the CEO's day with nothing
    said. "9:30 AM" did the same, because slicing five characters off it gives
    "9:30 ". Both shapes are accepted here.
    """
    if not raw_time:
        return None
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*([APap][Mm])?", raw_time)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    meridiem = (m.group(3) or "").lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def _as_percent(value):
    """A 0-100 completion percentage from an unvalidated producer field.

    `completion_rate` arrives from the viraid `state.json`, where only
    JSONDecodeError and OSError were ever caught -- the VALUE's type was never
    looked at, and it reached an f-string as `{rate:.0f}%` in the build phase.
    Two ways that went wrong, and the second is worse than the first:

    * a string ("75") raised at format time and took the whole dashboard down
      after every collector had already succeeded;
    * a fraction (0.87) rendered as "1%" -- a wrong number, on the CEO's
      dashboard, with nothing anywhere saying it was wrong.

    A value in 0..1 is read as a fraction and scaled. That is a JUDGEMENT, not
    a fact the producer states, so it is made once, here, where it can be seen
    and argued with, rather than implied by a format string. 1.0 is ambiguous
    between "1%" and "100%" and is read as 100%, because a completion rate of
    exactly 1% is the less likely of the two by a wide margin.
    """
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, str):
        try:
            value = float(value.strip().rstrip("%"))
        except ValueError:
            print(f"[generate-dashboard] viraid completion_rate {value!r} is not "
                  f"a number; showing 0%", file=sys.stderr)
            return 0.0
    if not isinstance(value, (int, float)):
        print(f"[generate-dashboard] viraid completion_rate has type "
              f"{type(value).__name__}; showing 0%", file=sys.stderr)
        return 0.0
    if 0 < value <= 1:
        value *= 100
    return float(max(0.0, min(100.0, value)))


def collect_calendar():
    """Parse upcoming.md for today's meetings."""
    content = read_file(CALENDAR_FILE)
    result = {"meetings": [], "sync_time": "", "date_str": TODAY.strftime("%Y-%m-%d")}

    if not content:
        return result

    # Extract sync time
    sync_match = re.search(r"Synced:\s*(.+)", content)
    if sync_match:
        result["sync_time"] = sync_match.group(1).strip()

    # NO conversion. `upcoming.md` is already local, in both of its fields.
    #
    # `sync-exchange.sync_calendar` groups events by `_to_local(...).date()` and
    # writes each Time cell through `_event_time_str`, which is
    # `event.start.astimezone(local_tz).strftime("%H:%M")`. It has done both
    # since the engine's initial import; the file has never held UTC.
    #
    # This function believed otherwise from that same initial import. It began
    # by adding a constant `CALENDAR_UTC_OFFSET_HOURS` under the comment
    # "Convert meeting times from UTC to the configured local timezone" --
    # converting data that was already converted. On 2026-08-23 an earlier night
    # of this audit replaced the constant with a tz-aware `astimezone` and added
    # a filter on the converted date. That removed the hardcoding and kept the
    # false premise, which made the symptom worse rather than better: on
    # Asia/Dubai a 09:00 meeting rendered as 13:00 (as it always had), and a
    # 21:00 meeting now became 01:00 tomorrow and DISAPPEARED from the day
    # entirely. Measured, both of them, on the fixture in
    # tests/test_a_morning_calendar_shifted_by_its_own_timezone.py.
    #
    # The section header is a local date and the clock is a local clock, so
    # today's meetings are the rows under today's header, shown as written.
    meetings = []
    for sec in re.finditer(r"##\s*(\d{4}-\d{2}-\d{2})", content):
        try:
            section_date = date.fromisoformat(sec.group(1))
        except ValueError:
            continue
        if section_date != TODAY:
            continue
        rest = content[sec.start():]
        nxt = re.search(r"\n##\s*\d{4}-\d{2}-\d{2}", rest[3:])
        section = rest[:nxt.start() + 3] if nxt else rest
        for m in parse_md_table(section, source=str(CALENDAR_FILE)):
            raw_time = m.get("Time", "").strip()
            clock = _parse_clock(raw_time)
            if clock is None:
                # Kept and flagged, never dropped in silence. This is the one
                # panel where an absent row is the worst outcome: a meeting the
                # CEO does not know about costs more than a row that reads
                # oddly. The unparsed text stays in the cell so it is visible.
                if raw_time:
                    print(f"[generate-dashboard] calendar row kept with an "
                          f"unparsed Time {raw_time!r}", file=sys.stderr)
                    meetings.append(m)
                continue
            m["Time"] = clock.strftime("%H:%M")
            meetings.append(m)

    meetings.sort(key=lambda m: m.get("Time", ""))
    result["meetings"] = meetings
    return result


def collect_emails():
    """Parse inbox-latest.md for email summary."""
    content = read_file(EMAIL_FILE)
    result = {"emails": [], "sync_time": "", "count": 0}

    if not content:
        return result

    sync_match = re.search(r"Synced:\s*(.+)", content)
    if sync_match:
        result["sync_time"] = sync_match.group(1).strip()

    count_match = re.search(r"Count:\s*(\d+)", content)
    if count_match:
        result["count"] = int(count_match.group(1))

    emails = parse_md_table(content, source=str(EMAIL_FILE))
    result["emails"] = emails
    return result


def collect_strategy():
    """Extract key strategic context."""
    content = read_file(STRATEGY_FILE)
    result = {"priorities": [], "heading": "", "year": "", "phase": ""}

    if not content:
        return result

    # Current year heading
    y1_match = re.search(r"Year 1.*?:\s*(.+)", content)
    if y1_match:
        result["year"] = "Year 1: " + y1_match.group(1).strip()

    # Extract Q1 priorities
    priorities = []
    in_priorities = False
    priority_num = 0
    for line in content.split("\n"):
        if re.search(r"Current Strategic Priorities|Q1 2026", line, re.IGNORECASE):
            in_priorities = True
            continue
        if in_priorities:
            m = re.match(r"\d+\.\s+\*\*(.+?)\*\*", line.strip())
            if m:
                priorities.append(m.group(1))
                priority_num += 1
                if priority_num >= 5:
                    break
            if line.strip().startswith("##") and priority_num > 0:
                break
    result["priorities"] = priorities

    # Go-to-market phase
    phase_match = re.search(r"Phase 1.*?Now", content)
    if phase_match:
        result["phase"] = "Phase 1: Home Region (Active)"

    result["heading"] = "Post-Launch Commercial Activation"
    return result


def collect_metrics():
    """Extract key business metrics from current-data.md."""
    content = read_file(METRICS_FILE)
    result = {
        "headcount": "50+", "countries": "14", "hiring_target": "200",
        "modules_live": "4/4", "processing": "1.2 Tbps",
        "dpi_tam_2024": "$25.21B", "dpi_tam_2030": "$78.04B", "cagr": "22.05%",
        "mea_2024": "$1.01B", "mea_2030": "$3.47B",
        "cis_2024": "$420M", "cis_2030": "$1.15B",
        "predecessor_vacuum_countries": "56",
        "fundraising_raised": "$6M", "next_round": "$20M",
    }

    if not content:
        return result

    # Try to extract specific numbers (fallback to defaults above)
    hc_match = re.search(r"Headcount.*?(\d+\+?)", content)
    if hc_match:
        result["headcount"] = hc_match.group(1)

    return result


def collect_freshness():
    """Check freshness markers on context files."""
    files_to_check = [
        ("pipeline.md", CONTEXT_DIR / "pipeline.md"),
        ("current-data.md", CONTEXT_DIR / "current-data.md"),
        ("strategy.md", CONTEXT_DIR / "strategy.md"),
        # get_people_file() is the seam; a second literal here checked a
        # different file whenever the two disagreed, and PEOPLE_FILE (which
        # held the seam value) was assigned at import and read nowhere.
        ("people.md", get_people_file()),
    ]
    result = []
    for name, path in files_to_check:
        if not path.exists():
            result.append({"name": name, "date": None, "age": None, "health": "red"})
            continue
        content = path.read_text(encoding="utf-8")
        match = re.search(r"Last verified:\s*(\d{4}-\d{2}-\d{2})", content)
        if match:
            date_str = match.group(1)
            try:
                verified = date.fromisoformat(date_str)
            except ValueError:
                # The regex matches the SHAPE of a date, not a real one, so
                # `Last verified: 2026-02-30` used to raise here and kill the
                # whole run: no dashboard at all, from one bad line in one of
                # four files. One unreadable marker degrades one row instead.
                print(f"  Warning: {name} has an impossible "
                      f"'Last verified: {date_str}'", file=sys.stderr)
                result.append({"name": name, "date": date_str, "age": None,
                               "health": "gray"})
                continue
            age = (TODAY - verified).days
            health = "green" if age <= 7 else ("yellow" if age <= 14 else "red")
            result.append({"name": name, "date": date_str, "age": age, "health": health})
        else:
            result.append({"name": name, "date": None, "age": None, "health": "gray"})
    return result


def collect_hiring():
    """Parse hiring-pipeline.md for open roles and urgency."""
    content = read_file(HIRING_FILE)
    result = {"p1": [], "p2": [], "p3": [], "urgent": [], "total": 0}

    if not content:
        return result

    # A `current_priority` state machine used to run over every line here,
    # assigning p1/p2/p3 and never reading the result. The three
    # parse_md_table calls below are what actually reads the sections.

    # Parse tables for each priority section
    p1 = parse_md_table(content, r"###\s*P1", source=str(HIRING_FILE))
    p2 = parse_md_table(content, r"###\s*P2", source=str(HIRING_FILE))
    p3 = parse_md_table(content, r"###\s*P3", source=str(HIRING_FILE))

    result["p1"] = p1
    result["p2"] = p2
    result["p3"] = p3
    result["total"] = len(p1) + len(p2) + len(p3)

    # Find URGENT roles
    for role in p1 + p2 + p3:
        status = role.get("Status", "") + " " + role.get("Notes", "")
        if "URGENT" in status.upper():
            result["urgent"].append(role)

    return result


def collect_content_cadence():
    """Check recent content output for newsletter and LinkedIn."""
    result = {
        "newsletter_days": None, "newsletter_status": "NO DATA",
        "newsletter_last": None,
        "linkedin_count_week": 0, "linkedin_status": "NO DATA",
    }

    # Newsletter: check most recent dated directory in outputs/intel/newsletters/
    if NEWSLETTERS_DIR.exists():
        dated_dirs = []
        for d in NEWSLETTERS_DIR.iterdir():
            if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}", d.name):
                try:
                    dt = date.fromisoformat(d.name)
                    dated_dirs.append(dt)
                except ValueError:
                    pass
        if dated_dirs:
            latest = max(dated_dirs)
            days_since = (TODAY - latest).days
            result["newsletter_days"] = days_since
            result["newsletter_last"] = latest.strftime("%Y-%m-%d")
            result["newsletter_status"] = "ON TRACK" if days_since <= 7 else "BEHIND"

    # LinkedIn: count posts in the last 7 days
    week_ago = TODAY - timedelta(days=7)
    linkedin_count = 0
    for ldir in [LINKEDIN_DIR, LINKEDIN_DRAFTS_DIR]:
        if ldir.exists():
            for f in ldir.iterdir():
                if f.is_file():
                    # Check by file modification date
                    mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=get_default_tz()).date()
                    if mtime >= week_ago:
                        linkedin_count += 1
    result["linkedin_count_week"] = linkedin_count
    result["linkedin_status"] = "ON TRACK" if linkedin_count >= 2 else "BEHIND"

    return result


def collect_viraid():
    """Parse Viraid tasks and state for summary."""
    result = {
        "active_total": 0, "p1": 0, "p2": 0, "p3": 0,
        "aging": 0, "completion_rate": 0.0,
        "tasks": [],
    }

    # Parse tasks.md for active items
    tasks_content = read_file(VIRAID_TASKS_FILE)
    if tasks_content:
        in_active = False
        for line in tasks_content.split("\n"):
            if re.match(r"##\s*Active", line, re.IGNORECASE):
                in_active = True
                continue
            # ANY other level-2 heading ends the Active section. Only
            # "## Completed" used to, so a "## Backlog" or "## On Hold"
            # between the two left in_active True and its unchecked items
            # were counted as active work, inflating the P1-P3 counts and
            # the aging counts with tasks nobody had started.
            if re.match(r"##(?!#)", line.strip()):
                in_active = False
                continue
            if in_active and line.strip().startswith("- [ ]"):
                result["active_total"] += 1
                # Extract priority
                p_match = re.search(r"`(P[123])`", line)
                if p_match:
                    p = p_match.group(1).lower()
                    result[p] = result.get(p, 0) + 1

                # Extract date for aging check (>3 days)
                date_match = re.search(r"\*\*(\d{4}-\d{2}-\d{2})\*\*", line)
                if date_match:
                    try:
                        task_date = date.fromisoformat(date_match.group(1))
                        if (TODAY - task_date).days > 3:
                            result["aging"] += 1
                    except ValueError:
                        pass

                # Store task text for display
                text_match = re.search(r"`P[123]`\s*\|\s*(.+?)(?:\s*\||\s*$)", line)
                if text_match:
                    result["tasks"].append(text_match.group(1).strip())

    # Parse state.json for completion rate
    if VIRAID_STATE_FILE.exists():
        try:
            state = json.loads(VIRAID_STATE_FILE.read_text(encoding="utf-8"))
            stats = state.get("stats", {})
            result["completion_rate"] = _as_percent(stats.get("completion_rate"))
        except (json.JSONDecodeError, OSError):
            pass

    return result


def collect_capture_payoff():
    """R10: daily /zk capture payoff. Counts signals captured in the last 7 days
    (knowledge notes + Odin episodes) and surfaces whether an episode cluster is
    ripe to promote to an Odin principle (reusing odin-cadence.py). Gives the
    weekly capture loop a DAILY surface so /zk capture visibly pays off.

    Degrades to {"available": False} when there is no Odin brain (e.g. an exec
    workspace), so the panel hides rather than erroring."""
    if not ODIN_BRAIN_DIR.exists():
        return {"available": False}

    cutoff = TODAY - timedelta(days=7)

    def _recent(md_path):
        try:
            fm, _ = _parse_fm(md_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        for key in ("updated", "created", "date", "ingested"):
            val = fm.get(key)
            if not val:
                continue
            try:
                # `if ... return True`, not `return ... >= cutoff`. The loop
                # reads as a fallback chain and behaved as a first-hit-wins
                # verdict: an old `date` returned False and `ingested` was
                # never consulted, so a note captured THIS WEEK from an old
                # source was missing from "Signals Captured (7d)" -- the one
                # number the panel exists to report.
                if datetime.fromisoformat(str(val)[:10]).date() >= cutoff:
                    return True
            except ValueError:
                continue
        return False

    signals = 0
    recent_titles = []
    for md in KNOWLEDGE_DIR.rglob("*.md"):
        if md.name.lower() in ("index.md", "readme.md", "templates.md"):
            continue
        if _recent(md):
            signals += 1
            if len(recent_titles) < 5:
                recent_titles.append(md.stem.replace("-", " "))

    promote_ready = last_collect = days_since = None
    if ODIN_CADENCE_SCRIPT.exists():
        try:
            out = subprocess.run(
                [sys.executable, str(ODIN_CADENCE_SCRIPT), "--json"],
                capture_output=True, text=True, timeout=30,
            )
            data = json.loads(out.stdout) if out.stdout.strip() else {}
            # A non-numeric value here used to reach `promote > 0` and
            # raise TypeError, uncaught, killing the whole dashboard -- and
            # only on the days when there WERE clusters to promote.
            promote_ready = _as_int_or_count(data.get("reflect_clusters"))
            last_collect = data.get("last_collect")
            # Through the same guard as `reflect_clusters` one line above.
            # Both come verbatim from `odin-cadence.py --json`, whose shape
            # `_as_int_or_count` documents as never promised -- and this one
            # reaches `days_since >= 7` in the BUILD phase, after every
            # collector has succeeded, so a string here killed the whole
            # dashboard with all of its data already in hand.
            days_since = _as_int_or_count(data.get("days_since"))
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError, ValueError) as e:
            # Best-effort Odin cadence block; degrade to None but surface why.
            print(f"[generate-dashboard] odin cadence collect failed: {e}", file=sys.stderr)

    return {
        "available": True,
        "signals_week": signals,
        "recent_titles": recent_titles,
        "promote_ready": promote_ready,
        "last_collect": last_collect,
        "days_since": days_since,
    }


# ============================================================
# CSS
# ============================================================
def build_css(gt_light_b64="", gt_medium_b64=""):
    """Brand-compliant CSS for the Morning Dashboard.

    Embeds GT Standard fonts as base64 WOFF2 @font-face when provided.
    Falls back to Inter / system stack otherwise.

    Brand authority: reference/corporate-style-guide.md +
    .claude/skills/design/references/brand.css
    """
    font_face = ""
    if gt_light_b64:
        font_face += f"""
@font-face {{
  font-family: 'GT Standard';
  src: url(data:font/woff2;base64,{gt_light_b64}) format('woff2');
  font-weight: 300;
  font-style: normal;
  font-display: swap;
}}"""
    if gt_medium_b64:
        font_face += f"""
@font-face {{
  font-family: 'GT Standard';
  src: url(data:font/woff2;base64,{gt_medium_b64}) format('woff2');
  font-weight: 500;
  font-style: normal;
  font-display: swap;
}}"""

    return render_template("dashboard.css", FONT_FACE=font_face)


# ============================================================
# Rendering / HTML Section Builders
# ============================================================
def build_cover(white_logo_b64):
    """Dark Palantinate-Blue / black cover page with white logo and orange corner.
    Brand spec: reference/corporate-style-guide.md (Signature Brand Elements).
    """
    date_long = NOW.strftime("%A, %B %d, %Y")
    time_str = NOW.strftime("%H:%M")
    zone_suffix = _zone_suffix()
    logo_html = (
        f'<img class="cover-logo" src="{white_logo_b64}" alt="31 Concept"/>'
        if white_logo_b64 else ''
    )
    return f"""
<div class="cover">
  <div class="cover-corner"></div>
  <div class="cover-accent"></div>
  <div class="cover-inner">
    {logo_html}
    <div class="cover-eyebrow">CEO Morning Dashboard</div>
    <div class="cover-title">Heading<span class="one-blue">.</span>State<span class="one-blue">.</span>Drift<span class="one-blue">.</span></div>
    <div class="cover-date">{esc(date_long)}</div>
    <div class="cover-meta">Generated {esc(time_str)}{zone_suffix} &middot; Internal &mdash; CEO Eyes Only</div>
  </div>
  <div class="cover-footer">
    <div class="cover-footer-marks"><span class="sq blue"></span><span class="sq orange"></span></div>
    <span>&copy; 2025-2026 / 31 Concept &middot; 31C.io &middot; Proprietary &amp; Confidential</span>
  </div>
</div>
"""


def build_header(logo_b64):
    date_long = NOW.strftime("%A, %B %d, %Y")
    time_str = NOW.strftime("%H:%M")
    zone_suffix = _zone_suffix()
    return f"""
<div class="topbar">
  <div class="topbar-left">
    {'<img class="logo-img" src="' + logo_b64 + '" alt="31C"/>' if logo_b64 else ''}
    <div class="pulse"></div>
    <span class="topbar-title">Morning Dashboard</span>
  </div>
  <div class="topbar-right">Internal &mdash; CEO Eyes Only</div>
</div>
<div class="datebar">
  <div class="datebar-date">{esc(date_long)}</div>
  <div class="datebar-meta">Generated {esc(time_str)}{zone_suffix}</div>
</div>
"""


def build_urgent(crm):
    items_html = []

    # Overdue commitments
    overdue = [c for c in crm["commitments_due"] if c["overdue"]]
    for c in overdue:
        items_html.append(f"""
<div class="alert-card">
  <div class="alert-label">Overdue Commitment</div>
  <div class="alert-text"><strong>{esc(c['name'])}</strong> ({esc(c['company'])})</div>
  <div class="alert-sub">{esc(c['text'])}</div>
</div>""")

    # RED contacts
    for c in crm["red"][:8]:
        days_str = f"{c['days_since']} days ago" if c.get("days_since") is not None else "no recorded touch"
        items_html.append(f"""
<div class="alert-card">
  <div class="alert-label">Relationship Overdue</div>
  <div class="alert-text"><strong>{esc(c['name'])}</strong> ({esc(c.get('company', ''))}) &mdash; {esc(c.get('type', ''))}</div>
  <div class="alert-sub">Last touch: {esc(days_str)} &bull; Cadence: {esc(c.get('cadence', '?'))} days</div>
</div>""")

    # Upcoming commitments (not overdue)
    upcoming = [c for c in crm["commitments_due"] if not c["overdue"]]
    for c in upcoming[:5]:
        items_html.append(f"""
<div class="alert-card warn">
  <div class="alert-label">Commitment Due</div>
  <div class="alert-text"><strong>{esc(c['name'])}</strong> ({esc(c['company'])})</div>
  <div class="alert-sub">{esc(c['text'])} &bull; Due: {esc(c['due'])}</div>
</div>""")

    if crm.get("failed"):
        # "All Clear" must be unreachable from an error path. `collect_crm_health`
        # returns its empty skeleton when the scan raises, and an empty skeleton
        # is indistinguishable here from a genuinely quiet morning -- so a single
        # malformed contact file used to render the CEO a green card while
        # overdue commitments sat unread. An empty result that came from a
        # FAILURE says so, and says it in the alarming direction.
        items_html.append(f"""
<div class="alert-card">
  <div class="alert-label">CRM Data Unavailable</div>
  <div class="alert-text">The CRM scan failed, so this panel is EMPTY, not clear.</div>
  <div class="alert-sub">{esc(crm.get("failed"))}</div>
</div>""")
    elif not items_html:
        items_html.append("""
<div class="alert-card ok">
  <div class="alert-label">All Clear</div>
  <div class="alert-text">No urgent items this morning. Steady as she goes.</div>
</div>""")

    return f"""
<div class="section">
  <div class="section-num">01</div>
  <div class="section-title">Urgent Items</div>
  {"".join(items_html)}
</div>
"""


def build_bridge(calendar, emails):
    # Calendar table
    cal_rows = ""
    for m in calendar["meetings"]:
        time_val = m.get("Time", "")
        subject = m.get("Subject", "")
        duration = m.get("Duration", "")
        cal_rows += f"<tr><td>{esc(time_val)}</td><td>{esc(subject)}</td><td>{esc(duration)}</td></tr>\n"

    if not cal_rows:
        cal_rows = '<tr><td colspan="3" style="color:var(--ink35);font-style:italic;">No meetings scheduled today</td></tr>'

    cal_sync = f'<div class="sync-label">Calendar &bull; {esc(calendar["sync_time"])}</div>' if calendar["sync_time"] else '<div class="sync-label">Calendar</div>'

    # Email table
    email_rows = ""
    for e in emails["emails"][:6]:
        from_val = e.get("From", "")
        subject = e.get("Subject", "")
        read = e.get("Read", "")
        dot = '<span style="color:var(--orange);">&#9679;</span> ' if read.lower() == "no" else ""
        email_rows += f"<tr><td>{dot}{esc(from_val)}</td><td>{esc(subject[:50])}</td></tr>\n"

    if not email_rows:
        email_rows = '<tr><td colspan="2" style="color:var(--ink35);font-style:italic;">No recent emails</td></tr>'

    email_sync = f'<div class="sync-label">Email &bull; {esc(emails["sync_time"])}</div>' if emails["sync_time"] else '<div class="sync-label">Email</div>'

    return f"""
<div class="section">
  <div class="section-num">02</div>
  <div class="section-title">Today's Bridge</div>
  <div class="two-col">
    <div class="col-left">
      {cal_sync}
      <table class="dtable">
        <thead><tr><th>Time</th><th>Subject</th><th>Duration</th></tr></thead>
        <tbody>{cal_rows}</tbody>
      </table>
    </div>
    <div class="col-right">
      {email_sync}
      <table class="dtable">
        <thead><tr><th>From</th><th>Subject</th></tr></thead>
        <tbody>{email_rows}</tbody>
      </table>
    </div>
  </div>
</div>
"""


def build_pipeline(pipeline):
    # Bar chart -- canonical pipeline stages in funnel order
    raw_stages = pipeline["stages"]
    canonical_order = ["Lead", "Qualified", "Demo/POC", "Proposal", "Negotiation", "Won"]

    if not raw_stages:
        bars_html = '<div style="color:var(--ink35);font-style:italic;">No pipeline data available</div>'
    else:
        max_count = max(raw_stages.get(s, 0) for s in canonical_order) if raw_stages else 1
        if max_count == 0:
            max_count = 1
        bars = []
        for stage in canonical_order:
            count = raw_stages.get(stage, 0)
            height_pct = max(10, int((count / max_count) * 100)) if count > 0 else 4
            bars.append(f"""
<div class="bar-item">
  <div class="bar-count">{count}</div>
  <div class="bar-fill" style="height:{height_pct}%;"></div>
  <div class="bar-label">{esc(stage)}</div>
</div>""")
        bars_html = f'<div class="bar-chart">{"".join(bars)}</div>'

    # Format currency values
    total_val = pipeline.get("total_value", 0)
    weighted_val = pipeline.get("weighted_value", 0)
    stale = pipeline.get("stale_count", 0)
    total_str = f"${total_val / 1_000_000:.1f}M" if total_val >= 1_000_000 else f"${total_val:,.0f}"
    weighted_str = f"${weighted_val / 1_000_000:.1f}M" if weighted_val >= 1_000_000 else f"${weighted_val:,.0f}"

    stale_cls = "danger" if stale > 5 else ("accent" if stale > 0 else "up")

    # Top 3 deals by weighted value
    top_deals_html = ""
    if pipeline.get("top_deals"):
        top_rows = ""
        for deal, wval in pipeline["top_deals"]:
            company = deal.get("Company", "Unknown")
            stage = deal.get("Stage", "")
            w_str = f"${wval / 1_000_000:.1f}M" if wval >= 1_000_000 else f"${wval:,.0f}"
            top_rows += f"<tr><td><strong>{esc(company)}</strong></td><td>{esc(stage)}</td><td style='text-align:right;'>{esc(w_str)}</td></tr>\n"
        top_deals_html = f"""
<div class="sync-label" style="margin-top:16px;">Top 3 by Weighted Value</div>
<table class="dtable">
  <thead><tr><th>Company</th><th>Stage</th><th style="text-align:right;">Weighted</th></tr></thead>
  <tbody>{top_rows}</tbody>
</table>"""

    # Metrics strip
    return f"""
<div class="section">
  <div class="section-num">03</div>
  <div class="section-title">Pipeline Pulse</div>
  {bars_html}
  <div class="metrics-strip">
    <div class="metric-box">
      <div class="metric-val accent">{pipeline['total_deals']}</div>
      <div class="metric-label">Active Deals</div>
    </div>
    <div class="metric-box">
      <div class="metric-val accent">{esc(total_str)}</div>
      <div class="metric-label">Total Value</div>
    </div>
    <div class="metric-box">
      <div class="metric-val up">{esc(weighted_str)}</div>
      <div class="metric-label">Weighted Value</div>
    </div>
    <div class="metric-box">
      <div class="metric-val {stale_cls}">{stale}</div>
      <div class="metric-label">Stale (&gt;14d)</div>
    </div>
    <div class="metric-box">
      <div class="metric-val up">{pipeline['total_won']}</div>
      <div class="metric-label">Won</div>
    </div>
    <div class="metric-box">
      <div class="metric-val">{pipeline['total_investors']}</div>
      <div class="metric-label">Investor Talks</div>
    </div>
    <div class="metric-box">
      <div class="metric-val">{pipeline['total_partnerships']}</div>
      <div class="metric-label">Partnerships</div>
    </div>
  </div>
  {top_deals_html}
</div>
"""


def build_radar(crm):
    red_count = len(crm["red"])
    yellow_count = len(crm["yellow"])
    green_count = len(crm["green"])

    # RED contact details
    red_rows = ""
    for c in crm["red"][:10]:
        days_str = f"{c['days_since']}d" if c.get("days_since") is not None else "N/A"
        red_rows += f"<tr><td><strong>{esc(c['name'])}</strong></td><td>{esc(c.get('company',''))}</td><td>{esc(c.get('type',''))}</td><td style='color:var(--red);'>{esc(days_str)}</td></tr>\n"

    # YELLOW contact details
    yellow_rows = ""
    for c in crm["yellow"][:8]:
        days_str = f"{c['days_since']}d" if c.get("days_since") is not None else "N/A"
        yellow_rows += f"<tr><td><strong>{esc(c['name'])}</strong></td><td>{esc(c.get('company',''))}</td><td>{esc(c.get('type',''))}</td><td style='color:var(--yellow);'>{esc(days_str)}</td></tr>\n"

    contacts_table = ""
    if red_rows:
        contacts_table += f"""
<div class="sync-label" style="margin-top:16px;">Overdue &mdash; Need Attention</div>
<table class="dtable">
  <thead><tr><th>Name</th><th>Company</th><th>Type</th><th>Since</th></tr></thead>
  <tbody>{red_rows}</tbody>
</table>"""

    if yellow_rows:
        contacts_table += f"""
<div class="sync-label" style="margin-top:16px;">Approaching &mdash; Watch</div>
<table class="dtable">
  <thead><tr><th>Name</th><th>Company</th><th>Type</th><th>Since</th></tr></thead>
  <tbody>{yellow_rows}</tbody>
</table>"""

    return f"""
<div class="section">
  <div class="section-num">04</div>
  <div class="section-title">Relationship Radar</div>
  <div class="radar-row">
    <div class="radar-circle r">
      <div class="radar-num">{red_count}</div>
      <div class="radar-lbl">Overdue</div>
    </div>
    <div class="radar-circle y">
      <div class="radar-num">{yellow_count}</div>
      <div class="radar-lbl">Approaching</div>
    </div>
    <div class="radar-circle g">
      <div class="radar-num">{green_count}</div>
      <div class="radar-lbl">On Track</div>
    </div>
  </div>
  {contacts_table}
</div>
"""


def build_heading(strategy, pipeline, metrics):
    # Determine indicator states based on available data
    indicators = []

    # 1. Revenue conversion
    won = pipeline["total_won"]
    rev_state = "g" if won >= 2 else ("y" if won >= 1 else "r")
    rev_status = f"{won} won" if won else "Pre-revenue"
    indicators.append(("Revenue Conversion", rev_state, rev_status))

    # 2. Partner channel activation
    active_partners = sum(1 for p in pipeline["partnerships"] if p.get("Stage", "").lower() == "active")
    partner_state = "g" if active_partners >= 3 else ("y" if active_partners >= 1 else "r")
    indicators.append(("Partner Channel Activation", partner_state, f"{active_partners} active"))

    # 3. Post-MWC follow-up execution
    post_mwc = sum(1 for d in pipeline["deals"] if "post-mwc" in d.get("Stage", "").lower() or "mwc" in d.get("Notes", "").lower())
    mwc_state = "g" if post_mwc >= 5 else ("y" if post_mwc >= 2 else "r")
    indicators.append(("Post-MWC Follow-up Execution", mwc_state, f"{post_mwc} prospects"))

    # 4. Hiring momentum
    indicators.append(("Hiring Momentum", "y", f"{metrics['headcount']} of {metrics['hiring_target']}"))

    # 5. Fundraising progress
    fund_state = "y"
    investors_active = pipeline["total_investors"]
    indicators.append(("Fundraising Progress", fund_state, f"{investors_active} conversations"))

    items = ""
    for label, state, status in indicators:
        items += f"""
<li class="heading-item">
  <div class="heading-dot {state}"></div>
  <div class="heading-text">{esc(label)}</div>
  <div class="heading-status" style="color:var(--{'green' if state == 'g' else 'yellow' if state == 'y' else 'red'});">{esc(status)}</div>
</li>"""

    heading_text = strategy.get("heading", "Post-Launch Commercial Activation")
    phase_text = strategy.get("phase", "Phase 1: Home Region")

    return f"""
<div class="section">
  <div class="section-num">05</div>
  <div class="section-title">Heading Check</div>
  <div style="display:flex;gap:16px;margin-bottom:16px;">
    <div style="flex:1;padding:12px 16px;background:var(--orlight);border-left:3px solid var(--orange);border-radius:0 4px 4px 0;">
      <div class="sync-label" style="margin-bottom:2px;">Current Heading</div>
      <div style="font-size:16px;font-weight:600;">{esc(heading_text)}</div>
    </div>
    <div style="padding:12px 16px;background:var(--ink);border-radius:4px;min-width:160px;text-align:center;">
      <div style="font-family:'IBM Plex Mono',monospace;font-size:7.5px;letter-spacing:1.5px;text-transform:uppercase;color:rgba(255,255,255,0.4);margin-bottom:2px;">Go-To-Market</div>
      <div style="font-family:'Bebas Neue',sans-serif;font-size:16px;color:var(--orange);">{esc(phase_text)}</div>
    </div>
  </div>
  <ul class="heading-list">{items}</ul>
</div>
"""


def build_market(metrics):
    return f"""
<div class="section" style="padding-top:20px;padding-bottom:20px;">
  <div class="section-num">06</div>
  <div class="section-title">Market Context</div>
  <div class="market-strip">
    <div class="market-item">
      <div class="market-val">{esc(metrics['dpi_tam_2030'])}</div>
      <div class="market-label">Global DPI 2030</div>
    </div>
    <div class="market-item">
      <div class="market-val">{esc(metrics['mea_2030'])}</div>
      <div class="market-label">MEA 2030</div>
    </div>
    <div class="market-item">
      <div class="market-val">{esc(metrics['cis_2030'])}</div>
      <div class="market-label">Home Region 2030</div>
    </div>
    <div class="market-item">
      <div class="market-val">{esc(metrics['predecessor_vacuum_countries'])}</div>
      <div class="market-label">Incumbent Vacuum</div>
    </div>
    <div class="market-item">
      <div class="market-val">{esc(metrics['cagr'])}</div>
      <div class="market-label">CAGR</div>
    </div>
  </div>
</div>
"""


def build_freshness(freshness):
    rows = ""
    for f in freshness:
        dot_cls = {"green": "g", "yellow": "y", "red": "r"}.get(f["health"], "gray")
        age_str = f"{f['age']}d ago" if f["age"] is not None else "unknown"
        date_str = f["date"] if f["date"] else "no marker"
        rows += f"""
<div class="fresh-row">
  <div class="fresh-dot {dot_cls}"></div>
  <div class="fresh-name">{esc(f['name'])}</div>
  <div class="fresh-age">{esc(date_str)}</div>
  <div class="fresh-age">{esc(age_str)}</div>
</div>"""

    return f"""
<div class="section" style="padding-top:16px;padding-bottom:16px;">
  <div class="section-num">07</div>
  <div class="section-title">Data Freshness</div>
  {rows}
</div>
"""


def build_hiring(hiring):
    p1_count = len(hiring["p1"])
    p2_count = len(hiring["p2"])
    p3_count = len(hiring["p3"])
    total = hiring["total"]
    urgent = hiring["urgent"]

    # Urgent roles alert
    urgent_html = ""
    if urgent:
        for role in urgent:
            role_name = role.get("Role", "Unknown")
            status = role.get("Status", "")
            urgent_html += f"""
<div class="alert-card">
  <div class="alert-label">Urgent Hire</div>
  <div class="alert-text"><strong>{esc(role_name)}</strong></div>
  <div class="alert-sub">Status: {esc(status)}</div>
</div>"""

    if total == 0:
        body = '<div style="color:var(--ink35);font-style:italic;">No hiring pipeline data available</div>'
    else:
        body = f"""
{urgent_html}
<div class="metrics-strip">
  <div class="metric-box">
    <div class="metric-val danger">{p1_count}</div>
    <div class="metric-label">P1 Critical</div>
  </div>
  <div class="metric-box">
    <div class="metric-val accent">{p2_count}</div>
    <div class="metric-label">P2 High</div>
  </div>
  <div class="metric-box">
    <div class="metric-val">{p3_count}</div>
    <div class="metric-label">P3 Planned</div>
  </div>
  <div class="metric-box">
    <div class="metric-val accent">{total}</div>
    <div class="metric-label">Total Open</div>
  </div>
</div>"""

    return f"""
<div class="section">
  <div class="section-num">08</div>
  <div class="section-title">Hiring Pipeline</div>
  {body}
</div>
"""


def build_content_cadence(cadence):
    nl_days = cadence["newsletter_days"]
    nl_status = cadence["newsletter_status"]
    nl_last = cadence["newsletter_last"]
    li_count = cadence["linkedin_count_week"]
    li_status = cadence["linkedin_status"]

    # Newsletter indicator
    if nl_days is not None:
        nl_color = "var(--green)" if nl_status == "ON TRACK" else "var(--red)"
        nl_detail = f"Last issue: {esc(nl_last)} ({nl_days}d ago)"
    else:
        nl_color = "var(--ink35)"
        nl_detail = "No newsletter issues found"
        nl_status = "NO DATA"

    # LinkedIn indicator
    li_color = "var(--green)" if li_status == "ON TRACK" else "var(--red)"

    return f"""
<div class="section">
  <div class="section-num">09</div>
  <div class="section-title">Content Cadence</div>
  <div style="display:flex;gap:16px;">
    <div style="flex:1;padding:14px 16px;border:1px solid var(--ink12);border-radius:4px;">
      <div class="sync-label">Newsletter (Target: Weekly)</div>
      <div style="display:flex;align-items:center;gap:10px;margin-top:6px;">
        <div class="heading-dot {'g' if nl_status == 'ON TRACK' else 'r'}"></div>
        <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:{nl_color};font-weight:700;">{esc(nl_status)}</span>
      </div>
      <div class="alert-sub" style="margin-top:6px;">{nl_detail}</div>
    </div>
    <div style="flex:1;padding:14px 16px;border:1px solid var(--ink12);border-radius:4px;">
      <div class="sync-label">LinkedIn (Target: 2+/week)</div>
      <div style="display:flex;align-items:center;gap:10px;margin-top:6px;">
        <div class="heading-dot {'g' if li_status == 'ON TRACK' else 'r'}"></div>
        <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:{li_color};font-weight:700;">{esc(li_status)}</span>
      </div>
      <div class="alert-sub" style="margin-top:6px;">{li_count} posts/drafts this week</div>
    </div>
  </div>
</div>
"""


def build_viraid(viraid):
    active = viraid["active_total"]
    p1 = viraid["p1"]
    p2 = viraid["p2"]
    p3 = viraid["p3"]
    aging = viraid["aging"]
    rate = viraid["completion_rate"]

    if active == 0:
        body = '<div style="color:var(--ink35);font-style:italic;">No Viraid tasks data available</div>'
    else:
        aging_cls = "danger" if aging > 5 else ("accent" if aging > 0 else "up")

        body = f"""
<div class="metrics-strip">
  <div class="metric-box">
    <div class="metric-val accent">{active}</div>
    <div class="metric-label">Active Tasks</div>
  </div>
  <div class="metric-box">
    <div class="metric-val danger">{p1}</div>
    <div class="metric-label">P1 Tasks</div>
  </div>
  <div class="metric-box">
    <div class="metric-val accent">{p2}</div>
    <div class="metric-label">P2 Tasks</div>
  </div>
  <div class="metric-box">
    <div class="metric-val">{p3}</div>
    <div class="metric-label">P3 Tasks</div>
  </div>
  <div class="metric-box">
    <div class="metric-val {aging_cls}">{aging}</div>
    <div class="metric-label">Aging (&gt;3d)</div>
  </div>
  <div class="metric-box">
    <div class="metric-val">{rate:.0f}%</div>
    <div class="metric-label">Completion</div>
  </div>
</div>"""

    return f"""
<div class="section">
  <div class="section-num">10</div>
  <div class="section-title">Viraid Task Summary</div>
  {body}
</div>
"""


def build_capture_payoff(payoff):
    """R10: the daily capture-payoff panel. Hidden entirely when no Odin brain."""
    if not payoff.get("available"):
        return ""

    signals = payoff.get("signals_week", 0)
    promote = payoff.get("promote_ready")
    days_since = payoff.get("days_since")

    sig_cls = "up" if signals > 0 else ""
    promote_val = "-" if promote is None else str(promote)
    promote_cls = "accent" if (promote or 0) > 0 else ""
    collect_val = "-" if days_since is None else f"{days_since}d"
    collect_cls = "danger" if (days_since is not None and days_since >= 7) else ""

    titles = payoff.get("recent_titles") or []
    if titles:
        recent = '<div style="margin-top:12px;color:var(--ink55);font-size:12px;">Recent: ' \
                 + esc(", ".join(titles)) + "</div>"
    else:
        recent = '<div style="margin-top:12px;color:var(--ink35);font-style:italic;font-size:12px;">' \
                 'No captures in the last 7 days &mdash; a quiet week for /zk.</div>'

    if promote and promote > 0:
        nudge = f'<div style="margin-top:10px;color:var(--accent);font-size:12px;">' \
                f'{promote} episode cluster(s) ripe to promote &mdash; run <code>/odin reflect</code>.</div>'
    else:
        nudge = ""

    body = f"""
<div class="metrics-strip">
  <div class="metric-box">
    <div class="metric-val {sig_cls}">{signals}</div>
    <div class="metric-label">Signals Captured (7d)</div>
  </div>
  <div class="metric-box">
    <div class="metric-val {promote_cls}">{promote_val}</div>
    <div class="metric-label">Clusters to Promote</div>
  </div>
  <div class="metric-box">
    <div class="metric-val {collect_cls}">{collect_val}</div>
    <div class="metric-label">Since Last Harvest</div>
  </div>
</div>
{recent}
{nudge}"""

    return f"""
<div class="section">
  <div class="section-num">11</div>
  <div class="section-title">Capture Payoff &mdash; /zk &amp; Odin</div>
  {body}
</div>
"""


def build_footer():
    return f"""
<div class="footer">
  <div class="footer-marks"><span class="sq blue"></span><span class="sq orange"></span></div>
  <div class="footer-left">31C Morning Dashboard &middot; Generated {esc(NOW.strftime("%Y-%m-%d %H:%M"))} &middot; &copy; 2025-2026 / 31 Concept &middot; 31C.io</div>
  <div class="footer-right">Internal &mdash; CEO Eyes Only</div>
</div>
"""


# ============================================================
# CLI / Main
# ============================================================
def generate_html(crm, pipeline, calendar, emails, strategy, metrics, freshness,
                   hiring, content_cadence, viraid, capture_payoff=None):
    # blue_logo_b64 = load_logo_base64(LOGO_BLUE_PATH) used to be read here.
    # Only the white logo is rendered, so this was a file read per run for a
    # value nothing used.
    white_logo_b64 = load_logo_base64(LOGO_WHITE_PATH)
    gt_light_b64 = load_font_b64(GT_LIGHT_FONT)
    gt_medium_b64 = load_font_b64(GT_MEDIUM_FONT)
    css = build_css(gt_light_b64, gt_medium_b64)

    cover = build_cover(white_logo_b64)
    sections = [
        # Topbar sits on dark ink background -> use white logo.
        build_header(white_logo_b64),
        build_urgent(crm),
        build_bridge(calendar, emails),
        build_pipeline(pipeline),
        build_radar(crm),
        build_heading(strategy, pipeline, metrics),
        build_market(metrics),
        build_freshness(freshness),
        build_hiring(hiring),
        build_content_cadence(content_cadence),
        build_viraid(viraid),
        build_capture_payoff(capture_payoff or {}),
        build_footer(),
    ]

    # Inter fallback (Google Fonts) only loaded if GT Standard embed failed.
    inter_link = ""
    if not gt_light_b64 or not gt_medium_b64:
        inter_link = (
            '<link rel="preconnect" href="https://fonts.googleapis.com"/>\n'
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>\n'
            '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;700&display=swap" rel="stylesheet"/>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>31C Morning Dashboard &mdash; {esc(TODAY.strftime("%Y-%m-%d"))}</title>
{inter_link}
<style>{css}</style>
</head>
<body>
{cover}
<div class="page">
{"".join(sections)}
</div>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="31C CEO Morning Dashboard Generator")
    parser.add_argument("--output-dir", help="Custom output directory")
    parser.add_argument("--pdf", action="store_true", help="Also generate PDF via html-to-pdf.py")
    args = parser.parse_args()

    # Determine output directory
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        out_dir = get_outputs_dir() / "operations" / "dashboard" / TODAY.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)

    html_path = out_dir / "morning-dashboard.html"

    print("Collecting data...")

    crm = collect_crm_health()
    print(f"  CRM: {crm['total']} contacts ({len(crm['red'])} red, {len(crm['yellow'])} yellow, {len(crm['green'])} green)")

    pipeline = collect_pipeline()
    print(f"  Pipeline: {pipeline['total_deals']} deals, {pipeline['total_investors']} investors, {pipeline['total_partnerships']} partnerships")

    calendar = collect_calendar()
    print(f"  Calendar: {len(calendar['meetings'])} meetings today")

    emails = collect_emails()
    print(f"  Email: {emails['count']} recent emails")

    strategy = collect_strategy()
    print(f"  Strategy: heading = {strategy['heading']}")

    metrics = collect_metrics()
    print(f"  Metrics: {metrics['headcount']} headcount, {metrics['modules_live']} modules live")

    freshness = collect_freshness()
    stale = sum(1 for f in freshness if f["health"] == "red")
    print(f"  Freshness: {stale} stale files" if stale else "  Freshness: all current")

    hiring = collect_hiring()
    print(f"  Hiring: {hiring['total']} open roles ({len(hiring['p1'])} P1, {len(hiring['p2'])} P2, {len(hiring['p3'])} P3), {len(hiring['urgent'])} urgent")

    content_cadence = collect_content_cadence()
    print(f"  Content: newsletter {content_cadence['newsletter_status']}, LinkedIn {content_cadence['linkedin_status']} ({content_cadence['linkedin_count_week']} this week)")

    viraid = collect_viraid()
    print(f"  Viraid: {viraid['active_total']} active tasks ({viraid['p1']} P1, {viraid['p2']} P2, {viraid['p3']} P3), {viraid['aging']} aging")

    capture_payoff = collect_capture_payoff()
    if capture_payoff.get("available"):
        print(f"  Capture payoff: {capture_payoff['signals_week']} signals/7d, "
              f"{capture_payoff.get('promote_ready')} cluster(s) to promote")

    print("\nGenerating HTML...")
    html_content = generate_html(crm, pipeline, calendar, emails, strategy, metrics, freshness,
                                 hiring, content_cadence, viraid, capture_payoff)
    html_path.write_text(html_content, encoding="utf-8")
    size = html_path.stat().st_size
    print(f"  Dashboard: {html_path}")
    print(f"  Size: {size:,} bytes")

    if args.pdf:
        print("\nGenerating PDF...")
        pdf_path = out_dir / "morning-dashboard.pdf"
        try:
            subprocess.run(
                [sys.executable, str(HTML_TO_PDF_SCRIPT), str(html_path), str(pdf_path)],
                check=True, timeout=60
            )
        except Exception as e:
            print(f"  PDF generation failed: {e}", file=sys.stderr)

    print("\nDone.")


if __name__ == "__main__":
    main()
