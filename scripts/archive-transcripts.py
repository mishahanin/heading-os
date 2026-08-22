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
"""
from __future__ import annotations

import argparse
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
from scripts.utils.workspace import get_data_root, get_workspace_root  # noqa: E402

# How long a transcript must sit untouched before it counts as finished. A live
# session is appended to continuously; archiving one mid-conversation stores a
# half-record and then stores it again on the next run, which is how a
# write-once archive turns into a rewrite loop. Two hours is longer than any
# pause inside a working session and far shorter than the retention window.
SETTLE_SECONDS = 2 * 60 * 60


def transcript_dir() -> Path:
    """Where Claude Code keeps THIS workspace's transcripts.

    Same slug rule `scripts/calibrate.py` uses: the absolute workspace path with
    every separator and dot turned into a dash.
    """
    root = get_workspace_root().resolve()
    slug = str(root).replace("/", "-").replace(".", "-")
    return Path.home() / ".claude" / "projects" / slug


def archive_root() -> Path:
    """Beside the Chronicle entries that point at these transcripts.

    DATA overlay, never the engine tree: a transcript carries whatever the
    session touched, personal threads included.
    """
    return get_data_root() / "chronicle" / "transcripts"


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
    if not source_dir.is_dir():
        return counts

    for source in sorted(source_dir.glob("*.jsonl")):
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

    return counts


def status() -> dict:
    """What is live, what is archived, and how much the archive holds."""
    live = sorted(transcript_dir().glob("*.jsonl")) if transcript_dir().is_dir() else []
    archived = sorted(archive_root().rglob("*.jsonl.gz")) if archive_root().is_dir() else []
    return {
        "live_count": len(live),
        "live_bytes": sum(p.stat().st_size for p in live),
        "archived_count": len(archived),
        "archived_bytes": sum(p.stat().st_size for p in archived),
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

    if args.status:
        s = status()
        print(f"{BOLD}Transcript archive{RESET}")
        print(f"  live      {s['live_count']:4d} file(s)  {_human(s['live_bytes'])}"
              f"  {GRAY}{transcript_dir()}{RESET}")
        print(f"  archived  {s['archived_count']:4d} file(s)  {_human(s['archived_bytes'])}"
              f"  {GRAY}{archive_root()}{RESET}")
        if s["oldest_archived"]:
            print(f"  oldest archived: {s['oldest_archived']}")
        return 0

    counts = archive(dry_run=args.dry_run)
    verb = "would archive" if args.dry_run else "archived"
    print(f"{GREEN}{verb} {counts['archived']}{RESET}, "
          f"{counts['skipped']} already current, "
          f"{counts['too_fresh']} still live, "
          f"{counts['failed']} failed")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
