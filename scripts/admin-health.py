#!/usr/bin/env python3
"""Fleet health dashboard for the 31C executive workspace ecosystem.

Reads heartbeat files from crm-central, calculates sync status for each
executive, and displays a consolidated fleet health dashboard.

Usage:
    python admin-health.py [--json]
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import (
    get_workspace_root, validate_admin, get_exec_slug, load_exec_registry,
    get_corporate_repo_path, load_admin_config,
    load_github_org, get_per_exec_repo_path, get_all_active_exec_slugs,
)
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET

GITHUB_ORG = load_github_org()

# Thresholds in seconds
OK_THRESHOLD = 2 * 3600        # 2 hours
STALE_THRESHOLD = 24 * 3600    # 24 hours


def run_cmd(cmd: list, cwd: str = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess command."""
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def ensure_per_exec_repos() -> list:
    """Pull latest for each active exec's per-exec CRM repo. Returns list of (slug, repo_path)."""
    pairs = []
    try:
        slugs = get_all_active_exec_slugs()
    except Exception:
        slugs = []
    for slug in slugs:
        repo_path = get_per_exec_repo_path(slug)
        if repo_path.exists():
            run_cmd(["git", "pull"], cwd=str(repo_path), check=False)
            pairs.append((slug, repo_path))
        else:
            try:
                run_cmd(["gh", "repo", "clone", f"{GITHUB_ORG}/31c-crm-{slug}", str(repo_path)])
                pairs.append((slug, repo_path))
            except (subprocess.CalledProcessError, FileNotFoundError):
                print(f"{YELLOW}[warn] Could not clone 31c-crm-{slug}{RESET}")
    return pairs


def collect_heartbeats(exec_repos: list) -> list:
    """Read .heartbeat.json from each per-exec CRM repo root."""
    heartbeats = []

    for slug, repo_path in exec_repos:
        heartbeat_file = repo_path / ".heartbeat.json"
        contacts_dir = repo_path / "contacts"

        # Count contact files
        contact_count = 0
        if contacts_dir.exists():
            contact_count = sum(
                1 for f in contacts_dir.iterdir()
                if f.is_file() and f.suffix == ".md" and f.name != "README.md"
            )

        if heartbeat_file.exists():
            try:
                data = json.loads(heartbeat_file.read_text(encoding="utf-8"))
                data["slug"] = slug
                data["contact_count"] = contact_count
                heartbeats.append(data)
            except (json.JSONDecodeError, OSError):
                heartbeats.append({
                    "slug": slug,
                    "last_sync": None,
                    "contact_count": contact_count,
                    "platform": "unknown",
                    "error": "corrupt heartbeat",
                })
        else:
            heartbeats.append({
                "slug": slug,
                "last_sync": None,
                "contact_count": contact_count,
                "platform": "unknown",
                "error": "no heartbeat",
            })

    return heartbeats


def calculate_status(heartbeat: dict) -> tuple:
    """Calculate status (OK/STALE/DEAD) and human-readable time delta.

    Returns (status_str, colored_status, time_ago_str).
    """
    last_sync = heartbeat.get("timestamp")

    if not last_sync:
        return "DEAD", f"{RED}DEAD{RESET}", "never"

    try:
        if isinstance(last_sync, str):
            # Handle ISO format with or without timezone
            sync_time = datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
            if sync_time.tzinfo is None:
                sync_time = sync_time.replace(tzinfo=timezone.utc)
        else:
            return "DEAD", f"{RED}DEAD{RESET}", "invalid"
    except (ValueError, TypeError):
        return "DEAD", f"{RED}DEAD{RESET}", "invalid"

    now = datetime.now(timezone.utc)
    delta = (now - sync_time).total_seconds()

    # Format time ago
    if delta < 60:
        time_ago = f"{int(delta)} sec ago"
    elif delta < 3600:
        time_ago = f"{int(delta / 60)} min ago"
    elif delta < 86400:
        hours = delta / 3600
        time_ago = f"{hours:.1f} hours ago"
    else:
        days = delta / 86400
        time_ago = f"{days:.1f} days ago"

    if delta < OK_THRESHOLD:
        return "OK", f"{GREEN}OK{RESET}", time_ago
    elif delta < STALE_THRESHOLD:
        return "STALE", f"{YELLOW}STALE{RESET}", time_ago
    else:
        return "DEAD", f"{RED}DEAD{RESET}", time_ago


def enrich_with_registry(heartbeats: list) -> list:
    """Add name and title from exec registry."""
    registry = load_exec_registry()
    registry_map = {}
    for e in registry.get("executives", []):
        registry_map[e.get("slug", "")] = e

    for hb in heartbeats:
        slug = hb.get("slug", "")
        if slug in registry_map:
            reg = registry_map[slug]
            hb["name"] = reg.get("name", slug)
            hb["title"] = reg.get("title", "")
            hb["registry_status"] = reg.get("status", "unknown")
        else:
            hb["name"] = slug
            hb["title"] = ""
            hb["registry_status"] = "unregistered"

    return heartbeats


def find_shared_contacts(exec_repos: list) -> int:
    """Count contacts that appear in multiple exec per-exec repos."""
    contact_owners: dict = {}
    for slug, repo_path in exec_repos:
        contacts_dir = repo_path / "contacts"
        if not contacts_dir.exists():
            continue
        for f in contacts_dir.iterdir():
            if f.is_file() and f.suffix == ".md" and f.name != "README.md":
                contact_owners.setdefault(f.name, []).append(slug)

    return sum(1 for owners in contact_owners.values() if len(owners) > 1)


def print_dashboard(heartbeats: list, shared_contacts: int) -> None:
    """Print the fleet health dashboard."""
    print(f"\n{BOLD}{CYAN}31C Fleet Health Dashboard{RESET}")
    print(f"{'=' * 78}")

    # Table header
    header = f"| {'Exec':<22}| {'Status':<8}| {'Last Sync':<18}| {'Contacts':<10}| {'Platform':<10}|"
    separator = f"|{'-' * 23}|{'-' * 9}|{'-' * 19}|{'-' * 11}|{'-' * 11}|"
    print(header)
    print(separator)

    counts = {"OK": 0, "STALE": 0, "DEAD": 0}

    for hb in heartbeats:
        slug = hb.get("slug", "unknown")
        status_raw, status_colored, time_ago = calculate_status(hb)
        counts[status_raw] = counts.get(status_raw, 0) + 1

        platform = hb.get("platform", "unknown")
        contacts = hb.get("contact_count", 0)

        # Pad status manually since ANSI codes mess up alignment
        # status_colored already has ANSI; we pad based on raw length
        status_pad = 8 - len(status_raw)
        status_field = status_colored + " " * status_pad

        print(f"| {slug:<22}| {status_field}| {time_ago:<18}| {contacts:<10}| {platform:<10}|")

    print(separator)

    # Summary
    total_contacts = sum(hb.get("contact_count", 0) for hb in heartbeats)
    ok_colored = f"{GREEN}{counts['OK']}{RESET}"
    stale_colored = f"{YELLOW}{counts['STALE']}{RESET}"
    dead_colored = f"{RED}{counts['DEAD']}{RESET}"

    print(f"\n{BOLD}Summary:{RESET} {ok_colored} OK, {stale_colored} STALE, {dead_colored} DEAD")
    print(f"\n{BOLD}Aggregate Stats:{RESET}")
    print(f"  Total contacts across fleet: {total_contacts}")
    print(f"  Shared contacts (multi-owner): {shared_contacts}")
    print(f"  Active executives: {counts['OK'] + counts['STALE']}")


def output_json(heartbeats: list, shared_contacts: int) -> None:
    """Output machine-readable JSON."""
    results = []
    counts = {"OK": 0, "STALE": 0, "DEAD": 0}

    for hb in heartbeats:
        status_raw, _, time_ago = calculate_status(hb)
        counts[status_raw] = counts.get(status_raw, 0) + 1
        results.append({
            "slug": hb.get("slug"),
            "name": hb.get("name", hb.get("slug")),
            "status": status_raw,
            "last_sync": hb.get("last_sync"),
            "time_ago": time_ago,
            "contact_count": hb.get("contact_count", 0),
            "platform": hb.get("platform", "unknown"),
            "registry_status": hb.get("registry_status", "unknown"),
        })

    total_contacts = sum(hb.get("contact_count", 0) for hb in heartbeats)
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "executives": results,
        "summary": counts,
        "aggregate": {
            "total_contacts": total_contacts,
            "shared_contacts": shared_contacts,
            "active_count": counts["OK"] + counts["STALE"],
        },
    }
    print(json.dumps(output, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="31C Fleet Health Dashboard -- monitor executive workspace sync status.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true",
                        help="Output machine-readable JSON instead of table")

    args = parser.parse_args()

    # Admin gate
    validate_admin()

    # Ensure per-exec repos are available and up to date
    exec_repos = ensure_per_exec_repos()

    # Collect data
    heartbeats = collect_heartbeats(exec_repos)
    heartbeats = enrich_with_registry(heartbeats)
    shared_contacts = find_shared_contacts(exec_repos)

    if not heartbeats:
        if args.json:
            print(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(),
                              "executives": [], "summary": {"OK": 0, "STALE": 0, "DEAD": 0},
                              "aggregate": {"total_contacts": 0, "shared_contacts": 0, "active_count": 0}}, indent=2))
        else:
            print(f"\n{YELLOW}No executive heartbeats found in crm-central.{RESET}")
            print(f"Ensure crm-central/contacts/*/. heartbeat.json files exist.")
        sys.exit(0)

    # Output
    if args.json:
        output_json(heartbeats, shared_contacts)
    else:
        print_dashboard(heartbeats, shared_contacts)


if __name__ == "__main__":
    main()
