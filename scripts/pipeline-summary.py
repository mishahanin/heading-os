#!/usr/bin/env python3
"""
Pipeline Quick Metrics for 31C Workspace

Parses pipeline.md and generates summary metrics including weighted pipeline
value, deals by stage, and velocity/stale deal detection.

Usage:
    python scripts/pipeline-summary.py              # print summary to terminal
    python scripts/pipeline-summary.py --update      # write summary into pipeline.md
    python scripts/pipeline-summary.py --verbose      # detailed per-deal breakdown
"""

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET
from scripts.utils.workspace import get_workspace_root, get_context_dir

WORKSPACE = get_workspace_root()

PIPELINE_FILE = get_context_dir() / "pipeline.md"

# Canonical deal stages with probability weights
STAGE_WEIGHTS = {
    "lead": 0.05,
    "qualified": 0.15,
    "demo/poc": 0.30,
    "proposal": 0.50,
    "negotiation": 0.75,
    "won": 1.00,
    "lost": 0.00,
}

# Investor stage weights (different lifecycle)
INVESTOR_STAGE_WEIGHTS = {
    "lead": 0.05,
    "qualified": 0.15,
    "demo/poc": 0.30,
    "proposal": 0.50,
    "negotiation": 0.75,
    "won": 1.00,
    "lost": 0.00,
}

STAGE_ORDER = ["Lead", "Qualified", "Demo/POC", "Proposal", "Negotiation", "Won", "Lost"]

STALE_THRESHOLD_DAYS = 14


def parse_money(text):
    """Parse a money string like '$500K' or '$1.2M' or '$5,500,000' into a number."""
    if not text or text.strip().lower() in ("tbd", "-", "n/a", "—", ""):
        return None
    text = text.strip().replace(",", "").replace("$", "")
    multiplier = 1
    if text.upper().endswith("K"):
        multiplier = 1_000
        text = text[:-1]
    elif text.upper().endswith("M"):
        multiplier = 1_000_000
        text = text[:-1]
    elif text.upper().endswith("B"):
        multiplier = 1_000_000_000
        text = text[:-1]
    # Handle ranges like "5-6M" by taking the midpoint
    if "-" in text and not text.startswith("-"):
        parts = text.split("-")
        try:
            low = float(parts[0])
            high = float(parts[1])
            return ((low + high) / 2) * multiplier
        except (ValueError, IndexError):
            pass
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def parse_date(text):
    """Parse a date string like '2026-03-05' into a datetime object."""
    if not text or text.strip().lower() in ("tbd", "-", "n/a", "—", ""):
        return None
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%b %Y", "%B %Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_table_rows(content, header_marker):
    """Extract table rows after a header containing header_marker.

    Returns list of dicts with column names as keys.
    """
    rows = []
    in_table = False
    header_found = False
    separator_seen = False
    columns = []

    for line in content.split("\n"):
        # Match section headers (## or ###)
        if header_marker.lower() in line.lower() and line.strip().startswith("#"):
            header_found = True
            in_table = False
            separator_seen = False
            columns = []
            continue
        if header_found and not in_table and "|" in line and line.strip().startswith("|"):
            # This is the column header row
            columns = [c.strip() for c in line.split("|")]
            columns = [c for c in columns if c]
            in_table = True
            continue
        if in_table and not separator_seen and "---" in line:
            separator_seen = True
            continue
        if in_table and separator_seen:
            if "|" in line and line.strip().startswith("|"):
                cells = [c.strip() for c in line.split("|")]
                cells = [c for c in cells if c]
                if cells:
                    row = {}
                    for i, col in enumerate(columns):
                        row[col] = cells[i] if i < len(cells) else ""
                    rows.append(row)
            elif not line.strip() or line.strip().startswith("---"):
                header_found = False
                in_table = False
                separator_seen = False

    return rows


def normalize_stage(stage_text):
    """Normalize a stage string to canonical form."""
    s = stage_text.strip().lower()
    for canonical in STAGE_WEIGHTS:
        if canonical == s:
            return canonical
    # Fuzzy matching for common variants
    if "won" in s or "closed" in s:
        return "won"
    if "lost" in s:
        return "lost"
    if "negoti" in s:
        return "negotiation"
    if "proposal" in s:
        return "proposal"
    if "demo" in s or "poc" in s:
        return "demo/poc"
    if "qualif" in s:
        return "qualified"
    if "lead" in s:
        return "lead"
    return "lead"  # default


def analyze_stale_deals(deals, today=None):
    """Identify deals with no stage movement in >STALE_THRESHOLD_DAYS days."""
    if today is None:
        today = datetime.now()
    stale = []
    for deal in deals:
        stage_date = deal.get("stage_date")
        if stage_date and deal.get("stage") not in ("won", "lost"):
            days_in_stage = (today - stage_date).days
            if days_in_stage > STALE_THRESHOLD_DAYS:
                stale.append({
                    "company": deal["company"],
                    "stage": deal["stage"],
                    "days_in_stage": days_in_stage,
                    "value": deal.get("value"),
                })
    return stale


def generate_summary(content):
    """Generate pipeline summary metrics from pipeline.md content."""
    today = datetime.now()

    # Parse Active Deals table
    deal_rows = parse_table_rows(content, "Active Deals")
    won_rows = parse_table_rows(content, "Won / Closed")
    investor_rows = parse_table_rows(content, "Investor Conversations")
    partnership_rows = parse_table_rows(content, "Partnership Discussions")

    deals = []
    stage_counts = dict.fromkeys(STAGE_ORDER, 0)
    stage_values = dict.fromkeys(STAGE_ORDER, 0)

    # Process active deals
    for row in deal_rows:
        company = row.get("Company", row.get("Prospect", "Unknown"))
        stage_raw = row.get("Stage", "Lead")
        stage = normalize_stage(stage_raw)
        value = parse_money(row.get("Est. Value", ""))
        stage_date = parse_date(row.get("Stage Date", ""))
        next_action = row.get("Next Action", "")

        # Capitalize stage for display
        display_stage = stage.title() if stage != "demo/poc" else "Demo/POC"
        if display_stage in stage_counts:
            stage_counts[display_stage] += 1
        if value and display_stage in stage_values:
            stage_values[display_stage] += value

        deals.append({
            "company": company,
            "stage": stage,
            "value": value,
            "stage_date": stage_date,
            "weight": STAGE_WEIGHTS.get(stage, 0.05),
            "next_action": next_action,
        })

    # Process won deals
    for row in won_rows:
        company = row.get("Client", "Unknown")
        value = parse_money(row.get("Est. Value", row.get("Value", "")))
        stage_counts["Won"] += 1
        if value:
            stage_values["Won"] += value
        deals.append({
            "company": company,
            "stage": "won",
            "value": value,
            "stage_date": None,
            "weight": 1.0,
            "next_action": "",
        })

    # Calculate totals
    total_active_deals = sum(stage_counts[s] for s in STAGE_ORDER if s not in ("Won", "Lost"))
    total_pipeline_value = sum(
        d["value"] for d in deals if d["value"] and d["stage"] not in ("won", "lost")
    )
    weighted_pipeline_value = sum(
        d["value"] * d["weight"] for d in deals if d["value"] and d["stage"] not in ("won", "lost")
    )
    won_value = sum(d["value"] for d in deals if d["value"] and d["stage"] == "won")
    tbd_active_count = sum(
        1 for d in deals if d["value"] is None and d["stage"] not in ("won", "lost")
    )

    # Stale deals
    stale_deals = analyze_stale_deals(deals, today)

    # Build summary text for pipeline.md
    lines = []
    lines.append("## Pipeline Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total active deals | {total_active_deals} |")

    for stage in STAGE_ORDER:
        if stage_counts[stage] > 0:
            lines.append(f"| Deals by stage — {stage} | {stage_counts[stage]} |")

    if tbd_active_count > 0:
        lines.append(f"| Total pipeline value (priced deals only) | ${total_pipeline_value:,.0f} |")
        lines.append(f"| Deals with TBD value (sizing pending) | {tbd_active_count} |")
        lines.append(f"| Weighted pipeline value (priced deals only) | ${weighted_pipeline_value:,.0f} |")
    else:
        lines.append(f"| Total pipeline value (excl. Won) | ${total_pipeline_value:,.0f} |")
        lines.append(f"| Weighted pipeline value | ${weighted_pipeline_value:,.0f} |")
    won_count = stage_counts['Won']
    won_revenue_str = f"{won_count} deals closed"
    if won_value:
        won_revenue_str += f" (${won_value:,.0f})"
    lines.append(f"| Won revenue (YTD) | {won_revenue_str} |")
    lines.append(f"| Investor conversations | {len(investor_rows)} |")
    lines.append(f"| Partnership discussions | {len(partnership_rows)} |")
    lines.append(f"| Last updated | {today.strftime('%Y-%m-%d')} |")

    if stale_deals:
        lines.append(f"| Stale deals (>{STALE_THRESHOLD_DAYS}d no movement) | {len(stale_deals)} |")

    lines.append("")
    if tbd_active_count > 0:
        lines.append(
            f"> Auto-generated totals. {tbd_active_count} deal(s) carry TBD values "
            "(sizing pending) and are excluded from pipeline value math. "
            "Run `python scripts/pipeline-summary.py` to refresh stage counts."
        )
    else:
        lines.append("> Auto-generated totals. Run `python scripts/pipeline-summary.py` to refresh.")
    lines.append("")

    metrics = {
        "total_active": total_active_deals,
        "stage_counts": stage_counts,
        "stage_values": stage_values,
        "total_pipeline_value": total_pipeline_value,
        "weighted_pipeline_value": weighted_pipeline_value,
        "won_count": stage_counts["Won"],
        "won_value": won_value,
        "investors": len(investor_rows),
        "partnerships": len(partnership_rows),
        "stale_deals": stale_deals,
        "deals": deals,
    }

    return "\n".join(lines), metrics


def print_terminal_summary(metrics):
    """Print a rich terminal summary."""
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  31C Pipeline Summary{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}\n")

    # Stage breakdown
    print(f"  {BOLD}Deals by Stage:{RESET}")
    for stage in STAGE_ORDER:
        count = metrics["stage_counts"][stage]
        value = metrics["stage_values"][stage]
        if count > 0:
            weight = STAGE_WEIGHTS.get(stage.lower() if stage != "Demo/POC" else "demo/poc", 0)
            prob_str = f"{weight * 100:.0f}%"
            value_str = f"${value:,.0f}" if value else "TBD"
            color = GREEN if stage == "Won" else (RED if stage == "Lost" else YELLOW)
            print(f"    {color}{stage:15s}{RESET}  {count:3d} deals  {value_str:>15s}  ({prob_str} prob)")

    print()

    # Totals
    print(f"  {BOLD}Pipeline Totals:{RESET}")
    print(f"    Total active deals:         {CYAN}{metrics['total_active']}{RESET}")
    print(f"    Total pipeline value:       {CYAN}${metrics['total_pipeline_value']:,.0f}{RESET}")
    print(f"    Weighted pipeline value:    {GREEN}${metrics['weighted_pipeline_value']:,.0f}{RESET}")
    if metrics["won_value"]:
        print(f"    Won value (YTD):            {GREEN}${metrics['won_value']:,.0f}{RESET}")
    print(f"    Investor conversations:     {metrics['investors']}")
    print(f"    Partnership discussions:     {metrics['partnerships']}")
    print()

    # Stale deals
    stale = metrics["stale_deals"]
    if stale:
        print(f"  {RED}{BOLD}Stale Deals (>{STALE_THRESHOLD_DAYS} days in current stage):{RESET}")
        for d in sorted(stale, key=lambda x: -x["days_in_stage"]):
            val_str = f"${d['value']:,.0f}" if d["value"] else "TBD"
            print(f"    {RED}{d['company']:35s}{RESET}  {d['stage']:12s}  {d['days_in_stage']:3d}d  {val_str}")
        print()
    else:
        print(f"  {GREEN}No stale deals detected.{RESET}\n")

    print(f"  {BOLD}{'=' * 60}{RESET}\n")


def print_verbose(metrics):
    """Print per-deal breakdown."""
    print(f"\n{BOLD}Per-Deal Breakdown:{RESET}\n")
    deals = metrics["deals"]
    # Sort by weighted value descending
    sorted_deals = sorted(
        deals,
        key=lambda d: (d["value"] or 0) * d["weight"],
        reverse=True,
    )
    for d in sorted_deals:
        val = f"${d['value']:,.0f}" if d["value"] else "TBD"
        wval = f"${d['value'] * d['weight']:,.0f}" if d["value"] else "—"
        stage_display = d["stage"].title() if d["stage"] != "demo/poc" else "Demo/POC"
        print(f"  {d['company']:40s}  {stage_display:12s}  {val:>12s}  weighted: {wval:>12s}")


def main():
    parser = argparse.ArgumentParser(description="31C Pipeline Summary")
    parser.add_argument("--update", action="store_true", help="Write summary into pipeline.md")
    parser.add_argument("--verbose", action="store_true", help="Show per-deal breakdown")
    args = parser.parse_args()

    if not PIPELINE_FILE.exists():
        print(f"{RED}Error: {PIPELINE_FILE} not found{RESET}")
        sys.exit(1)

    content = PIPELINE_FILE.read_text(encoding="utf-8")
    summary_text, metrics = generate_summary(content)

    if args.update:
        # Remove existing Pipeline Summary block and replace
        pattern = r"## Pipeline Summary\n.*?(?=\n---\n\n## Stage Definitions|\n---\n\n## Active Deals|\Z)"
        match = re.search(pattern, content, flags=re.DOTALL)

        if match:
            # Replace existing summary
            updated = content[:match.start()] + summary_text + content[match.end():]
        else:
            # Insert after the freshness marker / intro section
            # Find the first "---" separator after the header
            first_sep = content.find("\n---\n")
            if first_sep >= 0:
                # Insert after the first separator
                insert_point = first_sep + len("\n---\n")
                updated = content[:insert_point] + "\n" + summary_text + content[insert_point:]
            else:
                updated = summary_text + "\n" + content

        PIPELINE_FILE.write_text(updated, encoding="utf-8")
        print(f"{GREEN}Pipeline summary written to {PIPELINE_FILE.name}{RESET}")

    # Print terminal output
    print_terminal_summary(metrics)

    if args.verbose:
        print_verbose(metrics)

    # Warnings
    stale = metrics["stale_deals"]
    if stale:
        print(f"{YELLOW}Warning: {len(stale)} deal(s) have been in the same stage for >{STALE_THRESHOLD_DAYS} days.{RESET}")


if __name__ == "__main__":
    main()
