#!/usr/bin/env python3
"""Offboard an executive from the 31C workspace ecosystem.

Revokes GitHub access, archives workspace, preserves CRM contacts,
optionally reassigns contacts, and logs the offboarding event.

Revocation means the exec can no longer reach the data, and the run VERIFIES
that rather than asserting it. Direct collaborator grants, team memberships and
the organisation membership are all removed, and `has_repo_access` then asks
GitHub whether each repo is still reachable by any route. A repo that answers
"yes" makes the run exit 1 and says so in words. Until 2026-08-25 only the
direct grants were removed, the org and team removals sat on a manual
checklist, and two of the three repo names it used had been retired -- so an
exec's actual data overlay was never in the list at all.

Usage:
    python offboard-exec.py --exec "marlow-carter" [--reassign-to "jordan-blake"]

Tests: tests/test_an_offboard_that_left_the_door_open.py
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
    get_data_config_dir,
    load_admin_config,
    load_github_org, get_crm_contacts_dir, get_outputs_dir, repo_name_for,
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
    print(f"This will remove their direct collaborator grants, their TEAM")
    print(f"memberships, and their MEMBERSHIP OF THE {GITHUB_ORG} ORGANISATION,")
    print(f"archive their workspace repo, and preserve their CRM contacts.")
    print(f"Org removal reaches every repo in the org, not only this workspace.\n")
    confirmation = input(f"Type the exec slug to confirm [{slug}]: ").strip()
    if confirmation != slug:
        print(f"\n{RED}Confirmation failed. Aborting.{RESET}")
        return False
    return True


def exec_repos(slug: str) -> list[str]:
    """Every private repo an offboarded exec may hold a grant on.

    Two of the three names this used were RETIRED. `31c-crm-{slug}` and
    `31c-workspace-{slug}` belong to the pre-cutover model; an exec provisioned
    on the current one holds their data in `repo_name_for(slug)`, and that repo
    was never in the list -- so the revocation 404'd on two names that do not
    exist, printed "not a direct collaborator" for all of them, and left the
    departing exec's access to their actual data untouched.

    The retired names are kept deliberately. A 404 on a repo that no longer
    exists costs one request; missing a repo an exec is still on costs their
    continued access to it.

    The ENGINE repo is absent on purpose: it is public, so there is no grant to
    remove and no data behind it.
    """
    names = [
        "heading-os-corporate",
        repo_name_for(slug),
        f"31c-crm-{slug}",          # retired name, still checked
        f"31c-workspace-{slug}",    # retired name, still checked
    ]
    seen: set[str] = set()
    out = []
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(f"{GITHUB_ORG}/{name}")
    return out


def has_repo_access(repo: str, github_username: str) -> bool | None:
    """Does this user reach `repo` by ANY route? True / False / None-if-unknown.

    `GET /repos/{repo}/collaborators/{user}` answers 204 when the user is a
    collaborator through a direct grant, an org membership, OR a team, and 404
    when they are not. That makes it the only honest proof that a revocation
    worked -- the DELETE's own exit status says nothing about the other routes.

    Returns None when GitHub gave neither answer, so a caller cannot read an
    unreachable API as "no access".
    """
    try:
        result = run_cmd(
            ["gh", "api", f"repos/{repo}/collaborators/{github_username}"],
            check=False)
    except FileNotFoundError:
        return None
    if result.returncode == 0:
        return True
    if "404" in (result.stderr or ""):
        return False
    return None


def revoke_github_access(slug: str, exec_info: dict) -> tuple[bool, list[str]]:
    """Remove direct collaborator grants, then VERIFY the access is gone.

    Returns `(all_deletes_ok, repos_still_reachable)`.

    The collaborators DELETE removes a direct grant and nothing else, so a user
    who reaches these repos through an org membership or a team is not a
    collaborator at all: the DELETE answered 404 and the old code printed
    "skip", which reads as "already clear" for the one case that is dangerous.
    The verification pass below closes that.

    The second element is REPORTING ONLY. This docstring used to say it "is
    handed to `remove_residual_access`", and the call site said the same thing
    in its own words. Neither was true: `main` binds it to `_first_pass` and
    never reads it again, and `remove_residual_access(slug, exec_info)` takes no
    repo list at all -- it re-derives the teams and the org membership itself.
    Two independent sentences describing plumbing that does not exist, either of
    which would survive a fix aimed at the other.
    """
    print(f"\n{BOLD}Step 1: Removing direct collaborator grants{RESET}")

    repos = exec_repos(slug)

    # github_username from exec-registry.json (field: github_user); falls back to slug.
    github_username = exec_info.get("github_user") or slug

    all_ok = True
    for repo in repos:
        try:
            result = run_cmd([
                "gh", "api",
                f"repos/{repo}/collaborators/{github_username}",
                "-X", "DELETE",
            ], check=False)
            if result.returncode == 0:
                print(f"  {GREEN}[ok]{RESET} Removed direct collaborator grant on {repo}")
            elif "404" in (result.stderr or ""):
                print(f"  {YELLOW}[skip]{RESET} Not a direct collaborator on {repo} "
                      f"(this does NOT mean they have no access)")
            else:
                all_ok = False
                print(f"  {RED}[error]{RESET} Failed for {repo}: {result.stderr}")
        except FileNotFoundError as e:
            all_ok = False
            print(f"  {RED}[error]{RESET} {repo}: {e}")

    print(f"\n{BOLD}Step 1a: Verifying the access is actually gone{RESET}")
    still: list[str] = []
    for repo in repos:
        reachable = has_repo_access(repo, github_username)
        if reachable is True:
            still.append(repo)
            print(f"  {RED}[STILL HAS ACCESS]{RESET} {repo}")
        elif reachable is None:
            all_ok = False
            print(f"  {RED}[UNKNOWN]{RESET} {repo}: GitHub did not answer; "
                  f"treat as access RETAINED until checked by hand")
        else:
            print(f"  {GREEN}[ok]{RESET} No access to {repo}")
    return all_ok, still


def check_residual_access(slug: str, exec_info: dict) -> list[str]:
    """Read-only: report access routes the collaborator DELETE cannot reach.

    Returns a list of human-readable residual-access descriptions; empty means
    none were found. Purely GET requests -- this function never mutates.
    """
    print(f"\n{BOLD}Step 1b: Checking for residual org/team access{RESET}")
    github_username = exec_info.get("github_user") or slug
    residual: list[str] = []

    try:
        member = run_cmd(
            ["gh", "api", f"orgs/{GITHUB_ORG}/memberships/{github_username}"],
            check=False)
        if member.returncode == 0:
            residual.append(f"org membership in {GITHUB_ORG}")
        elif "404" not in (member.stderr or ""):
            residual.append(
                f"org membership in {GITHUB_ORG} COULD NOT BE CHECKED: "
                f"{(member.stderr or '').strip()}")

        teams = run_cmd(
            ["gh", "api", f"orgs/{GITHUB_ORG}/teams", "--jq", ".[].slug"],
            check=False)
        if teams.returncode == 0:
            for team in (teams.stdout or "").split():
                got = run_cmd(
                    ["gh", "api",
                     f"orgs/{GITHUB_ORG}/teams/{team}/memberships/{github_username}"],
                    check=False)
                if got.returncode == 0:
                    residual.append(f"team membership in {GITHUB_ORG}/{team}")
                elif "404" not in (got.stderr or ""):
                    # Only a 404 means "not a member". A 403 (token without
                    # `read:org`), a 429 or a 5xx used to read the same way, so
                    # the run printed "No org or team access found" about a
                    # team it never managed to query -- and Step 1d's re-check
                    # is literally this function, so the re-verification was
                    # blind in exactly the same place and the verdict went
                    # green.
                    residual.append(
                        f"team membership in {GITHUB_ORG}/{team} COULD NOT BE "
                        f"CHECKED: {(got.stderr or '').strip()}")
        else:
            residual.append(
                f"team memberships COULD NOT BE CHECKED: {(teams.stderr or '').strip()}")
    except FileNotFoundError as e:
        residual.append(f"org and team access COULD NOT BE CHECKED: {e}")

    if residual:
        for item in residual:
            print(f"  {RED}[residual]{RESET} {item}")
    else:
        print(f"  {GREEN}[ok]{RESET} No org or team access found")
    return residual


def remove_residual_access(slug: str, exec_info: dict) -> list[str]:
    """Remove the team and org grants a collaborator DELETE cannot reach.

    Returns what is STILL present afterwards; empty means the exec no longer
    reaches anything.

    This used to be read-only, and the removal sat on the manual checklist
    because deleting an org membership is wider than anything else here. The
    operator overruled that on 2026-08-25: an executive who has left 31C must
    not still reach the data, and a checklist entry is a reminder, not a
    revocation. The gate that makes it safe is `safety_gate`, which already
    requires the slug to be typed out; this function only ever runs behind it,
    and only inside `offboard-exec.py`, whose entire purpose is departure.

    Teams are removed BEFORE the org membership even though removing the org
    membership would take the teams with it. Doing it in that order makes the
    log name each team that granted access, which is what an audit of the
    incident needs afterwards.
    """
    print(f"\n{BOLD}Step 1c: Removing org and team access{RESET}")
    github_username = exec_info.get("github_user") or slug

    try:
        teams = run_cmd(
            ["gh", "api", f"orgs/{GITHUB_ORG}/teams", "--jq", ".[].slug"],
            check=False)
        if teams.returncode == 0:
            for team in (teams.stdout or "").split():
                got = run_cmd(
                    ["gh", "api",
                     f"orgs/{GITHUB_ORG}/teams/{team}/memberships/{github_username}"],
                    check=False)
                if got.returncode != 0:
                    if "404" not in (got.stderr or ""):
                        # Not "they are not on this team" -- "I could not ask".
                        # Skipping silently meant the DELETE was never even
                        # attempted on a team that may still grant access.
                        print(f"  {RED}[error]{RESET} Could not check membership "
                              f"of {GITHUB_ORG}/{team}: {(got.stderr or '').strip()}")
                    continue
                gone = run_cmd(
                    ["gh", "api",
                     f"orgs/{GITHUB_ORG}/teams/{team}/memberships/{github_username}",
                     "-X", "DELETE"], check=False)
                if gone.returncode == 0:
                    print(f"  {GREEN}[ok]{RESET} Removed from team {GITHUB_ORG}/{team}")
                else:
                    print(f"  {RED}[error]{RESET} Could not remove from team "
                          f"{GITHUB_ORG}/{team}: {(gone.stderr or '').strip()}")
        else:
            print(f"  {RED}[error]{RESET} Could not list teams: "
                  f"{(teams.stderr or '').strip()}")

        member = run_cmd(
            ["gh", "api", f"orgs/{GITHUB_ORG}/memberships/{github_username}"],
            check=False)
        if member.returncode == 0:
            gone = run_cmd(
                ["gh", "api", f"orgs/{GITHUB_ORG}/memberships/{github_username}",
                 "-X", "DELETE"], check=False)
            if gone.returncode == 0:
                print(f"  {GREEN}[ok]{RESET} Removed org membership in {GITHUB_ORG}")
            else:
                print(f"  {RED}[error]{RESET} Could not remove org membership: "
                      f"{(gone.stderr or '').strip()}")
                print(f"    {RED}An organisation OWNER cannot be removed this way. "
                      f"Demote them in the GitHub UI first.{RESET}")
    except FileNotFoundError as e:
        print(f"  {RED}[error]{RESET} gh is unavailable: {e}")
        return [f"org and team access COULD NOT BE REMOVED: {e}"]

    print(f"\n{BOLD}Step 1d: Re-checking after removal{RESET}")
    return check_residual_access(slug, exec_info)


def _archive_repo(repo: str) -> bool | None:
    """Archive one repo. True archived, None absent (404), False failed.

    None and False are kept apart on purpose. A name this exec never used
    answers 404 and is not a failure; anything else is.
    """
    try:
        result = run_cmd(["gh", "repo", "archive", repo, "--yes"], check=False)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"  {RED}[error]{RESET} {repo}: {e}")
        return False
    if result.returncode == 0:
        print(f"  {GREEN}[ok]{RESET} Archived {repo}")
        return True
    if "404" in (result.stderr or ""):
        print(f"  {YELLOW}[skip]{RESET} {repo} not found (already archived or "
              f"deleted, or a name this exec never used)")
        return None
    print(f"  {RED}[error]{RESET} Could not archive {repo}: "
          f"{(result.stderr or '').strip()}")
    return False


def archive_workspace_repo(slug: str) -> bool:
    """Archive the exec's data-overlay repo, CURRENT name and retired name.

    Returns False when a repo that exists could not be archived; the caller
    feeds that to `offboard_verdict`.

    Two defects lived here. It archived `31c-workspace-{slug}` alone -- the
    pre-cutover name -- so a current-model exec's real data overlay,
    `repo_name_for(slug)`, was never archived at all. `exec_repos` was corrected
    for exactly this on 2026-08-25 and this function was left behind, while the
    module docstring above went on asserting the repair. And its result was
    thrown away at the call site, so a failed archive could not stop the run
    printing "Offboarding complete".

    It also had no 404 branch, unlike its CRM sibling, so on every current-model
    exec it printed a red `[error]` about a repo that legitimately does not
    exist -- a permanent false alarm, which trains the operator to ignore the
    line that would matter.

    BOTH names are tried, for the same reason `exec_repos` keeps both: a 404 on
    a retired name costs one request; missing a repo the exec still holds costs
    their continued access to it.

    Neither name existing is a WARNING, not a failure: an already-deleted repo
    is a legitimate re-run state, and failing there would make a second
    offboard run permanently red.
    """
    print(f"\n{BOLD}Step 2: Archiving the exec's data-overlay repo{RESET}")
    results = [
        _archive_repo(f"{GITHUB_ORG}/{name}")
        for name in (repo_name_for(slug), f"31c-workspace-{slug}")
    ]
    if all(r is None for r in results):
        print(f"  {YELLOW}[warn]{RESET} Neither name exists on GitHub; "
              f"nothing was archived.")
    return False not in results


def archive_per_exec_crm_repo(slug: str) -> bool:
    """Archive the per-exec CRM repo. Retired model only, deliberately.

    The current model keeps an exec's contacts INSIDE their data overlay at
    `crm/contacts`, so there is no separate CRM repo to archive; that case
    answers 404 and skips. Returns False only on a real failure.
    """
    print(f"\n{BOLD}Step 2b: Archiving per-exec CRM repo (retired model){RESET}")
    return _archive_repo(f"{GITHUB_ORG}/31c-crm-{slug}") is not False


# Where an exec's contacts live, newest model first. `(repo, subpath)`.
#
# The current model keeps them INSIDE the data overlay at `crm/contacts` --
# the same path `scripts/aggregate-crm.py` clones and reads, which is the other
# fleet tool that touches exec contacts. `31c-crm-{slug}` with a top-level
# `contacts/` is the pre-cutover model, kept because an exec provisioned before
# the cutover still has one.
#
# Both levels matter: the retired REPO name and the retired SUBDIRECTORY are
# different retirements, and swapping only the repo name still finds nothing.
def _contacts_candidates(slug: str):
    # The literal below is a subpath INSIDE a cloned remote repo belonging to
    # another operator, not a path in this machine's data overlay. The
    # `get_*_dir()` seam resolves against THIS workspace's data root, so it
    # cannot name where a different exec keeps their contacts.
    return ((repo_name_for(slug), "crm/contacts"),  # leak-guard: ok (subpath in a cloned exec repo)
            (f"31c-crm-{slug}", "contacts"))


def _find_exec_contacts(slug: str) -> tuple[Path | None, bool]:
    """Locate the exec's contacts directory, cloning the repo if needed.

    Returns `(directory_or_None, looked_everywhere)`. `looked_everywhere` is
    False when a repo could not be reached at all -- "there are no contacts"
    and "I could not check" are different answers, and only the first one may
    be reported as a success.
    """
    workspace_root = get_workspace_root()
    reachable = False
    for repo_name, subpath in _contacts_candidates(slug):
        local = workspace_root.parent / repo_name
        if not local.exists():
            try:
                run_cmd(["gh", "repo", "clone", f"{GITHUB_ORG}/{repo_name}", str(local)])
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                # FileNotFoundError = no `gh` on PATH. Catching only
                # CalledProcessError let that crash the run partway through an
                # offboard, leaving contacts unpreserved and the registry
                # untouched with no rollback.
                print(f"  {YELLOW}[skip]{RESET} Could not clone {repo_name}: {exc}")
                continue
        else:
            run_cmd(["git", "pull"], cwd=str(local), check=False)
        reachable = True
        src = local / subpath
        if src.is_dir():
            print(f"  {GREEN}[ok]{RESET} Found contacts in {repo_name}/{subpath}")
            return src, True
    return None, reachable


def preserve_crm_contacts(slug: str) -> bool:
    """Snapshot the exec's contacts to a CEO-local backup before archiving.

    Looked only in the RETIRED `31c-crm-{slug}` repo, at a top-level
    `contacts/`. On the current model an exec's contacts sit in their data
    overlay at `crm/contacts`, which this never touched -- so on a
    current-model exec the step either failed loudly on the 404 or, with a
    stale local clone lying around, reported "no contacts directory found" and
    returned True. Zero contacts preserved, fed to the verdict as a success,
    immediately before the archive step made the repo read-only.
    """
    print(f"\n{BOLD}Step 3: Preserving CRM contacts{RESET}")

    workspace_root = get_workspace_root()
    src, looked_everywhere = _find_exec_contacts(slug)
    dst = get_outputs_dir() / "operations" / "offboarding" / f"{slug}-crm-final"

    if src is None:
        if not looked_everywhere:
            print(f"  {RED}[error]{RESET} No contacts repo could be reached for "
                  f"{slug}; nothing was preserved and nothing was checked.")
            return False
        print(f"  {YELLOW}[warn]{RESET} No contacts directory in any known "
              f"location for {slug}; there is nothing to preserve.")
        return True

    dst.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in src.iterdir():
        if item.is_file() and item.suffix == ".md":
            shutil.copy2(item, dst / item.name)
            count += 1

    # `dst` comes from get_outputs_dir(), which resolves under the DATA overlay --
    # a SIBLING of the engine clone on every conforming deployment, so
    # relative_to(workspace_root) raised ValueError and crashed the step AFTER
    # the contacts were already copied.
    try:
        shown = dst.relative_to(workspace_root)
    except ValueError:
        shown = dst
    print(f"  {GREEN}[ok]{RESET} Preserved {count} contacts to {shown}/")
    return True


def reassign_contacts(slug: str, reassign_to: str) -> None:
    """Copy contacts to CEO-local CRM with transfer notes.

    Same location resolution as `preserve_crm_contacts`, and for the same
    reason: this looked only in the retired repo at the retired subdirectory,
    so on a current-model exec it reported "No contacts to reassign" and the
    operator's explicit `--reassign-to` did nothing at all.
    """
    print(f"\n{BOLD}Step 4: Reassigning contacts to {reassign_to}{RESET}")
    src, looked_everywhere = _find_exec_contacts(slug)
    dst = get_crm_contacts_dir()

    if src is None:
        if not looked_everywhere:
            print(f"  {RED}[error]{RESET} No contacts repo could be reached; "
                  f"nothing was reassigned and nothing was checked.")
        else:
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
                frontmatter, n = re.subn(r"^owner:\s*.*$", f"owner: {reassign_to}",
                                         frontmatter, flags=re.MULTILINE)
                if n == 0:
                    # No `owner:` line to rewrite. `re.sub` returned the frontmatter
                    # unchanged and the contact landed in the new CRM still owned by
                    # nobody, which is the one thing "reassign" is supposed to do.
                    frontmatter = frontmatter + f"owner: {reassign_to}\n"
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
    # Per-instance DATA. The corporate-repo and engine-root fallbacks that stood
    # here resolved to paths that exist on no machine, and the corporate one
    # would have published a `private`-classified registry to every exec.
    registry_file = get_data_config_dir() / "exec-registry.json"

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

    # Atomic: a crash mid-write left a TRUNCATED audit log, and this file is the
    # only durable record that the offboard happened at all.
    atomic_write_text(log_file, existing + entry)
    print(f"  {GREEN}[ok]{RESET} Logged to audit/offboarding-log.md")


def offboard_verdict(revoke_ok: bool, preserved: bool,
                     residual: list[str],
                     repos_reachable: list[str] | None = None,
                     archived: bool = True) -> tuple[bool, list[str]]:
    """Decide whether this run may claim the offboard is complete.

    Pure, so it is testable without touching GitHub. The script used to print
    "Offboarding complete" unconditionally, including on a run where every
    collaborator DELETE returned 404 and the exec kept org-wide access.

    `repos_reachable` is the verification pass: a repo the exec can still reach
    AFTER every removal ran. It is the only one of these signals that measures
    the thing the operator actually cares about, so it is named separately and
    never folded into `residual`.

    `archived` defaults True so the existing call shape in the tests keeps
    working; the production call passes the real result. It carries a repo that
    EXISTS and could not be archived -- a name that simply does not exist is a
    skip, not a failure.
    """
    reasons: list[str] = []
    if not revoke_ok:
        reasons.append("at least one collaborator removal failed")
    if not preserved:
        reasons.append("CRM contacts were not preserved")
    if not archived:
        reasons.append("a repo that exists could not be archived")
    for item in residual:
        reasons.append(f"access remains: {item} (remove it by hand)")
    for repo in (repos_reachable or []):
        reasons.append(f"THE EXEC CAN STILL REACH {repo} - revoke it by hand NOW")
    return (not reasons), reasons


def print_manual_checklist(slug: str, exec_info: dict) -> None:
    """Print manual steps that require human action."""
    name = exec_info.get("name", slug) if exec_info else slug
    email = exec_info.get("email", "unknown") if exec_info else "unknown"

    github_username = exec_info.get("github_user") or slug if exec_info else slug

    print(f"\n{BOLD}{YELLOW}Manual Checklist (requires human action):{RESET}")
    print(f"  {BOLD}Org and team access is now removed by this script.{RESET} Run "
          f"these ONLY if a step above reported an error:")
    print(f"       gh api orgs/{GITHUB_ORG}/memberships/{github_username} -X DELETE")
    print(f"       gh api orgs/{GITHUB_ORG}/teams --jq '.[].slug' | while read t; do \\")
    print(f"         gh api orgs/{GITHUB_ORG}/teams/$t/memberships/{github_username} -X DELETE; done")
    print(f"       An organisation OWNER cannot be removed by API. Demote first.")
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
                        help="Exec slug to offboard (e.g., 'marlow-carter')")
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

    # Execute offboarding steps.
    #
    # Order matters and used to be wrong: the two archive calls ran BEFORE
    # preserve/reassign, so the irreversible step happened first and any
    # preserve failure left the contacts sitting in an archived repo. Recovery
    # steps now run first; archiving is last.
    # `_first_pass` is REPORTING ONLY and deliberately unused: the comment here
    # used to say "the first pass tells the removal step what to work on", which
    # was never true -- `remove_residual_access` takes no repo list and
    # re-derives everything itself.
    revoke_ok, _first_pass = revoke_github_access(slug, exec_info or {})
    residual = check_residual_access(slug, exec_info or {})
    if residual:
        residual = remove_residual_access(slug, exec_info or {})
    # Verified LAST, after every removal has run. Only this pass answers "can
    # they still get in?", which is the question the offboard exists to close.
    github_username = (exec_info or {}).get("github_user") or slug
    repos_reachable = [
        repo for repo in exec_repos(slug)
        if has_repo_access(repo, github_username) is not False
    ]
    preserved = preserve_crm_contacts(slug)

    if args.reassign_to:
        reassign_contacts(slug, args.reassign_to)

    # Both results reach the verdict. They used to be discarded, so a failure
    # to archive the exec's data overlay could not stop the run printing
    # "Offboarding complete".
    archived = archive_workspace_repo(slug)
    archived = archive_per_exec_crm_repo(slug) and archived

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

    complete, reasons = offboard_verdict(revoke_ok, preserved, residual,
                                         repos_reachable, archived)
    print(f"\n{'=' * 50}")
    if complete:
        print(f"{BOLD}{GREEN}Offboarding complete for {slug}.{RESET}")
    else:
        print(f"{BOLD}{RED}Offboarding INCOMPLETE for {slug}.{RESET}")
        for reason in reasons:
            print(f"  {RED}-{RESET} {reason}")
    print(f"{'=' * 50}")
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
