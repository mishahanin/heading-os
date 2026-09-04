#!/usr/bin/env python3
"""Ask which test files still have to run, and record the ones that passed.

Terminal-native and browser-free, per `.claude/rules/console-first.md`. Every
capability here is a subcommand; nothing renders a page and nothing listens on a
port. The store is one SQLite file (`.claude/rules/persistence.md`).

NOT WIRED INTO ANY GATE, DELIBERATELY. `scripts/run-tests.py`, the pre-push hook
and CI do not call this and must not: a cache is a claim about what could have
changed, and the gate that ships code is the wrong place to accept such a claim.
It is a tool day mode drives, and day mode is the thing that knows which files
are its mandatory core.

Usage:
  python scripts/test-cache.py key
  python scripts/test-cache.py classify tests/test_foo.py
  python scripts/test-cache.py classify --summary
  python scripts/test-cache.py plan --base <marker> --from -        # paths on stdin
  python scripts/test-cache.py record --base <marker> --from -
  python scripts/test-cache.py revoke --base <marker> --from -
  python scripts/test-cache.py status
  python scripts/test-cache.py clear

THE BASE. `--base` is required on `plan`, `record` and `revoke`, and it is not
decoration: it is day mode's night-contract marker, moved by `mark-green` after
a full nightly suite. A verdict is stored against it, so moving the base
discards every verdict at the old one. Making the flag REQUIRED rather than
defaulting it to the empty string is the fail-closed shape: forgetting the base
is then an error, not a silent agreement that there is no night contract.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.test_cache import (  # noqa: E402
    ROOT, Classifier, KeyUnavailable, VerdictStore, corpus_key, plan_run,
)


def _read_paths(args) -> list[str]:
    raw = (sys.stdin.read() if args.source == "-"
           else Path(args.source).read_text(encoding="utf-8"))
    return [p for p in raw.split() if p]


def _all_test_files() -> list[str]:
    return sorted(p.relative_to(ROOT).as_posix()
                  for p in (ROOT / "tests").rglob("test_*.py"))


def cmd_key(args) -> int:
    try:
        print(corpus_key(ROOT))
    except KeyUnavailable as exc:
        print(f"{RED}no key: {exc}{RESET}", file=sys.stderr)
        return 1
    return 0


def cmd_classify(args) -> int:
    files = args.files or _all_test_files()
    classifier = Classifier(ROOT)
    verdicts = [classifier.classify(f) for f in files]
    if args.summary:
        counts: dict[str, int] = {}
        for verdict in verdicts:
            counts[verdict.bucket] = counts.get(verdict.bucket, 0) + 1
        total = len(verdicts) or 1
        print(f"{BOLD}{len(verdicts)} file(s){RESET}")
        for bucket, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            colour = GREEN if bucket == "engine" else YELLOW
            print(f"  {colour}{bucket:14s}{RESET} {count:5d}  {100 * count / total:5.1f}%")
        return 0
    for verdict in verdicts:
        colour = GREEN if verdict.cacheable else YELLOW
        print(f"{colour}{verdict.bucket:14s}{RESET} {verdict.path}  {GRAY}{verdict.why}{RESET}")
    return 0


def cmd_plan(args) -> int:
    files = _read_paths(args)
    if not files:
        # An empty input is not "nothing to run"; it is a caller that produced
        # no list, and answering "skip everything" to that is the same shape as
        # a guard reporting clean over an empty corpus.
        print(f"{RED}no test files given: refusing to answer over an empty "
              f"list{RESET}", file=sys.stderr)
        return 2
    plan = plan_run(files, args.base, root=ROOT)
    for warning in plan.warnings:
        print(f"{RED}{BOLD}{warning}{RESET}", file=sys.stderr)
    if args.explain:
        for name in files:
            marker = f"{GREEN}SKIP{RESET}" if name in plan.skip else f"{CYAN}RUN {RESET}"
            print(f"{marker} {name}  {GRAY}{plan.reasons.get(name, '')}{RESET}",
                  file=sys.stderr)
    print(f"{len(plan.run)} to run, {len(plan.skip)} skipped "
          f"({len(plan.must_run)} of the runs must always run)", file=sys.stderr)
    for name in plan.run:
        print(name)
    return 0


def cmd_record(args) -> int:
    files = _read_paths(args)
    if not files:
        print(f"{RED}no test files given{RESET}", file=sys.stderr)
        return 2
    try:
        key = corpus_key(ROOT)
    except KeyUnavailable as exc:
        print(f"{RED}nothing recorded: {exc}{RESET}", file=sys.stderr)
        return 1
    store = VerdictStore()
    if not store.record(args.base, key, files):
        print(f"{RED}nothing recorded: {store.corrupt_reason}{RESET}", file=sys.stderr)
        return 1
    print(f"{GREEN}recorded {len(files)} green verdict(s){RESET} at base "
          f"{args.base} key {key[:12]}", file=sys.stderr)
    return 0


def cmd_revoke(args) -> int:
    files = _read_paths(args)
    try:
        key = corpus_key(ROOT)
    except KeyUnavailable as exc:
        print(f"{RED}nothing revoked: {exc}{RESET}", file=sys.stderr)
        return 1
    store = VerdictStore()
    if not store.revoke(args.base, key, files):
        print(f"{RED}nothing revoked: {store.corrupt_reason}{RESET}", file=sys.stderr)
        return 1
    print(f"{YELLOW}revoked {len(files)} verdict(s){RESET}", file=sys.stderr)
    return 0


def cmd_status(args) -> int:
    store = VerdictStore()
    rows = store.rows()
    print(f"store:  {store.path}")
    if store.corrupt_reason:
        print(f"{RED}state:  UNREADABLE ({store.corrupt_reason}) "
              f"- every file would run{RESET}")
        return 1
    print(f"rows:   {rows}")
    try:
        print(f"key:    {corpus_key(ROOT)}")
    except KeyUnavailable as exc:
        print(f"{RED}key:    unavailable ({exc}) - every file would run{RESET}")
        return 1
    return 0


def cmd_clear(args) -> int:
    store = VerdictStore()
    if not store.clear():
        print(f"{RED}could not clear: {store.corrupt_reason}{RESET}", file=sys.stderr)
        return 1
    print(f"{GREEN}cleared{RESET}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("key", help="print the corpus key").set_defaults(func=cmd_key)

    classify = subparsers.add_parser(
        "classify", help="what each test file reads, and so whether it can be cached")
    classify.add_argument("files", nargs="*")
    classify.add_argument("--summary", action="store_true")
    classify.set_defaults(func=cmd_classify)

    for name, func, helptext in (
        ("plan", cmd_plan, "print the files that still have to run"),
        ("record", cmd_record, "record these files as green at the current key"),
        ("revoke", cmd_revoke, "drop these files' verdicts (a nightly failure)"),
    ):
        sub = subparsers.add_parser(name, help=helptext)
        sub.add_argument("--base", required=True,
                         help="day mode's night-contract base marker")
        sub.add_argument("--from", dest="source", default="-",
                         help="file holding the test paths, or - for stdin")
        if name == "plan":
            sub.add_argument("--explain", action="store_true",
                             help="print the reason for every file, on stderr")
        sub.set_defaults(func=func)

    subparsers.add_parser("status", help="store location, row count, current key"
                          ).set_defaults(func=cmd_status)
    subparsers.add_parser("clear", help="drop every verdict"
                          ).set_defaults(func=cmd_clear)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
