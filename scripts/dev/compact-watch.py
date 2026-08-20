#!/usr/bin/env python3
"""Record WHO compacts this session and HOW, into a file that survives it.

The question this answers is not "did compaction happen" - `compact_history`
already records that - but the sequence around it: whether the Stop hook saved a
handoff first, whether it submitted `/compact` through HERDR, and whether the
harness's own auto-compact got there first. After a compaction the context is
gone, so the evidence has to be on disk before it is needed.

Writes JSONL to the DATA overlay, one line per observed CHANGE, never a line per
poll: a poller that logs every tick buries the three moments that matter.

Usage:
  python scripts/dev/compact-watch.py --session <id> [--minutes 180]

Exits on its own at the deadline. Not a daemon, not installed, not scheduled -
this is an instrument for one question, kept under scripts/dev/ for that reason.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils import checkpoint_paths as CP  # noqa: E402
from scripts.utils.workspace import get_data_root  # noqa: E402

# The keys whose movement tells the story. Everything else in the state file
# changes every render and would drown these.
WATCHED = (
    "used_percentage",
    "current_bucket",
    "last_offered_bucket",
    "needs_compact_offer",
    "offer_level",
    "last_offer_at",
    "last_compact_at",
    # `compact_requested_at`, NOT `compact_request_at`. The first spelling is
    # what `_request_compaction` writes; this file watched the second for its
    # first hour and therefore reported "no request" through a run where the
    # request had fired at 07:41:02. An instrument that names a key nobody
    # writes reads exactly like an instrument reporting nothing happened.
    "compact_requested_at",
    "compact_requested_bucket",
    "compact_request_count",
    "compact_requests",
    "compact_host",
    "compact_request_error_at",
    "compact_host_checked_at",
    "unattended_continuations",
    "unattended_paused_at",
    "unattended_stop_reason",
    "unattended_done_at",
    "session_unattended",
    "session_auto",
)


def _snapshot(state_path: Path, archive_dir: Path, slug: str) -> dict:
    state = CP.read_json(state_path)
    snap = {k: state.get(k) for k in WATCHED}
    snap["_compact_history_len"] = len(state.get("compact_history") or [])
    snap["_compact_history_last"] = (state.get("compact_history") or [None])[-1]
    if archive_dir.is_dir():
        names = sorted(p.name for p in archive_dir.glob(f"*{slug}*.md"))
        snap["_archives"] = len(names)
        snap["_archive_last"] = names[-1] if names else None
    return snap


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", required=True, help="session id to watch")
    ap.add_argument("--minutes", type=int, default=180, help="stop after this long")
    ap.add_argument("--poll", type=float, default=1.0, help="seconds between reads")
    args = ap.parse_args()

    slug = CP.safe_slug(args.session)
    project = CP.project_root()
    state_path = CP.state_path(project, slug)
    archive_dir = CP.handoff_dir(project, CP.engine_root())

    log = Path(get_data_root()) / "outputs" / "operations" / "compact-watch" / f"{slug}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + args.minutes * 60
    previous = _snapshot(state_path, archive_dir, slug)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "at": CP.utc_now().isoformat(), "event": "watch_start",
            "session": args.session, "state": previous,
        }) + "\n")
        fh.flush()

        while time.monotonic() < deadline:
            time.sleep(args.poll)
            try:
                current = _snapshot(state_path, archive_dir, slug)
            except OSError as exc:
                fh.write(json.dumps({"at": CP.utc_now().isoformat(),
                                     "event": "read_error", "error": str(exc)}) + "\n")
                fh.flush()
                continue
            changed = {k: [previous.get(k), v] for k, v in current.items()
                       if previous.get(k) != v}
            if not changed:
                continue
            # A percentage that only drifts is noise unless something else moved
            # with it. The bucket, the offer and the compaction markers are the
            # signal; used_percentage alone is the meter ticking.
            if set(changed) <= {"used_percentage"}:
                previous = current
                continue
            fh.write(json.dumps({"at": CP.utc_now().isoformat(),
                                 "event": "change", "changed": changed}) + "\n")
            fh.flush()
            previous = current

        fh.write(json.dumps({"at": CP.utc_now().isoformat(),
                             "event": "watch_end", "state": previous}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
