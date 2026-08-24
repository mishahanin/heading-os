#!/usr/bin/env python3
"""Transfer a contact between execs in crm-central.

Moves the contact file from one exec's directory to another, updates the
owner field, logs the transfer, and commits the change.

Usage:
    python transfer-contact.py --contact "priya-anand" --from "james-bond" --to "marlow-carter"
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import (
    get_default_tz,
    get_workspace_root, validate_admin,
    get_corporate_repo_path, get_admin_slugs,
    get_per_exec_repo_path, get_per_exec_contacts_dir, get_all_active_exec_slugs,
    get_crm_contacts_dir,
)
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def update_owner_in_frontmatter(text: str, new_owner: str) -> str:
    """Replace or add the owner field in YAML frontmatter."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        # No frontmatter; prepend a minimal block
        return f"---\nowner: {new_owner}\n---\n\n{text}"

    fm_block = m.group(1)
    body = text[m.end():]

    if re.search(r"^owner\s*:", fm_block, re.MULTILINE):
        fm_block = re.sub(r"^owner\s*:.*$", f"owner: {new_owner}", fm_block, flags=re.MULTILINE)
    else:
        fm_block += f"\nowner: {new_owner}"

    return f"---\n{fm_block}\n---\n{body}"


def append_transfer_note(text: str, from_exec: str, to_exec: str) -> str:
    """Append a transfer note to the interaction log section."""
    today = datetime.now(get_default_tz()).strftime("%Y-%m-%d")
    note = (
        f"\n### {today} | Note | Contact Transferred\n"
        f"Transferred from {from_exec} to {to_exec} by admin.\n"
    )

    # Try to append after "## Interaction Log"
    log_re = re.compile(r"(## Interaction Log\s*\n)", re.MULTILINE)
    match = log_re.search(text)
    if match:
        insert_pos = match.end()
        return text[:insert_pos] + note + text[insert_pos:]

    # No interaction log section — append at end
    return text.rstrip("\n") + "\n\n## Interaction Log\n" + note


def git_commit(repo: Path, files: list[Path], message: str) -> None:
    """Stage exactly `files` -- present OR deleted -- and commit.

    `git add <path>` on a path that no longer exists is a no-op with older git
    and an error with none of them staging the removal, so passing only the
    `.md.transferred` backup left the ORIGINAL contact still tracked in the
    source repo: the transfer was not durable (a fresh clone resurrected the
    contact in both places) and `git status` kept an unstaged deletion forever.
    `-A --` stages the removal as well as the addition, which is what a move is.
    """
    subprocess.run(["git", "add", "-A", "--", *[str(f) for f in files]],
                   cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=str(repo), check=True,
                   capture_output=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transfer a contact between execs (per-exec CRM model)."
    )
    parser.add_argument("--contact", required=True, help="Contact slug (e.g. priya-anand)")
    parser.add_argument("--from", dest="from_exec", required=True,
                        help="Source exec slug (e.g. misha-hanin)")
    parser.add_argument("--to", required=True,
                        help="Target exec slug (e.g. marlow-carter)")
    args = parser.parse_args()

    validate_admin()

    workspace_root = get_workspace_root()
    # `get_admin_slugs()` is the shared resolver `validate_admin()` itself uses,
    # so the two agree by construction. The local reimplementation this replaces
    # read `admin.json` directly and fell back to an EMPTY set whenever the file
    # was absent or lacked the key -- and an empty set means the operator is not
    # an admin, so their own contacts resolved to a per-exec overlay directory
    # instead of the CEO crm tree. The transfer then succeeded, silently, into
    # the wrong repo.
    admin_slugs = set(get_admin_slugs())

    def _contacts_dir(exec_slug: str) -> Path:
        if exec_slug in admin_slugs:
            return get_crm_contacts_dir()
        return get_per_exec_contacts_dir(exec_slug)

    from_contacts = _contacts_dir(args.from_exec)
    to_contacts = _contacts_dir(args.to)

    source_path = from_contacts / f"{args.contact}.md"
    target_dir = to_contacts
    target_path = target_dir / f"{args.contact}.md"

    # Validate source exists
    if not source_path.exists():
        print(f"{RED}ERROR:{RESET} Source contact not found: {source_path}")
        sys.exit(1)

    # Check target doesn't already have it
    if target_path.exists():
        print(f"{RED}ERROR:{RESET} Target exec already has this contact: {target_path}")
        print(f"  Use {CYAN}merge-contacts.py{RESET} to merge the two versions instead:")
        print(f"    python merge-contacts.py --contact \"{args.contact}\" "
              f"--from \"{args.from_exec}\" --into \"{args.to}\"")
        sys.exit(1)

    # Ensure target directory exists
    target_dir.mkdir(parents=True, exist_ok=True)

    # Read, update owner, add transfer note
    text = source_path.read_text(encoding="utf-8")
    text = update_owner_in_frontmatter(text, args.to)
    text = append_transfer_note(text, args.from_exec, args.to)

    # Write to target
    target_path.write_text(text, encoding="utf-8")
    print(f"{GREEN}Contact written:{RESET} {target_path}")

    # Backup source
    # Date-stamped, and never clobbering: `with_suffix(".md.transferred")` is a
    # single fixed name, so transferring the same contact a second time renamed
    # the new file over the previous backup and destroyed it silently.
    stamp = datetime.now(get_default_tz()).strftime("%Y%m%d")
    backup_path = source_path.with_name(f"{source_path.stem}.md.transferred-{stamp}")
    suffix = 2
    while backup_path.exists():
        backup_path = source_path.with_name(f"{source_path.stem}.md.transferred-{stamp}-{suffix}")
        suffix += 1
    source_path.rename(backup_path)
    print(f"{YELLOW}Source backed up:{RESET} {backup_path}")

    # Commit changes in each affected per-exec repo
    to_repo = to_contacts.parent
    from_repo = from_contacts.parent
    # When both execs live in ONE repo, the target commit must carry the move
    # entire -- the new file, the backup, and the deletion of the original. The
    # `if to_repo != from_repo` guard below was the ONLY commit that touched the
    # backup, so in the same-repo case it was left uncommitted altogether.
    first_paths = [target_path]
    if to_repo == from_repo:
        first_paths += [backup_path, source_path]
    try:
        git_commit(to_repo, first_paths, (
            f"Transfer contact {args.contact} from {args.from_exec} to {args.to}"
        ))
        print(f"{GREEN}Committed to {args.to} repo.{RESET}")
    except subprocess.CalledProcessError as exc:
        print(f"{YELLOW}Warning:{RESET} git commit for target repo failed — commit manually.")
        print(f"  {exc.stderr.decode().strip() if exc.stderr else exc}")
    if to_repo != from_repo:
        try:
            # source_path as well as backup_path: the rename above left the
            # original tracked, and only naming it stages the deletion.
            git_commit(from_repo, [backup_path, source_path], (
                f"Backup transferred contact {args.contact} (moved to {args.to})"
            ))
            print(f"{GREEN}Committed backup to {args.from_exec} repo.{RESET}")
        except subprocess.CalledProcessError as exc:
            print(f"{YELLOW}Warning:{RESET} git commit for source repo failed — commit manually.")
            print(f"  {exc.stderr.decode().strip() if exc.stderr else exc}")

    # Confirmation
    today = datetime.now(get_default_tz()).strftime("%Y-%m-%d")
    print(f"\n{BOLD}Transfer complete:{RESET}")
    print(f"  Contact:  {args.contact}")
    print(f"  From:     {args.from_exec}")
    print(f"  To:       {args.to}")
    print(f"  Date:     {today}")
    print(f"  Backup:   {backup_path.name}")
    print()


if __name__ == "__main__":
    main()
