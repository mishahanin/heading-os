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
"""

import argparse
import base64
import html
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.image import load_logo_base64
from scripts.utils.crm import parse_config as _crm_parse_config, scan_contacts as _crm_scan_contacts
from scripts.utils.workspace import (
    get_crm_config_path as _get_crm_config_path,
    get_outputs_dir,
    get_knowledge_dir,
    get_datastore_dir,
    get_context_dir,
    get_people_file,
)
from scripts.utils.markdown import parse_frontmatter as _parse_fm

# ============================================================
# Paths
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent

PIPELINE_FILE = get_context_dir() / "pipeline.md"
STRATEGY_FILE = get_context_dir() / "strategy.md"
METRICS_FILE = get_context_dir() / "current-data.md"
PEOPLE_FILE = get_people_file()
CALENDAR_FILE = get_outputs_dir() / "_sync" / "calendar" / "upcoming.md"
EMAIL_FILE = get_outputs_dir() / "_sync" / "emails" / "inbox-latest.md"
CRM_HEALTH_SCRIPT = SCRIPT_DIR / "crm-health.py"
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

TODAY = datetime.now().date()
NOW = datetime.now()

# Calendar times from Exchange are stored in UTC.
# Convert to CEO local timezone (the configured timezone = UTC+4).
CALENDAR_UTC_OFFSET_HOURS = 4


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


def parse_md_table(text, header_pattern=None):
    """Parse a markdown table into list of dicts. Optionally start from a line
    matching header_pattern."""
    lines = text.split("\n")
    start = 0
    if header_pattern:
        for i, line in enumerate(lines):
            if re.search(header_pattern, line):
                start = i
                break
        else:
            return []

    # Find header row (first line with |)
    headers = None
    data_start = None
    for i in range(start, min(start + 20, len(lines))):
        line = lines[i].strip()
        if "|" in line and "---" not in line and not headers:
            cells = [c.strip() for c in line.split("|")]
            headers = [c for c in cells if c]
            continue
        if headers and "---" in line:
            data_start = i + 1
            break

    if not headers or data_start is None:
        return []

    rows = []
    for i in range(data_start, len(lines)):
        line = lines[i].strip()
        if not line or not line.startswith("|"):
            if line.startswith("#") or line.startswith("---"):
                break
            if not line:
                continue
            break
        cells = [c.strip() for c in line.split("|")]
        cells = [c for c in cells if c != ""]
        if len(cells) >= len(headers):
            row = {}
            for j, h in enumerate(headers):
                row[h] = cells[j] if j < len(cells) else ""
            rows.append(row)
    return rows


# ============================================================
# Data Collectors
# ============================================================
def collect_crm_health():
    """Scan CRM contacts via in-process import (no subprocess overhead).

    Replicates the JSON envelope crm-health.py --json produced, then bins
    contacts into red/yellow/green/gray and surfaces commitments due in the
    next 7 days. Behaviour-preserving refactor: previously this shelled out
    to crm-health.py and re-parsed its JSON output.
    """
    result = {"contacts": [], "red": [], "yellow": [], "green": [], "gray": [],
              "commitments_due": [], "total": 0}
    try:
        config = _crm_parse_config(_get_crm_config_path())
        raw_contacts, _tribe_warnings, _dangling_refs, _stages, _aliases = _crm_scan_contacts(config, today=TODAY)

        # Normalise to the previous JSON-derived shape: due dates as ISO strings.
        contacts = []
        for c in raw_contacts:
            commits = []
            for cm in c.get("commitments", []):
                due_iso = cm["due"].strftime("%Y-%m-%d") if cm.get("due") else None
                commits.append({"text": cm["text"], "due": due_iso})
            contacts.append({
                "name": c["name"],
                "company": c["company"],
                "type": c["type"],
                "last_touch": c["last_touch"],
                "cadence": c["cadence"],
                "health": c["health"],
                "days_since": c["days_since"],
                "commitments": commits,
                "file": c["file"],
            })

        result["contacts"] = contacts
        result["total"] = len(contacts)
        for c in contacts:
            health = c.get("health", "gray")
            result[health].append(c)
            for commit in c.get("commitments", []):
                due = commit.get("due")
                if due:
                    try:
                        due_date = datetime.strptime(due, "%Y-%m-%d").date()
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

    # Pipeline Summary table (pre-computed totals as fallback)
    summary_rows = parse_md_table(content, r"##\s*Pipeline Summary")
    summary = {}
    for row in summary_rows:
        metric = row.get("Metric", "")
        value = row.get("Value", "")
        summary[metric] = value

    # Active Deals table
    deals = parse_md_table(content, r"##\s*Active Deals")
    result["deals"] = deals
    result["total_deals"] = len(deals)

    total_value = 0
    weighted_value = 0
    stale_count = 0
    deal_weighted = []

    for d in deals:
        stage = d.get("Stage", "Unknown").strip()
        result["stages"][stage] = result["stages"].get(stage, 0) + 1

        # Parse estimated value
        val_str = d.get("Est. Value", "").replace("$", "").replace(",", "").strip()
        try:
            val = int(val_str)
        except (ValueError, TypeError):
            val = 0
        total_value += val

        prob = stage_prob.get(stage, 0.05)
        w_val = val * prob
        weighted_value += w_val
        deal_weighted.append((d, w_val))

        # Stale detection: stage date > 14 days old
        stage_date_str = d.get("Stage Date", "").strip()
        if stage_date_str:
            try:
                sd = datetime.strptime(stage_date_str, "%Y-%m-%d").date()
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
    investors = parse_md_table(content, r"##\s*Investor Conversations")
    result["investors"] = investors
    result["total_investors"] = len(investors)

    # Partnership Discussions
    partnerships = parse_md_table(content, r"##\s*Partnership Discussions")
    result["partnerships"] = partnerships
    result["total_partnerships"] = len(partnerships)

    # Won / Closed
    won = parse_md_table(content, r"##\s*Won\s*/\s*Closed")
    result["won"] = won
    result["total_won"] = len(won)

    return result


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

    # Find today's section
    today_str = TODAY.strftime("%Y-%m-%d")
    today_pattern = rf"##\s*{re.escape(today_str)}"
    today_match = re.search(today_pattern, content)
    if not today_match:
        return result

    # Extract text from today's header to next date header
    rest = content[today_match.start():]
    next_header = re.search(r"\n##\s*\d{4}-\d{2}-\d{2}", rest[3:])
    if next_header:
        section = rest[:next_header.start() + 3]
    else:
        section = rest

    # Parse the meeting table in this section
    meetings = parse_md_table(section)

    # Convert meeting times from UTC to the configured local timezone
    for m in meetings:
        raw_time = m.get("Time", "").strip()
        if raw_time and re.match(r"\d{1,2}:\d{2}", raw_time):
            try:
                t = datetime.strptime(raw_time, "%H:%M")
                t += timedelta(hours=CALENDAR_UTC_OFFSET_HOURS)
                m["Time"] = t.strftime("%H:%M")
            except ValueError:
                pass

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

    emails = parse_md_table(content)
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
        ("people.md", CONTEXT_DIR / "people.md"),
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
            verified = datetime.strptime(date_str, "%Y-%m-%d").date()
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

    current_priority = None
    for line in content.split("\n"):
        # Detect priority headers
        if re.search(r"###\s*P1\b", line, re.IGNORECASE):
            current_priority = "p1"
            continue
        elif re.search(r"###\s*P2\b", line, re.IGNORECASE):
            current_priority = "p2"
            continue
        elif re.search(r"###\s*P3\b", line, re.IGNORECASE):
            current_priority = "p3"
            continue
        elif line.strip().startswith("##") and current_priority:
            current_priority = None
            continue

    # Parse tables for each priority section
    p1 = parse_md_table(content, r"###\s*P1")
    p2 = parse_md_table(content, r"###\s*P2")
    p3 = parse_md_table(content, r"###\s*P3")

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
                    dt = datetime.strptime(d.name, "%Y-%m-%d").date()
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
                    mtime = datetime.fromtimestamp(f.stat().st_mtime).date()
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
            if re.match(r"##\s*Completed", line, re.IGNORECASE):
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
                        task_date = datetime.strptime(date_match.group(1), "%Y-%m-%d").date()
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
            result["completion_rate"] = stats.get("completion_rate", 0.0)
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
                return datetime.fromisoformat(str(val)[:10]).date() >= cutoff
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
            promote_ready = data.get("reflect_clusters")
            last_collect = data.get("last_collect")
            days_since = data.get("days_since")
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

    return font_face + """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}

:root {
  /* Brand authority tokens (datastore/brand/, corporate-style-guide.md) */
  --bg-light:   #EEECEA;
  --bg-cover:   #000000;
  --card:       #FFFFFF;
  --ink:        #1A1A1A;
  --ink60:      #555555;
  --ink35:      #999999;
  --ink12:      #DDDDDD;
  --accent:     #5B5FFF;       /* Palatinate / signature blue */
  --accent-tint:#EEEEFF;
  --orange:     #F5922B;       /* Signature orange (corner block) */
  --orange-hi:  #FF8C00;
  --orlight:    #FDF1E2;
  --green:      #175C30;
  --red:        #AA2208;
  --yellow:     #B8860B;
  --font-heading: 'GT Standard', 'Inter', 'Segoe UI', Calibri, sans-serif;
  --font-body:    'GT Standard', 'Inter', 'Segoe UI', Calibri, sans-serif;
  --font-mono:    'JetBrains Mono', 'IBM Plex Mono', Consolas, monospace;
}

body {
  background: var(--bg-light);
  color: var(--ink);
  font-family: var(--font-body);
  font-weight: 300;
  font-size: 14px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.page {
  max-width: 900px;
  margin: 0 auto;
  background: var(--card);
  border-left: 1px solid var(--ink12);
  border-right: 1px solid var(--ink12);
  border-bottom: 1px solid var(--ink12);
}

/* === DARK COVER PAGE === */
.cover {
  position: relative;
  background: var(--bg-cover);
  color: #FFFFFF;
  min-height: 1080px;
  padding: 100px 80px 60px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  page-break-after: always;
  break-after: page;
  overflow: hidden;
}
.cover-corner {
  position: absolute;
  top: 0;
  left: 0;
  width: 56px;
  height: 68px;
  background: var(--orange);
}
.cover-accent {
  position: absolute;
  top: 110px;
  right: 80px;
  width: 14px;
  height: 14px;
  background: var(--accent);
}
.cover-inner {
  margin-top: 80px;
}
.cover-logo {
  height: 64px;
  display: block;
  margin-bottom: 80px;
  opacity: 0.95;
}
.cover-eyebrow {
  font-family: var(--font-heading);
  font-weight: 500;
  font-size: 12px;
  letter-spacing: 5px;
  text-transform: uppercase;
  color: rgba(255,255,255,0.6);
  margin-bottom: 20px;
}
.cover-title {
  font-family: var(--font-heading);
  font-weight: 500;
  font-size: 64px;
  line-height: 1.05;
  letter-spacing: -0.5px;
  margin-bottom: 28px;
}
.cover-title .one-blue { color: var(--accent); }
.cover-date {
  font-family: var(--font-heading);
  font-weight: 300;
  font-size: 22px;
  color: rgba(255,255,255,0.85);
  margin-bottom: 8px;
}
.cover-meta {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 1.8px;
  text-transform: uppercase;
  color: rgba(255,255,255,0.45);
}
.cover-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: rgba(255,255,255,0.4);
}
.cover-footer-marks {
  display: flex;
  align-items: center;
  gap: 14px;
}
.cover-footer-marks .sq {
  width: 10px;
  height: 10px;
  display: inline-block;
}
.cover-footer-marks .sq.blue { background: var(--accent); }
.cover-footer-marks .sq.orange { background: var(--orange); }

/* === TOP BAR (light content header with small orange corner) === */
.topbar {
  position: relative;
  background: var(--ink);
  padding: 14px 32px 14px 60px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.topbar::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 28px;
  height: 34px;
  background: var(--orange);
}
.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.logo-img { height: 22px; display: block; }
.topbar-title {
  font-family: var(--font-heading);
  font-weight: 500;
  font-size: 14px;
  letter-spacing: 4px;
  color: rgba(255,255,255,0.9);
  text-transform: uppercase;
}
.pulse {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--orange);
  flex-shrink: 0;
  animation: blink 2s ease-in-out infinite;
}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0.3}}
.topbar-right {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: rgba(255,255,255,0.45);
}

/* === DATE BAR (orange band - iconic) === */
.datebar {
  background: var(--orlight);
  border-bottom: 2px solid var(--orange);
  padding: 16px 32px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.datebar-date {
  font-family: var(--font-heading);
  font-weight: 500;
  font-size: 26px;
  letter-spacing: -0.2px;
  color: var(--ink);
}
.datebar-meta {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--ink60);
}

/* === SECTION HEADERS (blue accent on number, ink title) === */
.section {
  padding: 26px 32px;
  border-bottom: 1px solid var(--ink12);
}
.section:last-child { border-bottom: none; }
.section-num {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 500;
  letter-spacing: 2.8px;
  text-transform: uppercase;
  color: var(--accent);
  margin-bottom: 6px;
}
.section-title {
  font-family: var(--font-heading);
  font-weight: 500;
  font-size: 22px;
  letter-spacing: -0.2px;
  color: var(--ink);
  margin-bottom: 18px;
  border-bottom: 3px solid var(--accent);
  padding-bottom: 8px;
  display: inline-block;
}

/* === ALERT CARDS (semantic: red/yellow/green) === */
.alert-card {
  border-left: 4px solid var(--red);
  background: #fdf6f4;
  padding: 12px 16px;
  margin-bottom: 8px;
  border-radius: 0 4px 4px 0;
}
.alert-card.warn {
  border-left-color: var(--yellow);
  background: #fdfaf0;
}
.alert-card.ok {
  border-left-color: var(--green);
  background: #f4faf6;
}
.alert-label {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--red);
  margin-bottom: 3px;
}
.alert-card.warn .alert-label { color: var(--yellow); }
.alert-card.ok .alert-label { color: var(--green); }
.alert-text {
  font-family: var(--font-body);
  font-size: 14px;
  font-weight: 300;
  color: var(--ink);
}
.alert-text strong { font-weight: 500; }
.alert-sub {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink35);
  margin-top: 2px;
}

/* === TWO COLUMN LAYOUT === */
.two-col {
  display: flex;
  gap: 24px;
}
.col-left { flex: 3; min-width: 0; }
.col-right { flex: 2; min-width: 0; }

/* === DATA TABLE === */
.dtable {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.dtable th {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--ink60);
  text-align: left;
  padding: 8px 8px;
  border-bottom: 2px solid var(--accent);
}
.dtable td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--ink12);
  vertical-align: top;
  font-family: var(--font-body);
  font-weight: 300;
}
.dtable tr:last-child td { border-bottom: none; }

/* === SYNC LABEL === */
.sync-label {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: var(--ink35);
  margin-bottom: 8px;
}

/* === PIPELINE BARS === */
.bar-chart {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  height: 120px;
  padding-top: 10px;
  margin-bottom: 8px;
}
.bar-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  height: 100%;
}
.bar-fill {
  width: 100%;
  background: linear-gradient(180deg, var(--orange) 0%, #F8B36A 100%);
  border-radius: 3px 3px 0 0;
  min-height: 4px;
  position: relative;
}
.bar-count {
  font-family: var(--font-heading);
  font-weight: 500;
  font-size: 13px;
  color: var(--ink);
  margin-bottom: 4px;
}
.bar-label {
  font-family: var(--font-mono);
  font-size: 7px;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  color: var(--ink60);
  text-align: center;
  margin-top: 6px;
  line-height: 1.3;
  word-break: break-word;
}

/* === METRICS STRIP (blue top border per brand spec) === */
.metrics-strip {
  display: flex;
  gap: 0;
  border: 1px solid var(--ink12);
  border-top: 3px solid var(--accent);
  border-radius: 0 0 4px 4px;
  overflow: hidden;
  margin-top: 16px;
  background: var(--card);
}
.metric-box {
  flex: 1;
  padding: 14px 10px;
  text-align: center;
  border-right: 1px solid var(--ink12);
}
.metric-box:last-child { border-right: none; }
.metric-val {
  font-family: var(--font-heading);
  font-weight: 500;
  font-size: 26px;
  color: var(--ink);
  line-height: 1;
  letter-spacing: -0.5px;
}
.metric-val.up { color: var(--green); }
.metric-val.danger { color: var(--red); }
.metric-val.accent { color: var(--orange); }
.metric-label {
  font-family: var(--font-mono);
  font-size: 8px;
  letter-spacing: 1.2px;
  text-transform: uppercase;
  color: var(--ink60);
  margin-top: 6px;
}

/* === RADAR CIRCLES === */
.radar-row {
  display: flex;
  gap: 16px;
  margin-bottom: 20px;
}
.radar-circle {
  flex: 1;
  text-align: center;
  padding: 18px 8px;
  border-radius: 8px;
}
.radar-circle.r { background: #fdf6f4; border: 1px solid #f0d0c8; }
.radar-circle.y { background: #fdfaf0; border: 1px solid #ede0b8; }
.radar-circle.g { background: #f4faf6; border: 1px solid #c8e0d0; }
.radar-num {
  font-family: var(--font-heading);
  font-weight: 500;
  font-size: 42px;
  line-height: 1;
  letter-spacing: -1px;
}
.radar-circle.r .radar-num { color: var(--red); }
.radar-circle.y .radar-num { color: var(--yellow); }
.radar-circle.g .radar-num { color: var(--green); }
.radar-lbl {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 2px;
  text-transform: uppercase;
  margin-top: 6px;
}
.radar-circle.r .radar-lbl { color: var(--red); }
.radar-circle.y .radar-lbl { color: var(--yellow); }
.radar-circle.g .radar-lbl { color: var(--green); }

/* === HEADING INDICATORS === */
.heading-list { list-style: none; }
.heading-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 0;
  border-bottom: 1px solid var(--ink12);
  font-size: 14px;
  font-family: var(--font-body);
  font-weight: 300;
}
.heading-item:last-child { border-bottom: none; }
.heading-dot {
  width: 10px; height: 10px; border-radius: 50%;
  flex-shrink: 0;
}
.heading-dot.g { background: var(--green); }
.heading-dot.y { background: var(--yellow); }
.heading-dot.r { background: var(--red); }
.heading-dot.gray { background: var(--ink35); }
.heading-text { flex: 1; }
.heading-status {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 1px;
  text-transform: uppercase;
}

/* === MARKET STRIP (dark, orange values) === */
.market-strip {
  display: flex;
  gap: 0;
  background: var(--ink);
  border-radius: 4px;
  overflow: hidden;
  border-top: 3px solid var(--accent);
}
.market-item {
  flex: 1;
  padding: 16px 10px;
  text-align: center;
  border-right: 1px solid rgba(255,255,255,0.08);
}
.market-item:last-child { border-right: none; }
.market-val {
  font-family: var(--font-heading);
  font-weight: 500;
  font-size: 22px;
  color: var(--orange);
  line-height: 1;
  letter-spacing: -0.5px;
}
.market-label {
  font-family: var(--font-mono);
  font-size: 8px;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: rgba(255,255,255,0.5);
  margin-top: 5px;
}

/* === FRESHNESS TABLE === */
.fresh-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 5px 0;
  font-family: var(--font-mono);
  font-size: 11px;
}
.fresh-dot {
  width: 8px; height: 8px; border-radius: 50%;
  flex-shrink: 0;
}
.fresh-dot.g { background: var(--green); }
.fresh-dot.y { background: var(--yellow); }
.fresh-dot.r { background: var(--red); }
.fresh-dot.gray { background: var(--ink35); }
.fresh-name { flex: 1; color: var(--ink60); }
.fresh-age { color: var(--ink35); width: 80px; text-align: right; }

/* === FOOTER (dark, with brand marks) === */
.footer {
  background: var(--ink);
  padding: 16px 32px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}
.footer-marks {
  display: flex;
  align-items: center;
  gap: 10px;
}
.footer-marks .sq {
  width: 10px;
  height: 10px;
  display: inline-block;
}
.footer-marks .sq.blue { background: var(--accent); }
.footer-marks .sq.orange { background: var(--orange); }
.footer-left {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: rgba(255,255,255,0.4);
  flex: 1;
}
.footer-right {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--orange);
}

/* === RESPONSIVE === */
@media (max-width: 640px) {
  .page { border: none; }
  .cover { padding: 60px 32px 40px; min-height: 760px; }
  .cover-title { font-size: 44px; }
  .two-col { flex-direction: column; }
  .metrics-strip, .market-strip { flex-wrap: wrap; }
  .metric-box, .market-item { min-width: 33%; }
  .radar-row { flex-wrap: wrap; }
  .radar-circle { min-width: 30%; }
  .bar-chart { height: 80px; }
}

/* === PRINT (PDF) === */
@media print {
  body { background: white; }
  .page { max-width: 100%; border: none; box-shadow: none; }
  .pulse { animation: none; opacity: 1; }
  .cover { min-height: 100vh; page-break-after: always; }
  @page { size: A4; margin: 0; }
}
"""


# ============================================================
# Rendering / HTML Section Builders
# ============================================================
def build_cover(white_logo_b64):
    """Dark Palantinate-Blue / black cover page with white logo and orange corner.
    Brand spec: reference/corporate-style-guide.md (Signature Brand Elements).
    """
    date_long = NOW.strftime("%A, %B %d, %Y")
    time_str = NOW.strftime("%H:%M")
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
    <div class="cover-meta">Generated {esc(time_str)} &middot; the configured timezone &middot; Internal &mdash; CEO Eyes Only</div>
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
  <div class="datebar-meta">Generated {esc(time_str)} (the configured timezone)</div>
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
  <div class="alert-sub">Last touch: {esc(days_str)} &bull; Cadence: {c.get('cadence', '?')} days</div>
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

    if not items_html:
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
        nl_cls = "ok" if nl_status == "ON TRACK" else ""
        nl_color = "var(--green)" if nl_status == "ON TRACK" else "var(--red)"
        nl_detail = f"Last issue: {esc(nl_last)} ({nl_days}d ago)"
    else:
        nl_cls = ""
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
    blue_logo_b64 = load_logo_base64(LOGO_BLUE_PATH)
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
