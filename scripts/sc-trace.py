#!/usr/bin/env python3
"""A12 — which criterion is decided by which test, read without running the gate.

    python scripts/sc-trace.py --anchor <gate artifact> --contract <dir>

Prints one row per success criterion the artifact states, naming the contract
files whose test docstrings claim it. Exits 1 on any of the three findings
`sc_trace.refusal` names: an artifact stating no criteria at all, a criterion
no contract test claims, and a claim naming a criterion the artifact does not
define. It used to say it exited on "the same finding that refuses `approve`
and `freeze`"; those two commands went with the Canopus freeze lifecycle on
2026-08-07, and this CLI is now the only reader of that finding.

Reads two things and writes nothing. It proves a criterion has a test CLAIMING
to decide it; it cannot prove the test decides it. That limitation is printed
under every clean trace on purpose: the reading "every criterion is decided" is
false, and an operator who adopts it stops reading the tests.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))

from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.sc_trace import (  # noqa: E402
    contract_sources,
    read_claims,
    read_criteria,
    refusal,
    trace,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="sc-trace",
        description="Which test decides which success criterion.")
    parser.add_argument("--anchor", required=True,
                        help="the gate artifact stating the success criteria")
    parser.add_argument("--contract", required=True, action="append",
                        help="a contract directory or test file (repeatable)")
    args = parser.parse_args(argv)

    anchor = Path(args.anchor)
    if not anchor.is_file():
        print(f"{RED}sc-trace: no artifact at {anchor}{RESET}", file=sys.stderr)
        return 2

    criteria = read_criteria(anchor.read_text(encoding="utf-8", errors="replace"))
    # Same courtesy `--anchor` already gets one branch up. A mistyped
    # `--contract` reached `read_text` and tracebacked, which reads as a broken
    # tool rather than as a wrong argument -- and this is the command an author
    # runs at step 4 to check a path he is still guessing at.
    try:
        sources = contract_sources(args.contract)
    except OSError as exc:
        print(f"{RED}sc-trace: cannot read the contract at "
              f"{exc.filename or args.contract}: {exc.strerror or exc}{RESET}",
              file=sys.stderr)
        return 2
    claims = read_claims(sources)
    result = trace(criteria, claims)

    print(f"{BOLD}CRITERIA{RESET}  {len(result['criteria'])} stated in {anchor.name}")
    for name in result["criteria"]:
        files = result["bound"].get(name)
        if files:
            print(f"  {GREEN}{name}{RESET}  {GRAY}{', '.join(files)}{RESET}")
        else:
            print(f"  {RED}{name}{RESET}  {RED}claimed by no test{RESET}")
    for name in result["orphan"]:
        files = ", ".join(sorted(claims[name]))
        print(f"  {YELLOW}{name}{RESET}  {YELLOW}claimed in {files}, defined "
              f"nowhere in the artifact{RESET}")

    message = refusal(result)
    if message:
        print()
        print(f"{RED}{BOLD}BLOCKED{RESET} {RED}{message}{RESET}")
        return 1

    print()
    print(f"{GRAY}Every criterion has a test claiming to decide it. That is not "
          f"the same as{RESET}")
    print(f"{GRAY}every criterion being decided: this reads docstrings, never "
          f"assertions.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
