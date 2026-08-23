#!/usr/bin/env python3
"""Merge two execs' versions of the same contact in crm-central.

Combines YAML frontmatter, interaction logs, and strategic notes from both
versions into a single authoritative file under the target exec's directory.

Usage:
    python merge-contacts.py --contact "priya-anand" --from "misha-hanin" --into "marlow-carter" [--repo PATH]
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
    get_per_exec_repo_path, get_per_exec_contacts_dir, get_all_active_exec_slugs,
    get_crm_contacts_dir,
)
from scripts.utils.operator_identity import operator_slug
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET


# ---------------------------------------------------------------------------
# YAML / Markdown parsing helpers
# ---------------------------------------------------------------------------

# `[ \t]*\n` after the closing `---`, not `\s*\n`. `\s` includes the newline, so
# the greedy form also swallowed every BLANK LINE that followed the frontmatter,
# and the writer then put exactly one back. A record with a blank line survived
# that round trip; a record with none came back with one inserted, which is a
# rewrite of a file the tool was asked to merge one field into. Invisible across
# the operator's 326 records, which all carry the blank line, and caught by
# `examples/crm/contacts/EXAMPLE-contact.md`, which does not.
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---[ \t]*\n", re.DOTALL)


class _BlockList(list):
    """A list that was written as `- item` lines, and goes back as those lines.

    Carries the indent of its items, because YAML accepts a block list at the
    same column as its key and 1 of the 326 live records uses that form. The
    style is all this carries; every other consumer sees an ordinary list.
    """

    indent = "  "

    def __init__(self, items, indent: str = "  "):
        super().__init__(items)
        self.indent = indent


class _Quoted(str):
    """A scalar that was written in quotes, and goes back in the same quotes.

    The parser strips the quotes so callers compare against a plain string. The
    serializer used to write the stripped value back, which is not the same
    document: `freeze_until: "2026-10-01"` is a string and `freeze_until:
    2026-10-01` is a date, to every reader downstream. Measured 2026-08-20 on
    the live overlay: 981 quoted values across 175 records, all of which a merge
    rewrote.
    """

    quote = '"'

    def __new__(cls, value: str, quote: str):
        obj = super().__new__(cls, value)
        obj.quote = quote
        return obj


def _scalar(raw: str):
    """Strip one layer of matching quotes, remembering that they were there."""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return _Quoted(raw[1:-1], raw[0])
    return raw


def _emit(value) -> str:
    """Write a scalar back in the quotes it arrived in."""
    if isinstance(value, _Quoted):
        return f"{value.quote}{value}{value.quote}"
    return str(value)

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

    Measured 2026-08-20, feeding both parsers through ``serialize_frontmatter``
    over the live 326-record corpus (165 contacts + 161 address-book entities):
    48 of 326 files would be written back with different bytes under
    ``parse_frontmatter_str`` - ``tags: [a, b]`` becomes ``tags: ['a', 'b']``,
    ``tribe_email_ok: true`` becomes ``True``, and ``yaml.safe_load`` truncates
    an unquoted value at a ``#`` (one record's ``source`` loses its trailing
    "#"-prefixed reference). A
    list-preserving str variant narrows that to 12 files but not to 0.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    raw_yaml = m.group(1)
    body = text[m.end():]
    fm: dict = {}
    lines = raw_yaml.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            # A flow list on one line: "tags: [a, b]".
            value = [_scalar(v.strip()) for v in value[1:-1].split(",") if v.strip()]
        elif not value:
            # A key with nothing after the colon may own a block list on the
            # lines below it. Those lines carry no colon, so the loop used to
            # skip them and write the key back empty - deleting the list from
            # a real CRM record. Consume them here instead. The item indent may
            # be zero: YAML allows a block list at its key's own column.
            items, indent = [], "  "
            while i < len(lines):
                item = lines[i]
                stripped = item.lstrip()
                if not stripped.startswith("- "):
                    break
                if not items:
                    indent = item[: len(item) - len(stripped)]
                items.append(_scalar(stripped[2:].strip()))
                i += 1
            if items:
                value = _BlockList(items, indent)
        else:
            value = _scalar(value)
        fm[key] = value
    return fm, body


def serialize_frontmatter(fm: dict) -> str:
    """Serialize a dict back to a YAML frontmatter block.

    A list keeps the style it was read in. `_BlockList` writes back as indented
    `- item` lines, every other list writes back as `[a, b]`. A merge changes
    the fields it was asked to change; restyling a field it only passed through
    is a diff the operator did not ask for and has to read anyway.
    """
    lines = ["---"]
    for key, value in fm.items():
        if isinstance(value, _BlockList):
            lines.append(f"{key}:")
            lines.extend(f"{value.indent}- {_emit(v)}" for v in value)
        elif isinstance(value, list):
            lines.append(f"{key}: [{', '.join(_emit(v) for v in value)}]")
        else:
            lines.append(f"{key}: {_emit(value)}")
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
    """Stage exactly `files` -- present or deleted -- and commit.

    The 2026-08-23 defect was in the CALLER, not here: this script renames the
    source contact to `.md.merged` and then passed only the backup path, so the
    deletion of the original was never staged and the commit shipped a second
    live copy of the contact. Measured on git 2.43: after `git add <backup>`
    alone, `git status` shows ` D contacts/x.md` -- unstaged. Naming the source
    path is what turns it into `R contacts/x.md -> contacts/x.md.merged`.

    `--all` is belt-and-braces for older git; the pathspec stays exactly the
    named files -- never their directory, which would sweep the operator's
    unrelated edits into this commit.
    """
    subprocess.run(["git", "add", "--all", "--", *[str(f) for f in files]],
                   cwd=str(repo), check=True, capture_output=True)
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
                        help="Target exec slug (e.g. marlow-carter)")
    args = parser.parse_args()

    validate_admin()

    # Source: per-exec CRM repo for from_exec, or CEO local CRM for misha-hanin
    workspace_root = get_workspace_root()
    admin_slugs = set()
    try:
        cfg = load_admin_config()
        admin_slugs = set(cfg.get("admin_slugs") or [])
    except Exception:
        admin_slugs = {operator_slug()}

    def _contacts_dir(exec_slug: str) -> Path:
        if exec_slug in admin_slugs:
            return get_crm_contacts_dir()
        return get_per_exec_contacts_dir(exec_slug)

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
    # No separator inserted here. `serialize_frontmatter` already ends with
    # `---\n`, and since the regex above stopped consuming blank lines the body
    # carries whatever gap the file had. Adding "\n" was what inserted one.
    merged_text = serialize_frontmatter(merged_fm) + merged_body

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
    # When both contacts live in ONE repo the second commit below is skipped
    # entirely, so the backup and the source deletion have to ride along here or
    # they are never committed at all.
    first_paths = [target_path]
    if into_repo == from_repo:
        first_paths += [backup_path, source_path]
    try:
        git_commit(into_repo, first_paths, (
            f"Merge contact {args.contact} from {args.from_exec} into {args.into}"
        ))
        print(f"{GREEN}Committed to {args.into} repo.{RESET}")
    except subprocess.CalledProcessError as exc:
        print(f"{YELLOW}Warning:{RESET} git commit for target repo failed — commit manually.")
        print(f"  {exc.stderr.decode().strip() if exc.stderr else exc}")
    if into_repo != from_repo:
        try:
            # source_path as well as backup_path: the rename above left the
            # original tracked, and only naming it stages the deletion.
            git_commit(from_repo, [backup_path, source_path], (
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
