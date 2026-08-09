#!/usr/bin/env python3
"""Thin CLI over the /scrutinize structured run record.

The writer, the schema and the validator all live in
`scripts/utils/scrutinize_record.py`, which is snake_case so the dispatcher and
`scrutinize-flag-fp.py` can import it by name. This file is the operator-facing
surface: append one row, or validate a run against its saved report.

Usage:
  python scripts/scrutinize-record.py --append --run-id <id> --kind role \\
      --target dir:scripts --role ops
  python scripts/scrutinize-record.py --validate --run-id <id> \\
      --report outputs/operations/scrutiny/2026-08-09-execution.md

Exit codes:
  0  clean (validate) or row written (append)
  1  the run failed validation; every defect is printed to stderr
  2  bad arguments
  3  the row was refused by the schema
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.scrutinize_record import (  # noqa: E402
    KINDS,
    append_row,
    record_path,
    validate,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Append to or validate the /scrutinize run record.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--append", action="store_true", help="Append one row.")
    mode.add_argument("--validate", action="store_true",
                      help="Reconcile a run against its saved report.")

    parser.add_argument("--run-id", required=True)
    parser.add_argument("--target", default="")
    parser.add_argument("--kind", choices=sorted(KINDS))
    parser.add_argument("--finding-id")
    parser.add_argument("--pass", dest="pass_", choices=["2.5a", "2.5b"])
    parser.add_argument("--judge-family", choices=["claude", "kimi"])
    parser.add_argument("--verdict")
    parser.add_argument("--confidence-before", type=int)
    parser.add_argument("--confidence-after", type=int)
    parser.add_argument("--role", choices=["ops", "scheduler", "boundary"])
    parser.add_argument("--degraded")
    parser.add_argument("--writer", default="dispatch")
    parser.add_argument("--report", help="Saved report path (required with --validate).")

    args = parser.parse_args(argv)

    if args.validate:
        if not args.report:
            print(f"{RED}ERROR: --validate requires --report{RESET}", file=sys.stderr)
            return 2
        defects = validate(run_id=args.run_id, report_path=Path(args.report))
        if not defects:
            print(f"{GREEN}run {args.run_id}: record and report reconcile{RESET}")
            return 0
        print(f"{RED}run {args.run_id} has {len(defects)} defect(s):{RESET}",
              file=sys.stderr)
        for d in defects:
            print(f"{RED}  - {d}{RESET}", file=sys.stderr)
        print(f"{YELLOW}note: this check sees omission, not intent. A Claude-side "
              f"verdict is still supplied by the session.{RESET}", file=sys.stderr)
        return 1

    if not args.kind or not args.target:
        print(f"{RED}ERROR: --append requires --kind and --target{RESET}",
              file=sys.stderr)
        return 2
    try:
        row = append_row(
            run_id=args.run_id, kind=args.kind, target=args.target,
            finding_id=args.finding_id, pass_=args.pass_,
            judge_family=args.judge_family, verdict=args.verdict,
            confidence_before=args.confidence_before,
            confidence_after=args.confidence_after,
            role=args.role, degraded=args.degraded, writer=args.writer,
        )
    except ValueError as exc:
        print(f"{RED}ERROR: row refused: {exc}{RESET}", file=sys.stderr)
        return 3
    print(json.dumps(row, ensure_ascii=False))
    print(f"{GREEN}appended to {record_path()}{RESET}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
