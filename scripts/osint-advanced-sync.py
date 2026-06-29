#!/usr/bin/env python3
"""
osint-advanced-sync.py -- Check awesome-osint for upstream changes, validate
registered tools, and generate reports. Does NOT auto-update any files.

Usage:
    python scripts/osint-advanced-sync.py check          # Diff upstream vs local registry
    python scripts/osint-advanced-sync.py validate        # HTTP health-check all registered tools
    python scripts/osint-advanced-sync.py validate-one URL # Test a single URL
    python scripts/osint-advanced-sync.py report          # Full report (check + validate)

Output:
    Reports saved to outputs/intel/osint-advanced/sync-reports/YYYY-MM-DD.md + .html
    Updates to reference/osint-advanced-toolkit.md must be done manually after review.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# Workspace utilities
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import get_outputs_dir, get_workspace_root

WORKSPACE_ROOT = get_workspace_root()
TOOLKIT_PATH = WORKSPACE_ROOT / "reference" / "osint-advanced-toolkit.md"
UPSTREAM_URL = (
    "https://raw.githubusercontent.com/jivoi/awesome-osint/master/README.md"
)

# Maps our local categories to awesome-osint section headers.
# If a section is renamed upstream, the script will warn about missing sections.
CATEGORY_MAP = {
    "Sanctions & Compliance": ["People Investigations", "Company Research"],
    "Corporate Registry": ["Company Research"],
    "People & Username Search": ["Username Check", "People Investigations"],
    "Email Intelligence": ["Email Search / Email Check"],
    "Domain/IP/Infrastructure": ["Domain and IP Research", "DNS", "Speciality Search Engines"],
    "Threat Intelligence": ["Threat Actor Search", "Live Cyber Threat Maps", "Threat Intelligence"],
    "Image & Face Search": ["Image Search", "Image Analysis"],
    "Geospatial & Conflict": ["Geospatial Research and Mapping Tools"],
    "Data Breach & Dark Web": ["Data Breach Search Engines"],
    "Fact Checking & Verification": [
        "Fact Checking",
        "Web History and Website Capture",
    ],
}

# Sections to explicitly skip (per user requirements)
SKIP_SECTIONS = {"Telegram Tools", "Telegram Bots", "Maritime"}

USER_AGENT = "31C-OSINT-Advanced-Sync/1.0"
TIMEOUT = 10


def fetch_upstream():
    """Fetch the awesome-osint README from GitHub."""
    req = Request(UPSTREAM_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=30) as resp:  # nosec B310 - URL is hardcoded UPSTREAM_URL constant
            return resp.read().decode("utf-8")
    except HTTPError as e:
        print(f"{RED}ERROR: HTTP {e.code} fetching upstream: {e.reason}{RESET}")
        sys.exit(1)
    except URLError as e:
        print(f"{RED}ERROR: Cannot reach upstream: {e.reason}{RESET}")
        sys.exit(1)


def extract_upstream_tools(content):
    """Extract tool entries from awesome-osint README."""
    tools = {}
    current_section = None
    for line in content.splitlines():
        header = re.match(r"^##\s+(.+)", line)
        if header:
            raw = header.group(1).strip()
            # Strip markdown anchor links like [](#-...) and emoji
            raw = re.sub(r"\[.*?\]\([^)]*\)\s*", "", raw)
            raw = re.sub(r"[^\x20-\x7E]", "", raw).strip()
            current_section = raw
            continue
        if current_section and current_section in SKIP_SECTIONS:
            continue
        tool = re.match(
            r"^[*-]\s+\[([^\]]+)\]\(([^)]+)\)\s*[-\u2013\u2014]\s*(.+)", line
        )
        if tool and current_section:
            name, url, desc = (
                tool.group(1).strip(),
                tool.group(2).strip(),
                tool.group(3).strip(),
            )
            key = url.rstrip("/").lower()
            tools[key] = {
                "name": name,
                "url": url,
                "description": desc,
                "section": current_section,
            }
    return tools


def extract_local_tools(content):
    """Extract tool entries from osint-advanced-toolkit.md."""
    tools = {}
    for line in content.splitlines():
        m = re.match(r"^###\s+(.+)", line)
        if m:
            current_name = m.group(1).strip()
            continue
        m = re.match(r"^-\s+URL:\s+(\S+)", line)
        if m:
            url = m.group(1).strip()
            key = url.rstrip("/").lower()
            tools[key] = {"name": current_name, "url": url}
    return tools


def validate_section_map(upstream_tools):
    """Warn if expected upstream sections are missing."""
    found = {t["section"] for t in upstream_tools.values()}
    expected = set()
    for sections in CATEGORY_MAP.values():
        expected.update(sections)
    missing = expected - found
    for s in sorted(missing):
        print(
            f"{YELLOW}WARNING: Expected upstream section '{s}' not found -- "
            f"may have been renamed{RESET}"
        )
    return missing


def check_upstream(upstream_tools, local_tools):
    """Compare upstream and local, return diff."""
    validate_section_map(upstream_tools)
    relevant = set()
    for sections in CATEGORY_MAP.values():
        relevant.update(sections)
    upstream_relevant = {
        k: v for k, v in upstream_tools.items() if v["section"] in relevant
    }
    new = set(upstream_relevant.keys()) - set(local_tools.keys())
    removed = set(local_tools.keys()) - set(upstream_tools.keys())
    return {
        "new": [upstream_relevant[k] for k in sorted(new)],
        "removed": [local_tools[k] for k in sorted(removed)],
        "local_count": len(local_tools),
        "upstream_relevant": len(upstream_relevant),
        "upstream_total": len(upstream_tools),
    }


def validate_url(url, method="web"):
    """Validate a single URL. Returns (status, detail)."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if (host == "github.com" or host.endswith(".github.com")) and "/search" not in parsed.path:
        return "CLI", "Repository/CLI tool -- skip HTTP check"
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        req.method = "HEAD" if method == "web" else "GET"
        with urlopen(req, timeout=TIMEOUT) as resp:  # nosec B310 - URL from validated registry
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
        if status < 400:
            return "WORKING", f"HTTP {status} ({content_type[:30]})"
        return "BLOCKED", f"HTTP {status}"
    except HTTPError as e:
        return "BLOCKED", f"HTTP {e.code} {e.reason}"
    except URLError as e:
        return "ERROR", str(e.reason)[:60]
    except Exception as e:
        return "ERROR", str(e)[:60]


def validate_all(local_tools):
    """Validate all registered tools."""
    results = []
    print(f"\n{BOLD}Validating {len(local_tools)} registered tools...{RESET}\n")
    for key, tool in sorted(local_tools.items()):
        url = tool["url"]
        status, detail = validate_url(url)
        color = GREEN if status == "WORKING" else (CYAN if status == "CLI" else (YELLOW if status == "BLOCKED" else RED))
        print(f"  {color}{status:8s}{RESET}  {tool['name']}")
        if status not in ("WORKING", "CLI"):
            print(f"           {GRAY}{detail}{RESET}")
        results.append({"name": tool["name"], "url": url, "status": status, "detail": detail})
    working = sum(1 for r in results if r["status"] == "WORKING")
    blocked = sum(1 for r in results if r["status"] == "BLOCKED")
    cli = sum(1 for r in results if r["status"] == "CLI")
    errors = sum(1 for r in results if r["status"] == "ERROR")
    print(f"\n{GREEN}WORKING: {working}{RESET}  {YELLOW}BLOCKED: {blocked}{RESET}  {CYAN}CLI: {cli}{RESET}  {RED}ERROR: {errors}{RESET}")
    return results


def generate_report_md(diff, validation, report_dir):
    """Generate Markdown report."""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# OSINT-Advanced Sync Report -- {today}",
        "",
        "## Tool Validation",
        "",
        "| Tool | Status | Details |",
        "|------|--------|---------|",
    ]
    for r in validation:
        lines.append(f"| {r['name']} | {r['status']} | {r['detail']} |")
    lines.extend([
        "",
        "## Upstream Changes",
        "",
        f"- Local tools: {diff['local_count']}",
        f"- Upstream relevant: {diff['upstream_relevant']}",
        f"- Upstream total: {diff['upstream_total']}",
        "",
    ])
    if diff["new"]:
        lines.append(f"### New tools in upstream ({len(diff['new'])})")
        lines.append("")
        for t in diff["new"]:
            lines.append(f"- **{t['name']}** ({t['section']}) -- {t['url']}")
            lines.append(f"  {t['description']}")
        lines.append("")
    if diff["removed"]:
        lines.append(f"### Removed from upstream ({len(diff['removed'])})")
        lines.append("")
        for t in diff["removed"]:
            lines.append(f"- **{t['name']}** -- {t['url']}")
        lines.append("")
    if not diff["new"] and not diff["removed"]:
        lines.append("No relevant upstream changes detected.")
        lines.append("")
    lines.extend([
        "---",
        "",
        "Updates to `reference/osint-advanced-toolkit.md` require manual review and approval.",
    ])
    md_path = report_dir / f"{today}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def generate_report_html(diff, validation, report_dir):
    """Generate branded HTML report."""
    today = datetime.now().strftime("%Y-%m-%d")
    working = sum(1 for r in validation if r["status"] == "WORKING")
    blocked = sum(1 for r in validation if r["status"] == "BLOCKED")
    cli = sum(1 for r in validation if r["status"] == "CLI")
    errors = sum(1 for r in validation if r["status"] == "ERROR")
    rows = []
    for r in validation:
        color = "#4ade80" if r["status"] == "WORKING" else ("#22d3ee" if r["status"] == "CLI" else ("#fbbf24" if r["status"] == "BLOCKED" else "#f87171"))
        rows.append(
            f'<tr><td>{r["name"]}</td>'
            f'<td style="color:{color};font-weight:bold">{r["status"]}</td>'
            f'<td style="color:#9ca3af">{r["detail"]}</td></tr>'
        )
    new_rows = ""
    if diff["new"]:
        for t in diff["new"]:
            new_rows += f'<tr><td>{t["name"]}</td><td>{t["section"]}</td><td><a href="{t["url"]}" style="color:#60a5fa">{t["url"]}</a></td></tr>'
    else:
        new_rows = '<tr><td colspan="3" style="color:#9ca3af">No new tools detected</td></tr>'
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OSINT-Advanced Sync Report -- {today}</title>
<style>
body {{ font-family: Inter, -apple-system, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 2rem; }}
.container {{ max-width: 960px; margin: 0 auto; }}
h1 {{ color: #f8fafc; border-bottom: 2px solid #334155; padding-bottom: 0.5rem; }}
h2 {{ color: #94a3b8; margin-top: 2rem; }}
table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; }}
th {{ background: #1e293b; color: #94a3b8; text-align: left; padding: 0.75rem; font-size: 0.85rem; text-transform: uppercase; }}
td {{ padding: 0.75rem; border-bottom: 1px solid #1e293b; }}
tr:hover {{ background: #1e293b; }}
.stats {{ display: flex; gap: 1.5rem; margin: 1.5rem 0; }}
.stat {{ background: #1e293b; border-radius: 8px; padding: 1rem 1.5rem; text-align: center; }}
.stat-num {{ font-size: 2rem; font-weight: bold; }}
.stat-label {{ color: #94a3b8; font-size: 0.85rem; }}
.footer {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #334155; color: #64748b; font-size: 0.8rem; text-align: center; }}
a {{ color: #60a5fa; text-decoration: none; }}
</style>
</head>
<body>
<div class="container">
<h1>OSINT-Advanced Sync Report</h1>
<p style="color:#94a3b8">{today} -- 31C Intelligence Division</p>
<div class="stats">
<div class="stat"><div class="stat-num" style="color:#4ade80">{working}</div><div class="stat-label">Working</div></div>
<div class="stat"><div class="stat-num" style="color:#fbbf24">{blocked}</div><div class="stat-label">Blocked</div></div>
<div class="stat"><div class="stat-num" style="color:#22d3ee">{cli}</div><div class="stat-label">CLI Only</div></div>
<div class="stat"><div class="stat-num" style="color:#f87171">{errors}</div><div class="stat-label">Errors</div></div>
</div>
<h2>Tool Validation</h2>
<table>
<thead><tr><th>Tool</th><th>Status</th><th>Details</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
<h2>Upstream Changes</h2>
<p>Local: {diff["local_count"]} tools | Upstream relevant: {diff["upstream_relevant"]} | Upstream total: {diff["upstream_total"]}</p>
<table>
<thead><tr><th>Tool</th><th>Section</th><th>URL</th></tr></thead>
<tbody>{new_rows}</tbody>
</table>
<div class="footer">
Updates to reference/osint-advanced-toolkit.md require manual review and approval.<br>
Generated by osint-advanced-sync.py -- 31C Intelligence Division
</div>
</div>
</body>
</html>"""
    html_path = report_dir / f"{today}.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path


def cmd_check():
    """Check upstream for changes."""
    if not TOOLKIT_PATH.exists():
        print(f"{RED}ERROR: {TOOLKIT_PATH.name} not found{RESET}")
        sys.exit(1)
    print(f"{CYAN}Fetching upstream awesome-osint...{RESET}")
    upstream = extract_upstream_tools(fetch_upstream())
    local = extract_local_tools(TOOLKIT_PATH.read_text(encoding="utf-8"))
    diff = check_upstream(upstream, local)
    print(f"\n{BOLD}Upstream Diff{RESET}")
    print(f"Local: {diff['local_count']} | Upstream relevant: {diff['upstream_relevant']} | Total: {diff['upstream_total']}")
    if diff["new"]:
        print(f"\n{GREEN}New tools ({len(diff['new'])}):{RESET}")
        for t in diff["new"]:
            print(f"  + [{t['section']}] {t['name']} -- {t['url']}")
            print(f"    {GRAY}{t['description']}{RESET}")
    if diff["removed"]:
        print(f"\n{RED}Removed ({len(diff['removed'])}):{RESET}")
        for t in diff["removed"]:
            print(f"  - {t['name']} -- {t['url']}")
    if not diff["new"] and not diff["removed"]:
        print(f"\n{GREEN}In sync. No relevant changes.{RESET}")


def cmd_validate():
    """Validate all registered tools."""
    if not TOOLKIT_PATH.exists():
        print(f"{RED}ERROR: {TOOLKIT_PATH.name} not found{RESET}")
        sys.exit(1)
    local = extract_local_tools(TOOLKIT_PATH.read_text(encoding="utf-8"))
    validate_all(local)


def cmd_validate_one(url):
    """Validate a single URL."""
    status, detail = validate_url(url)
    color = GREEN if status == "WORKING" else (YELLOW if status == "BLOCKED" else RED)
    print(f"{color}{status}{RESET}: {url}")
    print(f"  {detail}")


def cmd_report():
    """Full report: check + validate, save MD + HTML."""
    if not TOOLKIT_PATH.exists():
        print(f"{RED}ERROR: {TOOLKIT_PATH.name} not found{RESET}")
        sys.exit(1)
    report_dir = get_outputs_dir() / "intel" / "osint-advanced" / "sync-reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    print(f"{CYAN}Fetching upstream awesome-osint...{RESET}")
    upstream = extract_upstream_tools(fetch_upstream())
    local = extract_local_tools(TOOLKIT_PATH.read_text(encoding="utf-8"))
    diff = check_upstream(upstream, local)
    validation = validate_all(local)
    md_path = generate_report_md(diff, validation, report_dir)
    html_path = generate_report_html(diff, validation, report_dir)
    print(f"\n{GREEN}Reports saved:{RESET}")
    print(f"  MD:   {md_path}")
    print(f"  HTML: {html_path}")


def main():
    parser = argparse.ArgumentParser(
        description="OSINT-Advanced upstream sync and tool validation"
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check", help="Diff upstream awesome-osint vs local registry")
    sub.add_parser("validate", help="HTTP health-check all registered tools")
    p_one = sub.add_parser("validate-one", help="Test a single URL")
    p_one.add_argument("url", help="URL to validate")
    sub.add_parser("report", help="Full report (check + validate)")
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
    if args.command == "check":
        cmd_check()
    elif args.command == "validate":
        cmd_validate()
    elif args.command == "validate-one":
        cmd_validate_one(args.url)
    elif args.command == "report":
        cmd_report()


if __name__ == "__main__":
    main()
