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


class Unreadable(Exception):
    """The state file exists and did not parse into an object.

    NOT the same statement as "the state is empty", and the difference is the
    whole value of this log. `CP.read_json` swallows a corrupt read and returns
    `{}` - correct for a hook that must not stop a turn, wrong for an instrument
    whose output is a sequence of transitions. One torn read (the state file is
    rewritten by another process while this one reads it) turned every watched
    key into `[<value>, null]`, and the next poll turned all of them back. Two
    fabricated events per torn read, in the log that exists to say what happened
    around a compaction and in what order.
    """


def _read_state(state_path: Path) -> dict:
    """The state, or `Unreadable`. An absent file is genuinely empty."""
    if not state_path.exists():
        return {}
    data = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise Unreadable(f"state is {type(data).__name__}, not an object")
    return data


def _snapshot(state_path: Path, archive_dir: Path, slug: str) -> dict:
    try:
        state = _read_state(state_path)
    except ValueError as exc:      # json.JSONDecodeError is a ValueError
        raise Unreadable(str(exc)) from exc
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
    # `None` until the first snapshot that actually parsed. Seeding `previous`
    # with a failed read would make the first good poll look like every key
    # moving at once.
    try:
        previous = _snapshot(state_path, archive_dir, slug)
        start_note = None
    except (OSError, Unreadable) as exc:
        previous, start_note = None, str(exc)

    with log.open("a", encoding="utf-8") as fh:

        def _emit(record: dict) -> None:
            fh.write(json.dumps({"at": CP.utc_now().isoformat(), **record}) + "\n")
            fh.flush()

        _emit({"event": "watch_start", "session": args.session,
               "state": previous, "unreadable": start_note})

        while time.monotonic() < deadline:
            time.sleep(args.poll)
            try:
                current = _snapshot(state_path, archive_dir, slug)
            except Unreadable as exc:
                # Recorded as what it is, and `previous` is left ALONE: a read
                # that did not happen is not a transition.
                _emit({"event": "read_unparsed", "error": str(exc)})
                continue
            except OSError as exc:
                _emit({"event": "read_error", "error": str(exc)})
                continue
            if previous is None:
                _emit({"event": "first_read", "state": current})
                previous = current
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
            _emit({"event": "change", "changed": changed})
            previous = current

        _emit({"event": "watch_end", "state": previous})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
