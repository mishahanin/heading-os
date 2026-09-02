#!/usr/bin/env python3
"""Report what the workspace's guards refused, and how often.

The console-first read path over `scripts/utils/denial_log.py`. Answers the one
question no mechanism in this workspace could answer before it existed: is a
given guard catching anything, or is it ceremony?

The unit is one record per refused PATH, and it is stated here because it is not
the same as one record per refused action for every guard. A denied tool call is
one path and so one record; a commit refused by the content guard over six
offending lines is six. That is the right shape for `--detail`, which exists to
show every offending location, and it is why the counts below are labelled
records. Read them as caught-something versus never-fired — the discrimination
this instrument was built for, and the one that holds under either unit — and not
as a like-for-like frequency ranking across mechanisms.

Usage:
    python scripts/denials.py                 # counts per mechanism, all time
    python scripts/denials.py --days 30       # the mechanism budget window
    python scripts/denials.py --detail        # one line per refused path
    python scripts/denials.py --json          # machine-readable
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, GRAY, GREEN, RESET, YELLOW
from scripts.utils.denial_log import (  # noqa: E402
    denial_log_path,
    printable,
    read_denials,
    summarize,
)
from scripts.utils.workspace import get_default_tz


def _epoch(ts):
    """The record's timestamp as a float, or None when it cannot be read.

    `read_denials` already tolerates a truncated write by skipping the line, but
    a line can be valid JSON and still carry an unreadable `ts` — and the window
    filter used to hand that straight to `float()`, so one corrupt-but-parseable
    record killed `--days` with a traceback while every other path survived it.

    `OverflowError` is in the list because `float()` raises it, not ValueError,
    on an integer too large to convert. `read_denials` calls `json.loads` with
    the stdlib defaults, so a hand-edited or torn line carrying a 400-digit
    integer literal parses into a Python `int` and arrives here intact.
    MEASURED 2026-09-02: `float(int("9" * 400))` raises
    `OverflowError: int too large to convert to float`, which is an
    ArithmeticError and was caught by nothing, so `--days` died on exactly the
    record shape this function exists to absorb.
    """
    try:
        return float(ts)
    except (TypeError, ValueError, OverflowError):
        return None


def _printable(value) -> str:
    """Delegates to the shared guard in `denial_log`. See it for why."""
    return printable(value)


def _stamp(ts) -> str:
    """The record's timestamp rendered for the operator, or `"?"`.

    Display value, so the operator's local timezone, per the DTZ convention:
    serialized timestamps stay UTC epoch in the log, rendering is local.

    `OverflowError` is the third member of the same family `_epoch` above
    describes, and it was missing here while `_epoch` had already been widened
    for the window filter. `json.loads` accepts the bare literal `Infinity` with
    the stdlib defaults, so `{"ts": Infinity}` survives `read_denials`, passes
    `_epoch` as a real float, and clears the `--days` cutoff. MEASURED
    2026-09-02: `datetime.fromtimestamp(float("inf"))` raises
    `OverflowError: timestamp out of range for platform time_t`, so one such
    record ended `--detail` MID-TABLE and hid every entry after it, in the one
    view that exists to show every refused path. `1e300` does the same.
    """
    try:
        return datetime.fromtimestamp(float(ts), tz=get_default_tz()).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return "?"


def main() -> int:
    parser = argparse.ArgumentParser(description="Report guard refusals.")
    parser.add_argument("--days", type=float, default=None,
                        help="only refusals within the last N days")
    parser.add_argument("--detail", action="store_true",
                        help="one line per refused path instead of counts")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="machine-readable output")
    args = parser.parse_args()

    records = read_denials()
    # Counted, not dropped in silence. `(_epoch(...) or 0) >= cutoff` folded a
    # record whose `ts` cannot be read into "older than the window", so a
    # corrupt timestamp quietly reduced the count of an instrument whose whole
    # question is whether a guard caught anything. This file already insists
    # forty lines below that the per-mechanism totals add up to the printed
    # record count; an unexplained subtraction here breaks the same promise one
    # step earlier.
    undated = 0
    if args.days is not None:
        cutoff = time.time() - args.days * 86400
        kept = []
        for record in records:
            stamp = _epoch(record.get("ts"))
            if stamp is None:
                undated += 1
            elif stamp >= cutoff:
                kept.append(record)
        records = kept
    counts = summarize(records)

    if args.as_json:
        json.dump({"total": len(records), "by_mechanism": counts,
                   "log": str(denial_log_path()),
                   "window_days": args.days,
                   "undated_excluded": undated}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    window = f" in the last {args.days:g} days" if args.days is not None else ""
    dropped = (f"\n{YELLOW}{undated} record(s) carry an unreadable timestamp and "
               f"could not be placed in the window.{RESET}") if undated else ""
    if not records:
        print(f"{GREEN}0 refusals recorded{window}.{RESET}{dropped}")
        print(f"{GRAY}Log: {denial_log_path()}{RESET}")
        return 0

    if args.detail:
        for record in records:
            context = (f" [{_printable(record['context'])}]"
                       if record.get("context") else "")
            print(f"{_stamp(record.get('ts'))}  {_printable(record.get('mechanism'))}"
                  f"{context}  {_printable(record.get('action'))}  "
                  f"{_printable(record.get('path'))}")
        print()

    print(f"{BOLD}{len(records)} record(s){window}{RESET} "
          f"{GRAY}(one per refused path){RESET}{dropped}")
    # Summed, not overwritten: two mechanism names that render to the same safe
    # string must not silently collapse into one, or the per-mechanism totals
    # would stop adding up to the record count printed one line above.
    names = {}
    for name, count in counts.items():
        safe = _printable(name)
        names[safe] = names.get(safe, 0) + count
    width = max(len(name) for name in names)
    for name, count in sorted(names.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {YELLOW}{count:>5}{RESET}  {name:<{width}}")
    print(f"{GRAY}Log: {denial_log_path()}{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
