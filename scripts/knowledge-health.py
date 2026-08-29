#!/usr/bin/env python3
"""
Knowledge Base Health Engine for 31C Workspace

Scans the `/zk`-schema note directories under the knowledge root, validates
frontmatter schema, reports note counts by type and status, flags stale seeds,
orphan notes, and keyword frequency.

Scope, stated because it used to be over-claimed. This engine reads the
IMMEDIATE child directories of the knowledge root, minus the trees that carry a
different schema and have their own reader (`OTHER_SCHEMA_DIRS` below). It
reports every `.md` it did not read, so a directory it does not understand is
loud rather than silent.

Until 2026-08-28 the scan boundary was a hardcoded list of eight directory
names, and the docstring said "Scans knowledge/ directory". MEASURED against the
live knowledge root on that date: the directory `signal/` held one note and the
list spelled it `signals`, so the tool printed `Notes: 1 total` over a root
holding two `/zk` notes, and said nothing. The note was absent from the total,
the status and type counts, the stale-seed list, the orphan set, the schema-issue
list, the keyword frequency, `--json`, and the generated INDEX.md.

Usage:
    python scripts/knowledge-health.py           # terminal output (color-coded)
    python scripts/knowledge-health.py --json    # JSON for programmatic use
"""

import argparse
import io
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Force UTF-8 stdout on Windows (avoids cp1252 encoding errors)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET
from scripts.utils.workspace import display_path, get_default_tz, get_knowledge_dir, get_shared_knowledge_dir, is_exec_workspace
from scripts.utils.markdown import frontmatter_date, frontmatter_list
from scripts.utils.markdown import parse_frontmatter as _parse_frontmatter

KNOWLEDGE_DIR = get_knowledge_dir()
INDEX_FILE = KNOWLEDGE_DIR / "INDEX.md"
SHARED_KNOWLEDGE_DIR = get_shared_knowledge_dir()
TODAY = datetime.now(get_default_tz()).date()

VALID_TYPES = {"fleeting", "signal", "decision", "meeting", "research", "strategy", "people", "technology"}
VALID_STATUSES = {"seed", "growing", "evergreen", "archived"}
VALID_CONFIDENCES = {"high", "medium", "low", "unverified"}
REQUIRED_FIELDS = {"id", "title", "type", "keywords", "status", "created"}
STALE_SEED_DAYS = 7

# Immediate children of the knowledge root that are NOT `/zk`-schema notes.
# Each has its own reader, so folding them into this scan would double-count or
# report false schema violations:
#   odin-brain/  a different required-field list per subdir; scripts/odin-brain-health.py
#   shared/      the corporate tier, read by scan_shared_notes() below
OTHER_SCHEMA_DIRS = {"odin-brain", "shared"}

# Display ORDER ONLY, no longer the scan boundary. Directories found on disk that
# are absent from this list are scanned and sort after it, alphabetically.
#
# The list keeps its historical plural spellings on purpose. `VALID_TYPES` above
# is singular (`signal`, `decision`, `meeting`) and matches the note TYPE that
# `.claude/skills/zk/references/subcommands.md` defines, so the two never agreed;
# the live directory is `signal`. That disagreement is what hid a note while this
# list was the boundary. Renaming entries here would assert a directory
# vocabulary this engine does not own, and it no longer needs to: a name in
# neither the list nor the tree costs nothing, and a directory in the tree is
# read whatever it is called.
SUBDIR_ORDER = ["fleeting", "signals", "decisions", "meetings", "research", "strategy", "people", "technology"]


def parse_frontmatter(content):
    """Parse YAML frontmatter from a note file.

    Thin wrapper around :func:`scripts.utils.markdown.parse_frontmatter` that
    drops the body and preserves the historical return shape (dict only).
    Native YAML types are preserved when PyYAML is available.
    """
    fm, _body = _parse_frontmatter(content)
    return fm


def extract_links(content):
    """Extract [[wiki-links]] from note content."""
    return re.findall(r"\[\[(\d{14})(?:\|[^\]]+)?\]\]", content)


def note_subdirs(knowledge_dir=None):
    """The `/zk`-note directories to scan, DERIVED FROM DISK.

    Every immediate child directory of the knowledge root except
    `OTHER_SCHEMA_DIRS` and dotted directories. Ordered by `SUBDIR_ORDER` first,
    then alphabetically, so the generated INDEX.md keeps a stable shape while a
    directory nobody predicted still gets read.

    A hardcoded name list stood here until 2026-08-28. A directory the list did
    not name was not skipped-with-a-note; it was invisible, and the report's
    totals were quietly smaller than the tree.
    """
    root = KNOWLEDGE_DIR if knowledge_dir is None else knowledge_dir
    if not root.exists():
        return []
    found = sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and p.name not in OTHER_SCHEMA_DIRS and not p.name.startswith(".")
    )
    known = [n for n in SUBDIR_ORDER if n in found]
    return known + [n for n in found if n not in SUBDIR_ORDER]


def scanned_note_files(knowledge_dir=None):
    """Exactly the files `scan_notes` opens: one `*.md` glob per note subdir.

    `scan_notes` iterates THIS, and `unread_note_files` reconciles against it, so
    the read set has one definition. A second derivation of the same walk is a
    second thing to keep in step, and the pair would drift the way every other
    duplicated rule in this repo has.
    """
    root = KNOWLEDGE_DIR if knowledge_dir is None else knowledge_dir
    files = []
    for subdir in note_subdirs(root):
        dir_path = root / subdir
        if not dir_path.exists():
            continue
        files.extend((subdir, p) for p in sorted(dir_path.glob("*.md")))
    return files


def unread_note_files(knowledge_dir=None):
    """Every `.md` under the knowledge root that `scan_notes` did not read.

    The point is obligation 2 of `.claude/rules/scope-claims.md`: name what you
    left out, because silence about an exclusion reads as coverage. Deriving the
    scan set from disk fixes today's mismatch; this is what makes the NEXT one
    loud instead of silent.

    Three exclusions, each because another reader owns the file:
      * a `.md` at the root itself (INDEX.md and friends) is not a note;
      * `odin-brain/**` is scripts/odin-brain-health.py's corpus;
      * `shared/**` is scan_shared_notes()'s corpus.
    Anything else -- a note one directory deeper than this engine looks, a
    directory added after the reader was written -- is reported by name.
    """
    root = KNOWLEDGE_DIR if knowledge_dir is None else knowledge_dir
    if not root.exists():
        return []
    seen = {str(p) for _subdir, p in scanned_note_files(root)}
    missed = []
    for path in sorted(root.rglob("*.md")):
        if str(path) in seen:
            continue
        rel = path.relative_to(root)
        if len(rel.parts) == 1:
            continue
        if rel.parts[0] in OTHER_SCHEMA_DIRS:
            continue
        missed.append(path)
    return missed


def scan_notes():
    """Scan the `/zk`-schema note directories under the knowledge root.

    Iterates `scanned_note_files`, which `unread_note_files` reconciles against,
    so the read set has exactly one definition.
    """
    notes = []

    for subdir, file_path in scanned_note_files():
        content = file_path.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
        links = extract_links(content)

        issues = []
        # Validate required fields
        missing = REQUIRED_FIELDS - set(fm.keys())
        if missing:
            issues.append(f"missing fields: {', '.join(sorted(missing))}")

        # Validate type
        note_type = fm.get("type", "")
        if note_type and note_type not in VALID_TYPES:
            issues.append(f"invalid type: {note_type}")

        # Validate status
        status = fm.get("status", "")
        if status and status not in VALID_STATUSES:
            issues.append(f"invalid status: {status}")

        # Validate confidence
        confidence = fm.get("confidence", "")
        if confidence and confidence not in VALID_CONFIDENCES:
            issues.append(f"invalid confidence: {confidence}")

        # Check stale seed
        is_stale = False
        if status == "seed" and fm.get("created"):
            try:
                # Through the shared coercion, not `date.fromisoformat(str(...))`.
                # `parse_frontmatter` returns native YAML types, so a bare
                # `created: 2026-01-01` is a date and `created: 2026-01-01
                # 09:30:00` is a datetime whose `str()` this reader rejected.
                # MEASURED 2026-08-28: given one fixture note per shape,
                # odin-brain-health.py aged both and this engine aged only the
                # first, on the same rule over the same knowledge root.
                created = frontmatter_date(fm["created"])
                days_old = (TODAY - created).days
                if days_old > STALE_SEED_DAYS:
                    is_stale = True
                    issues.append(f"stale seed ({days_old} days old)")
            except ValueError as exc:
                # Reported, THEN skipped. `pass` sat here and is what the
                # global rule forbids outright: a seed whose `created` could
                # not be read left the stale count with nothing said, so the
                # report asserted a smaller number than it had measured. The
                # sibling engine fixed exactly this and printed a warning;
                # this copy did not.
                print(f"{YELLOW}warn:{RESET} {display_path(file_path)} has an "
                      f"unreadable `created:` value ({fm['created']!r}: {exc}); "
                      f"not aged.", file=sys.stderr)

        keywords = frontmatter_list(fm.get("keywords"))

        notes.append({
            "file": file_path.name,
            "path": display_path(file_path),
            "subdir": subdir,
            "id": str(fm.get("id", "")),
            "title": fm.get("title", file_path.stem),
            "type": note_type,
            "status": status,
            "keywords": keywords,
            "confidence": confidence,
            "created": str(fm.get("created", "")),
            "updated": str(fm.get("updated", "")),
            "links_out": links,
            "issues": issues,
            "is_stale": is_stale,
        })

    return notes


def find_orphans(notes):
    """Find notes with no incoming or outgoing links."""
    all_ids = {n["id"] for n in notes if n["id"]}
    linked_to = set()
    has_outgoing = set()

    for n in notes:
        if n["links_out"]:
            has_outgoing.add(n["id"])
            for link_id in n["links_out"]:
                linked_to.add(link_id)

    orphans = []
    for n in notes:
        nid = n["id"]
        if nid and nid not in linked_to and nid not in has_outgoing:
            orphans.append(n)

    return orphans


def keyword_frequency(notes):
    """Count keyword frequency across all notes."""
    counter = Counter()
    for n in notes:
        for kw in n["keywords"]:
            counter[kw.lower()] += 1
    return counter


def scan_shared_notes():
    """Scan shared knowledge notes (corporate tier) if available.

    The subdirectory set is derived from disk for the same reason `scan_notes`
    derives its own: this function carried a four-name list
    (`signals, strategy, research, technology`) that is a subset of the eight the
    other scan used, so a corporate note published into `decisions/` was counted
    by neither, and the count printed as "Corporate Shared Knowledge: N notes".
    """
    notes = []
    shared_dir = SHARED_KNOWLEDGE_DIR
    if not shared_dir.exists():
        return notes

    for subdir in note_subdirs(shared_dir):
        dir_path = shared_dir / subdir
        if not dir_path.exists():
            continue
        for file_path in sorted(dir_path.glob("*.md")):
            content = file_path.read_text(encoding="utf-8")
            fm = parse_frontmatter(content)
            notes.append({
                "file": file_path.name,
                "path": display_path(file_path),
                "subdir": f"shared/{subdir}",
                "id": str(fm.get("id", "")),
                "title": fm.get("title", file_path.stem),
                "type": fm.get("type", ""),
                "status": fm.get("status", ""),
                "keywords": frontmatter_list(fm.get("keywords")),
                "confidence": fm.get("confidence", ""),
                "created": str(fm.get("created", "")),
                "updated": str(fm.get("updated", "")),
                "links_out": extract_links(content),
                "issues": [],
                "is_stale": False,
            })
    return notes


def format_terminal_report(notes):
    """Format health report for terminal output."""
    lines = []
    lines.append(f"\n{BOLD}31C Knowledge Base Health{RESET}\n")

    # Shared knowledge stats (if exec workspace)
    if is_exec_workspace():
        shared = scan_shared_notes()
        if shared:
            lines.append(f"{CYAN}{BOLD}Corporate Shared Knowledge:{RESET} {len(shared)} notes")
            shared_types = Counter(n["type"] for n in shared)
            parts = [f"{t}: {c}" for t, c in sorted(shared_types.items())]
            lines.append(f"  {', '.join(parts)}")
            lines.append("")

    # Counts by status
    status_counts = Counter(n["status"] for n in notes)
    type_counts = Counter(n["type"] for n in notes)
    total = len(notes)

    lines.append(f"{BOLD}Notes:{RESET} {total} total | "
                 f"{GREEN}{status_counts.get('evergreen', 0)} evergreen{RESET} | "
                 f"{CYAN}{status_counts.get('growing', 0)} growing{RESET} | "
                 f"{YELLOW}{status_counts.get('seed', 0)} seeds{RESET} | "
                 f"{GRAY}{status_counts.get('archived', 0)} archived{RESET}")
    lines.append("")

    # By type
    if type_counts:
        lines.append(f"{BOLD}By Type:{RESET}")
        for t in sorted(VALID_TYPES):
            count = type_counts.get(t, 0)
            if count > 0:
                lines.append(f"  {t}: {count}")
        lines.append("")

    # Stale seeds
    stale = [n for n in notes if n["is_stale"]]
    if stale:
        lines.append(f"{RED}{BOLD}Stale Seeds (> {STALE_SEED_DAYS} days):{RESET}")
        for n in stale:
            lines.append(f"  {RED}{n['title']}{RESET} ({n['path']}) - created {n['created']}")
        lines.append("")

    # Orphans
    orphans = find_orphans(notes)
    if orphans:
        lines.append(f"{YELLOW}{BOLD}Orphan Notes (no links in or out):{RESET}")
        for n in orphans:
            lines.append(f"  {YELLOW}{n['title']}{RESET} ({n['path']})")
        lines.append("")

    # Schema issues
    issues_notes = [n for n in notes if n["issues"]]
    if issues_notes:
        lines.append(f"{RED}{BOLD}Schema Issues:{RESET}")
        # Filter per ISSUE, not per note. The outer test used to be
        # `if not n["is_stale"]`, which dropped the whole note -- so a note that
        # was both stale AND missing a required field had the missing field
        # reported nowhere on the terminal, while `--json` listed it. Two
        # consumers of one scan, two different health pictures. The inner filter
        # below is what the "already reported above" comment was written for:
        # the stale-seed line alone is the duplicate.
        for n in issues_notes:
            for issue in n["issues"]:
                if "stale seed" not in issue:
                    lines.append(f"  {RED}{n['title']}{RESET}: {issue}")
        lines.append("")

    # Tag cloud (top 15)
    kw_freq = keyword_frequency(notes)
    if kw_freq:
        top_kw = kw_freq.most_common(15)
        lines.append(f"{BOLD}Top Keywords:{RESET}")
        kw_strs = [f"{kw} ({count})" for kw, count in top_kw]
        lines.append(f"  {', '.join(kw_strs)}")
        lines.append("")

    if total == 0:
        lines.append(f"{GRAY}Knowledge base is empty. Use /zk add to create your first note.{RESET}")
        lines.append("")

    # What this run did NOT read, by name. Every number above is over the scanned
    # set; without this block a smaller set prints exactly like a complete one.
    unread = unread_note_files()
    if unread:
        lines.append(f"{YELLOW}{BOLD}Not read by this scan ({len(unread)}):{RESET}")
        for path in unread:
            lines.append(f"  {YELLOW}{display_path(path)}{RESET}")
        lines.append(f"  {GRAY}Counted nowhere above. This engine reads the immediate "
                     f"child directories of the knowledge root.{RESET}")
        lines.append("")

    odin_brain_dir = KNOWLEDGE_DIR / "odin-brain"
    if odin_brain_dir.exists():
        lines.append(f"{GRAY}Note: knowledge/odin-brain/ uses a separate schema - run scripts/odin-brain-health.py for its report.{RESET}")
        lines.append("")

    return "\n".join(lines)


def format_json(notes):
    """Output notes health as JSON."""
    status_counts = Counter(n["status"] for n in notes)
    type_counts = Counter(n["type"] for n in notes)
    orphans = find_orphans(notes)
    stale = [n for n in notes if n["is_stale"]]
    kw_freq = keyword_frequency(notes)

    output = {
        "total": len(notes),
        # The scope the totals cover, and what fell outside it. A consumer that
        # reads `total` as "every note under the knowledge root" was reading a
        # claim this tool never established; now it can check.
        "scanned_subdirs": note_subdirs(),
        "unread_files": [display_path(p) for p in unread_note_files()],
        "by_status": dict(status_counts),
        "by_type": dict(type_counts),
        "stale_seeds": [{"title": n["title"], "path": n["path"], "created": n["created"]} for n in stale],
        "orphans": [{"title": n["title"], "path": n["path"]} for n in orphans],
        "schema_issues": [
            {"title": n["title"], "path": n["path"], "issues": n["issues"]}
            for n in notes if n["issues"]
        ],
        "top_keywords": dict(kw_freq.most_common(20)),
        "notes": [
            {
                "id": n["id"],
                "title": n["title"],
                "type": n["type"],
                "status": n["status"],
                "keywords": n["keywords"],
                "path": n["path"],
                "created": n["created"],
                "links_out": n["links_out"],
            }
            for n in notes
        ],
    }
    return json.dumps(output, indent=2)


def regenerate_index(notes):
    """Regenerate knowledge/INDEX.md with current stats."""
    status_counts = Counter(n["status"] for n in notes)
    type_counts = Counter(n["type"] for n in notes)
    orphans = find_orphans(notes)
    kw_freq = keyword_frequency(notes)
    total = len(notes)

    lines = []
    lines.append("# Knowledge Base Index")
    lines.append("")
    lines.append("> Auto-generated by `/zk stats` and `scripts/knowledge-health.py`")
    lines.append(f"> Last updated: {TODAY.strftime('%Y-%m-%d')}")
    lines.append("")

    # Stats table
    lines.append("## Stats")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| Total notes | {total} |")
    lines.append(f"| Seeds | {status_counts.get('seed', 0)} |")
    lines.append(f"| Growing | {status_counts.get('growing', 0)} |")
    lines.append(f"| Evergreen | {status_counts.get('evergreen', 0)} |")
    lines.append(f"| Archived | {status_counts.get('archived', 0)} |")
    lines.append(f"| Orphans | {len(orphans)} |")
    lines.append("")

    # Recent notes (last 10 by created date)
    lines.append("## Recent Notes")
    lines.append("")
    sorted_notes = sorted(
        [n for n in notes if n["created"]],
        key=lambda n: n["created"],
        reverse=True
    )[:10]
    if sorted_notes:
        for n in sorted_notes:
            lines.append(f"- **{n['title']}** ({n['type']}, {n['status']}) - {n['created']} - `{n['path']}`")
    else:
        lines.append("*No notes yet. Use `/zk add` to create your first note.*")
    lines.append("")

    # By type
    lines.append("## Notes by Type")
    lines.append("")
    lines.append("| Type | Count |")
    lines.append("|------|-------|")
    for t in sorted(VALID_TYPES):
        lines.append(f"| {t} | {type_counts.get(t, 0)} |")
    lines.append("")

    # Tag cloud
    lines.append("## Tag Cloud")
    lines.append("")
    if kw_freq:
        top_kw = kw_freq.most_common(30)
        kw_strs = [f"**{kw}** ({count})" for kw, count in top_kw]
        lines.append(", ".join(kw_strs))
    else:
        lines.append("*No tags yet.*")
    lines.append("")

    # All notes by subdir
    lines.append("---")
    lines.append("")
    lines.append("## All Notes")
    lines.append("")
    if not notes:
        lines.append("*Empty knowledge base. Start with `/zk add` or `/zk distill`.*")
    else:
        # Group by the subdirs the notes CAME FROM, not by a fixed name list. The
        # stats table counts every scanned note; iterating a fixed list here meant
        # a note in an unlisted directory could be counted in the table above and
        # then be absent from this section, so one generated file disagreed with
        # itself.
        present = [s for s in note_subdirs() if any(n["subdir"] == s for n in notes)]
        extra = sorted({n["subdir"] for n in notes} - set(present))
        for subdir in present + extra:
            subdir_notes = [n for n in notes if n["subdir"] == subdir]
            if subdir_notes:
                lines.append(f"### {subdir.capitalize()}")
                lines.append("")
                for n in sorted(subdir_notes, key=lambda x: x["created"], reverse=True):
                    status_icon = {"seed": "S", "growing": "G", "evergreen": "E", "archived": "A"}.get(n["status"], "?")
                    kw_str = ", ".join(n["keywords"][:5]) if n["keywords"] else ""
                    lines.append(f"- [{status_icon}] **{n['title']}** - {kw_str} - `{n['path']}`")
                lines.append("")

    content = "\n".join(lines) + "\n"
    INDEX_FILE.write_text(content, encoding="utf-8")
    return True


def main():
    parser = argparse.ArgumentParser(description="31C Knowledge Base Health Engine")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--update-index", action="store_true", help="Regenerate INDEX.md")
    args = parser.parse_args()

    if not KNOWLEDGE_DIR.exists():
        print(f"{RED}Knowledge directory not found: {KNOWLEDGE_DIR}{RESET}")
        sys.exit(1)

    notes = scan_notes()

    if args.json:
        print(format_json(notes))
    else:
        print(format_terminal_report(notes))

    if args.update_index:
        if regenerate_index(notes):
            print(f"{GREEN}INDEX.md regenerated.{RESET}")


if __name__ == "__main__":
    main()
