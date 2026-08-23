#!/usr/bin/env python3
"""Fleet health dashboard for the executive workspace ecosystem.

For each active executive it pulls their data overlay, reads the timestamp of
the last commit in it, counts their CRM contacts, and prints one row per person.

What "Last Commit" does and does not establish: it is the newest commit in
their overlay, so it proves they (or a tool of theirs) wrote something and
pushed it. It is NOT a sync handshake and NOT proof their daemons are running.
`.claude/rules/scope-claims.md` is why the column carries the narrower name.

Until 2026-08-23 the column was called "Last Sync" and was read from
`<exec repo>/.heartbeat.json`. Nothing has ever written that file, and
`scripts/provision-exec.py` gitignores the name in every exec workspace, so it
could not have travelled even if something had. Every row on the live fleet
therefore read `DEAD / never / unknown`. Regression cover:
`tests/test_admin_health_reports_a_signal_that_exists.py`.

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
    get_workspace_root, validate_admin, get_exec_slug, load_fleet,
    get_corporate_repo_path, load_admin_config,
    load_github_org, get_per_exec_repo_path, get_all_active_exec_slugs,
    get_per_exec_contacts_dir,
)
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET

GITHUB_ORG = load_github_org()

# Thresholds in seconds, sized for a HUMAN commit cadence. The old values (2h /
# 24h) were written for a per-minute heartbeat; against commits they painted an
# executive who worked yesterday as STALE and one who took a week off as DEAD.
OK_THRESHOLD = 7 * 86400        # committed within the week
STALE_THRESHOLD = 30 * 86400    # quiet for a month


def run_cmd(cmd: list, cwd: str = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess command."""
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def repo_name_for(slug: str) -> str:
    """The GitHub repo name for an exec's data overlay, from the fleet roster.

    Falls back to the naming convention when the roster row omits `data_repo`,
    which is what a hand-added row usually does. The previous hardcoded
    `31c-crm-{slug}` named the retired aggregation model, so the clone branch
    could only ever 404.
    """
    for row in load_fleet():
        if row.get("slug") == slug and row.get("data_repo"):
            return row["data_repo"]
    return f"heading-os-data-{slug}"


def ensure_per_exec_repos() -> list:
    """Pull latest for each active exec's data overlay. Returns [(slug, path)]."""
    pairs = []
    try:
        slugs = get_all_active_exec_slugs()
    except (OSError, ValueError) as e:
        print(f"{YELLOW}[warn] Could not read the fleet roster: {e}{RESET}")
        slugs = []
    for slug in slugs:
        repo_path = get_per_exec_repo_path(slug)
        if repo_path.exists():
            run_cmd(["git", "pull"], cwd=str(repo_path), check=False)
            pairs.append((slug, repo_path))
        else:
            repo = repo_name_for(slug)
            try:
                run_cmd(["gh", "repo", "clone", f"{GITHUB_ORG}/{repo}", str(repo_path)])
                pairs.append((slug, repo_path))
            except (subprocess.CalledProcessError, FileNotFoundError):
                print(f"{YELLOW}[warn] Could not clone {GITHUB_ORG}/{repo}{RESET}")
    return pairs


def read_last_commit(repo_path: Path) -> str | None:
    """ISO-8601 committer date of HEAD, or None if there is nothing to read.

    None covers all three ways this legitimately has no answer: the path is not
    a git repo, the repo has no commits yet, or git is not installed. A freshly
    provisioned overlay hits the second case, and that is a real state to show,
    not an error to raise.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "log", "-1", "--format=%cI"],
            capture_output=True, text=True, check=False,
        )
    except (OSError, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def collect_exec_state(exec_repos: list) -> list:
    """One record per exec: last commit in their overlay plus their contact count."""
    records = []

    for slug, _repo_path in exec_repos:
        contacts_dir = get_per_exec_contacts_dir(slug)

        contact_count = 0
        if contacts_dir.exists():
            contact_count = sum(
                1 for f in contacts_dir.iterdir()
                if f.is_file() and f.suffix == ".md" and f.name != "README.md"
            )

        records.append({
            "slug": slug,
            "last_commit": read_last_commit(repo_path),
            "contact_count": contact_count,
        })

    return records


def calculate_status(record: dict) -> tuple:
    """Calculate status (OK/STALE/DEAD) and human-readable time delta.

    Returns (status_str, colored_status, time_ago_str).
    """
    last_commit = record.get("last_commit")

    if not last_commit:
        return "DEAD", f"{RED}DEAD{RESET}", "never"

    try:
        if isinstance(last_commit, str):
            # Handle ISO format with or without timezone
            sync_time = datetime.fromisoformat(last_commit.replace("Z", "+00:00"))
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


def enrich_with_registry(records: list) -> list:
    """Add name, title, platform and provisioning status from the fleet join.

    `load_fleet()` rather than either registry alone: `title` and `platform`
    live only in the org chart, `provisioning_status` only in the roster, and
    reading one file gave a blank for whatever the other owned. Platform used
    to come from the heartbeat, which never arrived, so it read `unknown` for
    everyone.
    """
    fleet = {row["slug"]: row for row in load_fleet()}

    for rec in records:
        slug = rec.get("slug", "")
        row = fleet.get(slug)
        if row:
            rec["name"] = row.get("name") or slug
            rec["title"] = row.get("title") or ""
            rec["platform"] = row.get("platform") or "unknown"
            rec["registry_status"] = row.get("provisioning_status") or "unknown"
        else:
            rec["name"] = slug
            rec["title"] = ""
            rec["platform"] = "unknown"
            rec["registry_status"] = "unregistered"

    return records


def find_shared_contacts(exec_repos: list) -> int:
    """Count contacts that appear in multiple exec per-exec repos."""
    contact_owners: dict = {}
    for slug, _repo_path in exec_repos:
        contacts_dir = get_per_exec_contacts_dir(slug)
        if not contacts_dir.exists():
            continue
        for f in contacts_dir.iterdir():
            if f.is_file() and f.suffix == ".md" and f.name != "README.md":
                contact_owners.setdefault(f.name, []).append(slug)

    return sum(1 for owners in contact_owners.values() if len(owners) > 1)


def print_dashboard(records: list, shared_contacts: int) -> None:
    """Print the fleet health dashboard."""
    print(f"\n{BOLD}{CYAN}31C Fleet Health Dashboard{RESET}")
    print(f"{'=' * 78}")

    # "Last Commit", not "Last Sync": the newest commit in their overlay is what
    # this reads, and that is a weaker claim than a completed sync.
    header = f"| {'Exec':<22}| {'Status':<8}| {'Last Commit':<18}| {'Contacts':<10}| {'Platform':<10}|"
    separator = f"|{'-' * 23}|{'-' * 9}|{'-' * 19}|{'-' * 11}|{'-' * 11}|"
    print(header)
    print(separator)

    counts = {"OK": 0, "STALE": 0, "DEAD": 0}

    for hb in records:
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
    total_contacts = sum(hb.get("contact_count", 0) for hb in records)
    ok_colored = f"{GREEN}{counts['OK']}{RESET}"
    stale_colored = f"{YELLOW}{counts['STALE']}{RESET}"
    dead_colored = f"{RED}{counts['DEAD']}{RESET}"

    print(f"\n{BOLD}Summary:{RESET} {ok_colored} OK, {stale_colored} STALE, {dead_colored} DEAD")
    print(f"\n{BOLD}Aggregate Stats:{RESET}")
    print(f"  Total contacts across fleet: {total_contacts}")
    print(f"  Shared contacts (multi-owner): {shared_contacts}")
    print(f"  Active executives: {counts['OK'] + counts['STALE']}")


def output_json(records: list, shared_contacts: int) -> None:
    """Output machine-readable JSON."""
    results = []
    counts = {"OK": 0, "STALE": 0, "DEAD": 0}

    for hb in records:
        status_raw, _, time_ago = calculate_status(hb)
        counts[status_raw] = counts.get(status_raw, 0) + 1
        results.append({
            "slug": hb.get("slug"),
            "name": hb.get("name", hb.get("slug")),
            "status": status_raw,
            "last_commit": hb.get("last_commit"),
            "time_ago": time_ago,
            "contact_count": hb.get("contact_count", 0),
            "platform": hb.get("platform", "unknown"),
            "registry_status": hb.get("registry_status", "unknown"),
        })

    total_contacts = sum(hb.get("contact_count", 0) for hb in records)
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
    records = collect_exec_state(exec_repos)
    records = enrich_with_registry(records)
    shared_contacts = find_shared_contacts(exec_repos)

    if not records:
        if args.json:
            print(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(),
                              "executives": [], "summary": {"OK": 0, "STALE": 0, "DEAD": 0},
                              "aggregate": {"total_contacts": 0, "shared_contacts": 0, "active_count": 0}}, indent=2))
        else:
            print(f"\n{YELLOW}No active executives to report on.{RESET}")
            print("Every row comes from an exec whose roster status is 'active' in")
            print("<data-root>/admin/executives.json. Check that file first.")
        sys.exit(0)

    # Output
    if args.json:
        output_json(records, shared_contacts)
    else:
        print_dashboard(records, shared_contacts)


if __name__ == "__main__":
    main()
