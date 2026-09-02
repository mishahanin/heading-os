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
from scripts.utils.brand_assets import brand_asset_path
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
from scripts.utils.markdown import frontmatter_date
from scripts.utils.markdown import parse_frontmatter as _parse_fm
from scripts.utils.markdown import parse_md_table
from scripts.utils.odin_cadence import read_cadence_json

# ============================================================
# Paths
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent

HTML_TO_PDF_SCRIPT = SCRIPT_DIR / "html-to-pdf.py"
# R10 capture-payoff: the ceo-only cadence script.
ODIN_CADENCE_SCRIPT = SCRIPT_DIR / "odin-cadence.py"


# ============================================================
# Data-root paths - functions, never constants
# ============================================================
def pipeline_file() -> Path:
    """Resolved at call time, never at import.

    Every path below reaches `get_context_dir()`, `get_outputs_dir()`,
    `get_knowledge_dir()` or `get_datastore_dir()`, and each of those reads
    HEADING_OS_DATA on every call, so it follows the environment for a caller
    that asks after the environment moved. As module-level constants they asked
    once, during this module's import, and stored the answer -- so a test that
    imported this module and then repointed the root still read the operator's
    real overlay. Sixteen one-line functions is the honest shape of sixteen
    paths; the docstring is here once rather than sixteen times.
    """
    return get_context_dir() / "pipeline.md"


def strategy_file() -> Path:
    return get_context_dir() / "strategy.md"


def metrics_file() -> Path:
    return get_context_dir() / "current-data.md"


def calendar_file() -> Path:
    return get_outputs_dir() / "_sync" / "calendar" / "upcoming.md"


def email_file() -> Path:
    return get_outputs_dir() / "_sync" / "emails" / "inbox-latest.md"


def context_dir() -> Path:
    return get_context_dir()


def hiring_file() -> Path:
    return get_context_dir() / "hiring-pipeline.md"


def viraid_tasks_file() -> Path:
    return get_outputs_dir() / "operations" / "viraid" / "tasks.md"


def viraid_state_file() -> Path:
    return get_outputs_dir() / "operations" / "viraid" / "state.json"


def newsletters_dir() -> Path:
    return get_outputs_dir() / "intel" / "newsletters"


def linkedin_dir() -> Path:
    return get_outputs_dir() / "content" / "linkedin"


def linkedin_drafts_dir() -> Path:
    return get_outputs_dir() / "content" / "linkedin-drafts"


def linkedin_archive_dir() -> Path:
    """Where a PUBLISHED post ends up. `scripts/linkedin-archive.py` `git mv`s
    the staged .md out of `linkedin_dir()` into here, under
    {posts|articles|comments}/{slug}/, so a post counted while it sat
    unpublished stopped being counted the moment it went live.
    """
    return get_datastore_dir() / "content" / "linkedin-archive"


def knowledge_dir() -> Path:
    return get_knowledge_dir()


def odin_brain_dir() -> Path:
    return get_knowledge_dir() / "odin-brain"


# Canonical brand assets (per reference/corporate-style-guide.md).
# Each of the four asks the private manifest for its filename. They were spelled
# here until 2026-09-02, when the operator ruled that a datastore filename is
# itself private and this repository is public; the reasoning, and why a public
# clone gets a named refusal rather than a plausible default, is in
# scripts/utils/brand_assets.py.
def logo_blue_path() -> Path:
    return brand_asset_path("logo_primary")


def logo_white_path() -> Path:
    return brand_asset_path("logo_on_dark")


def gt_light_font() -> Path:
    return brand_asset_path("font_gt_l_light")


def gt_medium_font() -> Path:
    return brand_asset_path("font_gt_l_medium")


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
    """The text of a dashboard source, or "" when this cannot read it.

    Every panel already distinguishes "the source was not read" from "the source
    was empty": the collectors leave `source_read` False on a falsy return and
    `_sync_label` renders that as NOT SYNCED. A file this cannot decode belongs
    in the first of those two states, and until 2026-09-01 it reached neither -
    `read_text(encoding="utf-8")` raised `UnicodeDecodeError` (a ValueError, so
    no relation to the OSError handlers elsewhere on this page) straight out of
    `collect_calendar`, and the whole dashboard died over one byte in one source.

    Announced rather than swallowed, because "" is also what an empty file
    gives: without this line the panel would report NOT SYNCED with the reason
    recorded nowhere.
    """
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"[generate-dashboard] could not read {path}: {exc}",
              file=sys.stderr)
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
    content = read_file(pipeline_file())
    result = {
        "deals": [], "investors": [], "partnerships": [], "won": [],
        "stages": {}, "off_stages": {}, "total_deals": 0, "total_investors": 0,
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
    deals = parse_md_table(content, r"##\s*Active Deals", source=str(pipeline_file()))
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
                          where=f"{pipeline_file()} deal {d.get('Company', '?')!r}")
        total_value += val

        # A stage spelled anything other than the six canonical strings was
        # weighted at 5% and drawn in no bar, in silence, while the same deal
        # still counted in Active Deals and its money in Total Value. So
        # "Demo/PoC", "demo/poc" and a renamed stage each moved a deal off the
        # funnel chart and cut its weighted contribution by up to 95% with
        # nothing said - and `parse_money`, reading the cell one column to the
        # left of this one, has warned about an unreadable value since it was
        # written. The rule was already in this loop, applied to one field.
        if stage not in stage_prob:
            result["off_stages"][stage] = result["off_stages"].get(stage, 0) + 1
            print(f"  Warning: deal {d.get('Company', '?')!r} has stage {stage!r}, "
                  f"which is not a canonical stage; weighted at 5% and drawn "
                  f"under 'Other'", file=sys.stderr)

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
    investors = parse_md_table(content, r"##\s*Investor Conversations", source=str(pipeline_file()))
    result["investors"] = investors
    result["total_investors"] = len(investors)

    # Partnership Discussions
    partnerships = parse_md_table(content, r"##\s*Partnership Discussions", source=str(pipeline_file()))
    result["partnerships"] = partnerships
    result["total_partnerships"] = len(partnerships)

    # Won / Closed
    won = parse_md_table(content, r"##\s*Won\s*/\s*Closed", source=str(pipeline_file()))
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


# A sync file older than this is reported as stale rather than read as fact.
# `/prime` has flagged these same two paths RED at 48 hours, under the literal
# label "SYNC STALE", since before this script existed.
SYNC_STALE_HOURS = 48


def sync_age_hours(sync_time):
    """Hours since a `> Synced: YYYY-MM-DD HH:MM [(Zone)]` stamp, or None.

    `sync-exchange` writes that stamp at the top of both files it produces, and
    both collectors below have parsed it out and rendered it since the initial
    import. NOTHING has ever compared it with the clock. So the page carried the
    age of its own inputs, in plain text, beside a table it presented as today's
    truth, and could not tell a sync from an hour ago apart from one from last
    week.

    Returns None when the stamp is missing or unreadable, which the callers
    render as "age unknown" rather than as fresh. A stamp that cannot be read is
    not evidence of freshness.
    """
    if not sync_time:
        return None
    m = re.match(r"\s*(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})", str(sync_time))
    if not m:
        return None
    try:
        stamped = datetime(
            *(int(p) for p in m.group(1).split("-")),
            int(m.group(2)), int(m.group(3)), tzinfo=get_default_tz(),
        )
    except ValueError:
        # The regex matches the SHAPE of a timestamp, not a real one, so
        # `2026-02-30 09:00` reaches here. One unreadable stamp degrades to
        # "unknown"; it never kills the run, the way an impossible
        # `Last verified:` date once killed the whole dashboard.
        return None
    return (NOW - stamped).total_seconds() / 3600.0


def collect_calendar():
    """Parse upcoming.md for today's meetings."""
    content = read_file(calendar_file())
    result = {"meetings": [], "sync_time": "", "date_str": TODAY.strftime("%Y-%m-%d"),
              "source_read": False, "age_hours": None}

    if not content:
        # An absent or empty file leaves `source_read` False, and `build_bridge`
        # says the source was not read. Until this flag existed the same empty
        # meeting list produced the sentence "No meetings scheduled today" - an
        # affirmative claim about the CEO's day, generated from a file nothing
        # had opened. `build_urgent` was hardened against exactly this shape one
        # panel to the left, through `crm["failed"]`, and this panel was not.
        return result
    result["source_read"] = True

    # Extract sync time
    sync_match = re.search(r"Synced:\s*(.+)", content)
    if sync_match:
        result["sync_time"] = sync_match.group(1).strip()
        result["age_hours"] = sync_age_hours(result["sync_time"])

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
        for m in parse_md_table(section, source=str(calendar_file())):
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
    content = read_file(email_file())
    result = {"emails": [], "sync_time": "", "count": 0,
              "source_read": False, "age_hours": None}

    if not content:
        # Same rule as the calendar beside it: an unread source is not an empty
        # inbox. "No recent emails" used to be printed either way.
        return result
    result["source_read"] = True

    sync_match = re.search(r"Synced:\s*(.+)", content)
    if sync_match:
        result["sync_time"] = sync_match.group(1).strip()
        result["age_hours"] = sync_age_hours(result["sync_time"])

    count_match = re.search(r"Count:\s*(\d+)", content)
    if count_match:
        result["count"] = int(count_match.group(1))

    emails = parse_md_table(content, source=str(email_file()))
    result["emails"] = emails
    return result


def collect_strategy():
    """Extract key strategic context."""
    content = read_file(strategy_file())
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


# Every figure section 06 renders, with the pattern that finds it in
# current-data.md. The value in the tuple is the last-known reading, kept so a
# file that cannot be read still draws a page; it is NOT a fallback the code
# quietly prefers, which is what the old comment called it.
_METRIC_PATTERNS = {
    "headcount": (r"Headcount.*?(\d+\+?)", "50+"),
    "hiring_target": (r"Hiring target.*?\|\s*~?(\d+)", "200"),
    "dpi_tam_2030": (r"Global market \(2030\)\s*\|\s*\*{0,2}(\$[\d.]+[KMB])", "$78.04B"),
    "cagr": (r"Global market \(2030\)\s*\|[^|]*\|\s*\*{0,2}([\d.]+%)", "22.05%"),
    "cis_2030": (r"CIS DPI market[^|]*\|[^|]*?(\$[\d.]+[KMB])\s*\|", "$1.15B"),
    # `exited`, not a bare `(\d+) countries`. The loose form matched "Across 14
    # countries" in the headcount row, thirty lines earlier in the file, and
    # would have printed 14 under the label "Incumbent Vacuum" - a NEW wrong
    # number introduced by the fix for a stale one. Caught by running the
    # pattern against the real file before shipping it.
    "predecessor_vacuum_countries": (r"exited\s+(\d+)\s+countries", "56"),
}


def collect_metrics():
    """The figures section 06 draws, read from current-data.md where they exist.

    This said "Extract key business metrics from current-data.md" and extracted
    exactly ONE of the fifteen values it returned. The other fourteen were
    literals in this file under a comment calling them a fallback, so the Market
    Context panel could not change when its stated source did: editing
    current-data.md, or deleting it outright, produced byte-identical output,
    while section 07 certified that same file green and "0d ago" underneath.

    Measured on 2026-08-28, every constant still equalled the file, so no number
    on the page was wrong that day. That is the whole danger of this shape: it
    is correct until the day it silently is not, and nothing anywhere would have
    said which day that was.

    A figure that cannot be found now says so by name, on stderr, and the page
    falls back to the last known reading rather than to a blank. Two of the
    patterns below read rows whose exact shape was not verified when they were
    written; if either guessed wrong, the warning fires on the first run rather
    than staying silent for a year.

    `mea_2030` stays a literal on purpose. Its row lives in the geography table,
    whose cells carry a bare `$3.47` where the global table writes `$78.04B`, and
    that table states no unit anywhere. Extracting it would render "$3.47" under
    the label "MEA 2030" - a wrong figure introduced by the fix for a stale one.
    The page will not guess a unit its source does not state.

    `modules_live`, `processing`, `countries`, the 2024 figures, and the two
    fundraising values stay literals with no pattern too: `modules_live` appears
    nowhere in current-data.md, and the rest are pre-existing keys that no
    builder on this page reads.
    """
    content = read_file(metrics_file())
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

    for field, (pattern, _known) in _METRIC_PATTERNS.items():
        m = re.search(pattern, content)
        if m:
            result[field] = m.group(1)
        else:
            print(f"  Warning: {field} not found in {metrics_file().name}; "
                  f"showing the last known reading {result[field]!r}",
                  file=sys.stderr)

    return result


def collect_freshness():
    """Check freshness markers on context files."""
    files_to_check = [
        ("pipeline.md", context_dir() / "pipeline.md"),
        ("current-data.md", context_dir() / "current-data.md"),
        ("strategy.md", context_dir() / "strategy.md"),
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
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            # The impossible-date branch below already refuses to let one bad
            # line in one of four files kill the whole run. An undecodable
            # context file did exactly that, by the other door and with no
            # handler at all: `read_text(encoding="utf-8")` raised
            # UnicodeDecodeError out of the loop and the CEO got no dashboard.
            # Measured 2026-09-01. One unreadable file degrades one row.
            print(f"  Warning: {name} is unreadable ({exc})", file=sys.stderr)
            result.append({"name": name, "date": None, "age": None,
                           "health": "gray"})
            continue
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


def freshness_summary(freshness) -> str:
    """One line naming every band that is not green.

    `collect_freshness` returns FOUR health values and the summary line used to
    read one of them: "all current" was printed whenever the RED count was zero.
    So a file 8 to 14 days old (yellow) reported as current, and so did a file
    with no `Last verified:` marker at all or an impossible date (gray) - which
    is the worse of the two, because nothing about that file was ever measured.
    The HTML panel has always drawn every row with its own dot; only this line,
    the one an operator reads in the terminal, collapsed three states into one
    word.

    A function rather than four lines inside `main()` so a test can call the
    thing that runs, instead of a copy of it that can drift and stay green.
    """
    stale = sum(1 for f in freshness if f["health"] == "red")
    ageing = sum(1 for f in freshness if f["health"] == "yellow")
    unmarked = sum(1 for f in freshness if f["health"] == "gray")
    if not (stale or ageing or unmarked):
        return f"all {len(freshness)} files current"
    parts = []
    if stale:
        parts.append(f"{stale} stale")
    if ageing:
        parts.append(f"{ageing} ageing")
    if unmarked:
        parts.append(f"{unmarked} with no readable marker")
    return f"{', '.join(parts)} of {len(freshness)} files"


def collect_hiring():
    """Parse hiring-pipeline.md for open roles and urgency."""
    content = read_file(hiring_file())
    result = {"p1": [], "p2": [], "p3": [], "urgent": [], "total": 0}

    if not content:
        return result

    # A `current_priority` state machine used to run over every line here,
    # assigning p1/p2/p3 and never reading the result. The three
    # parse_md_table calls below are what actually reads the sections.

    # Parse tables for each priority section
    p1 = parse_md_table(content, r"###\s*P1", source=str(hiring_file()))
    p2 = parse_md_table(content, r"###\s*P2", source=str(hiring_file()))
    p3 = parse_md_table(content, r"###\s*P3", source=str(hiring_file()))

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
    if newsletters_dir().exists():
        dated_dirs = []
        for d in newsletters_dir().iterdir():
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

    # LinkedIn: count posts in the last 7 days.
    #
    # The archive is counted too, and it is the half that matters. The panel's
    # target is "2+/week" PUBLISHED, and `/linkedin-archive` `git mv`s a post out
    # of linkedin_dir() the moment it goes live. So this counted staged drafts and
    # nothing else: publishing two posts moved the indicator from ON TRACK to
    # BEHIND, and the way to stay green was to leave work unpublished. `git mv`
    # is a rename, so the file keeps the mtime this window is measured against.
    week_ago = TODAY - timedelta(days=7)
    linkedin_count = 0
    any_source = False
    for ldir, files in ((linkedin_dir(), "flat"), (linkedin_drafts_dir(), "flat"),
                        (linkedin_archive_dir(), "deep")):
        if not ldir.exists():
            continue
        any_source = True
        # The archive nests one folder per slug, so a flat iterdir sees only
        # directories and counts nothing.
        entries = ldir.rglob("*.md") if files == "deep" else ldir.iterdir()
        for f in entries:
            if f.is_file():
                # Check by file modification date
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=get_default_tz()).date()
                if mtime >= week_ago:
                    linkedin_count += 1
    result["linkedin_count_week"] = linkedin_count
    # Only overwrite NO DATA when something was actually measured. This line ran
    # unconditionally, so the "NO DATA" the dict is initialised with was
    # unreachable for LinkedIn and a workspace with no content directory at all
    # reported BEHIND - a verdict on a cadence nothing had looked at. The
    # newsletter half of this same function has always guarded its own status
    # behind `if newsletters_dir().exists()`.
    if any_source:
        result["linkedin_status"] = "ON TRACK" if linkedin_count >= 2 else "BEHIND"

    return result


def collect_viraid():
    """Parse Viraid tasks and state for summary."""
    result = {
        "active_total": 0, "p1": 0, "p2": 0, "p3": 0,
        "aging": 0, "completion_rate": 0.0,
        "tasks": [],
        # Whether each of the two sources was actually read. Without these the
        # panel could not tell "read it, the answer is zero" from "never opened
        # it", and rendered the second as the first.
        "tasks_read": False, "rate_known": False,
    }

    # Parse tasks.md for active items
    tasks_content = read_file(viraid_tasks_file())
    if tasks_content:
        result["tasks_read"] = True
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
    if viraid_state_file().exists():
        try:
            state = json.loads(viraid_state_file().read_text(encoding="utf-8"))
            stats = state.get("stats", {})
            result["completion_rate"] = _as_percent(stats.get("completion_rate"))
            result["rate_known"] = True
        except (json.JSONDecodeError, UnicodeError, OSError) as e:
            # UnicodeError, because the decode happens inside `read_text` and a
            # UnicodeDecodeError is a ValueError, so neither named clause caught
            # it: the corrupt-state case this handler was written for escaped it
            # in the one shape that produces no JSON at all.
            #
            # The only handler in this file that said nothing at all. A corrupt
            # or unreadable state.json left `completion_rate` at its initialised
            # 0.0, which reached `{rate:.0f}%` and drew a measured-looking "0%"
            # on the CEO's page: a truncated write and a genuinely idle week
            # produced the same number, with nothing on stderr and nothing on
            # the page. Every sibling reader here degrades AND says so;
            # `_as_percent`, whose result this line assigns, warns on three
            # separate paths one function away.
            print(f"[generate-dashboard] viraid state.json unreadable "
                  f"({type(e).__name__}: {e}); completion rate unknown",
                  file=sys.stderr)

    return result


def collect_capture_payoff():
    """R10: daily /zk capture payoff. Counts signals captured in the last 7 days
    (knowledge notes + Odin episodes) and surfaces whether an episode cluster is
    ripe to promote to an Odin principle (reusing odin-cadence.py). Gives the
    weekly capture loop a DAILY surface so /zk capture visibly pays off.

    Degrades to {"available": False} when there is no Odin brain (e.g. an exec
    workspace), so the panel hides rather than erroring."""
    if not odin_brain_dir().exists():
        return {"available": False}

    cutoff = TODAY - timedelta(days=7)

    def _recent(md_path):
        try:
            fm, _ = _parse_fm(md_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            # Named, not swallowed. This was `except Exception: return False`
            # with no line printed, so a note this reader could not open was
            # dropped from "Signals Captured (7d)" in complete silence -- the
            # same undercount the `no readable date` warning fifteen lines down
            # exists to make visible, by the other door. A cp1251 note is the
            # ordinary way to hit it.
            print(f"[generate-dashboard] {md_path.name}: unreadable ({exc}); "
                  f"not counted as a captured signal", file=sys.stderr)
            return False
        unreadable = []
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
                #
                # Through the shared coercion, not `str(val)[:10]`. A blind
                # ten-character slice does not refuse a broken date, it INVENTS
                # one: MEASURED 2026-08-28, `"2026-08-25garbage"` read as
                # 2026-08-25 and the note was counted as a signal captured this
                # week. That is the only input on which the two disagree, and it
                # disagrees in the direction that inflates the number.
                if frontmatter_date(val) >= cutoff:
                    return True
            except ValueError:
                unreadable.append(f"{key}={val!r}")
                continue
        if unreadable:
            # Said, not swallowed. A note whose every date field is unreadable is
            # not counted, and the panel prints a number that looks measured.
            print(f"[generate-dashboard] {md_path.name}: no readable date "
                  f"({'; '.join(unreadable)}); not counted as a captured signal",
                  file=sys.stderr)
        return False

    signals = 0
    recent_titles = []
    for md in knowledge_dir().rglob("*.md"):
        if md.name.lower() in ("index.md", "readme.md", "templates.md"):
            continue
        if _recent(md):
            signals += 1
            if len(recent_titles) < 5:
                recent_titles.append(md.stem.replace("-", " "))

    promote_ready = last_collect = days_since = None
    # `returncode` was never read here, and the two other callers of this same
    # child both read it (`prime-health-parallel.run_odin_cadence`,
    # `ops_signals.odin_cadence_state`). A crashed helper writes its traceback
    # to stderr and leaves stdout EMPTY, which parsed to `{}` and set all three
    # cadence fields to None, which is the exact state the panel already uses
    # for "no cadence script on this workspace". MEASURED 2026-08-29: exit 1 drew
    # "Clusters to Promote: -" and "Since Last Harvest: -" with zero bytes on
    # stderr, so a dead helper and a quiet week were the same page. The shared
    # reader carries the check for both `--json` call sites now.
    cadence, cadence_error = read_cadence_json(
        WORKSPACE, script=ODIN_CADENCE_SCRIPT, timeout=30)
    if cadence_error:
        print(f"[generate-dashboard] odin cadence collect failed: {cadence_error}",
              file=sys.stderr)
    else:
        # A non-numeric value here used to reach `promote > 0` and
        # raise TypeError, uncaught, killing the whole dashboard -- and
        # only on the days when there WERE clusters to promote.
        promote_ready = _as_int_or_count(cadence.get("reflect_clusters"))
        last_collect = cadence.get("last_collect")
        # Through the same guard as `reflect_clusters` one line above.
        # Both come verbatim from `odin-cadence.py --json`, whose shape
        # `_as_int_or_count` documents as never promised -- and this one
        # reaches `days_since >= 7` in the BUILD phase, after every
        # collector has succeeded, so a string here killed the whole
        # dashboard with all of its data already in hand.
        days_since = _as_int_or_count(cadence.get("days_since"))

    return {
        "available": True,
        "signals_week": signals,
        "recent_titles": recent_titles,
        "promote_ready": promote_ready,
        "last_collect": last_collect,
        "days_since": days_since,
        # None when the helper is absent (an exec workspace, a legitimate
        # blank) and a reason string when it ran and failed. The panel needs
        # the difference: one is nothing to show, the other is a broken read.
        "cadence_error": cadence_error,
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


# How many of the synced emails the table shows. Named, because the number has
# to reach the label that admits to the cut.
EMAIL_PREVIEW_ROWS = 6


def _sync_label(name, source):
    """The panel's source line: when it synced, and whether that is stale.

    Both halves of this panel printed the sync stamp and neither ever compared
    it with the clock, so a file from last week and one from ten minutes ago
    rendered the same way.
    """
    if not source.get("source_read"):
        return (f'<div class="sync-label" style="color:var(--red);">{esc(name)} '
                f'&bull; NOT SYNCED</div>')
    age = source.get("age_hours")
    stamp = esc(source.get("sync_time") or "no timestamp")
    if age is None:
        return f'<div class="sync-label">{esc(name)} &bull; {stamp} (age unknown)</div>'
    if age >= SYNC_STALE_HOURS:
        return (f'<div class="sync-label" style="color:var(--red);">{esc(name)} '
                f'&bull; {stamp} &bull; STALE ({age / 24:.0f}d old)</div>')
    return f'<div class="sync-label">{esc(name)} &bull; {stamp}</div>'


def _empty_row(source, span, empty_text):
    """The row shown when a table has nothing in it.

    An unread source and an empty one are different facts and used to produce
    the same sentence. "No meetings scheduled today" is an affirmative claim
    about the CEO's day; generated from a file that was never opened, it is a
    false one. `build_urgent` refuses to print "All Clear" from an error path
    for exactly this reason, one panel above.
    """
    if not source.get("source_read"):
        return (f'<tr><td colspan="{span}" style="color:var(--red);font-style:italic;">'
                f'Source not synced &mdash; this panel is EMPTY, not clear.</td></tr>')
    return (f'<tr><td colspan="{span}" style="color:var(--ink35);font-style:italic;">'
            f'{empty_text}</td></tr>')


def build_bridge(calendar, emails):
    # Calendar table
    cal_rows = ""
    for m in calendar["meetings"]:
        time_val = m.get("Time", "")
        subject = m.get("Subject", "")
        duration = m.get("Duration", "")
        cal_rows += f"<tr><td>{esc(time_val)}</td><td>{esc(subject)}</td><td>{esc(duration)}</td></tr>\n"

    if not cal_rows:
        cal_rows = _empty_row(calendar, 3, "No meetings scheduled today")

    cal_sync = _sync_label("Calendar", calendar)

    # Email table
    email_rows = ""
    for e in emails["emails"][:EMAIL_PREVIEW_ROWS]:
        from_val = e.get("From", "")
        subject = e.get("Subject", "")
        read = e.get("Read", "")
        dot = '<span style="color:var(--orange);">&#9679;</span> ' if read.lower() == "no" else ""
        email_rows += f"<tr><td>{dot}{esc(from_val)}</td><td>{esc(subject[:50])}</td></tr>\n"

    # The cut, admitted. Six rows were drawn under a heading carrying no count,
    # so thirty synced emails and six read as the same inbox. `collect_emails`
    # has parsed the real figure out of the file's `Count:` header since it was
    # written and no builder had ever rendered it.
    shown = min(len(emails["emails"]), EMAIL_PREVIEW_ROWS)
    if len(emails["emails"]) > EMAIL_PREVIEW_ROWS:
        email_rows += (f'<tr><td colspan="2" style="color:var(--ink35);font-style:italic;">'
                       f'Showing {shown} of {len(emails["emails"])} synced'
                       f'</td></tr>\n')

    if not email_rows:
        email_rows = _empty_row(emails, 2, "No recent emails")

    email_sync = _sync_label("Email", emails)

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

    # A deal whose Stage is not one of the six was counted in Active Deals and
    # its money in Total Value, then drawn in no bar at all: the chart summed to
    # fewer deals than the number printed beside it, and nothing on the page
    # said a deal was missing. The bars iterate `canonical_order` alone, so the
    # off-list deals need a column of their own or they cannot appear.
    off_total = sum(pipeline.get("off_stages", {}).values())
    bar_stages = canonical_order + (["Other"] if off_total else [])
    counts = {s: raw_stages.get(s, 0) for s in canonical_order}
    counts["Other"] = off_total

    if not raw_stages:
        bars_html = '<div style="color:var(--ink35);font-style:italic;">No pipeline data available</div>'
    else:
        max_count = max(counts[s] for s in bar_stages)
        if max_count == 0:
            max_count = 1
        bars = []
        for stage in bar_stages:
            count = counts[stage]
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
    gray_count = len(crm["gray"])

    # The fourth bucket, finally on the page. `HEALTH_BUCKETS` has four values,
    # `health_bucket` routes every unrecognised health to gray precisely so a bad
    # value cannot vanish, and `result["total"]` counts all four - but only three
    # were ever drawn here or printed in the terminal summary. So the circles
    # summed to less than the total beside them, and the contacts whose health
    # could not be read, the ones a person most needs to see, were the ones
    # nothing showed. Drawn only when non-zero: an empty fourth circle would be
    # noise on the ordinary morning.
    #
    # Inline style rather than a `.radar-circle.n` class. The stylesheet lives in
    # the private data overlay, so a class would make this engine file render
    # correctly only on a workspace that also carries the matching CSS edit.
    gray_circle = ""
    if gray_count:
        gray_circle = f"""
    <div class="radar-circle" style="background:#f7f7f7;border:1px solid #dcdcdc;">
      <div class="radar-num" style="color:var(--ink55);">{gray_count}</div>
      <div class="radar-lbl" style="color:var(--ink55);">Health Unreadable</div>
    </div>"""

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
    </div>{gray_circle}
  </div>
  {contacts_table}
</div>
"""


def build_heading(strategy, pipeline, metrics, hiring):
    # Determine indicator states based on available data.
    #
    # Three of the five did. Indicators 4 and 5 were the literal "y" under this
    # comment: Hiring Momentum never consulted `collect_hiring()`, whose p1/p2/p3
    # and urgent lists are collected on every run and were reaching this page
    # only through section 08, and Fundraising Progress hardcoded its dot beside
    # an `investors_active` it had already computed for the text next to it. Two
    # amber lights that could not turn red however bad the underlying data got,
    # on a panel whose entire job is to say which way the ship is heading.
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

    # 4. Hiring momentum. An unfilled URGENT role is the drag this indicator
    # exists to show, and the status names the same numbers the state is read
    # from - a dot computed from one fact beside a caption quoting another is
    # the smaller version of the defect above.
    open_roles = hiring["total"]
    urgent_roles = len(hiring["urgent"])
    hire_state = "r" if urgent_roles else ("y" if open_roles else "g")
    # Plain text, no HTML entity: this string goes through `esc()` at the render
    # below, which would turn a `&bull;` into the literal characters "&bull;".
    hire_status = (f"{metrics['headcount']} of {metrics['hiring_target']}, "
                   f"{open_roles} open, {urgent_roles} urgent")
    indicators.append(("Hiring Momentum", hire_state, hire_status))

    # 5. Fundraising progress, on the same bands as Partner Channel Activation
    # above. Zero live investor conversations is a red state on this page, and
    # was drawn amber.
    investors_active = pipeline["total_investors"]
    fund_state = "g" if investors_active >= 3 else ("y" if investors_active >= 1 else "r")
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

    # LinkedIn indicator, on the same three states as the newsletter beside it.
    # This half had two and the collector could only ever hand it two, so a
    # workspace with no LinkedIn directory at all was told it was BEHIND on a
    # cadence nothing had measured.
    if li_status == "NO DATA":
        li_color = "var(--ink35)"
        li_detail = "No LinkedIn content directory found"
    else:
        li_color = "var(--green)" if li_status == "ON TRACK" else "var(--red)"
        li_detail = f"{li_count} posts/drafts this week"

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
      <div class="alert-sub" style="margin-top:6px;">{esc(li_detail)}</div>
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

    # `active == 0` was the test, so a tasks.md that was never opened and one
    # read to the end with nothing outstanding produced the same sentence - and
    # the branch also threw away `completion_rate`, which comes from a DIFFERENT
    # file and may have been read perfectly well. An empty in-tray is a result;
    # a file nobody opened is not.
    if not viraid.get("tasks_read"):
        body = '<div style="color:var(--ink35);font-style:italic;">No Viraid tasks data available</div>'
    else:
        aging_cls = "danger" if aging > 5 else ("accent" if aging > 0 else "up")
        # "0%" is a measurement. An unreadable state.json is not one, and drew
        # the same three characters.
        rate_str = f"{rate:.0f}%" if viraid.get("rate_known") else "-"

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
    <div class="metric-val">{rate_str}</div>
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
    cadence_error = payoff.get("cadence_error")

    sig_cls = "up" if signals > 0 else ""
    # "?" and not "-" when the cadence read FAILED. A dash is what this panel
    # draws for an exec workspace that has no cadence helper at all, so a
    # crashed helper used to borrow the look of a legitimate blank.
    unknown = "?" if cadence_error else "-"
    unread = bool(cadence_error)
    promote_val = unknown if promote is None else str(promote)
    if unread and promote is None:
        promote_cls = "danger"
    else:
        promote_cls = "accent" if (promote or 0) > 0 else ""
    collect_val = unknown if days_since is None else f"{days_since}d"
    if unread and days_since is None:
        collect_cls = "danger"
    else:
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

    # Named on the page, not only on stderr. The CEO reads the rendered file,
    # often hours after the run, and a cadence read that failed silently is a
    # nudge that did not fire: the "Since Last Harvest" box turns red at 7 days
    # and a dead helper never turned it any colour at all.
    if cadence_error:
        failure = ('<div style="margin-top:10px;color:var(--red);font-size:12px;">'
                   'Odin cadence unread: ' + esc(cadence_error)
                   + '. Clusters and last-harvest figures above are unknown, '
                     'not zero.</div>')
    else:
        failure = ""

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
{nudge}{failure}"""

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
    # blue_logo_b64 = load_logo_base64(logo_blue_path()) used to be read here.
    # Only the white logo is rendered, so this was a file read per run for a
    # value nothing used.
    white_logo_b64 = load_logo_base64(logo_white_path())
    gt_light_b64 = load_font_b64(gt_light_font())
    gt_medium_b64 = load_font_b64(gt_medium_font())
    css = build_css(gt_light_b64, gt_medium_b64)

    cover = build_cover(white_logo_b64)
    sections = [
        # Topbar sits on dark ink background -> use white logo.
        build_header(white_logo_b64),
        build_urgent(crm),
        build_bridge(calendar, emails),
        build_pipeline(pipeline),
        build_radar(crm),
        build_heading(strategy, pipeline, metrics, hiring),
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
    # Four buckets, four numbers. Printing three of them against a total that
    # counts all four made the line fail to add up on exactly the mornings when
    # a contact file was malformed.
    print(f"  CRM: {crm['total']} contacts ({len(crm['red'])} red, {len(crm['yellow'])} yellow, "
          f"{len(crm['green'])} green, {len(crm['gray'])} unreadable)")

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
    print(f"  Freshness: {freshness_summary(freshness)}")

    hiring = collect_hiring()
    print(f"  Hiring: {hiring['total']} open roles ({len(hiring['p1'])} P1, {len(hiring['p2'])} P2, {len(hiring['p3'])} P3), {len(hiring['urgent'])} urgent")

    content_cadence = collect_content_cadence()
    print(f"  Content: newsletter {content_cadence['newsletter_status']}, LinkedIn {content_cadence['linkedin_status']} ({content_cadence['linkedin_count_week']} this week)")

    viraid = collect_viraid()
    print(f"  Viraid: {viraid['active_total']} active tasks ({viraid['p1']} P1, {viraid['p2']} P2, {viraid['p3']} P3), {viraid['aging']} aging")

    capture_payoff = collect_capture_payoff()
    if capture_payoff.get("available"):
        promote_ready = capture_payoff.get("promote_ready")
        clusters = "unknown" if promote_ready is None else promote_ready
        line = (f"  Capture payoff: {capture_payoff['signals_week']} signals/7d, "
                f"{clusters} cluster(s) to promote")
        if capture_payoff.get("cadence_error"):
            line += f" [cadence unread: {capture_payoff['cadence_error']}]"
        print(line)

    print("\nGenerating HTML...")
    html_content = generate_html(crm, pipeline, calendar, emails, strategy, metrics, freshness,
                                 hiring, content_cadence, viraid, capture_payoff)
    html_path.write_text(html_content, encoding="utf-8")
    size = html_path.stat().st_size
    print(f"  Dashboard: {html_path}")
    print(f"  Size: {size:,} bytes")

    pdf_failed = False
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
            pdf_failed = True

    # A requested output that was not produced is a failed run. This printed
    # "Done." and returned 0 either way, so the only trace of a missing PDF was
    # one stderr line, and a cron entry or a wrapper script had no way at all to
    # tell a complete run from half of one.
    if pdf_failed:
        print("\nDone, WITHOUT the requested PDF.")
        return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    # `main()` alone. The return value was discarded, so the exit code was 0
    # whatever happened inside.
    sys.exit(main())
