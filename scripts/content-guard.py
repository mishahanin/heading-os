#!/usr/bin/env python3
"""Engine CONTENT-leak gate: scan engine-routed files for real-entity tokens.

The routing guards (leak-guard, engine_guard, the push wall) check WHERE a file
goes; this one checks WHAT is inside an engine-routed file. It builds a real-entity
denylist from the private DATA overlay (scripts/utils/content_denylist.py) and
refuses any engine-routed file that carries a real person slug/name, handle,
e-mail, Telegram ID, or a curated company/event/codename.

On a public clone / CI the DATA overlay is absent: the denylist is empty and the
gate no-ops (the only machine that authors AND pushes engine files -- the
operator's -- has the overlay). Annotate a genuine false positive inline with
``content-guard: ok <reason>`` to suppress one line (mirrors ``leak-guard: ok``).

Usage:
  python scripts/content-guard.py --all                 # scan whole engine surface
  python scripts/content-guard.py --files a.py b.md      # scan specific files
  python scripts/content-guard.py --stdin               # newline-delimited paths on stdin

Exit: 0 clean, 1 leak(s) found OR a file that could not be scanned,
2 internal error.

An unreadable engine-routed file used to be warned about on stderr and then
exit 0. The exit code is the contract CI consumes, so "clean" shipped over a
file nobody had looked at - which is the one outcome this gate exists to
prevent. Not-scanned now fails the same way a leak does: unverified is not
clean.

Tests: tests/test_a_gate_that_shipped_what_it_never_read.py
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.content_denylist import build_denylist
from scripts.utils.denial_log import log_denial
from scripts.utils.engine_guard import engine_text_files, repo_carried_paths
from scripts.utils.workspace import get_data_root, get_workspace_root


def main() -> int:
    ap = argparse.ArgumentParser(description="HEADING OS engine content-leak gate")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="scan the whole engine surface")
    g.add_argument("--files", nargs="*", help="scan these paths")
    g.add_argument("--stdin", action="store_true", help="read newline-delimited paths from stdin")
    ap.add_argument("--data-root", help="override the DATA overlay path (default: get_data_root())")
    ap.add_argument("--strict", action="store_true",
                    help="also flag bare name-words split from person slugs (noisy; deep-audit only)")
    ap.add_argument("--quiet", action="store_true", help="print nothing on a clean result")
    args = ap.parse_args()

    root = get_workspace_root()

    if args.data_root:
        data_root = Path(args.data_root)
    else:
        try:
            data_root = get_data_root()
        except Exception:
            data_root = None
    # In the pre-cutover single repo (data == engine) the overlay IS the repo, so
    # every real entity would flag the repo against itself. No-op in that mode.
    if data_root is not None and Path(data_root) == root:
        if not args.quiet:
            print(f"{GRAY}content-guard: data root == engine (single repo); skipped.{RESET}")
        return 0

    dl = build_denylist(data_root, strict=args.strict)
    if dl.degraded or not dl.tokens:
        # Name the state, not a guessed cause. This printed "no DATA overlay"
        # for BOTH the absent overlay and a harvest that failed part-way -- so a
        # malformed config on the operator's own machine switched the content
        # layer off while blaming a condition that was not true. The overlay
        # directory is right there to check.
        if not data_root.is_dir():
            why = "no DATA overlay at this path"
        elif dl.degraded:
            why = "the denylist harvest failed; see the stderr line above"
        else:
            why = "the overlay holds no entities to guard"
        if not args.quiet:
            print(f"{GRAY}content-guard: denylist unavailable ({why}); skipped.{RESET}")
        return 0

    if args.all:
        candidates = repo_carried_paths(root)
    elif args.stdin:
        candidates = [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
    else:
        candidates = args.files or []

    files = engine_text_files(root, candidates)

    findings: list[tuple[str, int, str, str]] = []
    unscanned: list[str] = []
    for rel in files:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            # Never silent. This gate exists so nothing unscanned ships, and a
            # bare `continue` meant an engine-routed file with invalid UTF-8, or
            # one hitting a transient read error, passed with no record at all —
            # a clean verdict over a file nobody looked at.
            unscanned.append(f"{rel}: {exc}")
            continue
        for lineno, matched, category in dl.scan_text(text):
            findings.append((rel, lineno, matched, category))

    if unscanned:
        print(f"{YELLOW}content-guard: {len(unscanned)} file(s) could not be read "
              f"and were NOT scanned:{RESET}", file=sys.stderr)
        for note in unscanned:
            print(f"  {YELLOW}{note}{RESET}", file=sys.stderr)

    if findings:
        # --all is the hand-run sweep of the whole engine surface; no gate drives
        # it and nothing is in flight to refuse. The hit is still real and worth
        # recording, but calling it a commit would be false, so the record says
        # which it was and a reader can tell a caught commit from a report.
        action = "audit" if args.all else "commit"
        for rel, lineno, _matched, category in findings:
            # The matched token IS the real-entity value, so it never enters the
            # record: where and what class, not what.
            log_denial(mechanism="content-guard", action=action,
                       path=f"{rel}:{lineno}", reason=f"real-entity token [{category}]")
        print(f"{RED}{BOLD}BLOCKED — real-entity content in engine-routed file(s):{RESET}")
        for rel, lineno, matched, category in findings:
            print(f"  {RED}{rel}:{lineno}{RESET}  \"{matched}\"  {GRAY}[{category}]{RESET}")
        print(f"{GRAY}The engine ships no real data. Genericize to a placeholder, move the "
              f"value to the private DATA overlay, or — if it is a true false positive — "
              f"annotate the line with `content-guard: ok <reason>`.{RESET}")
        return 1

    if unscanned:
        # Refused, not merely reported. See the module docstring: the exit code
        # is what a gate is, and one that prints a warning and returns 0 has
        # told CI the surface is clean.
        print(f"{RED}content-guard: REFUSED{RESET} {GRAY}({len(unscanned)} "
              f"engine-routed file(s) could not be scanned; fix or exclude "
              f"them, do not ship unverified){RESET}", file=sys.stderr)
        return 1

    if not args.quiet:
        scope = "engine surface" if args.all else f"{len(files)} file(s)"
        print(f"{GREEN}content-guard: clean{RESET} {GRAY}({scope}; "
              f"{len(dl.tokens)} denylist tokens){RESET}")
    return 0


if __name__ == "__main__":
    # Exit 2 is the documented "internal error" code and nothing produced it:
    # `build_denylist` or the workspace resolver raising propagated as a
    # traceback and Python exited 1 — indistinguishable, to any CI step keying
    # on the contract, from "a leak was found". A gate that reports a crash as a
    # catch is worse than one that crashes loudly.
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - the documented exit-2 contract
        print(f"{RED}content-guard: internal error: "
              f"{type(exc).__name__}: {exc}{RESET}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(2) from exc
