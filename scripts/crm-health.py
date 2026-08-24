#!/usr/bin/env python3
"""
CRM Relationship Health Scorer for 31C Workspace

Tests: tests/test_a_closing_fence_that_only_half_read_crlf.py

Reads contact files from crm/contacts/, calculates health scores based on
last_touch dates and expected cadence, and outputs a relationship radar.

Usage:
    python scripts/crm-health.py              # print health radar to terminal
    python scripts/crm-health.py --update     # also regenerate radar table in people.md
    python scripts/crm-health.py --json       # output as JSON (for programmatic use)
"""

import argparse
import datetime as _dt
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.atomic import atomic_write_text
from scripts.utils.colors import GREEN, YELLOW, RED, GRAY, BOLD, RESET
from scripts.utils.workspace import (
    get_default_tz,
    get_crm_contacts_dir, get_crm_config_path, get_people_file,
    get_workspace_root,
)
from scripts.utils.crm import (
    NO_CADENCE_TYPES,
    parse_config,
    parse_frontmatter,
    parse_commitments,
    calculate_health,
    scan_contacts,
)

WORKSPACE = get_workspace_root()

CONTACTS_DIR = get_crm_contacts_dir()
CONFIG_FILE = get_crm_config_path()
PEOPLE_FILE = get_people_file()

TODAY = datetime.now(get_default_tz()).date()


def format_terminal_report(contacts, tribe_warnings=None):
    """Format health report for terminal output."""
    red = [c for c in contacts if c["health"] == "red"]
    yellow = [c for c in contacts if c["health"] == "yellow"]
    green = [c for c in contacts if c["health"] == "green"]
    gray = [c for c in contacts if c["health"] == "gray"]

    lines = []
    lines.append(f"\n{BOLD}31C Relationship Radar{RESET}\n")

    # Tribe detection warnings (before health sections)
    if tribe_warnings:
        lines.append(f"{YELLOW}{BOLD}TRIBE DETECTION WARNINGS{RESET}")
        for w in tribe_warnings:
            lines.append(
                f"  {YELLOW}{w['name']}{RESET} ({w['company']}) - type: {w['type']} - "
                f"has @31c.io email ({w['email']}) but is NOT typed as tribe/tribe-leadership"
            )
        lines.append("")

    if red:
        lines.append(f"{RED}{BOLD}RED - Overdue{RESET}")
        for c in red:
            days_str = f"{c['days_since']} days" if c["days_since"] is not None else "no recorded touch"
            lines.append(f"  {RED}{c['name']}{RESET} ({c['company']}) - {c['type']} - {days_str} (cadence: {c['cadence']})")
        lines.append("")

    if yellow:
        lines.append(f"{YELLOW}{BOLD}YELLOW - Approaching{RESET}")
        for c in yellow:
            lines.append(f"  {YELLOW}{c['name']}{RESET} ({c['company']}) - {c['type']} - {c['days_since']} days (cadence: {c['cadence']})")
        lines.append("")

    if green:
        lines.append(f"{GREEN}{BOLD}GREEN - On Track{RESET}")
        for c in green:
            lines.append(f"  {GREEN}{c['name']}{RESET} ({c['company']}) - {c['type']} - {c['days_since']} days (cadence: {c['cadence']})")
        lines.append("")

    if gray:
        lines.append(f"{GRAY}{BOLD}GRAY - No Cadence{RESET}")
        for c in gray:
            lines.append(f"  {GRAY}{c['name']}{RESET} ({c['company']}) - {c['type']}")
        lines.append("")

    # Upcoming commitments
    upcoming = []
    for c in contacts:
        for commit in c["commitments"]:
            if commit["due"] and commit["due"] <= TODAY + timedelta(days=7):
                upcoming.append((c["name"], commit))

    if upcoming:
        lines.append(f"{BOLD}Active Commitments Due Soon:{RESET}")
        for name, commit in sorted(upcoming, key=lambda x: x[1]["due"] or TODAY):
            due_str = commit["due"].strftime("%Y-%m-%d") if commit["due"] else "no date"
            overdue = " (OVERDUE)" if commit["due"] and commit["due"] < TODAY else ""
            lines.append(f"  [ ] {name} - {commit['text']}{RED}{overdue}{RESET}")
        lines.append("")

    # Summary
    total = len(contacts)
    lines.append(f"{BOLD}Total:{RESET} {total} contacts tracked | {RED}{len(red)} red{RESET} | {YELLOW}{len(yellow)} yellow{RESET} | {GREEN}{len(green)} green{RESET}")
    lines.append("")

    return "\n".join(lines)


_FM_OPEN_RE = re.compile(r"\A---[ \t]*\r?\n")
# `\r?` on the close too. The OPEN fence spells out `\r?\n`, so this file
# already claims to read CRLF; the close did not, and on a CRLF file the
# fence line is `---\r`, which `[ \t]*$` cannot match (Python's `$` sits
# before `\n`, not before `\r`). frontmatter_end then returned -1 for every
# CRLF contact, and --demote-candidates reported "no frontmatter" about
# files whose frontmatter was fine. Two regexes, two line-ending policies.
_FM_CLOSE_RE = re.compile(r"^---[ \t]*\r?$", re.MULTILINE)


def frontmatter_end(text: str) -> int:
    """Index where the CLOSING frontmatter fence begins, or -1 if there is none.

    Anchored, and requiring the document to OPEN with a fence. This was
    `text.find("---", 3)`, a plain substring search, and it failed twice over:

    * A frontmatter VALUE containing `---` (`notes: 2026-01-01---draft`) ended
      the slice early. If `status:` sat after that point the anchored guard
      found nothing, and the else-branch inserted `status: dormant` at
      `text.rfind("\\n", 0, fm_end)` — inside the frontmatter, splitting a value
      line and leaving malformed YAML.
    * A file with NO frontmatter but a `---` horizontal rule in the body sailed
      past the `fm_end == -1` guard. `text[:fm_end]` was then body text, and a
      `^status:` line in an interaction-log entry got rewritten — the exact
      body-rewrite the comment at the call site says the slicing prevents. The
      guard tested the wrong thing.
    """
    if not _FM_OPEN_RE.match(text):
        return -1
    match = _FM_CLOSE_RE.search(text, _FM_OPEN_RE.match(text).end())
    return -1 if match is None else match.start()


def _radar_insert_pos(content: str) -> int:
    """Where the radar table may be inserted without displacing line 1.

    Never 0. `context-freshness.py` reads ONLY the first line looking for
    `> Last verified:`, so anything written above it makes a stamped file
    report as unstamped, and the next `stamp` then adds a second marker.

    Order: after a closing frontmatter fence when the file opens with one;
    otherwise after the first line, whatever that line is.

    Uses `frontmatter_end`, whose docstring above explains why a substring
    search is wrong. This function kept the substring form and reproduced both
    halves of it: `startswith("---\n")` refused an opener of `--- ` or `---\r\n`,
    and `find("\n---", 3)` matched any line merely BEGINNING with `---` -- a
    `----` rule, or `--- draft` inside a block scalar. Both mistakes land in the
    same place. The fallback below is "after the first line", and on a file with
    frontmatter the first line IS the opening fence, so `--update` spliced the
    Contact Radar table INTO the YAML of the file it was writing.
    """
    fm_end = frontmatter_end(content)
    if fm_end != -1:
        line_end = content.find("\n", fm_end)
        return line_end + 1 if line_end != -1 else len(content)
    first_break = content.find("\n")
    return first_break + 1 if first_break != -1 else len(content)


def generate_radar_table(contacts):
    """Generate the Contact Radar markdown table for people.md."""
    lines = []
    lines.append("## Contact Radar")
    lines.append("")
    lines.append(f"> Auto-generated by crm-health.py on {TODAY.strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append("| Name | Company | Type | Last Touch | Health | CRM File |")
    lines.append("|------|---------|------|-----------|--------|----------|")

    # Sort: red first, then yellow, then green, then gray
    order = {"red": 0, "yellow": 1, "green": 2, "gray": 3}
    sorted_contacts = sorted(contacts, key=lambda c: (order.get(c["health"], 4), c["name"]))

    for c in sorted_contacts:
        health_icon = {"red": "RED", "yellow": "YELLOW", "green": "GREEN", "gray": "GRAY"}.get(c["health"], "?")
        last = c["last_touch"] if c["last_touch"] else "-"
        link = f"[crm](../crm/contacts/{c['file']})"
        lines.append(f"| {c['name']} | {c['company']} | {c['type']} | {last} | {health_icon} | {link} |")

    lines.append("")
    return "\n".join(lines)


def update_people_md(contacts):
    """Update the Contact Radar table in context/people.md."""
    if not PEOPLE_FILE.exists():
        print(f"{RED}Error: {PEOPLE_FILE} not found{RESET}")
        return False

    content = PEOPLE_FILE.read_text(encoding="utf-8")
    radar_table = generate_radar_table(contacts)

    # Remove the existing radar table. The old lookahead was
    # `(?=\n---|\n## [^C])`, which required the table to be followed by a rule
    # or by a `## ` heading whose first letter is NOT C — so it failed in two
    # ordinary cases: the table sitting at end of file, and the next section
    # being `## CRM Pipeline` or `## Contacts`. When the lookahead failed the
    # old table was simply left in place and a second `## Contact Radar` was
    # appended on every `--update`, stacking one more each run.
    #
    # Stop at the next `## ` heading of any letter, at a `---` rule, or at end
    # of input. `\Z` is what was missing.
    pattern = r"\n?## Contact Radar\n.*?(?=\n---|\n## |\Z)"
    content = re.sub(pattern, "", content, flags=re.DOTALL)

    # Insert after the frontmatter block. The comment here said "after the
    # header block (after first ---)" while the code found the SECOND `---`,
    # which for a file with frontmatter is the closing fence — right, but not
    # what the comment said. Worse was the else-branch: with fewer than two
    # `---` separators it put the table at byte 0, pushing the
    # `> Last verified:` marker off line 1. `context-freshness.py` reads only
    # the first line, so it then reported "No marker" for a file that has one,
    # and a later `stamp` inserted a SECOND marker above the table. Two scripts
    # in this workspace silently corrupted each other's invariant.
    insert_pos = _radar_insert_pos(content)
    content = content[:insert_pos] + "\n" + radar_table + "\n" + content[insert_pos:]

    atomic_write_text(PEOPLE_FILE, content)
    return True


def format_json(contacts):
    """Output contacts as JSON, including the fields /crm next ranker depends on."""
    import json
    output = []
    for c in contacts:
        # Compute days_overdue: how many days past the red threshold we are.
        # Negative or zero means within cadence; ranker filters to >0 for REDs.
        cadence = c.get("cadence", 0)
        days_since = c.get("days_since")
        if days_since is None or not cadence:
            days_overdue = 0
        else:
            days_overdue = max(0, int(days_since) - int(cadence))

        entry = {
            "name": c["name"],
            "company": c["company"],
            "email": c.get("email", ""),
            "type": c["type"],
            "stage": c.get("stage", ""),
            "last_touch": c["last_touch"],
            "cadence": cadence,
            "health": c["health"],
            "days_since": days_since,
            "days_overdue": days_overdue,
            "radar_freeze_until": c.get("radar_freeze_until", ""),
            "commitments": [
                {"text": cm["text"], "due": cm["due"].strftime("%Y-%m-%d") if cm["due"] else None}
                for cm in c["commitments"]
            ],
            "file": c["file"],
        }
        output.append(entry)
    return json.dumps(output, indent=2)


def main():
    parser = argparse.ArgumentParser(description="31C CRM Relationship Health Scorer")
    parser.add_argument("--update", action="store_true", help="Regenerate radar table in people.md")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--demote-candidates", action="store_true",
                        help="Surface dormancy candidates (90+ days silent prospects) for approval")
    parser.add_argument("--demote-threshold", type=int, default=90,
                        help="Days silent before a contact qualifies for demotion (default 90)")
    args = parser.parse_args()

    config = parse_config(CONFIG_FILE)
    contacts, tribe_warnings, dangling_refs, _stages, _aliases = scan_contacts(config)

    # Phase 2.4: log pipeline.md companies with no matching CRM contact.
    # Helps maintain crm/aliases.md - run crm-health.py to see stale entries.
    _contact_companies: set = set()
    for _c in contacts:
        _co = (_c.get("pipeline_company", "") or _c.get("company", "")).lower().strip()
        if _co:
            _contact_companies.add(_co)
            # If the contact's company normalises through an alias (e.g., contact has
            # `company: TT` and aliases.md maps tt -> exampletelco), we add the canonical
            # form. This ensures _contact_companies matches _stages.keys() which uses the
            # canonical pipeline.md company name.
            _contact_companies.add(_aliases.get(_co, _co))
    _unmatched = set(_stages.keys()) - _contact_companies
    if _unmatched:
        _log_dir = WORKSPACE / ".sync" / "logs"
        _log_dir.mkdir(parents=True, exist_ok=True)
        _log_path = _log_dir / f"crm-unmatched-pipeline-{_dt.datetime.now(get_default_tz()).strftime('%Y-%m-%d')}.log"
        _log_path.write_text(
            "Pipeline.md companies with no matching CRM contact:\n" +
            "\n".join(f"  - {c}" for c in sorted(_unmatched)) + "\n",
            encoding="utf-8",
        )

    if dangling_refs:
        # Route to stderr so `crm-health.py --json` produces a clean JSON
        # stream on stdout (crm_next.py and any other pipeline consumer
        # parses stdout). Terminal callers still see the warning - stderr
        # mixes into the same TTY by default.
        print(f"{YELLOW}WARN: {len(dangling_refs)} contact(s) reference missing address-book entities:{RESET}", file=sys.stderr)
        for dr in dangling_refs:
            print(f"  - {dr['file']} -> entity_ref {dr['entity_ref']}", file=sys.stderr)
        print(f"  Fix: create crm/address-book/{{slug}}.md for each missing entity.", file=sys.stderr)

    if not contacts:
        # An empty CRM is still an answer, and under --json the answer is `[]`.
        # This path used to print the two lines below on STDOUT whatever the
        # flags said, so `--json` returned prose and every stdout consumer broke
        # on a workspace with no contacts yet: cold_sweep_core died on a raw
        # JSONDecodeError, and crm_next and ops_signals each grew a private
        # handler for a producer nobody fixed. Two comments in this same
        # function already said stdout must stay clean for JSON consumers.
        if args.json:
            print("[]")
        else:
            print(f"{YELLOW}No contact files found in {CONTACTS_DIR}{RESET}")
        print("Add contacts with: /crm add [name] [company] [type]",
              file=sys.stderr)
        sys.exit(0)

    if args.json:
        print(format_json(contacts))
    else:
        print(format_terminal_report(contacts, tribe_warnings))

    if args.update:
        # stderr, for the same reason the dangling-ref warning above is on
        # stderr: `--json --update` printed these lines AFTER the JSON document
        # on the same stream, so `crm-health.py --json --update | jq .` failed
        # to parse. The comment twenty lines up already knew stdout must stay
        # clean for crm_next.py; the flags were simply never made exclusive.
        if update_people_md(contacts):
            print(f"{GREEN}Radar table updated in {PEOPLE_FILE.name}{RESET}",
                  file=sys.stderr)
        else:
            print(f"{RED}Failed to update {PEOPLE_FILE.name}{RESET}",
                  file=sys.stderr)

    if args.demote_candidates:
        from scripts.utils.crm import find_dormancy_candidates
        from scripts.utils.crm_autolog import atomic_write
        import re as _re
        candidates = find_dormancy_candidates(contacts, today=datetime.now(get_default_tz()).date(), threshold_days=args.demote_threshold)
        if not candidates:
            print(f"{GREEN}No dormancy candidates - all active contacts within {args.demote_threshold}d.{RESET}")
        else:
            print(f"\n{YELLOW}{BOLD}DORMANCY CANDIDATES ({len(candidates)} contacts {args.demote_threshold}+ days silent):{RESET}")
            for c in candidates:
                name = c.get("name", c.get("slug", c["file"]))
                ctype = c.get("type", "?")
                lt = c.get("last_touch", "?")
                days = c.get("days_silent", "?")
                print(f"  {c['file']}: {name} ({ctype}) - last touch {lt} ({days} days)")
            print(f"\nTo demote: set `status: dormant` in each contact file.")
            print(f"Confirm demote all {len(candidates)} contacts to dormant? [yes/no]")
            try:
                resp = input("> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                return
            if resp == "yes":
                demoted = 0
                for c in candidates:
                    path = CONTACTS_DIR / c["file"]
                    text = path.read_text(encoding="utf-8")
                    # Scope the status:-check to the frontmatter slice (text before
                    # the closing ---). Whole-file check would match body content
                    # (e.g., a `status:` literal in an interaction log entry).
                    fm_end = frontmatter_end(text)
                    if fm_end == -1:
                        # No frontmatter — skip silently. Should not happen for valid
                        # relationship records but defensive.
                        # `c.get("slug", ...)`, matching the defensive chain
                        # thirty lines up that already treats slug as optional.
                        # A hard index here crashed mid-demote, AFTER some
                        # contacts had been rewritten and with no rollback.
                        label = c.get("slug", c["file"])
                        print(f"  {YELLOW}[skipped]{RESET} {label}: no frontmatter")
                        continue
                    frontmatter = text[:fm_end]
                    # Anchored, like the replacement it guards. `"status:" in
                    # frontmatter` is a substring test, so frontmatter carrying
                    # `notes: status: pending` and no top-level `status:` passed
                    # the guard, the anchored re.sub replaced nothing, the file
                    # was rewritten byte-identical, and the script printed
                    # [demoted] over a no-op.
                    if _re.search(r"^status:", frontmatter, _re.MULTILINE):
                        new_frontmatter = _re.sub(r"^status:.*$", "status: dormant", frontmatter, count=1, flags=_re.MULTILINE)
                        text = new_frontmatter + text[fm_end:]
                    else:
                        # Insert status field before closing --- of frontmatter
                        insert_at = text.rfind("\n", 0, fm_end)
                        text = text[:insert_at] + "\nstatus: dormant" + text[insert_at:]
                    atomic_write(path, text)
                    demoted += 1
                    print(f"  {GREEN}[demoted]{RESET} {c['file']}")
                # What was WRITTEN, not what was offered. This printed
                # `len(candidates)`, and the loop above can `continue` past a
                # file with no frontmatter, so the confirmation line overstated
                # a human-approved bulk mutation by the number it had skipped.
                skipped = len(candidates) - demoted
                print(f"{GREEN}{demoted} contacts demoted to dormant.{RESET}")
                if skipped:
                    print(f"{YELLOW}{skipped} skipped (see above).{RESET}")
            else:
                print("No changes made.")


if __name__ == "__main__":
    main()
