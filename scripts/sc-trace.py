#!/usr/bin/env python3
"""A12 — which criterion is decided by which test, read without running the gate.

    python scripts/sc-trace.py --anchor <gate artifact> --contract <dir>

Prints one row per success criterion the artifact states, naming the contract
files whose test docstrings claim it. Exits 1 on the same finding that refuses
`approve` and `freeze`, because a report that disagrees with the gate is
decoration.

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
    claims = read_claims(contract_sources(args.contract))
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
