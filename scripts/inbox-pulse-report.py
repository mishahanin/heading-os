#!/usr/bin/env python3
"""Inbox Pulse shadow-mode observation CLI.

Fetches JSONL classifier logs from the managed service-host VM, aggregates tier
distribution, identifies "known-good but classified LOW" candidates,
produces YAML tuning suggestions, and renders a markdown report.

Usage:
    python scripts/inbox-pulse-report.py
    python scripts/inbox-pulse-report.py --days 7
    python scripts/inbox-pulse-report.py --days 3 --no-open
    python scripts/inbox-pulse-report.py --days 1 --no-open

Options:
    --days N     Number of calendar days to include (default 1 - today only).
    --no-open    Skip opening the report in VS Code.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.paths import get_workspace_root, load_env
from scripts.utils.workspace import get_crm_contacts_dir, get_data_config_dir, get_outputs_dir

# ===========================================================================
# Constants
# ===========================================================================

# VM_HOST / VM_STATE_DIR describe the REMOTE managed service-host VM reached over SSH (see
# ssh_read). They are NOT local-machine paths, so they must not be routed
# through Path.home() / get_workspace_root() - doing so would break the live
# SSH data fetch. They are correct literals for the remote host. To stay
# portable across operators and avoid embedding a host-specific literal, the
# real host + path come from env vars (loaded from .env); the defaults below
# are non-revealing placeholders so the engine ships no instance topology.
load_env()
VM_HOST = os.environ.get("INBOX_PULSE_VM_HOST", "root@service-host")
VM_STATE_DIR = os.environ.get(
    "INBOX_PULSE_VM_STATE_DIR", "/path/to/service-host/state/email-triage"
)
VM_STATE_FILE = f"{VM_STATE_DIR}/state.json"
SSH_TIMEOUT = 30

TIER_HIGH = "HIGH_LIKELY"
TIER_MAYBE = "MAYBE"
TIER_LOW = "LOW"

# Suggestion thresholds
SUGGEST_ALWAYS_NORMAL_MIN_ENTRIES = 5
SUGGEST_CRM_KNOWN_LOW_MIN_ENTRIES = 3


# ===========================================================================
# SSH helpers
# ===========================================================================


def ssh_read(remote_path: str) -> str | None:
    """Read a remote file via SSH. Returns text or None on failure."""
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         VM_HOST, "cat", remote_path],
        capture_output=True,
        text=True,
        timeout=SSH_TIMEOUT,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def fetch_jsonl_for_date(target_date: date) -> list[dict[str, Any]]:
    """Fetch and parse JSONL entries for one day from the VM."""
    remote_path = f"{VM_STATE_DIR}/log-{target_date.isoformat()}.jsonl"
    raw = ssh_read(remote_path)
    if not raw:
        return []
    entries = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def fetch_state_json() -> dict[str, Any]:
    """Fetch state.json from the VM. Returns empty dict on failure."""
    raw = ssh_read(VM_STATE_FILE)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


# ===========================================================================
# YAML config parsing (local)
# ===========================================================================


def load_yaml_overrides(workspace_root: Path) -> dict[str, set[str]]:
    """Parse email-triage-rules.yaml and return override sets.

    Returns dict with keys:
      "always_critical", "always_important", "always_normal"
    Each value is a set of raw pattern strings (e.g. "*@noreply.com").
    """
    yaml_path = get_data_config_dir() / "email-triage-rules.yaml"  # config-DATA -> data root
    result: dict[str, set[str]] = {
        "always_critical": set(),
        "always_important": set(),
        "always_normal": set(),
    }
    if not yaml_path.exists():
        return result
    text = yaml_path.read_text(encoding="utf-8")
    current_key: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("always_critical:"):
            current_key = "always_critical"
            continue
        if stripped.startswith("always_important:"):
            current_key = "always_important"
            continue
        if stripped.startswith("always_normal:"):
            current_key = "always_normal"
            continue
        if current_key and stripped.startswith("- "):
            val = stripped[2:].strip().strip('"').strip("'")
            result[current_key].add(val)
            continue
        if stripped and not stripped.startswith("#") and ":" in stripped and not stripped.startswith("-"):
            current_key = None
    return result


def _domain_in_yaml(domain: str, yaml_overrides: dict[str, set[str]]) -> bool:
    """Return True if domain matches any pattern in any YAML override list."""
    for patterns in yaml_overrides.values():
        for pat in patterns:
            if _pattern_matches_domain(pat, domain):
                return True
    return False


def _pattern_matches_domain(pattern: str, domain: str) -> bool:
    """Match a YAML pattern (e.g. '*@noreply.com', 'alice@example.com') against a domain."""
    pattern = pattern.lower()
    domain = domain.lower()
    # Bare domain or email-style match
    if pattern == domain:
        return True
    # "*@domain.com" style
    if pattern.startswith("*@"):
        pat_domain = pattern[2:]
        return domain == pat_domain or domain.endswith("." + pat_domain)
    # "noreply@*" style - the whole right side is wildcard, match on left prefix
    if pattern.endswith("@*"):
        return False  # left-side only; domain doesn't carry username info
    # "*@*" or exact
    if pattern == "*@*":
        return True
    return False


# ===========================================================================
# CRM contact parsing (local)
# ===========================================================================


def load_known_crm_domains(workspace_root: Path) -> set[str]:
    """Walk crm/contacts/*.md and extract email domains from YAML frontmatter."""
    crm_dir = get_crm_contacts_dir()
    domains: set[str] = set()
    if not crm_dir.is_dir():
        return domains
    for md_file in crm_dir.glob("*.md"):
        text = md_file.read_text(encoding="utf-8", errors="replace")
        # Extract YAML frontmatter between --- delimiters
        match = re.match(r"^---\n(.+?)\n---", text, re.DOTALL)
        if not match:
            continue
        frontmatter = match.group(1)
        for line in frontmatter.splitlines():
            if line.strip().startswith("email:"):
                email_val = line.split(":", 1)[1].strip()
                if "@" in email_val:
                    domain = email_val.split("@", 1)[1].lower().strip()
                    if domain:
                        domains.add(domain)
    return domains


# ===========================================================================
# Aggregation
# ===========================================================================


def extract_triggers(entry: dict[str, Any]) -> str:
    """Build a human-readable trigger string from reason_breakdown."""
    breakdown = entry.get("reason_breakdown", {}) or {}
    parts = []
    sender_ov = breakdown.get("sender_override")
    if sender_ov:
        parts.append(f"sender_override={sender_ov}")
    keyword_ov = breakdown.get("keyword_override")
    if keyword_ov:
        parts.append(f"keyword_override={keyword_ov}")
    for key in ("crm_contact", "pipeline", "threads", "calendar", "time_sensitivity"):
        val = breakdown.get(key, 0)
        if val:
            parts.append(f"{key}={val}")
    return ", ".join(parts) if parts else "-"


def _any_breakdown_fired(entry: dict[str, Any]) -> bool:
    """Return True if any reason_breakdown signal is non-zero / non-null."""
    breakdown = entry.get("reason_breakdown", {}) or {}
    for val in breakdown.values():
        if val:
            return True
    return False


def aggregate(
    entries: list[dict[str, Any]],
    today: date,
    days: int,
    all_entries_by_date: dict[date, list[dict[str, Any]]],
    known_crm_domains: set[str],
    yaml_overrides: dict[str, set[str]],
) -> dict[str, Any]:
    """Aggregate entries into report data structure."""
    by_tier: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        tier = e.get("tier_guess", TIER_LOW)
        by_tier[tier].append(e)

    total = len(entries)
    high = by_tier[TIER_HIGH]
    maybe = by_tier[TIER_MAYBE]
    low = by_tier[TIER_LOW]

    # "Known good but LOW" - domain is in CRM or YAML always_critical/always_important,
    # but scored LOW. Excludes always_normal (those SHOULD score LOW by design).
    # "All known" for deduplication purposes (suppresses unknown-domain section).
    always_normal_domains = _yaml_domain_set({"always_normal": yaml_overrides["always_normal"]})
    priority_known_domains = (
        known_crm_domains
        | _yaml_domain_set({"always_critical": yaml_overrides["always_critical"]})
        | _yaml_domain_set({"always_important": yaml_overrides["always_important"]})
    )
    all_known = known_crm_domains | _yaml_domain_set(yaml_overrides)

    known_good_low: dict[str, dict[str, Any]] = {}
    for e in low:
        domain = e.get("sender_domain", "").lower()
        # Only flag if domain is "priority known" (CRM or critical/important overrides)
        # Skip if it's in always_normal (expected to be LOW).
        if domain and domain in priority_known_domains and domain not in always_normal_domains:
            if domain not in known_good_low:
                known_good_low[domain] = {"count": 0, "last_ts": ""}
            known_good_low[domain]["count"] += 1
            ts = e.get("ts", "")
            if ts > known_good_low[domain]["last_ts"]:
                known_good_low[domain]["last_ts"] = ts

    # Unknown domains (not in CRM, not in YAML at all)
    all_domains_in_window: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "tiers": defaultdict(int)}
    )
    for e in entries:
        domain = e.get("sender_domain", "").lower()
        if not domain:
            continue
        if domain not in all_known:
            all_domains_in_window[domain]["count"] += 1
            tier = e.get("tier_guess", TIER_LOW)
            all_domains_in_window[domain]["tiers"][tier] += 1

    # Top 10 unknown by count
    top_unknown = sorted(
        all_domains_in_window.items(),
        key=lambda kv: kv[1]["count"],
        reverse=True,
    )[:10]

    # YAML tuning suggestions
    suggestions = _compute_suggestions(
        low=low,
        known_crm_domains=known_crm_domains,
        yaml_overrides=yaml_overrides,
    )

    # 7-day distribution (for trend section)
    daily_dist = _compute_daily_distribution(all_entries_by_date, today)

    return {
        "total": total,
        "high": high,
        "maybe": maybe,
        "low": low,
        "known_good_low": known_good_low,
        "top_unknown": top_unknown,
        "suggestions": suggestions,
        "daily_dist": daily_dist,
    }


def _yaml_domain_set(yaml_overrides: dict[str, set[str]]) -> set[str]:
    """Extract plain domain strings from YAML patterns (best-effort)."""
    domains: set[str] = set()
    for patterns in yaml_overrides.values():
        for pat in patterns:
            pat = pat.lower()
            if pat.startswith("*@"):
                domains.add(pat[2:])
            elif "@" in pat:
                domains.add(pat.split("@", 1)[1])
    return domains


def _compute_suggestions(
    low: list[dict[str, Any]],
    known_crm_domains: set[str],
    yaml_overrides: dict[str, set[str]],
) -> list[str]:
    """Produce YAML tuning suggestion strings."""
    # Count LOW entries per domain, track breakdown signal
    domain_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "any_signal": False}
    )
    for e in low:
        domain = e.get("sender_domain", "").lower()
        if not domain:
            continue
        domain_stats[domain]["count"] += 1
        if _any_breakdown_fired(e):
            domain_stats[domain]["any_signal"] = True

    suggestions = []
    for domain, stats in domain_stats.items():
        count = stats["count"]
        any_signal = stats["any_signal"]
        in_yaml = _domain_in_yaml(domain, yaml_overrides)
        in_crm = domain in known_crm_domains

        # Skip if already in any YAML override list
        if in_yaml:
            continue

        # Suggest always_normal: high volume LOW with zero breakdown signal
        if count >= SUGGEST_ALWAYS_NORMAL_MIN_ENTRIES and not any_signal and not in_crm:
            suggestions.append(
                f"**Add to `always_normal`:** `*@{domain}` - "
                f"{count} LOW items, weight always 0 - looks like newsletter or automated traffic."
            )

        # Suggest checking CRM miss: known via CRM but classified LOW
        if count >= SUGGEST_CRM_KNOWN_LOW_MIN_ENTRIES and in_crm and not any_signal:
            suggestions.append(
                f"**Promote to `always_important`:** `*@{domain}` - "
                f"{count} LOW items but domain appears in CRM - check if classifier missed a signal."
            )

    return suggestions


def _compute_daily_distribution(
    all_entries_by_date: dict[date, list[dict[str, Any]]],
    today: date,
) -> dict[str, Any]:
    """Compute today + 7-day avg for tier distribution."""
    today_entries = all_entries_by_date.get(today, [])
    today_counts = {TIER_HIGH: 0, TIER_MAYBE: 0, TIER_LOW: 0}
    for e in today_entries:
        tier = e.get("tier_guess", TIER_LOW)
        if tier in today_counts:
            today_counts[tier] += 1

    # 7-day avg (from all available data, max 7 days)
    past_7 = [
        all_entries_by_date.get(today - timedelta(days=i), [])
        for i in range(1, 8)
    ]
    past_7_non_empty = [d for d in past_7 if d]
    if past_7_non_empty:
        avg_counts = {}
        for tier in (TIER_HIGH, TIER_MAYBE, TIER_LOW):
            totals = [
                sum(1 for e in day_entries if e.get("tier_guess") == tier)
                for day_entries in past_7_non_empty
            ]
            avg_counts[tier] = sum(totals) / len(past_7_non_empty)
    else:
        avg_counts = None

    def trend(today_val: int, avg_val: float | None) -> str:
        if avg_val is None:
            return "-"
        if avg_val == 0:
            return "up" if today_val > 0 else "="
        if today_val > 1.5 * avg_val:
            return "up"
        if today_val < 0.5 * avg_val:
            return "dn"
        return "="

    result = {
        "today": today_counts,
        "avg": avg_counts,
        "trend": {
            tier: trend(today_counts[tier], avg_counts[tier] if avg_counts else None)
            for tier in (TIER_HIGH, TIER_MAYBE, TIER_LOW)
        },
        "has_7day": avg_counts is not None,
    }
    return result


# ===========================================================================
# Report rendering
# ===========================================================================


def _fmt_time(ts: str) -> str:
    """Extract HH:MM from an ISO timestamp string."""
    if not ts:
        return "-"
    # "2026-05-28T23:35:01.138011+04:00" -> "23:35"
    try:
        time_part = ts.split("T", 1)[1][:5]
        return time_part
    except (IndexError, AttributeError):
        return ts[:5] if len(ts) >= 5 else ts


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "0%"
    return f"{100 * n / total:.0f}%"


def _tier_table_rows(entries: list[dict[str, Any]], max_rows: int = 50) -> str:
    """Render markdown table rows for HIGH_LIKELY or MAYBE tiers."""
    if not entries:
        return ""
    sorted_entries = sorted(
        entries,
        key=lambda e: (-(e.get("weight", 0)), e.get("ts", "") or ""),
        reverse=False,
    )
    # weight desc already handled by negation; sort again properly
    sorted_entries = sorted(
        entries,
        key=lambda e: (e.get("weight", 0), e.get("ts", "") or ""),
        reverse=True,
    )
    rows = []
    for e in sorted_entries[:max_rows]:
        ts = _fmt_time(e.get("ts", ""))
        domain = e.get("sender_domain", "-")
        weight = e.get("weight", "-")
        triggers = extract_triggers(e)
        rows.append(f"| {ts} | {domain} | {weight} | {triggers} |")
    return "\n".join(rows)


def render_report(
    agg: dict[str, Any],
    today: date,
    days: int,
    window_start: date,
    state_json: dict[str, Any],
    entries_total_in_window: int,
) -> str:
    """Render the full markdown report."""
    high = agg["high"]
    maybe = agg["maybe"]
    low = agg["low"]
    total = agg["total"]
    suggestions = agg["suggestions"]
    known_good_low = agg["known_good_low"]
    top_unknown = agg["top_unknown"]
    daily_dist = agg["daily_dist"]

    today_dist = daily_dist["today"]
    avg_dist = daily_dist.get("avg")
    trend = daily_dist["trend"]
    has_7day = daily_dist["has_7day"]

    heartbeat = state_json.get("last_heartbeat", "unknown")
    daemon_pid = state_json.get("daemon_pid", "unknown")

    # --- "At a glance" uses today's numbers only
    today_total = sum(today_dist.values())
    high_today = today_dist[TIER_HIGH]
    maybe_today = today_dist[TIER_MAYBE]
    low_today = today_dist[TIER_LOW]

    window_label = (
        f"last {days} day{'s' if days != 1 else ''} ({window_start} to {today})"
    )

    lines = []

    # Header
    lines.append(f"# Inbox Pulse shadow report -- {today}")
    lines.append(f"Window: {window_label}")
    lines.append(f"Total emails classified: {total}")
    lines.append("")

    # At a glance
    lines.append("## At a glance -- today")
    lines.append("")
    if today_total == 0:
        lines.append("No emails classified today.")
    else:
        lines.append(
            f"HIGH_LIKELY: {high_today} ({_pct(high_today, today_total)})"
        )
        lines.append(
            f"MAYBE: {maybe_today} ({_pct(maybe_today, today_total)})"
        )
        lines.append(
            f"LOW: {low_today} ({_pct(low_today, today_total)})"
        )
    lines.append("")
    lines.append(f"Heartbeat: {heartbeat}")
    lines.append(f"Daemon PID: {daemon_pid}")
    lines.append("")

    # HIGH_LIKELY
    lines.append(f"## HIGH_LIKELY items (window: {window_label})")
    lines.append("")
    if not high:
        lines.append("No HIGH_LIKELY items in window.")
    else:
        lines.append("| Time | Sender domain | Weight | Triggers |")
        lines.append("|---|---|---|---|")
        lines.append(_tier_table_rows(high))
    lines.append("")

    # MAYBE
    lines.append(f"## MAYBE items (window: {window_label})")
    lines.append("")
    if not maybe:
        lines.append("No MAYBE items in window.")
    else:
        lines.append("| Time | Sender domain | Weight | Triggers |")
        lines.append("|---|---|---|---|")
        lines.append(_tier_table_rows(maybe))
    lines.append("")

    # Known good but LOW
    lines.append(f"## LOW items from known good domains (potential false negatives)")
    lines.append("")
    lines.append(
        "Known good = domain appears in CRM contact files or in "
        "`config/email-triage-rules.yaml` sender_overrides."
    )
    lines.append("")
    if not known_good_low:
        lines.append("No known-good domains classified LOW in this window.")
    else:
        lines.append("| Sender domain | Count | Last seen |")
        lines.append("|---|---|---|")
        for domain, info in sorted(
            known_good_low.items(), key=lambda kv: kv[1]["count"], reverse=True
        ):
            last_seen = _fmt_time(info["last_ts"])
            lines.append(f"| {domain} | {info['count']} | {last_seen} |")
    lines.append("")

    # YAML tuning suggestions
    lines.append("## YAML tuning suggestions")
    lines.append("")
    lines.append("Based on patterns in the window:")
    lines.append("")
    if not suggestions:
        lines.append(
            "No suggestions today -- classifier appears well-tuned for current patterns."
        )
    else:
        for s in suggestions:
            lines.append(f"- {s}")
    lines.append("")

    # Unknown domains
    lines.append("## Unknown domains (not in CRM, not in YAML)")
    lines.append("")
    lines.append(
        "Top 10 unfamiliar domains in the window. These are candidates to classify:"
    )
    lines.append("")
    if not top_unknown:
        lines.append("No unknown domains in this window.")
    else:
        lines.append("| Sender domain | Count | Top tier_guess |")
        lines.append("|---|---|---|")
        for domain, info in top_unknown:
            count = info["count"]
            tiers_dict = info["tiers"]
            top_tier = max(tiers_dict, key=lambda t: tiers_dict[t]) if tiers_dict else "-"
            lines.append(f"| {domain} | {count} | {top_tier} |")
    lines.append("")

    # Raw distribution
    lines.append("## Raw distribution")
    lines.append("")
    trend_symbols = {"up": "up", "dn": "dn", "=": "~", "-": "-"}
    if has_7day and avg_dist:
        lines.append("| Tier | Today | 7-day avg | Trend |")
        lines.append("|---|---|---|---|")
        for tier in (TIER_HIGH, TIER_MAYBE, TIER_LOW):
            t = today_dist[tier]
            a = avg_dist[tier]
            tr = trend_symbols.get(trend[tier], "-")
            lines.append(f"| {tier} | {t} | {a:.1f} | {tr} |")
    else:
        lines.append("| Tier | Today |")
        lines.append("|---|---|")
        for tier in (TIER_HIGH, TIER_MAYBE, TIER_LOW):
            lines.append(f"| {tier} | {today_dist[tier]} |")
        if not has_7day:
            lines.append("")
            lines.append(
                "_7-day averages not available (run with --days 7 or more to enable)._"
            )
    lines.append("")

    # How to act
    lines.append("## How to act on this")
    lines.append("")
    lines.append("1. Edit `config/email-triage-rules.yaml` (locally, in ceo-main)")
    lines.append(
        "2. Run `python scripts/publish-service.py --push` to ship the change"
    )
    lines.append(
        "3. VM picks up rules within 30 seconds (auto-reload via mtime check, no restart needed)"
    )
    lines.append("4. Re-run this report tomorrow to verify the change took effect.")
    lines.append("")

    return "\n".join(lines)


# ===========================================================================
# Main
# ===========================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inbox Pulse shadow-mode observation CLI."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        metavar="N",
        help="Number of days to include (default 1 - today only).",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Skip opening the report in VS Code.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    days = max(1, args.days)
    today = date.today()
    window_start = today - timedelta(days=days - 1)

    workspace_root = get_workspace_root()

    print(f"{CYAN}{BOLD}Inbox Pulse shadow report{RESET}")
    print(f"{GRAY}Fetching {days} day(s) of JSONL from {VM_HOST}...{RESET}")

    # Fetch all entries
    all_entries_by_date: dict[date, list[dict[str, Any]]] = {}
    all_entries: list[dict[str, Any]] = []
    for i in range(days):
        target = today - timedelta(days=i)
        print(f"  {GRAY}SSH cat log-{target}.jsonl ...{RESET}", end=" ", flush=True)
        day_entries = fetch_jsonl_for_date(target)
        all_entries_by_date[target] = day_entries
        all_entries.extend(day_entries)
        print(f"{GREEN}{len(day_entries)} entries{RESET}")

    # Also load up to 7 extra days for 7-day avg (if days < 7)
    if days < 7:
        for i in range(days, 7):
            target = today - timedelta(days=i)
            if target not in all_entries_by_date:
                day_entries = fetch_jsonl_for_date(target)
                all_entries_by_date[target] = day_entries

    # Fetch state.json
    print(f"  {GRAY}SSH cat state.json ...{RESET}", end=" ", flush=True)
    state_json = fetch_state_json()
    print(f"{GREEN}ok{RESET}" if state_json else f"{YELLOW}not found{RESET}")

    # Load local context
    print(f"{GRAY}Loading local YAML + CRM...{RESET}")
    yaml_overrides = load_yaml_overrides(workspace_root)
    known_crm_domains = load_known_crm_domains(workspace_root)
    print(
        f"  {GRAY}YAML overrides: {sum(len(v) for v in yaml_overrides.values())} patterns. "
        f"CRM domains: {len(known_crm_domains)}{RESET}"
    )

    # Aggregate
    agg = aggregate(
        entries=all_entries,
        today=today,
        days=days,
        all_entries_by_date=all_entries_by_date,
        known_crm_domains=known_crm_domains,
        yaml_overrides=yaml_overrides,
    )

    # Render
    report_md = render_report(
        agg=agg,
        today=today,
        days=days,
        window_start=window_start,
        state_json=state_json,
        entries_total_in_window=len(all_entries),
    )

    # Write output
    out_dir = get_outputs_dir() / "operations" / "inbox-pulse"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{today.isoformat()}_shadow-report.md"
    out_path.write_text(report_md, encoding="utf-8")
    print(f"{GREEN}Report written:{RESET} {out_path}")

    # Hidden character scan
    scan_result = subprocess.run(
        [sys.executable, str(workspace_root / "scripts" / "sanitize-text.py"),
         str(out_path), "--scan"],
        capture_output=True,
        text=True,
    )
    scan_out = scan_result.stdout.strip()
    scan_lower = scan_out.lower()
    if scan_result.returncode != 0 or (
        "hidden" in scan_lower and "no hidden" not in scan_lower and "0 hidden" not in scan_lower
    ):
        print(f"{YELLOW}Hidden char scan: {scan_out}{RESET}")
    else:
        print(f"{GREEN}Hidden char scan: clean{RESET}")

    # VS Code open
    if not args.no_open:
        try:
            subprocess.run(["code", str(out_path)], check=False, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # graceful degradation

    # Terminal summary
    high_count = len(agg["high"])
    maybe_count = len(agg["maybe"])
    low_count = len(agg["low"])
    total = agg["total"]
    suggestion_count = len(agg["suggestions"])

    print()
    print(f"{BOLD}Inbox Pulse shadow report -- {today}{RESET}")
    print(
        f"Window: {days} day{'s' if days != 1 else ''}  "
        f"{GRAY}.{RESET}  {total} emails  "
        f"{GRAY}.{RESET}  {RED}{high_count} HIGH_LIKELY{RESET}  "
        f"{GRAY}.{RESET}  {YELLOW}{maybe_count} MAYBE{RESET}  "
        f"{GRAY}.{RESET}  {low_count} LOW"
    )
    print(f"Tuning suggestions: {suggestion_count}")
    print(f"Report: {out_path}")


if __name__ == "__main__":
    main()
