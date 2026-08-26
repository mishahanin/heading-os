#!/usr/bin/env python3
"""archive-transcripts.py - keep session transcripts past the harness's own clock.

Claude Code deletes transcripts under `~/.claude/projects/` after
`cleanupPeriodDays`. The default is 30. A Chronicle entry keeps WHAT was decided;
the transcript is the only place the reasoning behind it survives — what was
considered, what was rejected, which measurement settled it.

Measured 2026-08-22, before the window was raised: of 258 Chronicle entries, 177
(69%) already pointed at a transcript that no longer existed, and the oldest
surviving one was dated 2026-07-22 — the 30-day edge exactly. Raising the window
stops the deletions; it does not protect the files, which live outside both
repositories where no git and no `push-all.py` reaches them.

This copies each SETTLED transcript into the DATA overlay, gzipped, where the
normal backup already runs. Append-only by construction: a finished transcript
never changes, so an archived file is written once and never rewritten.

Compression is stdlib gzip (measured 3.8x on a real 88 MB transcript). `zstd -19`
reaches 6.3x on the same file and would cut roughly 2.2 GB/year to 1.3 GB, but it
is a CLI this repository does not require or a dependency it would have to add,
and the archive is write-once data in a private repository. Revisit if size bites.

Usage:
  python scripts/archive-transcripts.py                 # archive settled transcripts
  python scripts/archive-transcripts.py --dry-run       # report, write nothing
  python scripts/archive-transcripts.py --status        # what is archived vs live

Tests: tests/test_a_wizard_that_reached_outside_its_own_workspace.py
"""
from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, GRAY, GREEN, RESET, YELLOW  # noqa: E402
from scripts.utils import checkpoint_paths as CP  # noqa: E402
from scripts.utils.workspace import (  # noqa: E402
    DataRootError,
    get_workspace_root,
    require_writable_data_root,
)

# How long a transcript must sit untouched before it counts as finished. A live
# session is appended to continuously; archiving one mid-conversation stores a
# half-record and then stores it again on the next run, which is how a
# write-once archive turns into a rewrite loop. Two hours is longer than any
# pause inside a working session and far shorter than the retention window.
SETTLE_SECONDS = 2 * 60 * 60


UNRESOLVED = (
    "the transcript directory could not be resolved on this platform "
    "(the harness project-slug rule is POSIX-only). Nothing was read, and "
    "nothing was archived - this is NOT an empty archive."
)


def transcript_dir() -> Path | None:
    """Where Claude Code keeps THIS workspace's transcripts, or None.

    One line, because the slug rule has exactly one owner:
    `scripts/utils/checkpoint_paths.transcript_dir`. This function held a second
    copy of that rule until 2026-08-23, and pointed its docstring at
    `scripts/calibrate.py` as a third authority.

    None means the platform is not POSIX and the resolver refused to guess.
    Every caller here reports that out loud instead of counting zero files.
    """
    return CP.transcript_dir(get_workspace_root())


def archive_root() -> Path:
    """Beside the Chronicle entries that point at these transcripts.

    DATA overlay, never the engine tree: a transcript carries whatever the
    session touched, personal threads included.

    `require_writable_data_root()`, not `get_data_root()`. The plain resolver has
    a documented last resort: with no env override, no in-tree data and no
    sibling overlay, it answers `<workspace_root>/examples`, which is INSIDE the
    engine clone. The engine repository is public. So on a data-less clone the
    plain call would have this archiver copy whole session transcripts, personal
    threads included, into the tree that gets pushed. Refusing is the only
    correct answer there, and this is the line that has to refuse, rather than
    `main()`, because `dest_for()` and `status()` both build paths from here.

    Measured 2026-08-26: this is why CI has failed on every push to main since
    2026-08-22. `test_the_archive_lands_in_the_data_overlay_never_the_engine` is
    the guard that caught it, and it was right; the runner has no overlay, so the
    demo fallback fired and the archive root landed under the engine root.
    """
    return require_writable_data_root() / "chronicle" / "transcripts"


def _session_date(path: Path) -> str:
    """The date the session STARTED, from its first timestamped line.

    The start, specifically, because the date decides the archive path and the
    path must not move. An earlier cut read the mtime instead, which changes
    every time a session is resumed: the resumed transcript archived under a
    second date and the first, truncated copy stayed behind forever. A start
    timestamp is written once and never changes.

    Falls back to the mtime only when no line carries a timestamp, which is a
    transcript this archiver has no better answer for anyway.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _ in range(20):        # the stamp is on the first line or nowhere
                line = fh.readline()
                if not line:
                    break
                try:
                    stamp = json.loads(line).get("timestamp")
                except (ValueError, AttributeError):
                    continue
                if isinstance(stamp, str) and len(stamp) >= 10:
                    return stamp[:10]
    except OSError:
        pass
    return datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    ).strftime("%Y-%m-%d")


def _destination(path: Path) -> Path:
    date = _session_date(path)
    return archive_root() / date[:4] / f"{date}-{path.stem}.jsonl.gz"


def _needs_archiving(source: Path, dest: Path) -> bool:
    """True when there is no archived copy, or the session grew after it was made.

    Size, not mtime: a resumed session appends, and a copy made from the shorter
    file would silently keep the truncated reasoning. The uncompressed size is
    recorded in the gzip trailer, but reading it means opening every archive on
    every run, so the marker file beside the archive holds it instead.
    """
    if not dest.exists():
        return True
    marker = dest.with_suffix(".gz.size")
    try:
        return int(marker.read_text(encoding="utf-8").strip()) != source.stat().st_size
    except (OSError, ValueError):
        return True  # unreadable marker — re-archive rather than assume it is current


def archive(*, now: float | None = None, dry_run: bool = False) -> dict:
    """Archive every settled transcript. Returns counts; never raises on one file."""
    now = time.time() if now is None else now
    counts = {"archived": 0, "skipped": 0, "too_fresh": 0, "failed": 0}

    source_dir = transcript_dir()
    if source_dir is None:
        print(f"{YELLOW}archive-transcripts: {UNRESOLVED}{RESET}", file=sys.stderr)
        counts["unresolved"] = 1
        return counts
    if not source_dir.is_dir():
        return counts

    for source in sorted(source_dir.glob("*.jsonl")):
        tmp: Path | None = None
        try:
            if now - source.stat().st_mtime < SETTLE_SECONDS:
                counts["too_fresh"] += 1
                continue
            dest = _destination(source)
            if not _needs_archiving(source, dest):
                counts["skipped"] += 1
                continue
            if dry_run:
                counts["archived"] += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            # tmp + replace: a torn archive is worse than no archive, because the
            # next run would see a file present and count it done.
            tmp = dest.with_suffix(".gz.tmp")
            with open(source, "rb") as raw, gzip.open(tmp, "wb", compresslevel=6) as out:
                shutil.copyfileobj(raw, out)
            os.replace(tmp, dest)
            dest.with_suffix(".gz.size").write_text(
                str(source.stat().st_size), encoding="utf-8"
            )
            counts["archived"] += 1
        except (OSError, ValueError) as exc:
            # Counted and printed, never swallowed: one unreadable transcript must
            # not cost the rest of the run.
            print(f"{YELLOW}skip {source.name}:{RESET} {exc}", file=sys.stderr)
            counts["failed"] += 1
        finally:
            # The half-written `.gz.tmp` goes with the failure that made it.
            # `os.replace` consumes the tmp on the success path, so this only
            # ever finds one after a torn copy -- and nothing else did: the
            # name ends `.jsonl.gz.tmp`, which `status()` does not glob
            # (`*.jsonl.gz`) and no later run revisits, so every failed run
            # left a compressed partial transcript sitting in the DATA overlay
            # permanently, invisible to the command that reports what is there.
            if tmp is not None:
                with contextlib.suppress(OSError):
                    tmp.unlink(missing_ok=True)

    return counts


def _total_bytes(paths) -> int:
    """Sum sizes, skipping anything that disappeared between glob and stat.

    `archive()` already guards each file; `status()` did not, so the harness
    deleting a transcript mid-scan -- the entire premise of this script --
    produced an uncaught traceback from a read-only command.
    """
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def status() -> dict:
    """What is live, what is archived, and how much the archive holds."""
    source_dir = transcript_dir()
    live = sorted(source_dir.glob("*.jsonl")) if source_dir and source_dir.is_dir() else []
    # A DATA function reports facts and does not raise. On a clone with no
    # private overlay `archive_root()` refuses, and the honest answer here is
    # "nothing archived", not a traceback out of a reporting call. `main()` is
    # what turns the refusal into a message and an exit code.
    try:
        root = archive_root()
    except DataRootError:
        root = None
    archived = sorted(root.rglob("*.jsonl.gz")) if root and root.is_dir() else []
    return {
        "unresolved": source_dir is None,
        "live_count": len(live),
        "live_bytes": _total_bytes(live),
        "archived_count": len(archived),
        "archived_bytes": _total_bytes(archived),
        "oldest_archived": archived[0].name if archived else None,
    }


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be archived, write nothing")
    parser.add_argument("--status", action="store_true",
                        help="print live vs archived counts and exit")
    args = parser.parse_args(argv)

    # Degrade clearly, never silently: on a clone with no private overlay the
    # archive root resolves inside the public engine tree, and `archive_root()`
    # refuses. Say so and exit non-zero, rather than letting the traceback reach
    # a timer that reads only the exit code.
    #
    # Order matters, and it is measured rather than guessed. Both refusals hold
    # at once on a clone with no overlay. The transcript directory is the more
    # specific one, and it names something the operator can act on, so it is
    # reported first. Checking the destination first hid it: two tests in
    # `tests/test_transcript_dir_has_one_owner.py` went red under exactly that
    # ordering on 2026-08-26.
    if transcript_dir() is None:
        print(f"{YELLOW}archive-transcripts: {UNRESOLVED}{RESET}", file=sys.stderr)
        return 2
    try:
        archive_root()
    except DataRootError as exc:
        print(f"{YELLOW}archive-transcripts: {exc}{RESET}", file=sys.stderr)
        return 2

    if args.status:
        s = status()
        if s["unresolved"]:
            print(f"{YELLOW}archive-transcripts: {UNRESOLVED}{RESET}", file=sys.stderr)
            return 2
        print(f"{BOLD}Transcript archive{RESET}")
        print(f"  live      {s['live_count']:4d} file(s)  {_human(s['live_bytes'])}"
              f"  {GRAY}{transcript_dir()}{RESET}")
        print(f"  archived  {s['archived_count']:4d} file(s)  {_human(s['archived_bytes'])}"
              f"  {GRAY}{archive_root()}{RESET}")
        if s["oldest_archived"]:
            print(f"  oldest archived: {s['oldest_archived']}")
        return 0

    counts = archive(dry_run=args.dry_run)
    if counts.get("unresolved"):
        # Exit 2, the same code --status uses for this condition. archive mode
        # never looked at the flag, so an unresolvable transcript directory
        # printed "archived 0, 0 already current, 0 still live, 0 failed" and
        # exited 0 -- indistinguishable from success to the cron job calling
        # it, on a script whose whole purpose is preventing silent transcript
        # loss. The warning is already on stderr; this makes it visible to
        # automation, which does not read stderr.
        return 2
    verb = "would archive" if args.dry_run else "archived"
    print(f"{GREEN}{verb} {counts['archived']}{RESET}, "
          f"{counts['skipped']} already current, "
          f"{counts['too_fresh']} still live, "
          f"{counts['failed']} failed")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
