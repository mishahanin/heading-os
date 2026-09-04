#!/usr/bin/env python3
"""Day mode CLI: run the tests the day's changes can reach, and print why.

The engine, its routes, and the honest statement of what day mode can miss are
in `scripts/utils/day_mode.py`. Read that docstring before trusting a selection.

    python scripts/day-mode.py select            # what would run, and by which route
    python scripts/day-mode.py select --json     # the same, machine-readable
    python scripts/day-mode.py run               # select, then run pytest on it
    python scripts/day-mode.py core              # the derived mandatory core
    python scripts/day-mode.py blind             # files no route can select
    python scripts/day-mode.py nightly           # the full-run contract and command
    python scripts/day-mode.py mark-green REV    # record the revision the night passed

Day mode is NEVER the default. `scripts/run-tests.py`, the pre-push gate and CI
are untouched by this file and still run the whole suite.

THE NIGHT SIDE OF THE CONTRACT. Day mode is only safe because something else
runs everything it skipped. That something is the full suite, once a night:

    python scripts/run-tests.py

and on success, `python scripts/day-mode.py mark-green $(git rev-parse HEAD)`,
which moves the base day mode selects against. A nightly failure must be LOUD:
it is the only thing standing between a day-mode selection and an untested
regression, and a nightly that fails into a log nobody reads converts day mode
from a speed-up into a hole. `nightly` prints the contract in full.

This file does not schedule anything. Scheduling belongs to the main clone; a
worktree that installs a timer or starts a daemon is exactly the failure
`CLAUDE.md` records from 2026-09-03.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import GREEN, RED, RESET  # noqa: E402
from scripts.utils.day_mode import (  # noqa: E402
    DayModeError,
    Selection,
    blind_files,
    build_index,
    changed_files,
    known_green,
    record_green,
)
from scripts.utils.day_mode import ROOT as DEFAULT_ROOT  # noqa: E402

NIGHTLY_CONTRACT = """\
The night side of the day-mode contract.

WHAT DAY MODE SKIPS, THE NIGHT MUST RUN. Day mode selects the tests a change set
can reach by import, by string literal, or by being in the derived core. Three
things are outside all of that, and only the full run covers them:

  1. A test that reaches its subject by a name no literal spells. `day-mode.py
     blind` reports the files in that state; the night runs their tests anyway
     because it runs everything.
  2. An interaction between two changes that are individually selected but whose
     shared test is selected by neither.
  3. Anything that broke for a reason unrelated to the diff: a dependency
     upgrade, a clock, a machine.

THE COMMAND. One full regression run, the same gate a push already uses:

    python scripts/run-tests.py

On success, and only on success, move the base day mode selects against:

    python scripts/day-mode.py mark-green $(git rev-parse HEAD)

A FAILING NIGHT MUST BE LOUD. Two properties, and the second is the one that
gets dropped. It has to notify a human on failure, and it has to notify one when
it did not RUN at all -- a nightly that silently stopped firing looks exactly
like a nightly that keeps passing, and day mode would keep narrowing against a
green marker that stopped moving. `mark-green` is what makes that visible: if
the recorded revision stops advancing, the night stopped working, and
`day-mode.py select` prints the marker's age on every run.

WHERE IT IS INSTALLED. In the main clone, never from a worktree. This file
deliberately installs nothing.
"""


def _print_report(selection: Selection, root: Path, base: str | None, origin: str) -> None:
    """The reasoning, in full. A selection nobody can audit is not usable."""
    print(f"Day mode selection in {root}")
    print(f"  base: {base or 'none recorded'}  ({origin})")
    marker = known_green(root)
    if marker:
        try:
            when = subprocess.run(
                ["git", "-C", str(root), "log", "-1", "--format=%cr", marker],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
        except OSError:
            when = ""
        print(f"  last known-green: {marker[:12]}{f'  ({when})' if when else ''}")
    else:
        print(
            "  last known-green: NONE RECORDED. No full run has reported success"
            " through `mark-green`, so the base below is a fallback, not a"
            " guarantee."
        )
    print(f"  changed files: {len(selection.changed)}")
    for rel in selection.changed:
        print(f"    {rel}")
    if selection.unknown_changed:
        print(f"  no longer tracked (deleted or renamed): {len(selection.unknown_changed)}")
        for rel in selection.unknown_changed:
            print(f"    {rel}")

    total = selection.total_tests
    picked = len(selection.tests)
    share = (100.0 * picked / total) if total else 0.0
    print(f"  selected: {picked} of {total} test files ({share:.1f}%)")
    print("  by route:")
    for route, count in selection.by_route().items():
        print(f"    {route:<14} {count}")
    print(f"  files parsed this run: {selection.parsed}")

    if selection.ambiguous:
        print("  AMBIGUOUS -- a basename several tracked files share; all were selected:")
        for rel, twins in selection.ambiguous.items():
            print(f"    {rel} also names: {', '.join(twins)}")
    if selection.undecided:
        print(
            f"  {RED}COULD NOT DECIDE{RESET}: {len(selection.undecided)} changed"
            " file(s) reached no test by any route. Only the mandatory core"
            " covers them."
        )
        for rel in selection.undecided:
            print(f"    {rel}")


def cmd_select(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else DEFAULT_ROOT
    index = build_index(root, use_cache=not args.no_cache)
    # `is not None`, not truthiness: `--files` with no arguments is an EMPTY
    # change set, and reading it as "no change set given" sends the caller to
    # git instead, which is a different question with a different answer.
    if args.files is not None:
        changed, origin = list(args.files), "given on the command line"
        base = None
    else:
        base = args.since or known_green(root) or _fallback_base(root)
        changed, origin = changed_files(root, base)
    from scripts.utils.day_mode import select

    selection = select(index, changed)

    if args.json:
        print(
            json.dumps(
                {
                    "root": str(root),
                    "base": base,
                    "origin": origin,
                    "changed": selection.changed,
                    "tests": selection.tests,
                    "routes": selection.routes,
                    "by_route": selection.by_route(),
                    "undecided": selection.undecided,
                    "ambiguous": selection.ambiguous,
                    "unknown_changed": selection.unknown_changed,
                    "total_tests": selection.total_tests,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    _print_report(selection, root, base, origin)
    if args.list:
        print("  tests:")
        for test in selection.tests:
            print(f"    {test}   [{'; '.join(selection.routes[test])}]")
    return 0


def _fallback_base(root: Path) -> str | None:
    """`main` when it exists and is not HEAD, else nothing.

    Named as a fallback in the report rather than presented as the known-green
    revision, because it is not one: `main` can be red.
    """
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "main"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    main = result.stdout.strip()
    return None if main == head else "main"


def cmd_run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else DEFAULT_ROOT
    index = build_index(root, use_cache=not args.no_cache)
    # `is not None`, not truthiness: `--files` with no arguments is an EMPTY
    # change set, and reading it as "no change set given" sends the caller to
    # git instead, which is a different question with a different answer.
    if args.files is not None:
        changed, origin = list(args.files), "given on the command line"
        base = None
    else:
        base = args.since or known_green(root) or _fallback_base(root)
        changed, origin = changed_files(root, base)
    from scripts.utils.day_mode import select

    selection = select(index, changed)
    _print_report(selection, root, base, origin)

    if not selection.tests:
        print(f"{RED}No tests selected.{RESET} Not proceeding: an empty selection")
        print("is a selector failure, not a clean bill of health. Run the full suite:")
        print("    python scripts/run-tests.py")
        return 2

    argv = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-n",
        "auto",
        "-m",
        "not acceptance",
        *selection.tests,
        *args.pytest_args,
    ]
    print(f"\n$ {' '.join(argv[1:6])} ... ({len(selection.tests)} files)")
    started = time.monotonic()
    result = subprocess.run(argv, cwd=root, check=False)
    elapsed = time.monotonic() - started
    colour = GREEN if result.returncode == 0 else RED
    print(f"{colour}day mode: {len(selection.tests)} files in {elapsed:.1f}s{RESET}")
    if result.returncode == 0:
        print(
            "This is a DAY-MODE pass, not a full-suite pass. The night still owes"
            " you the rest; see `python scripts/day-mode.py nightly`."
        )
    return result.returncode


def cmd_core(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else DEFAULT_ROOT
    index = build_index(root, use_cache=not args.no_cache)
    print(f"Mandatory core: {len(index.core)} of {len(index.test_files)} test files")
    print("Derived, never hand-written: a test that reads the repository tree has")
    print("the whole tree as its input, so any change alters what it sees and no")
    print("import edge and no string literal names it.")
    for rel in sorted(index.core):
        print(f"  {rel}   [{', '.join(index.core[rel])}]")
    return 0


def cmd_blind(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else DEFAULT_ROOT
    index = build_index(root, use_cache=not args.no_cache)
    blind = blind_files(index)
    if not blind:
        print(f"{GREEN}No blind files.{RESET} Every source file a test mentions is")
        print("reachable by at least one route.")
        return 0
    print(f"{RED}{len(blind)} blind file(s).{RESET} A test names each of these, but no")
    print("route can select a test for it. A change here would run only the core.")
    for rel in blind:
        print(f"  {rel}")
    return 1


def cmd_nightly(args: argparse.Namespace) -> int:
    print(NIGHTLY_CONTRACT)
    return 0


def cmd_mark_green(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else DEFAULT_ROOT
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", args.revision],
        capture_output=True,
        text=True,
        check=False,
    )
    if revision.returncode != 0:
        print(f"{RED}Not a revision in this repository: {args.revision}{RESET}")
        return 2
    record_green(root, revision.stdout.strip())
    print(f"known-green: {revision.stdout.strip()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="day-mode.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command")

    # `--root` on every subcommand rather than only before it. Declaring it once
    # on the top-level parser makes `day-mode.py select --root X` an
    # "unrecognized arguments" error, which is the spelling every caller reaches
    # for first and the one the tests here use.
    def common(p: argparse.ArgumentParser, *, change_set: bool = True) -> None:
        p.add_argument("--root", help="repository root (default: this checkout)")
        p.add_argument("--no-cache", action="store_true", help="re-parse every file")
        if change_set:
            p.add_argument("--since", help="base revision (default: the known-green marker)")
            p.add_argument("--files", nargs="*", help="use this change set instead of git")

    p_select = sub.add_parser("select", help="print the selection and its reasoning")
    common(p_select)
    p_select.add_argument("--json", action="store_true")
    p_select.add_argument("--list", action="store_true", help="print each selected test file")
    p_select.set_defaults(func=cmd_select)

    p_run = sub.add_parser("run", help="select, then run pytest on the selection")
    common(p_run)
    p_run.add_argument("pytest_args", nargs="*", help="extra arguments passed to pytest")
    p_run.set_defaults(func=cmd_run)

    p_core = sub.add_parser("core", help="print the derived mandatory core")
    common(p_core, change_set=False)
    p_core.set_defaults(func=cmd_core)

    p_blind = sub.add_parser("blind", help="files a test names that no route can select")
    common(p_blind, change_set=False)
    p_blind.set_defaults(func=cmd_blind)

    p_night = sub.add_parser("nightly", help="print the night side of the contract")
    p_night.set_defaults(func=cmd_nightly)

    p_green = sub.add_parser("mark-green", help="record the revision a full run passed on")
    p_green.add_argument("--root", help="repository root (default: this checkout)")
    p_green.add_argument("revision")
    p_green.set_defaults(func=cmd_mark_green)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except DayModeError as exc:
        print(f"{RED}day mode: {exc}{RESET}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
