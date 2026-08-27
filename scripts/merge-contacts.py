#!/usr/bin/env python3
"""Merge two execs' versions of the same contact in crm-central.

Combines YAML frontmatter, interaction logs, and strategic notes from both
versions into a single authoritative file under the target exec's directory.

Usage:
    python merge-contacts.py --contact "priya-anand" --from "misha-hanin" --into "marlow-carter"
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
from scripts.utils.atomic import atomic_write_text
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET

# A CRM contact filename stem: no separators, no dots, no traversal.
_CONTACT_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


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


class _Raw(str):
    """A frontmatter line this parser does not interpret, kept VERBATIM.

    Comments, blank lines and anything else without a colon used to hit
    `if ":" not in line: continue` and vanish, because the serializer rebuilds
    the block from the dict alone. A merge asked to change one field silently
    deleted every colon-free comment in the record.

    A comment WITH a colon was worse in the other direction: `# reviewed: 2026-01`
    parsed as the key `"# reviewed"`, so `merge_frontmatter`'s union could inject
    one record's comment into another. Both directions are gone: a line whose
    first non-space character is `#` never becomes a key.
    """


class _Block(_Raw):
    """A key whose value is an indented block, kept VERBATIM as its own lines.

    A nested mapping (`address:` with indented `street:` / `city:` under it) had
    no branch at all: the block-list scan broke on the first non-`- ` line and
    the children fell through to the outer loop, where each parsed as a
    TOP-LEVEL key. The record came back with `address:` emptied and its children
    hoisted -- and `merge_frontmatter`'s union then carried the hoisted keys into
    the target on the first merge.

    This parser is deliberately naive (see `parse_frontmatter`), so it does not
    try to understand the nesting. It holds the lines and writes them back
    byte-for-byte, which is all a merge of a DIFFERENT field needs.
    """

    def __new__(cls, lines):
        obj = super().__new__(cls, "")
        obj.lines = list(lines)
        return obj


class _Empty(str):
    """A key with no value, remembering the exact text after its colon.

    Measured 2026-08-25 over the live 334-record corpus: 132 records write
    `timezone: ` WITH a trailing space, and the rest write it without one. No
    single template is right for both, so the style is carried -- exactly as
    `_Quoted` carries quote style and `_BlockList` carries item indent.
    Normalising in either direction rewrites a third of the corpus on a merge
    that was asked to change one field.

    Compares equal to "" like any other empty value, so nothing downstream has
    to know it exists.
    """

    raw = ""

    def __new__(cls, raw_value: str):
        obj = super().__new__(cls, "")
        obj.raw = raw_value
        return obj


# Synthetic key prefix for `_Raw` lines. NUL cannot appear in a YAML key, so
# these can never collide with a real field, and `merge_frontmatter` skips them
# by this prefix.
RAW_KEY = "\x00raw"


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


def _split_flow(inner: str) -> list[str]:
    """Split a YAML flow-list body on commas OUTSIDE quotes.

    A plain `.split(",")` cut inside quoted items, so
    `tags: ["acme, inc", partner]` parsed as three entries and was written back
    as three -- silently rewriting a field the merge was never asked to touch.
    """
    out: list[str] = []
    buf: list[str] = []
    quote = ""
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            continue
        if ch == ",":
            out.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [item for item in out if item]


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
        stripped_line = line.strip()
        # A comment, a blank line, or anything else this parser cannot read as a
        # field is kept VERBATIM in place rather than dropped. The comment test
        # comes FIRST, so `# reviewed: 2026-01` stays a comment instead of
        # becoming the key `"# reviewed"`. See `_Raw`.
        if stripped_line.startswith("#") or not stripped_line or ":" not in line:
            fm[f"{RAW_KEY}{len(fm)}"] = _Raw(line)
            continue
        key_indent = len(line) - len(line.lstrip())
        key, _, raw_value = line.partition(":")
        key = key.strip()
        value = raw_value.strip()
        if value.startswith("[") and value.endswith("]"):
            # A flow list on one line: "tags: [a, b]".
            value = [_scalar(v) for v in _split_flow(value[1:-1]) if v]
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
                # Not a block list. It may still own an indented block -- a
                # nested mapping. Consume those lines verbatim (see `_Block`);
                # anything at or left of the key's own column belongs to the
                # parent level and is left for the outer loop.
                block = []
                while i < len(lines):
                    nxt = lines[i]
                    if not nxt.strip():
                        break
                    if len(nxt) - len(nxt.lstrip()) <= key_indent:
                        break
                    block.append(nxt)
                    i += 1
                value = _Block(block) if block else _Empty(raw_value)
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
        if isinstance(value, _Block):
            lines.append(f"{key}:")
            lines.extend(value.lines)
        elif isinstance(value, _Raw):
            lines.append(str(value))       # a comment or blank line, in place
        elif isinstance(value, _BlockList):
            lines.append(f"{key}:")
            lines.extend(f"{value.indent}- {_emit(v)}" for v in value)
        elif isinstance(value, list):
            lines.append(f"{key}: [{', '.join(_emit(v) for v in value)}]")
        elif isinstance(value, _Empty):
            lines.append(f"{key}:{value.raw}")
        else:
            lines.append(f"{key}: {_emit(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def extract_interaction_log(body: str) -> tuple[str, list[str], str]:
    """Split body into (pre_log, log_entries, post_log).

    Each log entry starts with ``### YYYY-MM-DD``.

    The log ENDS at the next level-2 header. ``## Interaction Log`` is itself
    level 2, so a later ``## Follow-ups`` is a sibling section, not part of the
    entry above it. Without that bound the last entry ran to the end of the
    file and swallowed every section after it -- and because `merge_notes`
    sorts entries by their date, that swallowed section was then RELOCATED by
    the date of the entry it was stuck to. A `## Follow-ups` block could move
    above an older entry from the other record, silently reordering a part of
    the file the merge was never asked to touch.

    A `###` entry header is not matched by the level-2 pattern: `### ` is three
    hashes, so `^## ` cannot align with it.
    """
    log_header_re = re.compile(r"^(## Interaction Log\s*)$", re.MULTILINE)
    entry_re = re.compile(r"^### \d{4}-\d{2}-\d{2}", re.MULTILINE)
    section_re = re.compile(r"^## ", re.MULTILINE)

    header_match = log_header_re.search(body)
    if not header_match:
        return body, [], ""

    pre_log = body[:header_match.start()]
    rest = body[header_match.end():]

    next_section = section_re.search(rest)
    log_end = next_section.start() if next_section else len(rest)

    # Split the log region into entries; everything from the next level-2
    # header on is post_log, as this function's name has always promised.
    positions = [m.start() for m in entry_re.finditer(rest) if m.start() < log_end]
    entries: list[str] = []
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else log_end
        entries.append(rest[pos:end].rstrip("\n") + "\n")

    post_log = rest if not positions else rest[log_end:]

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

    # Union: add any keys present in source but missing in target.
    # Comments and blank lines are NOT unioned: a comment describes the record
    # it was written in, and importing the source's would drop it into the
    # target at an arbitrary position under a synthetic key.
    for key, value in fm_from.items():
        if key.startswith(RAW_KEY):
            continue
        if key not in merged:
            merged[key] = value

    # Special merge rules. Assigned only when a value exists: both helpers
    # return None when NEITHER record carries the field, and the serializer's
    # `str(value)` turned that into the literal text `last_touch: None` in the
    # merged file. `cadence: None` then re-parsed as the string "none", which
    # `CADENCE_RANK` scores as a genuine cadence label (rank 8) rather than
    # unknown, so the invented value survived and outranked a real one.
    last_touch = pick_more_recent(
        fm_from.get("last_touch"), fm_into.get("last_touch")
    )
    if last_touch is not None:
        merged["last_touch"] = last_touch
    cadence = pick_higher_cadence(
        fm_from.get("cadence"), fm_into.get("cadence")
    )
    if cadence is not None:
        merged["cadence"] = cadence

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
    except Exception as exc:  # noqa: BLE001 - reported, then a named fallback
        # Silently narrowing the admin set to the current operator routed real
        # admins to per-exec repo paths that hold none of their contacts, so the
        # tool either said "Source file not found" or merged against the wrong
        # tree. Neither said why.
        print(f"{YELLOW}Warning:{RESET} could not read the admin config ({exc}); "
              f"treating only {operator_slug()!r} as an admin. Paths may be wrong.")
        admin_slugs = {operator_slug()}

    def _contacts_dir(exec_slug: str) -> Path:
        if exec_slug in admin_slugs:
            return get_crm_contacts_dir()
        return get_per_exec_contacts_dir(exec_slug)

    from_contacts = _contacts_dir(args.from_exec)
    into_contacts = _contacts_dir(args.into)

    # A CONTACT SLUG, not a path fragment. `--contact "../../address-book/vip"`
    # escaped the contacts directory entirely, and this tool then overwrote one
    # arbitrary file and renamed another -- its two destructive operations,
    # outside the intended tree. `validate_admin()` gates WHO, never WHERE, and
    # `memory-touch.py` already sets the house standard of refusing any path
    # that does not resolve inside its directory.
    if not _CONTACT_SLUG_RE.fullmatch(args.contact):
        print(f"{RED}ERROR:{RESET} --contact must be a bare slug "
              f"(letters, digits, hyphen, underscore); got {args.contact!r}")
        sys.exit(2)

    source_path = from_contacts / f"{args.contact}.md"
    target_path = into_contacts / f"{args.contact}.md"

    for label, path, root in (("source", source_path, from_contacts),
                              ("target", target_path, into_contacts)):
        resolved, root_resolved = path.resolve(), root.resolve()
        if resolved != root_resolved and root_resolved not in resolved.parents:
            print(f"{RED}ERROR:{RESET} {label} path escapes {root}: {resolved}")
            sys.exit(2)

    if source_path.resolve() == target_path.resolve():
        # Both slugs resolving to one contacts dir (two admins, or a typo) made
        # source and target the SAME file: the merge was written, then the only
        # copy was renamed to `.merged`, and the tool reported success over a
        # contact that had left its canonical path.
        print(f"{RED}ERROR:{RESET} --from and --into resolve to the same file "
              f"({source_path}); nothing to merge.")
        sys.exit(2)

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
    # Atomic: this is the authoritative merged CRM record, and a torn write
    # leaves it truncated.
    atomic_write_text(target_path, merged_text)
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
