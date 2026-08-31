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

Exit: 0 clean, 1 leak(s) found OR a file that could not be scanned, 2 internal error.
An EMPTY denylist also exits 0, printing "denylist unavailable; skipped." That is
the public-clone / CI state described above; this line used to claim it exited 1,
contradicting both the paragraph above it and the code.

Keep "2 internal error." on one line. `tests/test_a_tool_that_reports_less_than_it_checked.py`
asserts that literal substring, and wrapping it across a newline made the test red
while every word survived -- the same shape as a substring that outlives a re-wrap.

An unreadable engine-routed file used to be warned about on stderr and then
exit 0. The exit code is the contract CI consumes, so "clean" shipped over a
file nobody had looked at - which is the one outcome this gate exists to
prevent. Not-scanned now fails the same way a leak does: unverified is not
clean.

Tests: tests/test_a_gate_that_shipped_what_it_never_read.py,
tests/test_two_controls_that_measured_themselves.py
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


def denylist_verdict(degraded: bool, token_count: int, root_state: str):
    """Decide whether a denylist is usable, and say why when it is not.

    Pure: three scalars in, ``(usable, why)`` out, no filesystem and no config.
    It was a bare ``if dl.degraded or not dl.tokens:`` inline in ``main`` until
    2026-08-29, and inline is why nothing ever exercised it: the four states it
    separates are only reachable through a real overlay, so the suite always
    drove exactly one of them. Measured that day, rewriting the ``or`` to
    ``and`` left the gate printing ``content-guard: clean (1 file(s); 0
    denylist tokens)`` at exit 0 while all 92 tests over this gate passed.
    Out here the states are just arguments, and each one has a case.

    ``root_state`` is one of ``unresolved`` (no path could be determined),
    ``absent`` (a path that is not a directory), or ``present``. It only
    chooses the sentence; it never changes the verdict, because a denylist
    with no tokens is unusable whatever the overlay looks like.
    """
    if not degraded and token_count > 0:
        return True, ""
    if root_state == "unresolved":
        why = "the DATA overlay path could not be resolved"
    elif root_state == "absent":
        why = "no DATA overlay at this path"
    elif degraded:
        why = "the denylist harvest failed; see the stderr line above"
    else:
        why = "the overlay holds no entities to guard"
    return False, why


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
    # Name the state, not a guessed cause. This printed "no DATA overlay" for
    # BOTH the absent overlay and a harvest that failed part-way -- so a
    # malformed config on the operator's own machine switched the content layer
    # off while blaming a condition that was not true. The overlay directory is
    # right there to check.
    # `data_root is None` FIRST. The `except` clause above sets it to None, and
    # the old inline branch then called `.is_dir()` on it, so the graceful path
    # raised AttributeError, the __main__ handler turned that into the
    # documented exit 2 "internal error", and the operator read a traceback
    # instead of the message written to name the state. The branch could never
    # be taken. The sibling wall `push_all.engine_content_scan` guards the same
    # value correctly. Resolving the state HERE, before the verdict, is what
    # keeps that ordering from being re-broken by an edit inside the verdict.
    #
    # An unresolvable root is also a DIFFERENT state from an overlay that is
    # simply absent - it means HEADING_OS_DATA names a path that is not there,
    # or the resolver itself failed - so it gets its own sentence. Reporting
    # "no DATA overlay at this path" for it is the guessed cause the comment
    # above forbids.
    if data_root is None:
        root_state = "unresolved"
    elif not data_root.is_dir():
        root_state = "absent"
    else:
        root_state = "present"
    usable, why = denylist_verdict(dl.degraded, len(dl.tokens), root_state)
    if not usable:
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

    if not dl.tokens:
        # Guard the CLAIM, not only the early return. Reaching here with an
        # empty denylist means the loop above compared every engine file
        # against nothing and found nothing, which is arithmetic, not evidence.
        # The early return near the top of main() is what normally makes this
        # line unreachable, and until 2026-08-29 it was the ONLY thing that
        # did: measured that day, changing its `or` to `and` printed
        # "content-guard: clean (1 file(s); 0 denylist tokens)" and exited 0,
        # and all 92 tests over this gate still passed. The clean line even
        # printed the token count that refuted it. A clean verdict is a
        # statement about what was compared, so it is refused when nothing was.
        # The legitimate empty cases (no overlay, unresolvable root, a harvest
        # that failed part-way) return 0 above and say "skipped", never
        # "clean", so this refusal cannot fire on a public clone or in CI.
        print(f"{RED}content-guard: REFUSED{RESET} {GRAY}(the denylist held 0 "
              f"tokens at scan time, so nothing was really compared; a clean "
              f"verdict here would be a verdict over nothing){RESET}",
              file=sys.stderr)
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
