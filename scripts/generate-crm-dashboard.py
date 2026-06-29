#!/usr/bin/env python3
"""
31C CRM Command Center Dashboard Generator

Aggregates company-wide CRM data from crm-central (company radar, ownership map,
shared contacts, exec registry, pipeline correlation) into a single-page HTML
dashboard. Self-contained (inline CSS, base64 logo, no external dependencies
beyond Google Fonts).

Usage:
    python scripts/generate-crm-dashboard.py                  # HTML only
    python scripts/generate-crm-dashboard.py --pdf            # HTML + PDF
    python scripts/generate-crm-dashboard.py --json           # raw data as JSON
    python scripts/generate-crm-dashboard.py --output-dir DIR # custom output dir
"""

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.image import load_logo_base64
from scripts.utils.workspace import (
    get_workspace_root,
    get_crm_contacts_dir,
    get_context_dir,
    get_datastore_dir,
    get_outputs_dir,
)
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET

# ============================================================
# Paths
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent

AGGREGATED_DIR = get_crm_contacts_dir().parent / "aggregated"
AGGREGATE_SCRIPT = SCRIPT_DIR / "aggregate-crm.py"
HTML_TO_PDF_SCRIPT = SCRIPT_DIR / "html-to-pdf.py"

COMPANY_RADAR_FILE = AGGREGATED_DIR / "company-radar.md"
OWNERSHIP_MAP_FILE = AGGREGATED_DIR / "ownership-map.md"
SHARED_CONTACTS_FILE = AGGREGATED_DIR / "shared-contacts.md"
EXEC_REGISTRY_FILE = WORKSPACE / "config" / "exec-registry.json"
PIPELINE_FILE = get_context_dir() / "pipeline.md"

LOGO_PATH = (
    get_datastore_dir() / "brand" / "assets"
    / "logos" / "31C_Logo_White_Color.png"
)

TODAY = datetime.now().date()
NOW = datetime.now()


# ============================================================
# Utilities
# ============================================================
def esc(text):
    """HTML-escape a string, returning empty string for None/empty."""
    if not text:
        return ""
    return html.escape(str(text))


def read_file(path):
    """Read a file and return its content, or empty string if missing."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def parse_md_table(text, header_pattern=None):
    """Parse a markdown table into a list of dicts.

    Optionally start searching from a line matching header_pattern.
    Handles missing columns gracefully.
    """
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
        row = {}
        for j, h in enumerate(headers):
            row[h] = cells[j] if j < len(cells) else ""
        rows.append(row)
    return rows


def count_files_in_dir(dirpath):
    """Count .md files in a directory (non-recursive)."""
    if not dirpath.exists():
        return 0
    return sum(1 for f in dirpath.iterdir() if f.is_file() and f.suffix == ".md")


# ============================================================
# Data Collectors
# ============================================================
def refresh_aggregated_data():
    """Run aggregate-crm.py to refresh crm-central data."""
    if not AGGREGATE_SCRIPT.exists():
        print(f"  {YELLOW}Warning: aggregate-crm.py not found, using cached data{RESET}",
              file=sys.stderr)
        return False
    try:
        proc = subprocess.run(
            [sys.executable, str(AGGREGATE_SCRIPT)],
            capture_output=True, text=True, timeout=30
        )
        if proc.returncode == 0:
            return True
        print(f"  {YELLOW}Warning: aggregate-crm.py returned {proc.returncode}{RESET}",
              file=sys.stderr)
        if proc.stderr.strip():
            print(f"  {proc.stderr.strip()}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  {YELLOW}Warning: aggregate-crm.py failed: {e}{RESET}", file=sys.stderr)
        return False


def collect_radar():
    """Parse company-radar.md for all contacts with health status."""
    content = read_file(COMPANY_RADAR_FILE)
    if not content:
        return []
    rows = parse_md_table(content)
    contacts = []
    for r in rows:
        health = r.get("Health", "GRAY").strip().upper()
        name = r.get("Name", "").strip()
        company = r.get("Company", "").strip()
        ctype = r.get("Type", "").strip()
        owner = r.get("Owner", "").strip()
        last_touch = r.get("Last Touch", "").strip()
        cadence = r.get("Cadence", "").strip()

        days_since = None
        if last_touch:
            try:
                lt_date = datetime.strptime(last_touch, "%Y-%m-%d").date()
                days_since = (TODAY - lt_date).days
            except ValueError:
                pass

        contacts.append({
            "name": name,
            "company": company,
            "type": ctype,
            "owner": owner,
            "last_touch": last_touch,
            "days_since": days_since,
            "cadence": cadence,
            "health": health,
        })
    return contacts


def collect_ownership(exec_registry):
    """Parse ownership-map.md for per-exec stats."""
    content = read_file(OWNERSHIP_MAP_FILE)
    execs = []

    if not content:
        return execs

    # Parse each exec section
    current_exec = None
    for line in content.split("\n"):
        # Match exec header: ## Name (`slug`)
        m = re.match(r"^##\s+(.+?)\s+\(`([^`]+)`\)", line)
        if m:
            if current_exec:
                execs.append(current_exec)
            name = m.group(1).strip()
            slug = m.group(2).strip()
            # Look up title from registry
            title = ""
            for ex in exec_registry.get("executives", []):
                if ex.get("slug") == slug:
                    title = ex.get("title", "")
                    break
            current_exec = {
                "name": name, "slug": slug, "title": title,
                "total": 0, "red": 0, "yellow": 0, "green": 0, "gray": 0,
                "types": {}, "contacts": [],
            }
            continue

        if current_exec is None:
            continue

        # Match health summary: - **Health:** X red, Y yellow, Z green, W gray
        hm = re.match(r"^-\s+\*\*Health:\*\*\s+(.*)", line)
        if hm:
            health_str = hm.group(1)
            for color in ["red", "yellow", "green", "gray"]:
                cm = re.search(rf"(\d+)\s+{color}", health_str)
                if cm:
                    current_exec[color] = int(cm.group(1))
            continue

        # Match total contacts: - **Total contacts:** N
        tm = re.match(r"^-\s+\*\*Total contacts:\*\*\s+(\d+)", line)
        if tm:
            current_exec["total"] = int(tm.group(1))
            continue

    if current_exec:
        execs.append(current_exec)

    return execs


def collect_shared_contacts():
    """Parse shared-contacts.md for contacts tracked by multiple execs."""
    content = read_file(SHARED_CONTACTS_FILE)
    if not content:
        return []
    if "No shared contacts detected" in content:
        return []
    return parse_md_table(content)


def collect_exec_registry():
    """Load exec registry JSON."""
    if not EXEC_REGISTRY_FILE.exists():
        return {"version": "1.0", "executives": []}
    try:
        return json.loads(EXEC_REGISTRY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": "1.0", "executives": []}


def collect_heartbeat():
    """Count contact files per exec by reading per-exec CRM repos."""
    from scripts.utils.workspace import get_all_active_exec_slugs, get_per_exec_repo_path
    heartbeat = {}
    try:
        for slug in get_all_active_exec_slugs():
            repo_path = get_per_exec_repo_path(slug)
            contacts_dir = repo_path / "contacts"
            heartbeat[slug] = count_files_in_dir(contacts_dir)
    except (OSError, ImportError, KeyError, ValueError) as e:
        # Best-effort per-exec heartbeat; return whatever was collected but surface why.
        print(f"[generate-crm-dashboard] heartbeat collect failed: {e}", file=sys.stderr)
    return heartbeat


def collect_pipeline_companies():
    """Parse pipeline.md to extract company names for correlation."""
    content = read_file(PIPELINE_FILE)
    if not content:
        return []
    deals = parse_md_table(content, r"##\s*Active Deals")
    companies = []
    for d in deals:
        company = d.get("Company", "").strip()
        if company:
            companies.append({
                "company": company,
                "country": d.get("Country", "").strip(),
                "stage": d.get("Stage", "").strip(),
                "value": d.get("Est. Value", "").strip(),
                "owner": d.get("Owner", "").strip(),
            })
    return companies


def correlate_pipeline_crm(radar_contacts, pipeline_companies):
    """Match CRM contacts by company against pipeline deals."""
    matches = []
    seen_companies = set()
    for deal in pipeline_companies:
        deal_company_lower = deal["company"].lower()
        for contact in radar_contacts:
            contact_company_lower = contact["company"].lower()
            if not contact_company_lower or not deal_company_lower:
                continue
            # Check if the CRM company appears in the deal company or vice versa
            if (contact_company_lower in deal_company_lower
                    or deal_company_lower in contact_company_lower):
                key = (deal["company"], contact["name"])
                if key not in seen_companies:
                    seen_companies.add(key)
                    matches.append({
                        "deal_company": deal["company"],
                        "contact_name": contact["name"],
                        "contact_company": contact["company"],
                        "stage": deal["stage"],
                        "value": deal["value"],
                        "crm-health": contact["health"],
                        "crm_owner": contact["owner"],
                        "deal_owner": deal["owner"],
                    })
    return matches


# ============================================================
# CSS
# ============================================================
def build_css():
    return """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}

:root {
  --bg: #0d0f11; --surface: #161a1e; --surface-raised: #1c2127;
  --text: #e8e8ed; --text-secondary: #8b8fa3; --accent: #5B5FFF;
  --red: #ef4444; --yellow: #f59e0b; --green: #22c55e; --gray: #6b7280;
  --orange: #FF8C00; --border: #2a2e35;
}

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Inter', -apple-system, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

.page {
  max-width: 1100px;
  margin: 0 auto;
  padding: 0;
}

/* HEADER */
.header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 20px 32px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}
.header-logo { height: 28px; }
.header-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--text);
  letter-spacing: -0.3px;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.header-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}
.badge-accent { background: rgba(91,95,255,0.15); color: var(--accent); }
.badge-muted { background: rgba(139,143,163,0.12); color: var(--text-secondary); }
.header-date {
  font-size: 12px;
  color: var(--text-secondary);
  text-align: right;
  line-height: 1.4;
}

/* HEALTH SUMMARY CARDS */
.health-row {
  display: flex;
  gap: 16px;
  padding: 24px 32px;
}
.health-card {
  flex: 1;
  background: var(--surface);
  border-radius: 8px;
  padding: 20px 16px;
  text-align: center;
  border-top: 3px solid var(--gray);
}
.health-card.red { border-top-color: var(--red); }
.health-card.yellow { border-top-color: var(--yellow); }
.health-card.green { border-top-color: var(--green); }
.health-card.gray { border-top-color: var(--gray); }
.health-num {
  font-size: 36px;
  font-weight: 700;
  line-height: 1;
}
.health-card.red .health-num { color: var(--red); }
.health-card.yellow .health-num { color: var(--yellow); }
.health-card.green .health-num { color: var(--green); }
.health-card.gray .health-num { color: var(--gray); }
.health-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 1.2px;
  text-transform: uppercase;
  color: var(--text-secondary);
  margin-top: 6px;
}

/* SECTIONS */
.section {
  padding: 24px 32px;
  border-top: 1px solid var(--border);
}
.section-title {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--text-secondary);
  margin-bottom: 16px;
}

/* EXEC SCORECARD GRID */
.exec-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.exec-card {
  background: var(--surface);
  border-radius: 8px;
  padding: 16px 20px;
  border-left: 4px solid var(--green);
}
.exec-card.warn { border-left-color: var(--yellow); }
.exec-card.danger { border-left-color: var(--red); }
.exec-name {
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
}
.exec-title {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
}
.exec-stats {
  display: flex;
  gap: 12px;
  margin-top: 10px;
  font-size: 12px;
}
.exec-stat {
  display: flex;
  align-items: center;
  gap: 4px;
}
.exec-stat .dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.dot-red { background: var(--red); }
.dot-yellow { background: var(--yellow); }
.dot-green { background: var(--green); }
.dot-gray { background: var(--gray); }
.exec-overdue {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  color: var(--text-secondary);
}
.exec-overdue-name {
  color: var(--red);
  font-weight: 500;
}

/* TABLE */
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.data-table th {
  text-align: left;
  padding: 10px 12px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: var(--text-secondary);
  border-bottom: 2px solid var(--border);
  background: var(--surface);
}
.data-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  color: var(--text);
}
.data-table tr:hover td {
  background: rgba(91,95,255,0.04);
}

/* HEALTH BADGE */
.badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}
.badge-red { background: rgba(239,68,68,0.15); color: var(--red); }
.badge-yellow { background: rgba(245,158,11,0.15); color: var(--yellow); }
.badge-green { background: rgba(34,197,94,0.15); color: var(--green); }
.badge-gray { background: rgba(107,114,128,0.15); color: var(--gray); }

/* HORIZONTAL BAR */
.bar-container {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.bar-label {
  width: 120px;
  font-size: 12px;
  color: var(--text-secondary);
  text-align: right;
  flex-shrink: 0;
}
.bar-track {
  flex: 1;
  height: 22px;
  background: var(--surface);
  border-radius: 4px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 4px;
  min-width: 2px;
  display: flex;
  align-items: center;
  padding-left: 8px;
}
.bar-value {
  font-size: 11px;
  font-weight: 600;
  color: #fff;
}

/* FOOTER */
.footer {
  background: var(--surface);
  border-top: 1px solid var(--border);
  padding: 16px 32px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.footer-text {
  font-size: 11px;
  color: var(--text-secondary);
  letter-spacing: 0.5px;
}
.footer-class {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--red);
}

/* EMPTY STATE */
.empty {
  color: var(--text-secondary);
  font-style: italic;
  padding: 16px 0;
}

/* TRUNCATE NOTE */
.truncate-note {
  font-size: 12px;
  color: var(--text-secondary);
  font-style: italic;
  padding-top: 8px;
}

/* RESPONSIVE */
@media (max-width: 700px) {
  .health-row { flex-direction: column; }
  .exec-grid { grid-template-columns: 1fr; }
  .header { flex-direction: column; gap: 12px; }
}

/* PRINT */
@media print {
  body { background: #0d0f11; }
  .page { max-width: 100%; }
  @page { size: A4 landscape; margin: 8mm; }
}
"""


# ============================================================
# Rendering / HTML Section Builders
# ============================================================
def build_header(logo_b64, exec_count, total_contacts):
    date_long = NOW.strftime("%A, %B %d, %Y")
    time_str = NOW.strftime("%H:%M")
    logo_html = ""
    if logo_b64:
        logo_html = f'<img class="header-logo" src="{logo_b64}" alt="31C"/>'
    return f"""
<div class="header">
  <div class="header-left">
    {logo_html}
    <span class="header-title">CRM Command Center</span>
  </div>
  <div class="header-right">
    <span class="header-badge badge-accent">{exec_count} Exec{"s" if exec_count != 1 else ""}</span>
    <span class="header-badge badge-muted">{total_contacts} Contacts</span>
    <div class="header-date">{esc(date_long)}<br/>{esc(time_str)} (the configured timezone)</div>
  </div>
</div>
"""


def build_health_summary(contacts):
    counts = {"RED": 0, "YELLOW": 0, "GREEN": 0, "GRAY": 0}
    for c in contacts:
        h = c["health"].upper()
        if h in counts:
            counts[h] += 1
    return f"""
<div class="health-row">
  <div class="health-card red">
    <div class="health-num">{counts['RED']}</div>
    <div class="health-label">Red - Overdue</div>
  </div>
  <div class="health-card yellow">
    <div class="health-num">{counts['YELLOW']}</div>
    <div class="health-label">Yellow - Due Soon</div>
  </div>
  <div class="health-card green">
    <div class="health-num">{counts['GREEN']}</div>
    <div class="health-label">Green - Healthy</div>
  </div>
  <div class="health-card gray">
    <div class="health-num">{counts['GRAY']}</div>
    <div class="health-label">Gray - No Cadence</div>
  </div>
</div>
"""


def build_exec_scorecards(ownership_data, radar_contacts, heartbeat):
    if not ownership_data:
        return """
<div class="section">
  <div class="section-title">Executive Scorecards</div>
  <div class="empty">No executive ownership data available.</div>
</div>
"""
    cards_html = ""
    for ex in ownership_data:
        red_count = ex["red"]
        # Determine card severity
        if red_count > 5:
            card_cls = "danger"
        elif red_count >= 3:
            card_cls = "warn"
        else:
            card_cls = ""

        # Get contact count from heartbeat
        file_count = heartbeat.get(ex["slug"], ex["total"])

        # Find top 3 overdue contacts for this exec
        overdue = []
        for c in radar_contacts:
            if (c["health"] == "RED"
                    and c["owner"]
                    and ex["name"].split()[-1].lower() in c["owner"].lower()):
                overdue.append(c["name"])
                if len(overdue) >= 3:
                    break

        overdue_html = ""
        if overdue:
            names = ", ".join(
                f'<span class="exec-overdue-name">{esc(n)}</span>' for n in overdue
            )
            overdue_html = f'<div class="exec-overdue">Overdue: {names}</div>'

        title_html = ""
        if ex["title"]:
            title_html = f'<div class="exec-title">{esc(ex["title"])}</div>'

        cards_html += f"""
<div class="exec-card {card_cls}">
  <div class="exec-name">{esc(ex['name'])}</div>
  {title_html}
  <div class="exec-stats">
    <div class="exec-stat"><span class="dot dot-red"></span> {ex['red']}</div>
    <div class="exec-stat"><span class="dot dot-yellow"></span> {ex['yellow']}</div>
    <div class="exec-stat"><span class="dot dot-green"></span> {ex['green']}</div>
    <div class="exec-stat"><span class="dot dot-gray"></span> {ex['gray']}</div>
    <div class="exec-stat" style="margin-left:auto;color:var(--text-secondary);">{file_count} contacts</div>
  </div>
  {overdue_html}
</div>
"""

    return f"""
<div class="section">
  <div class="section-title">Executive Scorecards</div>
  <div class="exec-grid">{cards_html}</div>
</div>
"""


def build_radar_table(contacts, limit=50):
    if not contacts:
        return """
<div class="section">
  <div class="section-title">Company-Wide Radar</div>
  <div class="empty">No radar data available.</div>
</div>
"""
    # Sort: RED first, then YELLOW, GREEN, GRAY
    order = {"RED": 0, "YELLOW": 1, "GREEN": 2, "GRAY": 3}
    sorted_contacts = sorted(contacts, key=lambda c: (order.get(c["health"], 4), -(c["days_since"] or 0)))

    total = len(sorted_contacts)
    display = sorted_contacts[:limit]
    rows_html = ""
    for c in display:
        badge_cls = {
            "RED": "badge-red", "YELLOW": "badge-yellow",
            "GREEN": "badge-green", "GRAY": "badge-gray",
        }.get(c["health"], "badge-gray")
        days_str = str(c["days_since"]) if c["days_since"] is not None else "-"
        rows_html += f"""
<tr>
  <td>{esc(c['name'])}</td>
  <td>{esc(c['company'])}</td>
  <td>{esc(c['type'])}</td>
  <td>{esc(c['owner'])}</td>
  <td style="text-align:right;">{esc(days_str)}</td>
  <td><span class="badge {badge_cls}">{esc(c['health'])}</span></td>
</tr>"""

    truncate_html = ""
    remaining = total - limit
    if remaining > 0:
        truncate_html = f'<div class="truncate-note">...and {remaining} more contacts</div>'

    return f"""
<div class="section">
  <div class="section-title">Company-Wide Radar</div>
  <table class="data-table">
    <thead><tr>
      <th>Contact</th><th>Company</th><th>Type</th><th>Owner</th>
      <th style="text-align:right;">Days Since</th><th>Health</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
  {truncate_html}
</div>
"""


def build_shared_contacts(shared):
    if not shared:
        return """
<div class="section">
  <div class="section-title">Shared Contacts</div>
  <div class="empty">No shared contacts detected.</div>
</div>
"""
    rows_html = ""
    for s in shared:
        rows_html += "<tr>"
        for key in s:
            rows_html += f"<td>{esc(s[key])}</td>"
        rows_html += "</tr>"

    headers_html = ""
    if shared:
        for key in shared[0]:
            headers_html += f"<th>{esc(key)}</th>"

    return f"""
<div class="section">
  <div class="section-title">Shared Contacts</div>
  <table class="data-table">
    <thead><tr>{headers_html}</tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>
"""


def build_type_distribution(contacts):
    if not contacts:
        return """
<div class="section">
  <div class="section-title">Type Distribution</div>
  <div class="empty">No data available.</div>
</div>
"""
    type_counts = {}
    for c in contacts:
        t = c["type"] if c["type"] else "unknown"
        type_counts[t] = type_counts.get(t, 0) + 1

    # Sort by count descending
    sorted_types = sorted(type_counts.items(), key=lambda x: -x[1])
    max_count = sorted_types[0][1] if sorted_types else 1

    bars_html = ""
    for t, count in sorted_types:
        pct = (count / max_count) * 100 if max_count > 0 else 0
        bars_html += f"""
<div class="bar-container">
  <div class="bar-label">{esc(t)}</div>
  <div class="bar-track">
    <div class="bar-fill" style="width:{pct:.0f}%;">
      <span class="bar-value">{count}</span>
    </div>
  </div>
</div>"""

    return f"""
<div class="section">
  <div class="section-title">Type Distribution</div>
  {bars_html}
</div>
"""


def build_top_overdue(contacts, limit=15):
    # Filter to only contacts with days_since and RED/YELLOW health
    overdue = [
        c for c in contacts
        if c["days_since"] is not None and c["health"] in ("RED", "YELLOW")
    ]
    overdue.sort(key=lambda c: -(c["days_since"] or 0))
    display = overdue[:limit]

    if not display:
        return """
<div class="section">
  <div class="section-title">Top 15 Overdue</div>
  <div class="empty">No overdue contacts.</div>
</div>
"""
    rows_html = ""
    for c in display:
        rows_html += f"""
<tr>
  <td>{esc(c['name'])}</td>
  <td>{esc(c['company'])}</td>
  <td>{esc(c['owner'])}</td>
  <td style="text-align:right;color:var(--red);font-weight:600;">{c['days_since']}d</td>
</tr>"""

    return f"""
<div class="section">
  <div class="section-title">Top {min(limit, len(display))} Overdue</div>
  <table class="data-table">
    <thead><tr>
      <th>Contact</th><th>Company</th><th>Owner</th>
      <th style="text-align:right;">Days Overdue</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>
"""


def build_pipeline_correlation(correlations):
    if not correlations:
        return """
<div class="section">
  <div class="section-title">Pipeline Correlation</div>
  <div class="empty">No matching companies found between CRM and pipeline.</div>
</div>
"""
    rows_html = ""
    for m in correlations:
        badge_cls = {
            "RED": "badge-red", "YELLOW": "badge-yellow",
            "GREEN": "badge-green", "GRAY": "badge-gray",
        }.get(m["crm-health"], "badge-gray")
        rows_html += f"""
<tr>
  <td>{esc(m['deal_company'])}</td>
  <td>{esc(m['contact_name'])}</td>
  <td>{esc(m['stage'])}</td>
  <td>{esc(m['value'])}</td>
  <td><span class="badge {badge_cls}">{esc(m['crm-health'])}</span></td>
</tr>"""

    return f"""
<div class="section">
  <div class="section-title">Pipeline Correlation</div>
  <table class="data-table">
    <thead><tr>
      <th>Deal / Company</th><th>CRM Contact</th><th>Stage</th>
      <th>Est. Value</th><th>CRM Health</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>
"""


def build_footer():
    return f"""
<div class="footer">
  <div class="footer-text">31C CRM Command Center | Generated {esc(NOW.strftime("%Y-%m-%d %H:%M"))}</div>
  <div class="footer-class">Internal - CEO Eyes Only</div>
</div>
"""


# ============================================================
# Full HTML Assembly
# ============================================================
def generate_html(radar_contacts, ownership_data, shared, heartbeat,
                  exec_registry, pipeline_correlations):
    css = build_css()
    logo_b64 = load_logo_base64(LOGO_PATH)

    exec_count = len(exec_registry.get("executives", []))
    total_contacts = len(radar_contacts)

    sections = [
        build_header(logo_b64, exec_count, total_contacts),
        build_health_summary(radar_contacts),
        build_exec_scorecards(ownership_data, radar_contacts, heartbeat),
        build_radar_table(radar_contacts, limit=50),
        build_shared_contacts(shared),
        build_type_distribution(radar_contacts),
        build_top_overdue(radar_contacts, limit=15),
        build_pipeline_correlation(pipeline_correlations),
        build_footer(),
    ]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>31C CRM Command Center - {esc(TODAY.strftime("%Y-%m-%d"))}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet"/>
<style>{css}</style>
</head>
<body>
<div class="page">
{"".join(sections)}
</div>
</body>
</html>"""


# ============================================================
# Output / JSON Export
# ============================================================
def build_json_export(radar_contacts, ownership_data, shared, heartbeat,
                      exec_registry, pipeline_correlations):
    counts = {"RED": 0, "YELLOW": 0, "GREEN": 0, "GRAY": 0}
    for c in radar_contacts:
        h = c["health"].upper()
        if h in counts:
            counts[h] += 1

    return {
        "generated": NOW.isoformat(),
        "total_contacts": len(radar_contacts),
        "health_summary": counts,
        "executives": ownership_data,
        "heartbeat": heartbeat,
        "contacts": radar_contacts,
        "shared_contacts": shared,
        "pipeline_correlations": pipeline_correlations,
    }


# ============================================================
# CLI / Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="31C CRM Command Center Dashboard")
    parser.add_argument("--output-dir", help="Custom output directory")
    parser.add_argument("--pdf", action="store_true", help="Also generate PDF via html-to-pdf.py")
    parser.add_argument("--json", action="store_true", help="Output raw data as JSON")
    args = parser.parse_args()

    # Preflight: check aggregated data directory exists (created by aggregate-crm.py)
    if not AGGREGATED_DIR.exists():
        print(f"{YELLOW}Warning: Aggregated CRM data not found at {AGGREGATED_DIR}{RESET}",
              file=sys.stderr)
        print("Run aggregate-crm.py first to generate aggregated data.", file=sys.stderr)

    # Determine output directory
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        out_dir = get_outputs_dir() / "operations" / "crm-dashboard" / TODAY.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"{BOLD}31C CRM Command Center{RESET}")
    print(f"{'=' * 40}")

    # Step 1: Refresh aggregated data
    print(f"\n{CYAN}Refreshing aggregated data...{RESET}")
    refreshed = refresh_aggregated_data()
    print(f"  Aggregation: {'refreshed' if refreshed else 'using cached data'}")

    # Step 2: Collect all data sources
    print(f"\n{CYAN}Collecting data...{RESET}")

    exec_registry = collect_exec_registry()
    active_execs = [e for e in exec_registry.get("executives", []) if e.get("status") == "active"]
    print(f"  Registry: {len(active_execs)} active executives")

    radar_contacts = collect_radar()
    health_counts = {"RED": 0, "YELLOW": 0, "GREEN": 0, "GRAY": 0}
    for c in radar_contacts:
        h = c["health"].upper()
        if h in health_counts:
            health_counts[h] += 1
    print(f"  Radar: {len(radar_contacts)} contacts "
          f"({health_counts['RED']} red, {health_counts['YELLOW']} yellow, "
          f"{health_counts['GREEN']} green, {health_counts['GRAY']} gray)")

    ownership_data = collect_ownership(exec_registry)
    print(f"  Ownership: {len(ownership_data)} exec section(s)")

    shared = collect_shared_contacts()
    print(f"  Shared: {len(shared)} shared contacts")

    heartbeat = collect_heartbeat()
    print(f"  Heartbeat: {sum(heartbeat.values())} total files across {len(heartbeat)} exec(s)")

    pipeline_companies = collect_pipeline_companies()
    print(f"  Pipeline: {len(pipeline_companies)} active deals")

    correlations = correlate_pipeline_crm(radar_contacts, pipeline_companies)
    print(f"  Correlations: {len(correlations)} CRM-pipeline matches")

    # Step 3: Generate output
    if args.json:
        json_path = out_dir / "crm-command-center.json"
        data = build_json_export(radar_contacts, ownership_data, shared,
                                 heartbeat, exec_registry, correlations)
        json_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        print(f"\n{GREEN}JSON: {json_path}{RESET}")
        print(f"  Size: {json_path.stat().st_size:,} bytes")
    else:
        print(f"\n{CYAN}Generating HTML...{RESET}")
        html_content = generate_html(radar_contacts, ownership_data, shared,
                                     heartbeat, exec_registry, correlations)
        html_path = out_dir / "crm-command-center.html"
        html_path.write_text(html_content, encoding="utf-8")
        size = html_path.stat().st_size
        print(f"  {GREEN}Dashboard: {html_path}{RESET}")
        print(f"  Size: {size:,} bytes")

        if args.pdf:
            print(f"\n{CYAN}Generating PDF...{RESET}")
            pdf_path = out_dir / "crm-command-center.pdf"
            try:
                subprocess.run(
                    [sys.executable, str(HTML_TO_PDF_SCRIPT), str(html_path), str(pdf_path)],
                    check=True, timeout=60
                )
                print(f"  {GREEN}PDF: {pdf_path}{RESET}")
            except Exception as e:
                print(f"  {RED}PDF generation failed: {e}{RESET}", file=sys.stderr)

    print(f"\n{GREEN}Done.{RESET}")


if __name__ == "__main__":
    main()
