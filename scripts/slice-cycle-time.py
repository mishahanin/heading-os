#!/usr/bin/env python3
"""How long a slice takes, and how much of that was friction.

Every subtraction argument weighs a mechanism's catch rate against its cost, and
until this existed the workspace measured only the first half. The denial counter
answers "did this guard catch anything". This answers "what did the process
cost", from the ledger Canopus already writes. See
`docs/superpowers/specs/2026-08-01-canopus-v2-design.md` §6 A9.

    python scripts/slice-cycle-time.py            # per slice, plus a summary
    python scripts/slice-cycle-time.py --json

**The limit, stated rather than implied.** The earliest machine-recorded moment
in a slice is its approval, so this reports approve-to-release. Deciding what to
build, writing the plan and writing the test all happen before that mark and are
real work this number does not contain. Read it as the cost of the LIFECYCLE, not
the cost of the slice, and do not let it be quoted as the latter.

Friction is reported beside duration on purpose. A six-hour slice that ran
straight through and a six-hour slice that opened a window, retook its approval
and failed a verify are the same number and completely different events, and only
the second one is an argument about the mechanism.
"""
import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, GRAY, GREEN, RESET, YELLOW
from scripts.utils.paths import get_workspace_root


def _history_path(root: Path) -> Path:
    try:
        from scripts.utils.canopus_freeze import history_state_path

        return history_state_path(root)
    except Exception:
        # The ledger's location is a stable convention; falling back to it keeps
        # the reader working against an archived tree with no canopus importable.
        return root / ".canopus" / "history.jsonl"


def _rows(path: Path) -> list:
    """Every readable entry. A corrupt line is skipped, never fatal: a truncated
    append must not cost the rest of the history."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _when(row):
    """The row's timestamp as an AWARE datetime, or None.

    `append_history` writes `datetime.now(timezone.utc).isoformat()`, so every
    row this workspace produces carries an offset. A hand-edit or an older
    ledger format need not, and a single naive row among aware ones used to
    raise `TypeError: can't compare offset-naive and offset-aware datetimes`
    out of the sort — killing the whole report over one bad line, in a file that
    otherwise skips a corrupt line and carries on. Naive is read as UTC, which
    is the convention the only writer uses.
    """
    try:
        stamp = datetime.fromisoformat(str(row.get("ts")))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp


def summarise(rows) -> dict:
    by_label = {}
    for row in rows:
        label = row.get("label") or "(unlabelled)"
        by_label.setdefault(label, []).append(row)

    slices = []
    for label, events in by_label.items():
        stamped = [(t, e) for e in events if (t := _when(e)) is not None]
        stamped.sort(key=lambda pair: pair[0])
        approvals = [t for t, e in stamped if e.get("event") == "approve"]
        ships = [t for t, e in stamped if e.get("event") == "release"
                 and e.get("kind") == "ship"]
        windows = sum(1 for _t, e in stamped
                      if e.get("event") == "release" and e.get("kind") == "window")
        reapprovals = sum(1 for _t, e in stamped if e.get("event") == "anchor_replaced")
        verify_failures = sum(1 for _t, e in stamped if e.get("event") == "verify_fail")

        # The FIRST approval, so a retake cannot shorten the slice it lengthened.
        start = approvals[0] if approvals else None
        end = ships[-1] if ships else None
        hours = round((end - start).total_seconds() / 3600, 2) if start and end else None
        slices.append({
            "label": label,
            "started": start.isoformat() if start else None,
            "shipped": bool(end),
            "hours": hours,
            "windows": windows,
            "reapprovals": reapprovals,
            "verify_failures": verify_failures,
            "events": len(events),
        })

    slices.sort(key=lambda s: (s["started"] or ""))
    # Open slices are excluded rather than counted as zero: a slice still running
    # has no duration yet, and folding it in as 0 would flatter every average.
    durations = [s["hours"] for s in slices if s["hours"] is not None]
    summary = {
        "slices": len(slices),
        "shipped_count": len(durations),
        "median_hours": round(statistics.median(durations), 2) if durations else None,
        "mean_hours": round(statistics.fmean(durations), 2) if durations else None,
        "total_windows": sum(s["windows"] for s in slices),
        "total_reapprovals": sum(s["reapprovals"] for s in slices),
        "total_verify_failures": sum(s["verify_failures"] for s in slices),
    }
    return {"slices": slices, "summary": summary}


def main() -> int:
    parser = argparse.ArgumentParser(description="Canopus slice cycle time and friction.")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    root = get_workspace_root()
    path = _history_path(root)
    rows = _rows(path)
    result = summarise(rows)

    if args.as_json:
        result["ledger"] = str(path)
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if not rows:
        print(f"{GREEN}No slice history yet.{RESET} {GRAY}({path}){RESET}")
        return 0

    print(f"{BOLD}Slice cycle time{RESET} {GRAY}measured approve to release; the "
          f"deciding, planning and test-writing before the approval are not in "
          f"this number{RESET}")
    print()
    width = max(len(s["label"]) for s in result["slices"])
    for entry in result["slices"]:
        duration = (f"{YELLOW}open{RESET}" if entry["hours"] is None
                    else f"{entry['hours']:>6.2f}h")
        friction = []
        if entry["windows"]:
            friction.append(f"{entry['windows']} window(s)")
        if entry["reapprovals"]:
            friction.append(f"{entry['reapprovals']} retake(s)")
        if entry["verify_failures"]:
            friction.append(f"{entry['verify_failures']} verify fail(s)")
        tail = f"  {GRAY}{', '.join(friction)}{RESET}" if friction else ""
        print(f"  {entry['label']:<{width}}  {duration}{tail}")

    summary = result["summary"]
    print()
    # No shipped slice means no duration to average, which is a real state on a
    # fresh ledger. It used to render as "median Noneh, mean Noneh".
    central = (f"median {summary['median_hours']}h, mean {summary['mean_hours']}h"
               if summary["shipped_count"] else "no completed slice to average yet")
    print(f"{BOLD}{summary['shipped_count']} shipped of {summary['slices']}{RESET}"
          f"  {GRAY}{central}{RESET}")
    print(f"{GRAY}friction across all slices: {summary['total_windows']} window(s), "
          f"{summary['total_reapprovals']} retake(s), "
          f"{summary['total_verify_failures']} verify failure(s){RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
