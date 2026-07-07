#!/usr/bin/env python3
"""Thread registry CLI - open, log, close, hold, reopen, list, find, show, archive-scan."""
from __future__ import annotations
import argparse
import os
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_threads_dir, get_data_root, get_default_tz  # noqa: E402
from scripts.utils.threads_lib import (  # noqa: E402
    ThreadFile, write_thread_file, new_thread_path,
    ensure_active_threads_section, add_thread_to_index,
    parse_thread_file, update_thread_hook, remove_thread_from_index,
    scan_for_archive,
)


def _threads_root() -> Path:
    return get_threads_dir()


def _memory_md() -> Path:
    """Resolve the canonical auto-memory MEMORY.md index.

    Resolution order:
      1. `MEMORY_MD` env var (returned as-is, no existence check; used by tests).
      2. `<data-root>/auto-memory/MEMORY.md` -- the durable, canonical memory home
         in the data repo (loaded into session context, indexed by memory-index).

    The native per-launch harness store under `~/.claude/projects/<slug>/memory/`
    is a runtime cache that `.claude/hooks/memory-reconcile.py` keeps in sync with
    canonical (newest-wins, both directions) at every SessionStart -- so writing
    canonical here propagates to the native store on the next launch from any path.
    Targeting the native store directly (pre-split behaviour) broke after the
    engine/data split: the project slug derives from the data-root path, which has
    no native project dir, and reconcile's newest-wins would overwrite a native-only
    edit with stale canonical anyway.
    """
    if env := os.environ.get("MEMORY_MD"):
        return Path(env)
    return get_data_root() / "auto-memory" / "MEMORY.md"


def _initial_body(title: str) -> str:
    return (
        f"# {title}\n\n"
        f"## Open follow-ups\n\n"
        f"## Decisions\n\n"
        f"## Log (newest first)\n\n"
        f"## Notes\n"
    )


def _find_thread_by_id(threads_root: Path, thread_id: str) -> Path:
    for type_ in ("business", "personal"):
        candidate = threads_root / type_ / f"{thread_id}.md"
        if candidate.exists():
            return candidate
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


def _tick_followup(body: str, index: int) -> str:
    """Convert the Nth `- [ ]` line to `- [x]`. Index is stable: items are appended, never prepended."""
    lines = body.split("\n")
    cursor = 0
    for i, line in enumerate(lines):
        if line.lstrip().startswith("- [ ]"):
            if cursor == index:
                lines[i] = line.replace("- [ ]", "- [x]", 1)
                return "\n".join(lines)
            cursor += 1
    raise IndexError(f"no follow-up at index {index}")


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

    memory_md = _memory_md()
    if not memory_md.exists():
        memory_md.parent.mkdir(parents=True, exist_ok=True)
        memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    ensure_active_threads_section(memory_md)
    rel_path = f"threads/{args.type}/{path.name}"  # leak-guard: ok (relative reference string, not a filesystem path)
    add_thread_to_index(memory_md, type_=args.type, title=args.title, path=rel_path,
                        hook="just opened")
    print(f"opened: {path}")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    # Sanitize: strip newlines, then collapse whitespace runs (so multi-paragraph
    # input doesn't produce a hook with embedded double spaces from \n→space).
    event = re.sub(r"\s+", " ", args.event.replace("\n", " ").replace("\r", "")).strip()

    threads_root = _threads_root()
    path = _find_thread_by_id(threads_root, args.thread_id)
    # Validate MEMORY.md before mutating the thread, so a missing index file
    # cannot leave thread/MEMORY out of sync.
    memory_md = _memory_md()
    if not memory_md.exists():
        raise FileNotFoundError(f"MEMORY.md does not exist at {memory_md}")
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
    rel_path = f"threads/{thread.type}/{path.name}"  # leak-guard: ok (relative reference string, not a filesystem path)
    # MEMORY hook is a short summary; full event is preserved in the thread body above.
    try:
        update_thread_hook(memory_md, path=rel_path, hook=event[:120])
    except ValueError:
        # Section missing or hand-edited: repair and re-add the index line.
        ensure_active_threads_section(memory_md)
        add_thread_to_index(memory_md, type_=thread.type, title=thread.title,
                            path=rel_path, hook=event[:120])
    print(f"logged to {path}")
    return 0


def _set_status(thread_id: str, new_status: str, index_action: str) -> int:
    """index_action: 'remove' | 'add'."""
    threads_root = _threads_root()
    path = _find_thread_by_id(threads_root, thread_id)
    thread = parse_thread_file(path)
    thread.status = new_status
    thread.last_touched = datetime.now(get_default_tz()).date().isoformat()
    write_thread_file(path, thread)
    rel_path = f"threads/{thread.type}/{path.name}"  # leak-guard: ok (relative reference string, not a filesystem path)
    memory_md = _memory_md()
    if index_action == "remove":
        try:
            remove_thread_from_index(memory_md, path=rel_path)
        except ValueError:
            pass
    elif index_action == "add":
        ensure_active_threads_section(memory_md)
        add_thread_to_index(memory_md, type_=thread.type, title=thread.title,
                            path=rel_path, hook="reopened")
    print(f"{thread_id}: status={new_status}")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    return _set_status(args.thread_id, "closed", "remove")


def cmd_hold(args: argparse.Namespace) -> int:
    return _set_status(args.thread_id, "on-hold", "remove")


def cmd_reopen(args: argparse.Namespace) -> int:
    return _set_status(args.thread_id, "active", "add")


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


def cmd_list(args: argparse.Namespace) -> int:
    threads = _all_threads(_threads_root())
    if args.type:
        threads = [t for t in threads if t.type == args.type]
    if args.status:
        threads = [t for t in threads if t.status == args.status]
    else:
        threads = [t for t in threads if t.status == "active"]
    for t in threads:
        print(f"[{t.status}] {t.type}/{t.id} - {t.title} (last_touched: {t.last_touched})")
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
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / c.path.name
            if args.apply:
                # Defensive: ensure no orphan MEMORY.md link survives the move.
                # /thread close should have already removed the line, but a hand-
                # edit or stale state could leave it. Catch ValueError silently.
                rel_path = f"threads/{type_}/{c.path.name}"  # leak-guard: ok (relative reference string, not a filesystem path)
                try:
                    remove_thread_from_index(_memory_md(), path=rel_path)
                except ValueError:
                    pass  # line already absent (closed/on-hold thread) - expected
                shutil.move(str(c.path), str(dest))
                print(f"archived: {c.path} -> {dest} ({c.reason})")
            else:
                print(f"would archive: {c.path} -> {dest} ({c.reason})")
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
        p.set_defaults(func=func)
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
    if not hasattr(args, "func"):
        parser.error(f"subcommand '{args.cmd}' has no handler registered")
    try:
        return args.func(args)
    except (FileNotFoundError, IndexError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
