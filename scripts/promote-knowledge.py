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
    get_corporate_repo_path, load_admin_config,
)
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

VALID_TYPES = [
    "fleeting", "signals", "decisions", "meetings",
    "research", "strategy", "people", "technology",
]


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


def rebuild_file(fm_raw: str, body: str) -> str:
    """Reassemble frontmatter + body."""
    return f"---\n{fm_raw}\n---\n{body}"


def git_commit_and_push(repo: Path, files: list[Path], message: str) -> None:
    """Stage, commit, and push in the given repo."""
    for f in files:
        subprocess.run(["git", "add", str(f)], cwd=str(repo), check=True,
                       capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=str(repo), check=True,
                   capture_output=True)
    subprocess.run(["git", "push"], cwd=str(repo), check=True,
                   capture_output=True)


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

    if target_path.exists():
        print(f"{YELLOW}Warning:{RESET} Target already exists and will be overwritten: {target_path}")

    # Read and parse
    text = source.read_text(encoding="utf-8")
    fm_raw, body = parse_frontmatter_raw(text)

    today = datetime.now().strftime("%Y-%m-%d")

    # Prepare promoted version
    promoted_fields = {
        "promoted_from": str(source),
        "promoted_date": today,
        "status": "growing",
    }
    new_fm = inject_frontmatter_fields(fm_raw, promoted_fields)
    promoted_text = rebuild_file(new_fm, body)

    # Write promoted file
    target_path.write_text(promoted_text, encoding="utf-8")
    print(f"{GREEN}Promoted note written:{RESET} {target_path}")

    # Mark original
    promotion_note = (
        f"\n\n---\n\n> **Promoted to corporate** on {today} "
        f"-- shared/{args.type}/{source.name}\n"
    )
    original_text = source.read_text(encoding="utf-8")
    source.write_text(original_text.rstrip("\n") + promotion_note, encoding="utf-8")
    print(f"{CYAN}Original marked:{RESET}       {source}")

    # Commit and push corporate repo
    try:
        git_commit_and_push(corp_repo, [target_path], (
            f"Promote knowledge note {source.name} to shared/{args.type}"
        ))
        print(f"{GREEN}Committed and pushed to corporate repo.{RESET}")
    except subprocess.CalledProcessError as exc:
        print(f"{YELLOW}Warning:{RESET} git commit/push failed — handle manually.")
        print(f"  {exc.stderr.decode().strip() if exc.stderr else exc}")

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
