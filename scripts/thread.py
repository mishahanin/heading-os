#!/usr/bin/env python3
"""Thread registry CLI - open, log, close, hold, reopen, quiet, list, find, show, archive-scan."""
from __future__ import annotations
import argparse
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_threads_dir, get_default_tz  # noqa: E402
from scripts.utils.threads_lib import (  # noqa: E402
    ThreadFile, write_thread_file, new_thread_path,
    parse_thread_file, scan_for_archive, is_quiet,
)

# This CLI writes thread files and nothing else. It used to also maintain a
# `## Active Threads` block in the auto-memory MEMORY.md, through a `_memory_md()`
# resolver and six index helpers; that block was retired on 2026-08-20 and the
# writer was removed on 2026-08-27. `scripts/utils/threads_lib.py` carries the
# full account under "The retired MEMORY.md index".


def _threads_root() -> Path:
    return get_threads_dir()


def _initial_body(title: str) -> str:
    return (
        f"# {title}\n\n"
        f"## Open follow-ups\n\n"
        f"## Decisions\n\n"
        f"## Log (newest first)\n\n"
        f"## Notes\n"
    )


def _find_thread_by_id(threads_root: Path, thread_id: str) -> Path:
    """Resolve an ID to one thread file, refusing an ambiguous hit.

    The scan used to return the first match and `business` is scanned first, so
    an ID present under BOTH types silently resolved to the business copy. That
    is the wrong direction to fail in: personal threads are CEO-only, and a
    `log` aimed at the personal one landed in the business file with no signal.
    """
    matches = [
        threads_root / type_ / f"{thread_id}.md"
        for type_ in ("business", "personal")
        if (threads_root / type_ / f"{thread_id}.md").exists()
    ]
    if len(matches) > 1:
        where = ", ".join(m.parent.name for m in matches)
        raise ValueError(
            f"thread '{thread_id}' exists under both {where}; rename one, the ID must be unique")
    if matches:
        # Unpacked, not indexed: the guard above already refused len > 1, so
        # there is exactly one. `matches[0]` read as "prefer the first", which
        # is the behaviour this function was fixed to stop having.
        (only,) = matches
        return only
    raise FileNotFoundError(f"thread '{thread_id}' not found in business/ or personal/")


def _append_under_section(body: str, section_header: str, new_line: str) -> str:
    """Append `new_line` at the end of the section starting with `section_header`.

    Section ends at the next level-2 header or end-of-file. New entries are
    appended (not prepended) so that index-based references like --done <N>
    stay stable across edits.
    """
    pattern = re.compile(rf"^{re.escape(section_header)}$", re.MULTILINE)
    m = pattern.search(body)
    if not m:
        # Section missing - inject it before the first existing level-2 header,
        # or append at end if none exist.
        next_h2 = re.search(r"^## ", body, flags=re.MULTILINE)
        section_block = f"{section_header}\n\n{new_line}\n\n"
        if next_h2:
            return body[: next_h2.start()] + section_block + body[next_h2.start():]
        return body.rstrip() + "\n\n" + section_block
    section_start = m.end()
    next_h2 = re.search(r"^## ", body[section_start:], flags=re.MULTILINE)
    section_end = section_start + (next_h2.start() if next_h2 else len(body) - section_start)
    section_body = body[section_start:section_end].rstrip("\n")
    # Leading "\n" is mandatory; without it, an empty section concatenates the
    # header and item ("## Header- [ ] item"), which breaks the next match.
    return (
        body[:section_start]
        + "\n"
        + (section_body + "\n" if section_body else "")
        + new_line
        + "\n\n"
        + body[section_end:].lstrip("\n")
    )


def _prepend_log_entry(body: str, entry: str) -> str:
    """Prepend a log entry under `## Log (newest first)` (newest first by definition)."""
    log_marker_re = re.compile(r"^## Log \(newest first\)$", re.MULTILINE)
    m = log_marker_re.search(body)
    if not m:
        # Inject before next level-2 or at end
        body = _append_under_section(body, "## Log (newest first)", "")
        m = log_marker_re.search(body)
    insert_at = m.end()
    return body[:insert_at] + "\n\n" + entry.rstrip("\n") + "\n" + body[insert_at:].lstrip("\n")


FOLLOWUPS_HEADER = "## Open follow-ups"


def _section_bounds(body: str, section_header: str) -> tuple[int, int] | None:
    """(start, end) line indices of a section's BODY, or None if absent.

    Same boundary rule as `_append_under_section`: a section runs to the next
    level-2 header or end of file.
    """
    lines = body.split("\n")
    start = None
    for i, line in enumerate(lines):
        if line.strip() == section_header:
            start = i + 1
            break
    if start is None:
        return None
    for j in range(start, len(lines)):
        if lines[j].startswith("## "):
            return start, j
    return start, len(lines)


def _tick_followup(body: str, index: int) -> str:
    """Convert the Nth `- [ ]` line IN THE FOLLOW-UPS SECTION to `- [x]`.

    Index is stable: items are appended, never prepended.

    The scan used to run over the whole file, while the CLI help promised an
    "index of follow-up". Any checkbox in Notes or Decisions -- or one written
    by hand above the section -- shifted every index, so `--done 0` ticked an
    unrelated line and left the follow-up open, silently.
    """
    lines = body.split("\n")
    bounds = _section_bounds(body, FOLLOWUPS_HEADER)
    if bounds is None:
        raise IndexError(f"no `{FOLLOWUPS_HEADER}` section in this thread")
    start, end = bounds
    cursor = 0
    for i in range(start, end):
        if lines[i].lstrip().startswith("- [ ]"):
            if cursor == index:
                lines[i] = lines[i].replace("- [ ]", "- [x]", 1)
                return "\n".join(lines)
            cursor += 1
    raise IndexError(
        f"no follow-up at index {index}; the section holds {cursor} open item(s)")


def cmd_open(args: argparse.Namespace) -> int:
    today = datetime.now(get_default_tz()).date().isoformat()
    threads_root = _threads_root()
    threads_root.mkdir(parents=True, exist_ok=True)
    path = new_thread_path(threads_root, args.type, args.title, today)
    if path.exists():
        print(f"thread already exists: {path}", file=sys.stderr)
        return 1
    thread = ThreadFile(
        id=path.stem,
        title=args.title,
        status="active",
        type=args.type,
        classification="ceo-only",
        opened=today,
        last_touched=today,
        counterparties=[],
        links={"crm": [], "pipeline": [], "outputs": [], "knowledge": []},
        tags=[],
        body=_initial_body(args.title),
        path=path,
    )
    write_thread_file(path, thread)
    print(f"opened: {path}")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    # Sanitize: strip newlines, then collapse whitespace runs (so multi-paragraph
    # input doesn't produce a hook with embedded double spaces from \n→space).
    event = re.sub(r"\s+", " ", args.event.replace("\n", " ").replace("\r", "")).strip()

    threads_root = _threads_root()
    path = _find_thread_by_id(threads_root, args.thread_id)
    thread = parse_thread_file(path)
    today = datetime.now(get_default_tz()).date().isoformat()

    log_entry = f"### {today} - {event}\n"
    thread.body = _prepend_log_entry(thread.body, log_entry)

    for artifact in (args.artifact or []):
        # links.outputs is normalized in parse_thread_file; just append unique
        if artifact not in thread.links["outputs"]:
            thread.links["outputs"].append(artifact)
    for decision in (args.decision or []):
        thread.body = _append_under_section(
            thread.body, "## Decisions", f"- {today} - {decision}",
        )
    for follow_up in (args.follow_up or []):
        # Append (not prepend) so --done <N> indexes stay stable across adds.
        thread.body = _append_under_section(
            thread.body, "## Open follow-ups", f"- [ ] {follow_up}",
        )
    if args.done is not None:
        thread.body = _tick_followup(thread.body, args.done)

    thread.last_touched = today
    write_thread_file(path, thread)
    print(f"logged to {path}")
    return 0


def _set_status(thread_id: str, new_status: str, reason: str | None = None) -> int:
    """Move a thread to `new_status`, recording `reason` as a log entry.

    A status change that RETIRES a thread must carry a reason. Until 2026-08-17
    it did not, and one operator run closed nineteen threads at once with only
    `status` and `last_touched` written. Six of them closed over a loop the deal
    pipeline still showed as live - one side awaiting a data dump, another
    awaiting a meeting slot - and nothing on disk distinguished those from the
    threads that were genuinely resolved. Reopening is exempt: resuming work is
    not a decision that needs defending, and friction there buys nothing.

    The test is the destination status, not an index action. It used to be
    `index_action == "remove"`, which named a MEMORY.md index this CLI no longer
    writes; `new_status != "active"` is the same set of commands (`close`,
    `hold`) stated in terms that still exist.
    """
    if new_status != "active" and not (reason or "").strip():
        raise ValueError(
            f"{new_status} needs --reason: a status change that retires a thread "
            f"must say why, or nobody can tell later whether the work finished "
            f"or just went quiet"
        )
    threads_root = _threads_root()
    path = _find_thread_by_id(threads_root, thread_id)
    thread = parse_thread_file(path)
    thread.status = new_status
    today = datetime.now(get_default_tz()).date().isoformat()
    thread.last_touched = today
    if reason:
        verb = {"closed": "Closed", "on-hold": "On hold"}.get(new_status, new_status)
        event = re.sub(r"\s+", " ", reason.replace("\n", " ").replace("\r", "")).strip()
        thread.body = _prepend_log_entry(
            thread.body, f"### {today} - **{verb}.** {event}\n")
    write_thread_file(path, thread)
    print(f"{thread_id}: status={new_status}")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    return _set_status(args.thread_id, "closed", args.reason)


def cmd_hold(args: argparse.Namespace) -> int:
    return _set_status(args.thread_id, "on-hold", args.reason)


def cmd_reopen(args: argparse.Namespace) -> int:
    return _set_status(args.thread_id, "active")


def cmd_quiet(args: argparse.Namespace) -> int:
    """Put a thread into, or take it out of, a deliberate quiet period."""
    if not args.clear and not args.indefinite:
        try:
            date.fromisoformat(args.until)
        except (ValueError, TypeError):
            print("error: pass --until YYYY-MM-DD, --indefinite, or --clear "
                  f"(got --until {args.until!r})", file=sys.stderr)
            return 1
    path = _find_thread_by_id(_threads_root(), args.thread_id)
    thread = parse_thread_file(path)
    thread.quiet_until = None if (args.clear or args.indefinite) else args.until
    thread.do_not_remind = bool(args.indefinite)
    write_thread_file(path, thread)
    if args.clear:
        print(f"{args.thread_id}: quiet period cleared")
    elif args.indefinite:
        print(f"{args.thread_id}: quiet indefinitely - surfaced only when you raise it")
    else:
        print(f"{args.thread_id}: quiet until {args.until} - not surfaced proactively before then")
    return 0


def _all_threads(threads_root: Path) -> list[ThreadFile]:
    threads: list[ThreadFile] = []
    for type_ in ("business", "personal"):
        type_dir = threads_root / type_
        if not type_dir.is_dir():
            continue
        for f in sorted(type_dir.glob("*.md")):
            try:
                threads.append(parse_thread_file(f))
            except (ValueError, OSError) as exc:
                # Surface corruption to stderr instead of silent skip; otherwise
                # broken threads disappear from list/find with no signal.
                print(f"warning: skipping {f}: {exc}", file=sys.stderr)
    return threads


# `cmd_reindex` stood here and rewrote every MEMORY.md hook from thread
# frontmatter, to repair drift between the index and the threads it pointed at.
# It went with the index on 2026-08-27: with no copy of the record, there is
# nothing to reconcile, and `list` reads the thread files directly. Its
# subcommand was removed from the parser in the same change, so `thread.py
# reindex` now exits 2 with argparse's "invalid choice" rather than silently
# doing nothing.


def cmd_list(args: argparse.Namespace) -> int:
    threads = _all_threads(_threads_root())
    if args.type:
        threads = [t for t in threads if t.type == args.type]
    if args.status:
        threads = [t for t in threads if t.status == args.status]
    else:
        threads = [t for t in threads if t.status == "active"]
    today = datetime.now(get_default_tz()).date()
    for t in threads:
        # `do_not_remind` is tested FIRST because `is_quiet` is true for it while
        # `quiet_until` is None, and the single-branch version interpolated that
        # None: an indefinitely frozen thread listed as `[quiet until None]`,
        # which is neither the documented suffix nor a date anyone can act on.
        if t.do_not_remind:
            quiet = " [quiet indefinitely]"
        elif is_quiet(t, today):
            quiet = f" [quiet until {t.quiet_until}]"
        else:
            quiet = ""
        print(f"[{t.status}] {t.type}/{t.id} - {t.title} (last_touched: {t.last_touched}){quiet}")
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    needle = args.query.lower()
    for t in _all_threads(_threads_root()):
        haystack = " ".join([t.title, " ".join(t.tags), " ".join(t.counterparties), t.body]).lower()
        if needle in haystack:
            print(f"[{t.status}] {t.type}/{t.id} - {t.title}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    path = _find_thread_by_id(_threads_root(), args.thread_id)
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_archive_scan(args: argparse.Namespace) -> int:
    today = datetime.now(get_default_tz()).date()
    candidates = scan_for_archive(_threads_root(), today=today)
    if not candidates:
        print("no archive candidates")
        return 0
    for c in candidates:
        if c.action == "archive":
            year = today.strftime("%Y")
            type_ = c.path.parent.name
            dest_dir = _threads_root() / "archive" / year / type_
            dest = dest_dir / c.path.name
            if args.apply:
                # The mkdir lives INSIDE the apply branch. It used to sit above
                # this `if`, so a plain `archive-scan` with no --apply printed
                # "would archive:" and created `threads/archive/<year>/<type>/`
                # on disk anyway - a filesystem side effect from a command whose
                # whole contract is that it previews and changes nothing.
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(c.path), str(dest))
                print(f"archived: {c.path} -> {dest} ({c.reason})")
            else:
                print(f"would archive: {c.path} -> {dest} ({c.reason})")
        elif c.action == "quiet-expired":
            print(f"quiet expired: {c.path} ({c.reason})")
        else:
            print(f"propose on-hold: {c.path} ({c.reason})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Thread registry CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_open = sub.add_parser("open", help="Open a new thread")
    p_open.add_argument("type", choices=["business", "personal"])
    p_open.add_argument("title")
    p_open.set_defaults(func=cmd_open)
    p_log = sub.add_parser("log", help="Append an event to a thread")
    p_log.add_argument("thread_id")
    p_log.add_argument("event")
    p_log.add_argument("--artifact", action="append",
                       help="Output path to add to links.outputs (repeatable)")
    p_log.add_argument("--decision", action="append",
                       help="Decision text to append to ## Decisions (repeatable)")
    p_log.add_argument("--follow-up", action="append",
                       help="Follow-up item to add to ## Open follow-ups (repeatable)")
    p_log.add_argument("--done", type=int, help="Index of follow-up to tick off")
    p_log.set_defaults(func=cmd_log)
    for name, func in [("close", cmd_close), ("hold", cmd_hold), ("reopen", cmd_reopen)]:
        p = sub.add_parser(name, help=f"{name} a thread")
        p.add_argument("thread_id")
        if name != "reopen":
            p.add_argument(
                "--reason", required=True,
                help="Why the thread leaves the index. Recorded as a log entry, "
                     "so a later reader can tell a finished thread from a quiet one")
        p.set_defaults(func=func)
    p_quiet = sub.add_parser(
        "quiet", help="Suppress a thread from proactive surfacing until a date")
    p_quiet.add_argument("thread_id")
    # Mutually exclusive: the three express one choice, and combining them used
    # to be accepted silently with --clear winning, so `--until 2026-09-01
    # --clear` reported "quiet period cleared" for someone who meant to set one.
    q_mode = p_quiet.add_mutually_exclusive_group(required=True)
    q_mode.add_argument("--until", metavar="YYYY-MM-DD",
                        help="Last date on which the thread stays quiet")
    q_mode.add_argument("--indefinite", action="store_true",
                        help="Quiet with no end date; lifts only when you raise it")
    q_mode.add_argument("--clear", action="store_true", help="Lift the quiet period")
    p_quiet.set_defaults(func=cmd_quiet)
    p_list = sub.add_parser("list", help="List threads")
    p_list.add_argument("--type", choices=["business", "personal"])
    p_list.add_argument("--status", choices=["active", "on-hold", "closed"])
    p_list.set_defaults(func=cmd_list)
    p_find = sub.add_parser("find", help="Search threads")
    p_find.add_argument("query")
    p_find.set_defaults(func=cmd_find)
    p_show = sub.add_parser("show", help="Print a thread file")
    p_show.add_argument("thread_id")
    p_show.set_defaults(func=cmd_show)
    p_arch = sub.add_parser("archive-scan", help="Scan for closed-90d threads to archive")
    p_arch.add_argument("--apply", action="store_true", help="Actually move files (default: dry-run)")
    p_arch.set_defaults(func=cmd_archive_scan)
    args = parser.parse_args(argv)
    # No `hasattr(args, "func")` guard here: subparsers are required=True and
    # every one sets a handler, so the branch could not run. The invariant is
    # checked at CI time instead, by test_every_thread_subcommand_has_a_handler.
    try:
        return args.func(args)
    except (FileNotFoundError, IndexError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
