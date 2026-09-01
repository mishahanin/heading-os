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

Tests: tests/test_a_queue_that_read_corrupt_as_empty.py
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
    get_corporate_repo_path, load_admin_config, repo_name_for,
    load_github_org, get_per_exec_repo_path, get_all_active_exec_slugs,
    get_per_exec_contacts_dir,
)
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET
from scripts.utils.crm import contact_identity_key, is_contact_file
from scripts.utils.markdown import parse_frontmatter_str

def github_org() -> str:
    """Resolved at call time, never at import.

    `load_github_org()` reads `HEADING_OS_DATA` on every call, so it follows the
    environment for a caller that asks after the environment moved. As a
    module-level constant it asked once, during its own import, and stored the
    answer, so a test that imported this module and then repointed the data root
    still got the operator's real overlay.
    """
    return load_github_org()

# Thresholds in seconds, sized for a HUMAN commit cadence. The old values (2h /
# 24h) were written for a per-minute heartbeat; against commits they painted an
# executive who worked yesterday as STALE and one who took a week off as DEAD.
SKEW_TOLERANCE = 300            # 5 min: ordinary NTP jitter, not a broken clock
OK_THRESHOLD = 7 * 86400        # committed within the week
STALE_THRESHOLD = 30 * 86400    # quiet for a month


def run_cmd(cmd: list, cwd: str = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess command.

    `errors="replace"` because `text=True` alone decodes strictly, and strict
    decoding of git's output raises `UnicodeDecodeError` - a `ValueError`, so
    caught by neither the `(CalledProcessError, FileNotFoundError, OSError)`
    around the pull in `ensure_per_exec_repos` nor anything above it. git quotes
    branch names, paths and a remote's own bytes back on stderr, so this is not
    exotic. MEASURED 2026-09-01: a single 0xff byte on one exec's pull stderr
    ended the whole dashboard in a traceback, and the failed-pull warning
    written three lines below the call - the control that exists so a stale
    clone is never presented as current - never ran.

    Replace rather than a wider `except`: the stderr text is what that warning
    quotes, so a degraded message keeps the operator's diagnostic where
    swallowing would print "no output".
    """
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True,
                          text=True, errors="replace")


# repo_name_for moved to scripts/utils/workspace.py so aggregate-crm.py reads
# the same roster. It lived here and aggregate-crm.py hardcoded the convention,
# so an exec whose roster row named a different data_repo had their overlay
# cloned correctly by this tool and 404'd by the aggregation, which then omitted
# their contacts and exited 0.


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
            # Inspect the pull. `check=False` with no look at the exit code
            # meant auth expiry, a merge conflict or an offline machine left a
            # stale clone that the dashboard then read and presented as
            # current. Only the CLONE branch below ever warned, so a failure
            # was announced the first time and silent every time after.
            try:
                pull = run_cmd(["git", "pull"], cwd=str(repo_path), check=False)
            except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
                print(f"{YELLOW}[warn] {slug}: git pull could not run ({e}); "
                      f"reading the clone as it stands{RESET}")
            else:
                if pull.returncode != 0:
                    detail = (pull.stderr or pull.stdout or "").strip().splitlines()
                    print(f"{YELLOW}[warn] {slug}: git pull failed "
                          f"({detail[-1] if detail else 'no output'}); the rows below "
                          f"describe the LOCAL clone, which may be behind{RESET}")
            pairs.append((slug, repo_path))
        else:
            repo = repo_name_for(slug)
            org = github_org()
            try:
                run_cmd(["gh", "repo", "clone", f"{org}/{repo}", str(repo_path)])
                pairs.append((slug, repo_path))
            except (subprocess.CalledProcessError, FileNotFoundError):
                print(f"{YELLOW}[warn] Could not clone {org}/{repo}{RESET}")
    return pairs


def read_last_commit(repo_path: Path) -> str | None:
    """ISO-8601 committer date of HEAD, or None if there is nothing to read.

    None covers all three ways this legitimately has no answer: the path is not
    a git repo, the repo has no commits yet, or git is not installed. A freshly
    provisioned overlay hits the second case, and that is a real state to show,
    not an error to raise.

    This builds its own `subprocess.run` rather than going through `run_cmd`, so
    it needs the same `errors="replace"` for the same reason: `text=True` decodes
    strictly, and `UnicodeDecodeError` is a `ValueError` that walks straight past
    the `(OSError, FileNotFoundError)` below. git writes the offending path to
    stderr when it cannot read a repo, and `capture_output` decodes both streams,
    so the documented "returns None" fallback was defeated by one byte.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "log", "-1", "--format=%cI"],
            capture_output=True, text=True, errors="replace", check=False,
        )
    except (OSError, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def collect_exec_state(exec_repos: list) -> list:
    """One record per exec: last commit in their overlay plus their contact count."""
    records = []

    # `repo_path`, not `_repo_path`. The loop unpacked into the throwaway name
    # while `read_last_commit(repo_path)` below read a name that exists nowhere
    # in this scope, so the FIRST iteration raised NameError and the whole
    # dashboard died: no table, no JSON, no per-row degradation. The empty-fleet
    # path never enters the loop, which is how it shipped.
    for slug, repo_path in exec_repos:
        contacts_dir = get_per_exec_contacts_dir(slug)

        contact_count = 0
        if contacts_dir.exists():
            # `is_contact_file` rather than a local rule. This excluded exactly
            # `README.md`, while `aggregate-crm.py` excluded `readme.md`
            # case-insensitively, so an overlay holding a lowercase readme was
            # counted by this dashboard and not by the aggregator -- two fleet
            # tools reporting different totals for one directory.
            contact_count = sum(1 for f in contacts_dir.iterdir()
                                if is_contact_file(f))

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

    # A commit dated AHEAD of this clock is skew, not freshness. Nothing floored
    # `delta`, so a negative value cleared every threshold and the row read OK
    # with "-3600 sec ago" beside it -- the one condition under which this
    # dashboard should not be trusted was the one guaranteed to look healthy.
    #
    # STALE rather than a fourth status: `print_dashboard` sums OK+STALE into
    # "Active executives" and prints a three-way summary, so a new status would
    # be counted in one place and dropped in two. SKEW_TOLERANCE covers ordinary
    # NTP jitter, which must not turn every healthy row yellow.
    if delta < -SKEW_TOLERANCE:
        return "STALE", f"{YELLOW}STALE{RESET}", "ahead of this clock"

    # Format time ago. `delta` is floored at 0 because the guard above returns
    # only for skew BEYOND tolerance: a commit 2 minutes ahead of this clock
    # (ordinary NTP jitter) fell through and rendered "-120 sec ago" beside an
    # OK row. A negative age is not a freshness claim anyone can act on, and
    # this dashboard exists to be trusted.
    delta = max(delta, 0.0)
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
    """Count contacts that appear in multiple exec per-exec repos.

    "Multiple" means multiple OWNERS, so the value is a set of slugs and not a
    list of them. With a list, one exec holding two files that resolve to the
    same identity -- `jordan-kim.md` and `kim-jordan.md`, both `name: Jordan
    Kim` -- appended their own slug twice, `len(owners) > 1` passed, and the
    dashboard printed a multi-owner shared contact owned by exactly one person.
    `aggregate-crm.detect_shared_contacts` has always used a set, so the two
    fleet tools printed different numbers for one directory, which is the class
    of disagreement the comment below says was closed.
    """
    contact_owners: dict = {}
    for slug, _repo_path in exec_repos:
        contacts_dir = get_per_exec_contacts_dir(slug)
        if not contacts_dir.exists():
            continue
        for f in contacts_dir.iterdir():
            if not is_contact_file(f):
                continue
            # Keyed on IDENTITY, not filename. Grouping by `f.name` meant a
            # person saved as `jordan-kim.md` by one exec and `kim-jordan.md`
            # by another counted as two people here and one in
            # `aggregate-crm.py`, so this dashboard and `shared-contacts.md`
            # disagreed about a number they both print.
            try:
                fm, _body = parse_frontmatter_str(f.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as exc:
                # `UnicodeDecodeError` is a `ValueError`, not an `OSError`, so
                # one contact record saved in a non-UTF-8 encoding raised out
                # of this loop and killed the whole fleet dashboard: no table,
                # no JSON, no per-row degradation. That is the same shape as
                # the `collect_exec_state` NameError this file was fixed for,
                # one function further down, and the handler already here says
                # a contact it cannot read is skipped with a warning.
                print(f"  {YELLOW}[warn]{RESET} unreadable contact {f}: {exc}",
                      file=sys.stderr)
                continue
            contact_owners.setdefault(contact_identity_key(fm), set()).add(slug)

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
        # Not "sync status". The module docstring, the renamed column and
        # tests/test_admin_health_reports_a_signal_that_exists.py all exist to
        # stop this tool claiming it measures a sync handshake; --help was the
        # one surface still saying it did.
        description="31C Fleet Health Dashboard -- last commit and contact count "
                    "per executive overlay.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true",
                        help="Output machine-readable JSON instead of table")

    args = parser.parse_args()

    # An unresolved org is a refusal, not a warning. `load_github_org()` answers
    # '' rather than raising (see its docstring) so that --help survives a
    # missing data overlay; the cost of that is that the empty string reaches
    # here, and every clone below would then target `/{repo}` and fail. The old
    # crash at import was at least loud. This would not be: each exec would
    # print one `[warn] Could not clone` line and the dashboard would render a
    # complete-looking table of DEAD rows about executives who are fine.
    #
    # Before validate_admin(), deliberately: that reaches admin.json and so the
    # same unreachable overlay, which is where the traceback used to come from.
    if not github_org():
        print(f"{RED}[STOP]{RESET} the GitHub org could not be resolved, so no "
              f"exec repo path here is real. Refusing to report fleet health "
              f"from paths that cannot be cloned.", file=sys.stderr)
        print(f"  Set github_org in your operator.yaml or admin.json, or point "
              f"HEADING_OS_DATA at your data overlay.", file=sys.stderr)
        sys.exit(1)

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
