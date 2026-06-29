#!/usr/bin/env python3
"""Merge two execs' versions of the same contact in crm-central.

Combines YAML frontmatter, interaction logs, and strategic notes from both
versions into a single authoritative file under the target exec's directory.

Usage:
    python merge-contacts.py --contact "priya-anand" --from "misha-hanin" --into "sam-carter" [--repo PATH]
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
    get_per_exec_repo_path, get_all_active_exec_slugs,
    get_crm_contacts_dir,
)
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET


# ---------------------------------------------------------------------------
# YAML / Markdown parsing helpers
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Cadence labels ranked from most frequent (shortest interval) to least
CADENCE_RANK = {
    "daily": 0,
    "weekly": 1,
    "biweekly": 2,
    "fortnightly": 2,
    "monthly": 3,
    "quarterly": 4,
    "biannual": 5,
    "annual": 6,
    "yearly": 6,
    "as-needed": 7,
    "none": 8,
}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body) from a markdown file with YAML front matter.

    NOT MIGRATED to ``scripts.utils.markdown.parse_frontmatter`` (deferred from
    Phase 6.2). This parser is paired with ``serialize_frontmatter`` below, which
    round-trips the dict back to a YAML block using a naive ``f"{key}: {value}"``
    template that assumes every value is a plain string or a list of plain
    strings. The shared util uses ``yaml.safe_load`` and would coerce ISO dates,
    booleans, and ints into native Python types (e.g. ``datetime.date``) that the
    serializer cannot stringify safely - corrupting the merged CRM file. Keep
    the paired parser/serializer until both sides migrate together.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    raw_yaml = m.group(1)
    body = text[m.end():]
    fm: dict = {}
    for line in raw_yaml.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # Handle simple lists on a single line like "[a, b]"
        if value.startswith("[") and value.endswith("]"):
            value = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",") if v.strip()]
        elif value.startswith('"') and value.endswith('"') or value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        fm[key] = value
    return fm, body


def serialize_frontmatter(fm: dict) -> str:
    """Serialize a dict back to YAML frontmatter block."""
    lines = ["---"]
    for key, value in fm.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(str(v) for v in value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def extract_interaction_log(body: str) -> tuple[str, list[str], str]:
    """Split body into (pre_log, log_entries, post_log).

    Each log entry starts with ``### YYYY-MM-DD``.
    """
    log_header_re = re.compile(r"^(## Interaction Log\s*)$", re.MULTILINE)
    entry_re = re.compile(r"^### \d{4}-\d{2}-\d{2}", re.MULTILINE)

    header_match = log_header_re.search(body)
    if not header_match:
        return body, [], ""

    pre_log = body[:header_match.start()]
    rest = body[header_match.end():]

    # Split rest into entries
    positions = [m.start() for m in entry_re.finditer(rest)]
    entries: list[str] = []
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(rest)
        entries.append(rest[pos:end].rstrip("\n") + "\n")

    # Anything after the last entry (rare) is post_log
    post_log = ""
    if not positions:
        post_log = rest

    return pre_log, entries, post_log


def entry_date(entry: str) -> str:
    """Extract the date string from a log entry header."""
    m = re.match(r"^### (\d{4}-\d{2}-\d{2})", entry)
    return m.group(1) if m else "0000-00-00"


def pick_more_recent(a: str | None, b: str | None) -> str | None:
    """Return the more recent ISO date string, or whichever is non-empty."""
    if not a:
        return b
    if not b:
        return a
    return max(a, b)


def pick_higher_cadence(a: str | None, b: str | None) -> str | None:
    """Return the cadence with the shorter interval."""
    if not a:
        return b
    if not b:
        return a
    rank_a = CADENCE_RANK.get(a.lower(), 99)
    rank_b = CADENCE_RANK.get(b.lower(), 99)
    return a if rank_a <= rank_b else b


def merge_frontmatter(fm_from: dict, fm_into: dict, from_slug: str, into_slug: str) -> dict:
    """Merge two frontmatter dicts with the defined strategy."""
    merged = dict(fm_into)  # start with target as base

    # Union: add any keys present in source but missing in target
    for key, value in fm_from.items():
        if key not in merged:
            merged[key] = value

    # Special merge rules
    merged["last_touch"] = pick_more_recent(
        fm_from.get("last_touch"), fm_into.get("last_touch")
    )
    merged["cadence"] = pick_higher_cadence(
        fm_from.get("cadence"), fm_into.get("cadence")
    )

    # Owner is the target
    merged["owner"] = into_slug

    # Track provenance
    prev = merged.get("previous_owners", [])
    if isinstance(prev, str):
        prev = [prev] if prev else []
    if from_slug not in prev:
        prev.append(from_slug)
    merged["previous_owners"] = prev

    return merged


def merge_notes(body_from: str, body_into: str, from_slug: str, into_slug: str) -> str:
    """Merge interaction logs chronologically and combine strategic notes."""
    pre_into, entries_into, post_into = extract_interaction_log(body_into)
    pre_from, entries_from, post_from = extract_interaction_log(body_from)

    # Interleave log entries by date (newest first after sort, but we keep chronological)
    all_entries = entries_into + entries_from
    all_entries.sort(key=entry_date)

    # Combine strategic / free-text sections
    combined_pre = pre_into.rstrip("\n")
    extra_from = pre_from.strip()
    if extra_from:
        combined_pre += f"\n\n---\n\n**Notes merged from {from_slug}:**\n\n{extra_from}"
    combined_pre += "\n\n"

    # Rebuild body
    result = combined_pre + "## Interaction Log\n\n"
    for entry in all_entries:
        result += entry.rstrip("\n") + "\n\n"

    # Append any trailing content
    trailing = (post_into.strip() + "\n" + post_from.strip()).strip()
    if trailing:
        result += trailing + "\n"

    return result


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git_commit(repo: Path, files: list[Path], message: str) -> None:
    """Stage files and commit in the given repo."""
    for f in files:
        subprocess.run(["git", "add", str(f)], cwd=str(repo), check=True,
                       capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=str(repo), check=True,
                   capture_output=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge two execs' versions of the same contact (per-exec CRM model)."
    )
    parser.add_argument("--contact", required=True, help="Contact slug (e.g. priya-anand)")
    parser.add_argument("--from", dest="from_exec", required=True,
                        help="Source exec slug (e.g. misha-hanin)")
    parser.add_argument("--into", required=True,
                        help="Target exec slug (e.g. sam-carter)")
    args = parser.parse_args()

    validate_admin()

    # Source: per-exec CRM repo for from_exec, or CEO local CRM for misha-hanin
    workspace_root = get_workspace_root()
    admin_slugs = set()
    try:
        from scripts.utils.workspace import load_admin_config
        cfg = load_admin_config()
        admin_slugs = set(cfg.get("admin_slugs") or [])
    except Exception:
        admin_slugs = {"misha-hanin"}

    def _contacts_dir(exec_slug: str) -> Path:
        if exec_slug in admin_slugs:
            return get_crm_contacts_dir()
        return get_per_exec_repo_path(exec_slug) / "contacts"

    from_contacts = _contacts_dir(args.from_exec)
    into_contacts = _contacts_dir(args.into)

    source_path = from_contacts / f"{args.contact}.md"
    target_path = into_contacts / f"{args.contact}.md"

    # Validate both files exist
    if not source_path.exists():
        print(f"{RED}ERROR:{RESET} Source file not found: {source_path}")
        sys.exit(1)
    if not target_path.exists():
        print(f"{RED}ERROR:{RESET} Target file not found: {target_path}")
        print(f"  Hint: use transfer-contact.py if only one exec has this contact.")
        sys.exit(1)

    # Parse both
    source_text = source_path.read_text(encoding="utf-8")
    target_text = target_path.read_text(encoding="utf-8")

    fm_from, body_from = parse_frontmatter(source_text)
    fm_into, body_into = parse_frontmatter(target_text)

    # ---- Side-by-side comparison ----
    print(f"\n{BOLD}{CYAN}=== Contact Merge: {args.contact} ==={RESET}\n")
    compare_keys = ["name", "company", "role", "last_touch", "cadence", "owner",
                    "priority", "status", "email", "phone"]
    print(f"  {'Field':<16} {args.from_exec:<30} {args.into:<30}")
    print(f"  {'─' * 16} {'─' * 30} {'─' * 30}")
    for key in compare_keys:
        val_from = str(fm_from.get(key, "—"))
        val_into = str(fm_into.get(key, "—"))
        marker = f"{YELLOW}*{RESET}" if val_from != val_into else " "
        print(f" {marker}{key:<16} {val_from:<30} {val_into:<30}")
    print()

    # ---- Merge ----
    merged_fm = merge_frontmatter(fm_from, fm_into, args.from_exec, args.into)
    merged_body = merge_notes(body_from, body_into, args.from_exec, args.into)
    merged_text = serialize_frontmatter(merged_fm) + "\n" + merged_body

    # Write merged file
    target_path.write_text(merged_text, encoding="utf-8")
    print(f"{GREEN}Merged file written:{RESET} {target_path}")

    # Backup source
    backup_path = source_path.with_suffix(".md.merged")
    source_path.rename(backup_path)
    print(f"{YELLOW}Source backed up:{RESET}   {backup_path}")

    # Commit changes in each affected per-exec repo
    into_repo = into_contacts.parent
    from_repo = from_contacts.parent
    try:
        git_commit(into_repo, [target_path], (
            f"Merge contact {args.contact} from {args.from_exec} into {args.into}"
        ))
        print(f"{GREEN}Committed to {args.into} repo.{RESET}")
    except subprocess.CalledProcessError as exc:
        print(f"{YELLOW}Warning:{RESET} git commit for target repo failed — commit manually.")
        print(f"  {exc.stderr.decode().strip() if exc.stderr else exc}")
    if into_repo != from_repo:
        try:
            git_commit(from_repo, [backup_path], (
                f"Backup merged contact {args.contact} (transferred to {args.into})"
            ))
            print(f"{GREEN}Committed backup to {args.from_exec} repo.{RESET}")
        except subprocess.CalledProcessError as exc:
            print(f"{YELLOW}Warning:{RESET} git commit for source repo failed — commit manually.")
            print(f"  {exc.stderr.decode().strip() if exc.stderr else exc}")

    # Summary
    entries_from = len(extract_interaction_log(body_from)[1])
    entries_into = len(extract_interaction_log(body_into)[1])
    print(f"\n{BOLD}Merge summary:{RESET}")
    print(f"  Interaction log entries: {entries_from} (source) + {entries_into} (target) merged chronologically")
    print(f"  Owner:       {merged_fm.get('owner')}")
    print(f"  Last touch:  {merged_fm.get('last_touch')}")
    print(f"  Cadence:     {merged_fm.get('cadence')}")
    print(f"  Provenance:  {merged_fm.get('previous_owners')}")
    print()


if __name__ == "__main__":
    main()
