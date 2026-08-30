#!/usr/bin/env python3
"""Transfer a contact between execs, across their own per-exec data overlays.

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
from scripts.utils.atomic import atomic_write_text
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET
from scripts.utils.crm import stamped_backup_path, try_commit
from scripts.utils.markdown import FM_OK, set_frontmatter_field, split_frontmatter_raw


def update_owner_in_frontmatter(text: str, new_owner: str) -> str:
    """Replace or add the owner field in YAML frontmatter.

    The fences come from `scripts.utils.markdown`, not from a regex written
    here. The local one was `^---\\s*\\n(.*?)\\n---\\s*\\n`, and the trailing `\\n`
    was required: MEASURED 2026-08-29, a card whose file ENDS at the closing
    fence matched nothing, took the no-frontmatter branch, and had a SECOND
    block prepended -- so the card's real fields, name included, became body
    text that no reader of frontmatter can see.

    Reassembly is byte-preserving too. The old form rebuilt the document from
    pieces and dropped the blank line between the closing fence and the body on
    every transfer, and rewrote a CRLF card as LF. A transfer changes the owner;
    a diff in the rest of the file is churn the operator has to read anyway.
    """
    front, _rest, kind = split_frontmatter_raw(text)
    if kind != FM_OK or front is None:
        # No usable block. Creating one is THIS caller's policy, not the shared
        # helper's: `crm_autolog` leaves such a document alone instead.
        return f"---\nowner: {new_owner}\n---\n\n{text}"
    return set_frontmatter_field(text, "owner", new_owner)


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

    # Both slugs are checked against the ROSTER before anything is moved.
    # Neither helper below validates: `get_per_exec_repo_path` rejects only path
    # shapes (`/`, `\`, `..`), so any other string produced a directory name.
    # A typo in `--to` therefore created `../.heading-os-data-<typo>/crm/contacts/`,
    # wrote the contact there, failed the git commit into a warning because that
    # phantom tree is not a repo, then SUCCEEDED at committing the source
    # deletion in the real repo, printed "Transfer complete:", and exited 0.
    # The contact was gone from where it belonged and present nowhere anyone
    # would look. `get_all_active_exec_slugs` was already imported here and
    # never called.
    known = set(get_all_active_exec_slugs()) | admin_slugs
    for label, slug in (("--from", args.from_exec), ("--to", args.to)):
        if slug not in known:
            print(f"{RED}ERROR:{RESET} {label} {slug!r} is not an active exec "
                  f"or admin slug.")
            print(f"  Known slugs: {', '.join(sorted(known)) or '(none resolved)'}")
            sys.exit(1)

    # The contact slug is a path component too, and it was the only one nobody
    # checked. It goes straight into `from_contacts / f"{slug}.md"`, so
    # `--contact "../config"` resolved to `crm/contacts/../config.md`: MEASURED
    # 2026-08-29, a `crm/config.md` sitting OUTSIDE the contacts tree was
    # rewritten with an owner field, moved into the target exec's `crm/` root,
    # renamed to a `.transferred-` backup in the source repo, committed in both,
    # and reported as "Transfer complete:" with exit 0. Same three shapes
    # `get_per_exec_repo_path` rejects for an exec slug, and for the same reason.
    if (not args.contact or "/" in args.contact or "\\" in args.contact
            or ".." in args.contact):
        print(f"{RED}ERROR:{RESET} --contact {args.contact!r} is not a contact "
              f"slug. Expected a bare file stem such as 'priya-anand', with no "
              f"'/', '\\' or '..'.")
        sys.exit(1)

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
    # Atomic, like its twin in merge-contacts.py. A plain write_text on the
    # TARGET of a move is a partial file if the process dies mid-write, and
    # the source has already been read but not yet renamed at that point.
    atomic_write_text(target_path, text)
    print(f"{GREEN}Contact written:{RESET} {target_path}")

    # Backup source. Date-stamped and never clobbering: see
    # scripts/utils/crm.stamped_backup_path, which is where this logic now lives
    # so merge-contacts.py cannot drift from it again. It did: the same four
    # lines were fixed here and left broken there for weeks.
    backup_path = stamped_backup_path(source_path, "transferred")
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
    # A move spans TWO repositories, and a failure of the first commit used to
    # be downgraded to a warning that fell straight through to the second - so
    # the source repo committed the removal while the target's copy stayed
    # untracked. In a fresh clone the contact then existed in NEITHER repo, and
    # the run printed "Transfer complete:" and exited 0.
    #
    # The commit of the REMOVAL is now conditional on the addition landing. The
    # working tree still holds both halves either way; what changes is that the
    # loss is no longer made durable, and the exit code says so.
    target_committed = try_commit(
        git_commit, to_repo, first_paths,
        f"Transfer contact {args.contact} from {args.from_exec} to {args.to}",
        f"target ({args.to})")
    source_committed = True
    if to_repo != from_repo:
        if target_committed:
            # source_path as well as backup_path: the rename above left the
            # original tracked, and only naming it stages the deletion.
            source_committed = try_commit(
                git_commit, from_repo, [backup_path, source_path],
                f"Backup transferred contact {args.contact} (moved to {args.to})",
                f"source ({args.from_exec})")
        else:
            source_committed = False
            print(f"{YELLOW}Skipped the source-repo commit.{RESET} Committing the "
                  f"removal while the addition is uncommitted would leave the "
                  f"contact in neither repository.")

    # Confirmation
    today = datetime.now(get_default_tz()).strftime("%Y-%m-%d")
    torn = not (target_committed and source_committed)
    header = (f"{BOLD}{RED}Transfer INCOMPLETE:{RESET}" if torn
              else f"{BOLD}Transfer complete:{RESET}")
    print(f"\n{header}")
    print(f"  Contact:  {args.contact}")
    print(f"  From:     {args.from_exec}")
    print(f"  To:       {args.to}")
    print(f"  Date:     {today}")
    print(f"  Backup:   {backup_path.name}")
    if torn:
        print(f"  {RED}The files are moved on disk but at least one repository "
              f"did not commit.{RESET}")
        print(f"  {RED}Commit both by hand before anyone clones either repo.{RESET}")
    print()
    if torn:
        sys.exit(1)


if __name__ == "__main__":
    main()
