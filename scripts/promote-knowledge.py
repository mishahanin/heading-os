#!/usr/bin/env python3
"""Promote a personal knowledge note to the corporate shared knowledge base.

Copies the note into heading-os-corporate/knowledge/shared/{type}/, adds provenance
metadata, resets status, marks the original, and commits to the corporate repo.

Usage:
    python promote-knowledge.py --note "path/to/note.md" --type "signals" [--corporate-repo PATH]
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import (
    get_workspace_root, validate_admin,
    get_corporate_repo_path, load_admin_config, get_default_tz,
)
from scripts.utils.colors import GREEN, GRAY, YELLOW, RED, CYAN, BOLD, RESET
from scripts.utils.git_push import current_branch, supervised_push
from scripts.utils.atomic import atomic_write_text
from scripts.utils.knowledge import KNOWLEDGE_TYPES
from scripts.utils.paths import get_data_root


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

VALID_TYPES = list(KNOWLEDGE_TYPES)

# Matches a promotion marker block appended by an EARLIER run, on any date and
# for any type. The date is what the old guard compared, which is why the guard
# was not idempotent: see strip_promotion_markers.
PROMOTION_MARKER_RE = re.compile(
    r"\n*---\n+> \*\*Promoted to corporate\*\* on \d{4}-\d{2}-\d{2} "
    r"-- shared/[^\n]*\n?"
)


def parse_frontmatter_raw(text: str) -> tuple[str | None, str]:
    """Return (frontmatter_raw_string, body). frontmatter_raw is None if missing.

    NOT MIGRATED to ``scripts.utils.markdown.parse_frontmatter`` (deferred from
    Phase 6.2). This function returns the YAML block as a raw string (not a
    parsed dict) by design - ``inject_frontmatter_fields`` then does line-by-line
    edits that preserve the author's original quoting, comments, ordering, and
    whitespace byte-for-byte. Round-tripping through ``yaml.safe_load`` +
    re-serialization would discard that fidelity and is incompatible with this
    script's "promote without rewriting" contract.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def inject_frontmatter_fields(fm_raw: str | None, fields: dict) -> str:
    """Add or overwrite fields in raw YAML frontmatter text."""
    lines: list[str] = []
    existing_keys: set[str] = set()

    if fm_raw:
        for line in fm_raw.splitlines():
            key_match = re.match(r"^(\w[\w_-]*)\s*:", line)
            if key_match:
                key = key_match.group(1)
                if key in fields:
                    # Overwrite with our value
                    lines.append(f"{key}: {fields[key]}")
                    existing_keys.add(key)
                    continue
            lines.append(line)

    # Append new fields that weren't already present
    for key, value in fields.items():
        if key not in existing_keys:
            lines.append(f"{key}: {value}")

    return "\n".join(lines)


def strip_promotion_markers(text: str) -> str:
    """Remove every promotion marker block a previous run appended.

    The old guard was ``if promotion_note.strip() not in original_text``, and
    ``promotion_note`` carries today's date. That deduplicates a SAME-DAY re-run
    and nothing else: a recovery re-run on the next calendar day compared
    against a marker dated yesterday, found no match, and appended a second
    block. Two "Promoted to corporate" footers then disagreed about when the
    note was shared, in the file that IS the audit trail for the promotion.

    Removing first and appending after keeps exactly one marker carrying the
    latest promotion date, which is the state the surrounding comment already
    claimed. Non-marker horizontal rules are untouched: the pattern requires the
    marker sentence immediately after the rule.
    """
    return PROMOTION_MARKER_RE.sub("", text)


def rebuild_file(fm_raw: str, body: str) -> str:
    """Reassemble frontmatter + body."""
    return f"---\n{fm_raw}\n---\n{body}"


def git_commit_and_push(repo: Path, files: list[Path], message: str) -> None:
    """Stage, commit, and push in the given repo. Raises on any failure.

    The push is SUPERVISED. A bare `git push` can exit 0 without advancing the
    ref -- the failure this repo documented and fixed elsewhere -- and this
    script then told the operator the note had reached every exec when it had
    not left the laptop.
    """
    for f in files:
        subprocess.run(["git", "add", str(f)], cwd=str(repo), check=True,
                       capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=str(repo), check=True,
                   capture_output=True)
    branch = current_branch(str(repo)) or "main"
    verdict = supervised_push(str(repo), branch=branch, stall_window=120,
                              label="promote-knowledge")
    if verdict["state"] != "ok":
        raise subprocess.CalledProcessError(
            1, "git push (supervised)",
            stderr=f"{verdict['state']}: {verdict['reason']}".encode())


def _provenance(source: Path) -> str:
    """Where the note came from, WITHOUT the private overlay's absolute path.

    `str(source)` recorded the CEO's fully resolved path -- machine username and
    the private overlay's directory layout -- into frontmatter that is then
    committed and pushed to the corporate repo every exec pulls. Relative to the
    data root it stays useful provenance and leaks nothing; if the note sits
    outside the data root, only its file name goes out.
    """
    try:
        return str(source.relative_to(get_data_root().resolve()))
    except ValueError:
        return source.name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Promote a personal knowledge note to corporate shared knowledge."
    )
    parser.add_argument("--note", required=True, type=Path,
                        help="Path to the source knowledge note (absolute or relative)")
    parser.add_argument("--type", required=True, choices=VALID_TYPES,
                        help="Knowledge type / subdirectory (e.g. signals, research)")
    parser.add_argument("--corporate-repo", type=Path, default=None,
                        help="Path to heading-os-corporate repo (default: auto-detect)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Replace an existing shared note of the same name")
    args = parser.parse_args()

    validate_admin()

    # Resolve source note
    source = args.note if args.note.is_absolute() else Path.cwd() / args.note
    source = source.resolve()
    if not source.exists():
        print(f"{RED}ERROR:{RESET} Source note not found: {source}")
        sys.exit(1)
    if not source.suffix == ".md":
        print(f"{RED}ERROR:{RESET} Expected a .md file, got: {source.name}")
        sys.exit(1)

    # Resolve corporate repo
    corp_repo = args.corporate_repo or get_corporate_repo_path()
    if not corp_repo.exists():
        print(f"{RED}ERROR:{RESET} Corporate repo not found at {corp_repo}")
        sys.exit(1)

    target_dir = corp_repo / "knowledge" / "shared" / args.type
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / source.name

    if target_path.exists() and not args.overwrite:
        # Refuse, do not warn-and-clobber. This is a SHARED repo: the existing
        # note may be another exec's, the overwrite is irreversible, and the
        # push propagates it to everyone before the warning has scrolled away.
        print(f"{RED}ERROR:{RESET} Target already exists: {target_path}")
        print(f"{GRAY}Pass --overwrite to replace it deliberately.{RESET}")
        sys.exit(1)
    if target_path.exists():
        print(f"{YELLOW}Overwriting existing shared note:{RESET} {target_path}")

    # Read and parse
    text = source.read_text(encoding="utf-8")
    fm_raw, body = parse_frontmatter_raw(text)

    today = datetime.now(get_default_tz()).strftime("%Y-%m-%d")

    # Prepare promoted version
    promoted_fields = {
        "promoted_from": _provenance(source),
        "promoted_date": today,
        "status": "growing",
    }
    new_fm = inject_frontmatter_fields(fm_raw, promoted_fields)
    promoted_text = rebuild_file(new_fm, body)

    # Mark the ORIGINAL first, then write the target. Both atomic.
    #
    # Order matters on a crash between the two writes. Marked-but-not-promoted
    # is a visible, self-correcting state -- the marker points at a shared note
    # the operator can see is missing, and a re-run completes it. The old order
    # produced the opposite: a promoted note in the corporate repo with no trace
    # in the source, which nothing surfaces. The marker is rewritten rather than
    # stacked -- any earlier block, on any date, is removed first -- so a re-run
    # leaves exactly one, carrying the latest promotion date.
    promotion_note = (
        f"\n\n---\n\n> **Promoted to corporate** on {today} "
        f"-- shared/{args.type}/{source.name}\n"
    )
    original_text = source.read_text(encoding="utf-8")
    marked_text = strip_promotion_markers(original_text).rstrip("\n") + promotion_note
    if marked_text != original_text:
        atomic_write_text(source, marked_text)
    print(f"{CYAN}Original marked:{RESET}       {source}")

    atomic_write_text(target_path, promoted_text)
    print(f"{GREEN}Promoted note written:{RESET} {target_path}")

    # Commit and push corporate repo
    try:
        git_commit_and_push(corp_repo, [target_path], (
            f"Promote knowledge note {source.name} to shared/{args.type}"
        ))
        print(f"{GREEN}Committed and pushed to corporate repo.{RESET}")
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode().strip() if exc.stderr else str(exc)
        print(f"{RED}ERROR:{RESET} git commit/push failed; the note did NOT reach "
              f"the corporate repo.")
        print(f"  {detail}")
        # Name the flag. The target file was already written above, so a plain
        # re-run of the same command hits the "Target already exists" refusal
        # and never reaches the push -- telling the operator to "re-run" without
        # that is advice that cannot work.
        print(f"{GRAY}The local files were written; the target now exists, so a "
              f"re-run needs --overwrite. Or commit and push {corp_repo} by "
              f"hand.{RESET}")
        # Non-zero, and no completion banner. Printing "Promotion complete" here
        # is what let a note be marked "Promoted to corporate" in the source
        # while it never left the laptop.
        sys.exit(1)

    # Confirmation
    print(f"\n{BOLD}Promotion complete:{RESET}")
    print(f"  Note:     {source.name}")
    print(f"  Type:     {args.type}")
    print(f"  Target:   {target_path}")
    print(f"  Status:   growing (reset)")
    print(f"  Date:     {today}")
    print()


if __name__ == "__main__":
    main()
