#!/usr/bin/env python3
"""
31C CRM Command Center Dashboard Generator

Aggregates company-wide CRM data from the operator's own aggregate at
<data-root>/crm/aggregated/ (company radar, ownership map, shared contacts,
exec roster, pipeline correlation) into a single-page HTML dashboard. Self-contained (inline CSS, base64 logo, no external dependencies
beyond Google Fonts).

Usage:
    python scripts/generate-crm-dashboard.py                  # HTML only
    python scripts/generate-crm-dashboard.py --pdf            # HTML + PDF
    python scripts/generate-crm-dashboard.py --json           # raw data as JSON
    python scripts/generate-crm-dashboard.py --output-dir DIR # custom output dir

Tests: tests/test_a_data_root_override_that_was_silently_ignored.py, tests/test_html_generators_render.py
"""

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.brand_assets import brand_asset_path
from scripts.utils.html_templates import load_template
from scripts.utils.image import load_logo_base64
from scripts.utils.markdown import parse_md_table
from scripts.utils.workspace import (
    get_crm_contacts_dir,
    get_data_config_dir,
    get_context_dir,
    get_default_tz,
    get_outputs_dir,
)
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET

# ============================================================
# Paths
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent

AGGREGATE_SCRIPT = SCRIPT_DIR / "aggregate-crm.py"
HTML_TO_PDF_SCRIPT = SCRIPT_DIR / "html-to-pdf.py"


def aggregated_dir():
    """Resolved at call time, never at import.

    `get_crm_contacts_dir()` reads `HEADING_OS_DATA` on every call, so it
    follows the environment for a caller that asks after the environment
    moved. As a module-level constant it asked once, during its own import,
    and stored the answer, so a test that imported this module and then
    repointed the data root still read the operator's real overlay. The same
    applies to every path below, including the three derived from this one:
    a constant built from a frozen constant is just as frozen.
    """
    return get_crm_contacts_dir().parent / "aggregated"


def company_radar_file():
    return aggregated_dir() / "company-radar.md"


def ownership_map_file():
    return aggregated_dir() / "ownership-map.md"


def shared_contacts_file():
    return aggregated_dir() / "shared-contacts.md"


def exec_registry_file():
    return get_data_config_dir() / "exec-registry.json"


def pipeline_file():
    return get_context_dir() / "pipeline.md"


def logo_path():
    """The white mark, asked for by key rather than by filename.

    Its twin in `scripts/generate-dashboard.py` moved to the manifest on
    2026-09-02, when the operator ruled that a datastore filename is itself
    private and this repository is public. Moving one of two identical lookups
    is how a fix half-lands, so this one moved with it.
    """
    return brand_asset_path("logo_on_dark")


TODAY = datetime.now(get_default_tz()).date()
NOW = datetime.now(get_default_tz())


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


# parse_md_table used to live here, byte-for-byte the same as the copy in
# generate-dashboard.py, and with the same defect: `[c for c in cells if c]`
# deleted an empty cell instead of keeping its position, so every value after a
# blank shifted one column left -- Owner showed the company, health showed a
# number. The docstring claimed it "handles missing columns gracefully". The
# shared implementation in scripts/utils/markdown.py holds the position.


def count_files_in_dir(dirpath):
    """Count .md files in a directory (non-recursive)."""
    if not dirpath.exists():
        return 0
    return sum(1 for f in dirpath.iterdir() if f.is_file() and f.suffix == ".md")


# ============================================================
# Data Collectors
# ============================================================
def refresh_aggregated_data():
    """Run aggregate-crm.py to refresh the aggregated CRM data."""
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
    radar_file = company_radar_file()
    content = read_file(radar_file)
    if not content:
        return []
    rows = parse_md_table(content, source=str(radar_file))
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
                lt_date = date.fromisoformat(last_touch)
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
    content = read_file(ownership_map_file())
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
    shared_file = shared_contacts_file()
    content = read_file(shared_file)
    if not content:
        return []
    if "No shared contacts detected" in content:
        return []
    return parse_md_table(content, source=str(shared_file))


def collect_exec_registry():
    """Load exec registry JSON."""
    registry_file = exec_registry_file()
    if not registry_file.exists():
        return {"version": "1.0", "executives": []}
    try:
        return json.loads(registry_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        # Named, not swallowed. A trailing comma in exec-registry.json used to
        # produce the perfectly legitimate-looking line "Registry: 0 active
        # executives", a header badge reading "0 Execs", and scorecards with
        # every title blank -- with nothing anywhere distinguishing a corrupt
        # file from a company that has no executives. Both sibling collectors
        # in this module print on failure; this one did not.
        print(f"[generate-crm-dashboard] exec registry unreadable "
              f"({registry_file}): {e}. Continuing with an EMPTY registry "
              f"-- titles and exec counts below are not to be trusted.",
              file=sys.stderr)
        return {"version": "1.0", "executives": []}


def collect_heartbeat():
    """Count contact files per exec by reading per-exec CRM repos."""
    from scripts.utils.workspace import get_all_active_exec_slugs, get_per_exec_contacts_dir
    heartbeat = {}
    try:
        slugs = list(get_all_active_exec_slugs())
    except (OSError, ImportError, KeyError, ValueError) as e:
        print(f"[generate-crm-dashboard] heartbeat roster unreadable: {e}",
              file=sys.stderr)
        return heartbeat
    # Per slug, not around the loop. The try used to wrap the whole `for`, so a
    # KeyError on exec #2 of 10 left execs 3-10 uncounted and the dashboard
    # rendered those zeros with no sign that the sweep had stopped early.
    for slug in slugs:
        try:
            contacts_dir = get_per_exec_contacts_dir(slug)
            heartbeat[slug] = count_files_in_dir(contacts_dir)
        except (OSError, ImportError, KeyError, ValueError) as e:
            print(f"[generate-crm-dashboard] heartbeat failed for {slug}: {e}",
                  file=sys.stderr)
    return heartbeat


def collect_pipeline_companies():
    """Parse pipeline.md to extract company names for correlation."""
    pipeline = pipeline_file()
    content = read_file(pipeline)
    if not content:
        return []
    deals = parse_md_table(content, r"##\s*Active Deals", source=str(pipeline))
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
    return load_template("crm-dashboard.css")


# ============================================================
# Rendering / HTML Section Builders
# ============================================================
def build_header(logo_b64, exec_count, total_contacts):
    date_long = NOW.strftime("%A, %B %d, %Y")
    # The zone's own abbreviation, not the words "the configured timezone".
    # That literal was a placeholder nobody replaced, and it rendered verbatim
    # in every dashboard and every PDF: a page headed "Internal - CEO Eyes
    # Only" told its reader the time was "14:32 (the configured timezone)".
    # `NOW` is already built with `get_default_tz()`, so the real name was
    # there the whole time. `tzname()` can return None for a zone with no
    # abbreviation, and the whole parenthetical is dropped when it does. Not
    # because "None" would print -- `esc()` maps a falsy value to "" -- but
    # because it would leave a bare "14:32 ()" in the header, which reads as a
    # rendering fault rather than as a time.
    time_str = NOW.strftime("%H:%M")
    zone = NOW.tzname()
    time_html = f"{esc(time_str)} ({esc(zone)})" if zone else esc(time_str)
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
    <div class="header-date">{esc(date_long)}<br/>{time_html}</div>
  </div>
</div>
"""


def active_exec_count(exec_registry):
    """Executives the console counts. Active only.

    The header badge used to count every registry row while main() filters on
    `status == "active"`, so a registry holding one inactive exec printed
    "4 active executives" on the console beside a badge reading "5 Execs".
    """
    return sum(1 for e in exec_registry.get("executives", [])
               if e.get("status") == "active")


_HEALTH_WARNED: set[tuple[str, str]] = set()


def _health_counts(contacts):
    """Contacts per health card. Every contact lands in exactly one.

    Both call sites did `if h in counts: counts[h] += 1`, so a contact whose
    Health column read "BLUE", "amber" or nothing at all was counted in the
    header's total and in none of the four cards. The cards stopped summing to
    the total and nothing said why. An unrecognised value is GRAY, and named
    on stderr so the source row can be fixed.

    "Named ONCE" is what this used to promise and could not keep: the helper
    runs two or three times per invocation (the health cards, the exec
    scorecards' source data, the JSON export, and since 2026-08-24 the console
    summary too), so one bad row printed one warning per call. The dedupe set
    below makes the claim true again -- once per offending contact and value,
    for the life of the process, no matter how many callers ask.
    """
    counts = {"RED": 0, "YELLOW": 0, "GREEN": 0, "GRAY": 0}
    for c in contacts:
        h = str(c.get("health") or "").strip().upper()
        if h not in counts:
            key = (str(c.get("name", "?")), str(c.get("health")))
            if key not in _HEALTH_WARNED:
                _HEALTH_WARNED.add(key)
                print(f"  Warning: contact {c.get('name', '?')!r} has health "
                      f"{c.get('health')!r}; counted as GRAY", file=sys.stderr)
            h = "GRAY"
        counts[h] += 1
    return counts


def build_health_summary(contacts):
    counts = _health_counts(contacts)
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

        # Find top 3 overdue contacts for this exec.
        #
        # Matched on the WHOLE owner string, not on a surname substring. Both
        # sides of this comparison are produced by one function from one slug:
        # `aggregate-crm.slug_to_display_name` writes the radar's Owner cell
        # and the `## <name> (`slug`)` header of ownership-map.md that
        # `collect_ownership` parses into `ex["name"]`. Substring-matching the
        # last word of that against free text was wrong in both directions.
        # False positive: exec "Ann Li" claimed every contact owned by "Julia
        # Li", "Ali Reza" or "Compliance Team". False negative: an Owner cell
        # reading "A. Li", initials, or two names dropped out of every
        # scorecard, so a card could read "3 red" and list no overdue names at
        # all, or list names belonging to someone else. Wrong names under the
        # wrong executive, on a page headed CEO Eyes Only.
        #
        # Two execs whose slugs collapse to one display name would both match.
        # That ambiguity is in the data format, not here, and the substring
        # version had it too.
        owner_key = ex["name"].strip().lower()
        overdue = []
        for c in radar_contacts:
            if (c["health"] == "RED"
                    and c["owner"]
                    and c["owner"].strip().lower() == owner_key):
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
    logo_b64 = load_logo_base64(logo_path())

    exec_count = active_exec_count(exec_registry)
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
                      pipeline_correlations):
    # `exec_registry` used to sit between heartbeat and pipeline_correlations,
    # accepted, passed by the caller, and read nowhere in the body.
    counts = _health_counts(radar_contacts)

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
    aggregated = aggregated_dir()
    if not aggregated.exists():
        print(f"{YELLOW}Warning: Aggregated CRM data not found at {aggregated}{RESET}",
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
    # Through `_health_counts`, like the HTML and the JSON export. This block
    # used to carry its own copy of the exact pattern that helper's docstring
    # describes as the OLD defect -- `if h in counts: counts[h] += 1` -- so a
    # contact whose Health cell read "amber", "BLUE" or nothing landed in the
    # header total and in none of the four buckets. The rendered page counted
    # it as GRAY and the console line below did not, and the two outputs of one
    # run disagreed by one with nothing saying why.
    health_counts = _health_counts(radar_contacts)
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
    if args.json and args.pdf:
        # The PDF branch lives inside the HTML branch, so --json --pdf used to
        # produce JSON and no word about the PDF that was asked for.
        print(f"  {YELLOW}Warning: --pdf is ignored with --json "
              f"(the PDF is rendered from the HTML output){RESET}",
              file=sys.stderr)
    if args.json:
        json_path = out_dir / "crm-command-center.json"
        data = build_json_export(radar_contacts, ownership_data, shared,
                                 heartbeat, correlations)
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
