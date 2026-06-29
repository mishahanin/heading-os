#!/usr/bin/env python3
"""Offboard an executive from the 31C workspace ecosystem.

Revokes GitHub access, archives workspace, preserves CRM contacts,
optionally reassigns contacts, and logs the offboarding event.

Usage:
    python offboard-exec.py --exec "sam-carter" [--reassign-to "jordan-blake"]
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.atomic import atomic_write_text
from scripts.utils.workspace import (
    get_workspace_root, validate_admin, get_exec_slug, load_exec_registry,
    get_corporate_repo_path, load_admin_config,
    load_github_org, get_crm_contacts_dir, get_outputs_dir,
)
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET
from scripts.utils.git_push import current_branch, supervised_push

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


def safety_gate(slug: str) -> bool:
    """Require the user to type the exec slug to confirm offboarding."""
    print(f"\n{RED}{BOLD}WARNING: You are about to offboard '{slug}'.{RESET}")
    print(f"This will revoke GitHub access, archive their workspace repo,")
    print(f"and preserve their CRM contacts.\n")
    confirmation = input(f"Type the exec slug to confirm [{slug}]: ").strip()
    if confirmation != slug:
        print(f"\n{RED}Confirmation failed. Aborting.{RESET}")
        return False
    return True


def revoke_github_access(slug: str, exec_info: dict) -> None:
    """Revoke GitHub access from all 31C repos."""
    print(f"\n{BOLD}Step 1: Revoking GitHub access{RESET}")

    repos = [
        f"{GITHUB_ORG}/heading-os-corporate",
        f"{GITHUB_ORG}/31c-crm-{slug}",
        f"{GITHUB_ORG}/31c-workspace-{slug}",
    ]

    # github_username from exec-registry.json (field: github_user); falls back to slug.
    github_username = exec_info.get("github_user") or slug

    for repo in repos:
        try:
            result = run_cmd([
                "gh", "api",
                f"repos/{repo}/collaborators/{github_username}",
                "-X", "DELETE",
            ], check=False)
            if result.returncode == 0:
                print(f"  {GREEN}[ok]{RESET} Revoked access from {repo}")
            elif "404" in (result.stderr or ""):
                print(f"  {YELLOW}[skip]{RESET} No access found on {repo}")
            else:
                print(f"  {RED}[error]{RESET} Failed for {repo}: {result.stderr}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"  {RED}[error]{RESET} {repo}: {e}")


def archive_workspace_repo(slug: str) -> None:
    """Archive the exec's workspace GitHub repo."""
    print(f"\n{BOLD}Step 2: Archiving workspace repo{RESET}")
    repo = f"{GITHUB_ORG}/31c-workspace-{slug}"
    try:
        result = run_cmd([
            "gh", "repo", "archive", repo, "--yes",
        ], check=False)
        if result.returncode == 0:
            print(f"  {GREEN}[ok]{RESET} Archived {repo}")
        else:
            print(f"  {RED}[error]{RESET} Failed to archive: {result.stderr}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"  {RED}[error]{RESET} {e}")


def archive_per_exec_crm_repo(slug: str) -> None:
    """Archive the per-exec CRM repo."""
    print(f"\n{BOLD}Step 2b: Archiving per-exec CRM repo{RESET}")
    repo = f"{GITHUB_ORG}/31c-crm-{slug}"
    try:
        result = run_cmd([
            "gh", "repo", "archive", repo, "--yes",
        ], check=False)
        if result.returncode == 0:
            print(f"  {GREEN}[ok]{RESET} Archived {repo}")
        elif "404" in (result.stderr or ""):
            print(f"  {YELLOW}[skip]{RESET} {repo} not found (may already be archived or deleted)")
        else:
            print(f"  {YELLOW}[warn]{RESET} Could not archive {repo}: {result.stderr}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"  {RED}[error]{RESET} {e}")


def preserve_crm_contacts(slug: str) -> bool:
    """Snapshot contacts from per-exec CRM repo to CEO-local backup."""
    print(f"\n{BOLD}Step 3: Preserving CRM contacts{RESET}")

    workspace_root = get_workspace_root()
    per_exec_repo = workspace_root.parent / f"31c-crm-{slug}"

    # Auto-clone if not present
    if not per_exec_repo.exists():
        try:
            run_cmd(["gh", "repo", "clone", f"{GITHUB_ORG}/31c-crm-{slug}", str(per_exec_repo)])
        except subprocess.CalledProcessError:
            print(f"  {RED}[error]{RESET} Could not clone 31c-crm-{slug}")
            return False
    else:
        run_cmd(["git", "pull"], cwd=str(per_exec_repo), check=False)

    src = per_exec_repo / "contacts"
    dst = get_outputs_dir() / "operations" / "offboarding" / f"{slug}-crm-final"

    if not src.exists():
        print(f"  {YELLOW}[warn]{RESET} No contacts directory found in 31c-crm-{slug}")
        return True

    dst.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in src.iterdir():
        if item.is_file() and item.suffix == ".md":
            shutil.copy2(item, dst / item.name)
            count += 1

    print(f"  {GREEN}[ok]{RESET} Preserved {count} contacts to {dst.relative_to(workspace_root)}/")
    return True


def reassign_contacts(slug: str, reassign_to: str) -> None:
    """Copy contacts to CEO-local CRM with transfer notes."""
    print(f"\n{BOLD}Step 4: Reassigning contacts to {reassign_to}{RESET}")
    workspace_root = get_workspace_root()
    per_exec_repo = workspace_root.parent / f"31c-crm-{slug}"
    src = per_exec_repo / "contacts"
    dst = get_crm_contacts_dir()

    if not src.exists():
        print(f"  {YELLOW}[warn]{RESET} No contacts to reassign")
        return

    dst.mkdir(parents=True, exist_ok=True)
    transferred = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for item in src.iterdir():
        if item.is_file() and item.suffix == ".md":
            content = item.read_text(encoding="utf-8")
            # Add transfer note
            transfer_note = (
                f"\n\n---\n**Transfer note ({now}):** "
                f"Contact transferred from {slug} during offboarding. "
                f"Previous owner: {slug}. Review and update as needed.\n"
            )
            # YAML-aware owner update (only modify frontmatter, not body)
            match = re.match(r"^(---\s*\n)(.*?\n)(---)", content, re.DOTALL)
            if match:
                pre, frontmatter, post = match.group(1), match.group(2), match.group(3)
                rest = content[match.end():]
                frontmatter = re.sub(r"^owner:\s*.*$", f"owner: {reassign_to}", frontmatter, flags=re.MULTILINE)
                content = pre + frontmatter + post + rest
            dest_file = dst / item.name
            if dest_file.exists():
                print(f"  {YELLOW}[skip]{RESET} {item.name} already exists in {reassign_to}")
            else:
                dest_file.write_text(content + transfer_note, encoding="utf-8")
                transferred += 1

    print(f"  {GREEN}[ok]{RESET} Transferred {transferred} contacts to {reassign_to}")


def update_exec_registry(slug: str) -> None:
    """Set exec status to 'offboarded' in exec-registry.json."""
    print(f"\n{BOLD}Step 5: Updating exec registry{RESET}")
    corp_repo = get_corporate_repo_path()
    registry_file = corp_repo / "config" / "exec-registry.json"

    if not registry_file.exists():
        # Try workspace root config
        registry_file = get_workspace_root() / "config" / "exec-registry.json"

    if not registry_file.exists():
        print(f"  {YELLOW}[warn]{RESET} exec-registry.json not found")
        return

    registry = json.loads(registry_file.read_text(encoding="utf-8"))
    updated = False
    for e in registry.get("executives", []):
        if e.get("slug") == slug:
            e["status"] = "offboarded"
            e["offboarded_at"] = datetime.now(timezone.utc).isoformat()
            updated = True
            break

    if updated:
        atomic_write_text(registry_file, json.dumps(registry, indent=2))
        print(f"  {GREEN}[ok]{RESET} Marked {slug} as offboarded in registry")

        # Try to commit and push
        try:
            cwd = str(registry_file.parent.parent)
            run_cmd(["git", "add", "config/exec-registry.json"], cwd=cwd)
            run_cmd(["git", "commit", "-m", f"Offboard exec: {slug}"], cwd=cwd)
            # Supervised + verified push: the registry change must actually land on
            # the remote for the offboard to take effect fleet-wide. A bare push
            # could exit 0 without advancing the ref (or hang indefinitely); verify
            # ahead/behind == 0 0 and surface a hard ERROR rather than reporting
            # the offboard complete on an unverified push.
            br = current_branch(cwd) or "main"
            v = supervised_push(cwd, branch=br, stall_window=120, label="offboard-registry")
            if v["state"] != "ok":
                raise subprocess.CalledProcessError(
                    1, "git push (supervised)", stderr=f"{v['state']}: {v['reason']}")
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip()
            print(f"  {RED}[ERROR]{RESET} Registry update did NOT reach the remote"
                  f"{(': ' + detail) if detail else ''}. Offboard is INCOMPLETE — "
                  f"re-run after resolving, do not assume {slug} is removed fleet-wide.")
    else:
        print(f"  {YELLOW}[warn]{RESET} {slug} not found in registry")


def log_offboarding(slug: str, exec_info: dict, reassign_to: str | None) -> None:
    """Log offboarding event to CEO-local audit log (outputs/operations/offboarding/audit/)."""
    print(f"\n{BOLD}Step 6: Logging offboarding{RESET}")
    audit_dir = get_outputs_dir() / "operations" / "offboarding" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    log_file = audit_dir / "offboarding-log.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    name = exec_info.get("name", slug) if exec_info else slug

    entry = (
        f"\n## {name} ({slug})\n"
        f"- **Date:** {now}\n"
        f"- **Performed by:** {get_exec_slug()}\n"
        f"- **Actions:** GitHub access revoked, workspace archived, contacts preserved\n"
    )
    if reassign_to:
        entry += f"- **Contacts reassigned to:** {reassign_to}\n"
    entry += "\n"

    if log_file.exists():
        existing = log_file.read_text(encoding="utf-8")
    else:
        existing = "# Offboarding Log\n\nChronological record of executive offboardings.\n"

    log_file.write_text(existing + entry, encoding="utf-8")
    print(f"  {GREEN}[ok]{RESET} Logged to audit/offboarding-log.md")


def print_manual_checklist(slug: str, exec_info: dict) -> None:
    """Print manual steps that require human action."""
    name = exec_info.get("name", slug) if exec_info else slug
    email = exec_info.get("email", "unknown") if exec_info else "unknown"

    print(f"\n{BOLD}{YELLOW}Manual Checklist (requires human action):{RESET}")
    print(f"  [ ] Revoke API keys (Anthropic, Firecrawl, Telegram, etc.)")
    print(f"  [ ] Disable email account: {email}")
    print(f"  [ ] Remove from Slack/Teams channels")
    print(f"  [ ] Terminate Telegram sessions")
    print(f"  [ ] Revoke VPN/SSH access")
    print(f"  [ ] Review and archive knowledge base content")
    print(f"  [ ] Notify relevant Tribe members")
    print(f"  [ ] Update org chart / people.md")
    print(f"  [ ] Confirm scheduled tasks removed on exec machine:")
    print(f"       Windows: schtasks /delete /tn \"31C-Sync-{slug}\" /f")
    print(f"                schtasks /delete /tn \"31C-Sentinel-{slug}\" /f")
    print(f"       macOS:   launchctl bootout gui/$(id -u)/io.31c.sync.{slug}")
    print(f"                launchctl bootout gui/$(id -u)/io.31c.sentinel.{slug}")
    print(f"                rm ~/Library/LaunchAgents/io.31c.sync.{slug}.plist")
    print(f"                rm ~/Library/LaunchAgents/io.31c.sentinel.{slug}.plist")


def main():
    parser = argparse.ArgumentParser(
        description="Offboard an executive from the 31C workspace ecosystem.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--exec", required=True, dest="exec_slug",
                        help="Exec slug to offboard (e.g., 'sam-carter')")
    parser.add_argument("--reassign-to", default=None,
                        help="Slug of exec to reassign contacts to")

    args = parser.parse_args()

    # Admin gate
    validate_admin()

    slug = args.exec_slug
    exec_info = get_exec_info(slug)

    if exec_info:
        name = exec_info.get("name", slug)
        print(f"\n{BOLD}{CYAN}31C Executive Offboarding{RESET}")
        print(f"{'=' * 50}")
        print(f"  Exec:   {name} ({slug})")
        print(f"  Title:  {exec_info.get('title', 'N/A')}")
        print(f"  Email:  {exec_info.get('email', 'N/A')}")
        print(f"  Status: {exec_info.get('status', 'N/A')}")
        if args.reassign_to:
            print(f"  Reassign contacts to: {args.reassign_to}")
        print(f"{'=' * 50}")
    else:
        print(f"\n{YELLOW}[warn]{RESET} Exec '{slug}' not found in registry. Proceeding anyway.")

    # Safety gate
    if not safety_gate(slug):
        sys.exit(1)

    # Execute offboarding steps
    revoke_github_access(slug, exec_info or {})
    archive_workspace_repo(slug)
    archive_per_exec_crm_repo(slug)
    preserve_crm_contacts(slug)

    if args.reassign_to:
        reassign_contacts(slug, args.reassign_to)

    # Best-effort removal of scheduled tasks on the admin machine if the
    # exec's local workspace lived alongside the CEO workspace. Remote exec
    # machines cannot be reached from here -- the manual checklist flags
    # that follow-up for the admin.
    print(f"\n{BOLD}Step: Removing scheduled tasks (local workspace only){RESET}")
    try:
        from scripts.utils.schedule import uninstall_sentinel_schedule, uninstall_sync_schedule
        exec_platform = (exec_info or {}).get("platform") if exec_info else None
        uninstall_sync_schedule(slug, target_platform=exec_platform)
        uninstall_sentinel_schedule(slug, target_platform=exec_platform)
    except (ImportError, OSError, subprocess.CalledProcessError) as e:
        print(f"  {YELLOW}[warn]{RESET} Scheduled-task cleanup skipped: {e}")

    print(f"\n{BOLD}Step: Flagging knowledge for review{RESET}")
    print(f"  {YELLOW}[action]{RESET} Manual review needed for {slug}'s knowledge base content")
    print(f"  Check: personal/knowledge/ in the archived workspace repo")

    update_exec_registry(slug)
    log_offboarding(slug, exec_info, args.reassign_to)

    print_manual_checklist(slug, exec_info)

    print(f"\n{'=' * 50}")
    print(f"{BOLD}{GREEN}Offboarding complete for {slug}.{RESET}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
