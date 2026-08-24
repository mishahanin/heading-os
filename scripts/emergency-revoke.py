#!/usr/bin/env python3
"""DISABLED. Emergency access revocation for a 31C executive.

**This script revokes nothing.** `main()` prints the manual checklist and exits
2, because the legacy 31c-crm-central path it automates was retired and nothing
below it has been re-pointed at the per-exec model. Tracking: scrutinize H4.

The header used to say it "immediately revokes all GitHub access", which was the
contract BEFORE the disable and is the worst possible thing for a file header to
say during an incident: the argparse description and epilog were corrected and
this was not, so anyone reading the source, or generated docs, got the old
promise. `--reason` is accepted and unused.

The revocation functions below are kept for a re-enable and are NOT dead by
accident. Two of their defects must be fixed before that happens, and both are
flagged in place: `revoke_all_github_access` only removes DIRECT collaborators
(an exec whose access comes from an org team is untouched), and nothing here
calls the org-membership or team endpoints at all.

Usage:
    python emergency-revoke.py --exec "marlow-carter" --reason "laptop stolen"
    -> prints the manual checklist, exits 2, revokes nothing

Tests: tests/test_a_revocation_that_reported_clear_for_the_dangerous_case.py
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
    get_data_config_dir,
    get_crm_central_path, get_corporate_repo_path, load_admin_config,
    load_github_org
)
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET

GITHUB_ORG = load_github_org()


def run_cmd(cmd: list, cwd: str = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess command."""
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def get_exec_info(slug: str) -> dict | None:
    """Look up exec in registry by slug."""
    registry = load_exec_registry()
    for e in registry.get("executives", []):
        if e.get("slug") == slug:
            return e
    return None


def revoke_all_github_access(slug: str, exec_info: dict) -> None:
    """Remove the exec as a DIRECT COLLABORATOR from the listed repos.

    NOT "all GitHub access", which is what the heading and the old name claimed.
    `DELETE /repos/{repo}/collaborators/{user}` removes a direct or outside
    collaborator and nothing else. Access granted through an ORGANIZATION TEAM
    leaves no per-repo collaborator record, so that endpoint answers 404 for such
    a member -- and this printed "[no access]" for a 404, reporting a person with
    full push rights as already clear. No call here touches
    `DELETE /orgs/{org}/memberships/{user}` or any team endpoint.

    The 404 wording is corrected below. Adding the org-level call is NOT done
    here: whether 31C grants exec access by direct invite or by team is the
    operator's knowledge, and guessing wrong in a revocation tool is how the
    wrong thing gets deleted during an incident. Raise it before re-enabling.
    """
    print(f"\n{BOLD}{RED}Step 1: REMOVING DIRECT COLLABORATOR ACCESS{RESET}")
    print(f"{YELLOW}  Org-team access is NOT touched by this step. Verify it by "
          f"hand.{RESET}")

    repos = [
        f"{GITHUB_ORG}/heading-os-corporate",
        f"{GITHUB_ORG}/31c-crm-central",
        f"{GITHUB_ORG}/31c-workspace-{slug}",
    ]

    # Fail closed. `.get("github_username", slug)` fell back to the exec SLUG,
    # which is generally not a GitHub username, so every request 404'd and every
    # repo printed "[no access]" -- a full green report for a revocation that
    # addressed nobody.
    github_username = (exec_info or {}).get("github_username")
    if not github_username:
        print(f"  {RED}[STOP]{RESET} the registry carries no github_username for "
              f"{slug!r}. Refusing to guess it from the slug: every request would "
              f"404 and read as 'no access'.")
        print(f"    {RED}MANUAL ACTION REQUIRED: revoke this exec via the GitHub "
              f"UI, at the org level as well as per repo.{RESET}")
        return

    for repo in repos:
        try:
            result = run_cmd([
                "gh", "api",
                f"repos/{repo}/collaborators/{github_username}",
                "-X", "DELETE",
            ], check=False)
            if result.returncode == 0:
                print(f"  {GREEN}[REVOKED]{RESET} {repo}")
            elif "404" in (result.stderr or ""):
                # NOT "[no access]". A 404 here establishes only that the user is
                # not a DIRECT collaborator on this repo. Org-team access
                # produces exactly this response, so the old wording turned the
                # most dangerous case into the reassuring one.
                print(f"  {YELLOW}[not a direct collaborator]{RESET} {repo}"
                      f" - org/team access NOT checked; verify by hand")
            else:
                print(f"  {RED}[FAILED]{RESET} {repo}: {result.stderr}")
                print(f"    {RED}MANUAL ACTION REQUIRED: Revoke access manually via GitHub UI{RESET}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"  {RED}[FAILED]{RESET} {repo}: {e}")
            print(f"    {RED}MANUAL ACTION REQUIRED: Revoke access manually via GitHub UI{RESET}")


def audit_recent_commits(slug: str) -> list:
    """Audit git log in crm-central for unauthorized pushes."""
    print(f"\n{BOLD}Step 2: Auditing recent commits{RESET}")
    suspicious = []

    crm_central = get_crm_central_path()
    if not crm_central.exists():
        try:
            run_cmd(["gh", "repo", "clone", f"{GITHUB_ORG}/31c-crm-central", str(crm_central)])
        except subprocess.CalledProcessError:
            print(f"  {RED}[error]{RESET} Could not clone crm-central for audit")
            return suspicious
    else:
        run_cmd(["git", "pull"], cwd=str(crm_central), check=False)

    # Check recent commits (last 48 hours)
    try:
        result = run_cmd([
            "git", "log", "--since=48 hours ago",
            "--format=%H%x1f%an%x1f%ae%x1f%s%x1f%ci",
        ], cwd=str(crm_central))

        if result.stdout.strip():
            commits = result.stdout.strip().split("\n")
            print(f"  Reviewing {len(commits)} commits from last 48 hours...")

            for line in commits:
                parts = line.split("\x1f", 4)
                if len(parts) >= 5:
                    commit_hash, author, email, subject, date = parts
                    # Flag commits from the revoked exec
                    if slug in author.lower() or slug in email.lower():
                        suspicious.append({
                            "hash": commit_hash[:8],
                            "author": author,
                            "email": email,
                            "subject": subject,
                            "date": date,
                        })
                        print(f"  {RED}[SUSPICIOUS]{RESET} {commit_hash[:8]} by {author}: {subject}")

            if not suspicious:
                print(f"  {GREEN}[clean]{RESET} No suspicious commits found")
        else:
            print(f"  {GREEN}[clean]{RESET} No commits in last 48 hours")
    except subprocess.CalledProcessError as e:
        print(f"  {RED}[error]{RESET} Could not audit git log: {e.stderr}")

    # Also check corporate repo
    corp_repo = get_corporate_repo_path()
    if corp_repo.exists():
        try:
            run_cmd(["git", "pull"], cwd=str(corp_repo), check=False)
            result = run_cmd([
                "git", "log", "--since=48 hours ago",
                "--format=%H%x1f%an%x1f%ae%x1f%s%x1f%ci",
            ], cwd=str(corp_repo))

            if result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    parts = line.split("\x1f", 4)
                    if len(parts) >= 5:
                        commit_hash, author, email, subject, date = parts
                        if slug in author.lower() or slug in email.lower():
                            suspicious.append({
                                "hash": commit_hash[:8],
                                "author": author,
                                "email": email,
                                "subject": subject,
                                "date": date,
                                "repo": "corporate",
                            })
                            print(f"  {RED}[SUSPICIOUS]{RESET} (corporate) {commit_hash[:8]} by {author}: {subject}")
        except subprocess.CalledProcessError:
            pass

    return suspicious


def update_registry_status(slug: str) -> None:
    """Set exec status to 'revoked' in exec-registry.json."""
    print(f"\n{BOLD}Step 3: Updating exec registry{RESET}")
    # Per-instance DATA. See offboard-exec.update_exec_registry for why the
    # corporate-repo and engine-root fallbacks were removed rather than kept.
    registry_file = get_data_config_dir() / "exec-registry.json"

    if not registry_file.exists():
        print(f"  {YELLOW}[warn]{RESET} exec-registry.json not found")
        return

    registry = json.loads(registry_file.read_text(encoding="utf-8"))
    matched = False
    for e in registry.get("executives", []):
        if e.get("slug") == slug:
            e["status"] = "revoked"
            e["revoked_at"] = datetime.now(timezone.utc).isoformat()
            matched = True
            break

    # A slug that matched nothing used to fall straight through: the file was
    # rewritten unchanged, a commit reading "EMERGENCY: Revoke access for {slug}"
    # was created and pushed, and the operator was told "Status set to
    # 'revoked'". During an incident a typo'd slug bought a confident green line
    # and a paper trail for a revocation that never happened.
    if not matched:
        known = sorted(str(e.get("slug")) for e in registry.get("executives", [])
                       if e.get("slug"))
        print(f"  {RED}[STOP]{RESET} no executive with slug {slug!r} in "
              f"{registry_file.name}; NOTHING was written or committed.")
        print(f"    Known slugs: {', '.join(known) or '(none)'}")
        return

    registry_file.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    print(f"  {GREEN}[ok]{RESET} Status set to 'revoked'")

    try:
        cwd = str(registry_file.parent.parent)
        run_cmd(["git", "add", "config/exec-registry.json"], cwd=cwd)
        run_cmd(["git", "commit", "-m", f"EMERGENCY: Revoke access for {slug}"], cwd=cwd)
        run_cmd(["git", "push"], cwd=cwd)
        print(f"  {GREEN}[ok]{RESET} Registry update pushed")
    except subprocess.CalledProcessError:
        print(f"  {YELLOW}[warn]{RESET} Could not push registry update")


def log_security_event(slug: str, reason: str, suspicious: list) -> None:
    """Log event to crm-central/audit/security-events.jsonl."""
    print(f"\n{BOLD}Step 4: Logging security event{RESET}")
    crm_central = get_crm_central_path()
    audit_dir = crm_central / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "emergency_revoke",
        "exec_slug": slug,
        "reason": reason,
        "performed_by": get_exec_slug(),
        "suspicious_commits": len(suspicious),
        "suspicious_details": suspicious,
    }

    events_file = audit_dir / "security-events.jsonl"
    with open(events_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    print(f"  {GREEN}[ok]{RESET} Event logged to audit/security-events.jsonl")

    # Commit
    try:
        cwd = str(crm_central)
        run_cmd(["git", "add", "audit/security-events.jsonl"], cwd=cwd)
        status = run_cmd(["git", "status", "--porcelain"], cwd=cwd)
        if status.stdout.strip():
            run_cmd(["git", "commit", "-m", f"SECURITY: Emergency revoke for {slug}"], cwd=cwd)
            run_cmd(["git", "push"], cwd=cwd)
    except subprocess.CalledProcessError:
        print(f"  {YELLOW}[warn]{RESET} Could not push security log")


def print_manual_checklist(slug: str, exec_info: dict) -> None:
    """Print urgent manual action items."""
    name = exec_info.get("name", slug) if exec_info else slug
    email = exec_info.get("email", "unknown") if exec_info else "unknown"

    print(f"\n{BOLD}{RED}URGENT MANUAL ACTIONS REQUIRED:{RESET}")
    print(f"  {RED}[!]{RESET} Revoke ALL API keys (Anthropic, Firecrawl, Perplexity, etc.)")
    print(f"  {RED}[!]{RESET} Disable email account: {email}")
    print(f"  {RED}[!]{RESET} Terminate ALL active Telegram sessions")
    print(f"  {RED}[!]{RESET} Revoke VPN credentials and SSH keys")
    print(f"  {RED}[!]{RESET} Disable Slack/Teams account")
    print(f"  {RED}[!]{RESET} Change shared passwords and secrets")
    print(f"  {RED}[!]{RESET} Review access logs for last 48 hours")
    print(f"  {RED}[!]{RESET} Notify 31C Tribe about access change (no details)")
    print(f"  {RED}[!]{RESET} If device compromised: remote wipe if possible")
    print(f"  {RED}[!]{RESET} If device still accessible, uninstall scheduled tasks:")
    print(f"         Windows: schtasks /delete /tn \"31C-Sync-{slug}\" /f")
    print(f"                  schtasks /delete /tn \"31C-Sentinel-{slug}\" /f")
    print(f"         macOS:   launchctl bootout gui/$(id -u)/io.31c.sync.{slug}")
    print(f"                  launchctl bootout gui/$(id -u)/io.31c.sentinel.{slug}")
    print(f"                  rm ~/Library/LaunchAgents/io.31c.sync.{slug}.plist")
    print(f"                  rm ~/Library/LaunchAgents/io.31c.sentinel.{slug}.plist")


def main():
    # This script is DISABLED, deliberately and fail-closed: the legacy
    # 31c-crm-central revoke path it automates no longer exists, so running it
    # would report success against a repo that is gone. Tracking: scrutinize H4.
    #
    # What changed 2026-08-24, and what did NOT. The automation stays off —
    # re-enabling GitHub access revocation is an outward-facing change that
    # needs the operator's decision and a real test, not a night-shift patch.
    # Two things about the DEAD END are fixed, because an emergency runbook
    # entry that exits 2 with no further help is worse than the gap itself:
    #
    #   1. `--help` now works. It exited 2 before argparse ever ran, so the one
    #      command an operator types under pressure told them nothing.
    #   2. The urgent MANUAL checklist is printed. It was already written, in
    #      `print_manual_checklist`, and sat unreachable below this exit — the
    #      exact information someone needs at 3am, behind the wall.
    parser = argparse.ArgumentParser(
        description="DISABLED. Emergency access revocation for a 31C executive.",
        epilog=("This script does NOT revoke anything. It prints the manual "
                "checklist and exits 2. See scrutinize H4."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--exec", dest="exec_slug",
                        help="Exec slug to revoke (e.g., 'marlow-carter')")
    parser.add_argument("--reason",
                        help="Reason for emergency revocation (e.g., 'laptop stolen')")
    args = parser.parse_args()

    print(f"\n{RED}{BOLD}DISABLED: emergency-revoke.py automates a revoke path that no "
          f"longer exists.{RESET}")
    print(f"{YELLOW}The legacy 31c-crm-central path was retired; nothing below it "
          f"has been re-pointed at the per-exec model.{RESET}")
    print(f"{YELLOW}NOTHING HAS BEEN REVOKED BY THIS RUN. Do the manual steps "
          f"below, now.{RESET}")
    print(f"{YELLOW}Tracking: scrutinize H4.{RESET}")
    slug = args.exec_slug or "<exec-slug>"
    exec_info = get_exec_info(slug) if args.exec_slug else None
    print_manual_checklist(slug, exec_info)
    sys.exit(2)
    # Original code below — kept for reference; do not execute until refactored.

    parser = argparse.ArgumentParser(
        description="Emergency access revocation for a 31C executive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--exec", required=True, dest="exec_slug",
                        help="Exec slug to revoke (e.g., 'marlow-carter')")
    parser.add_argument("--reason", required=True,
                        help="Reason for emergency revocation (e.g., 'laptop stolen')")

    args = parser.parse_args()

    # Admin gate
    validate_admin()

    slug = args.exec_slug
    exec_info = get_exec_info(slug)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{BOLD}{RED}{'=' * 50}{RESET}")
    print(f"{BOLD}{RED}  EMERGENCY ACCESS REVOCATION{RESET}")
    print(f"{BOLD}{RED}{'=' * 50}{RESET}")
    print(f"  Time:   {now}")
    print(f"  Exec:   {exec_info.get('name', slug) if exec_info else slug}")
    print(f"  Reason: {args.reason}")
    print(f"{RED}{'=' * 50}{RESET}")

    # Step 1: Immediately revoke access
    revoke_all_github_access(slug, exec_info or {})

    # Step 1b: Remove scheduled tasks on the admin machine if this exec's
    # workspace was mirrored here. Remote machines still need manual removal
    # via the checklist below, but local cleanup is immediate.
    try:
        from scripts.utils.schedule import uninstall_sentinel_schedule, uninstall_sync_schedule
        exec_platform = (exec_info or {}).get("platform") if exec_info else None
        uninstall_sync_schedule(slug, target_platform=exec_platform)
        uninstall_sentinel_schedule(slug, target_platform=exec_platform)
    except (ImportError, OSError, subprocess.CalledProcessError) as e:
        print(f"{YELLOW}[warn]{RESET} Local scheduled-task cleanup skipped: {e}")

    # Step 2: Audit commits
    suspicious = audit_recent_commits(slug)

    # Step 3: Flag suspicious commits
    if suspicious:
        print(f"\n{BOLD}{RED}SUSPICIOUS COMMITS DETECTED:{RESET}")
        for s in suspicious:
            repo_label = f" ({s['repo']})" if s.get("repo") else ""
            print(f"  {RED}>{RESET} {s['hash']}{repo_label} | {s['author']} | {s['subject']} | {s['date']}")
        print(f"\n  {RED}Review these commits manually and consider reverting if unauthorized.{RESET}")

    # Step 4: Update registry
    update_registry_status(slug)

    # Step 5: Log security event
    log_security_event(slug, args.reason, suspicious)

    # Step 6: Manual checklist
    print_manual_checklist(slug, exec_info)

    print(f"\n{RED}{'=' * 50}{RESET}")
    print(f"{BOLD}{RED}Emergency revocation complete for {slug}.{RESET}")
    print(f"{RED}Complete the manual checklist above IMMEDIATELY.{RESET}")
    print(f"{RED}{'=' * 50}{RESET}")


if __name__ == "__main__":
    main()
