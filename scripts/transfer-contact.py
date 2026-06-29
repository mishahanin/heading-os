#!/usr/bin/env python3
"""Transfer a contact between execs in crm-central.

Moves the contact file from one exec's directory to another, updates the
owner field, logs the transfer, and commits the change.

Usage:
    python transfer-contact.py --contact "priya-anand" --from "misha-hanin" --to "sam-carter" [--repo PATH]
"""

import argparse
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import (
    get_workspace_root, validate_admin,
    get_corporate_repo_path, load_admin_config,
    get_per_exec_repo_path, get_all_active_exec_slugs,
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
    today = datetime.now().strftime("%Y-%m-%d")
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
    """Stage files and commit in the given repo."""
    for f in files:
        subprocess.run(["git", "add", str(f)], cwd=str(repo), check=True,
                       capture_output=True)
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
                        help="Target exec slug (e.g. sam-carter)")
    args = parser.parse_args()

    validate_admin()

    workspace_root = get_workspace_root()
    admin_slugs = set()
    try:
        cfg = load_admin_config()
        admin_slugs = set(cfg.get("admin_slugs") or [])
    except Exception:
        admin_slugs = {"misha-hanin"}

    def _contacts_dir(exec_slug: str) -> Path:
        if exec_slug in admin_slugs:
            return get_crm_contacts_dir()
        return get_per_exec_repo_path(exec_slug) / "contacts"

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
    backup_path = source_path.with_suffix(".md.transferred")
    source_path.rename(backup_path)
    print(f"{YELLOW}Source backed up:{RESET} {backup_path}")

    # Commit changes in each affected per-exec repo
    to_repo = to_contacts.parent
    from_repo = from_contacts.parent
    try:
        git_commit(to_repo, [target_path], (
            f"Transfer contact {args.contact} from {args.from_exec} to {args.to}"
        ))
        print(f"{GREEN}Committed to {args.to} repo.{RESET}")
    except subprocess.CalledProcessError as exc:
        print(f"{YELLOW}Warning:{RESET} git commit for target repo failed — commit manually.")
        print(f"  {exc.stderr.decode().strip() if exc.stderr else exc}")
    if to_repo != from_repo:
        try:
            git_commit(from_repo, [backup_path], (
                f"Backup transferred contact {args.contact} (moved to {args.to})"
            ))
            print(f"{GREEN}Committed backup to {args.from_exec} repo.{RESET}")
        except subprocess.CalledProcessError as exc:
            print(f"{YELLOW}Warning:{RESET} git commit for source repo failed — commit manually.")
            print(f"  {exc.stderr.decode().strip() if exc.stderr else exc}")

    # Confirmation
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{BOLD}Transfer complete:{RESET}")
    print(f"  Contact:  {args.contact}")
    print(f"  From:     {args.from_exec}")
    print(f"  To:       {args.to}")
    print(f"  Date:     {today}")
    print(f"  Backup:   {backup_path.name}")
    print()


if __name__ == "__main__":
    main()
