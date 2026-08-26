#!/usr/bin/env python3
"""Run the integration and parser test suites with coverage reporting.

Suites included:
    tests/integration/          -- sentinel integration tests
    tests/test_calibrate_parser.py -- calibrate JSONL parser tests (CEO-only)

Usage:
    python scripts/run-integration-tests.py              # full run, terminal output
    python scripts/run-integration-tests.py --quiet      # suppress verbose output
    python scripts/run-integration-tests.py --no-cov     # skip coverage measurement

Exit codes. pytest's own code is returned VERBATIM, so this list is pytest's,
not a shorter one of our own. It used to stop at 2, which said that 3, 4, 5 and
6 could not occur; they can, they reach the caller unchanged, and every one of
them means the suite did not complete:
    0 - all tests passed
    1 - one or more tests failed
    2 - interrupted: a collection error, Ctrl-C, or --maxfail reached.
        Also returned by this script when pytest itself is not installed.
    3 - pytest internal error
    4 - pytest usage error (an unrecognised option, or a missing path)
    5 - no tests were collected: nothing was measured
    6 - max warnings exceeded
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

# Bootstrap path so we can import from scripts.utils.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root
from scripts.utils.colors import BOLD, GREEN, GRAY, RED, RESET, YELLOW

WORKSPACE_ROOT = get_workspace_root()

# pytest's codes above 1, in words. A bare "[WARN] pytest exited with code 5"
# left the reader to look the number up, and called the loudest outcome in the
# set -- nothing was measured at all -- a warning, in yellow. That is the same
# misdirection the pytest-not-installed probe below was written to end.
PYTEST_EXIT_MEANING = {
    2: "interrupted - a collection error, Ctrl-C, or --maxfail reached",
    3: "pytest internal error",
    4: "pytest usage error - an unrecognised option, or a missing path",
    5: "no tests were collected",
    6: "max warnings exceeded",
}


def run_tests(quiet: bool = False, with_coverage: bool = True) -> int:
    """Execute pytest on tests/integration/ and registered parser tests.

    Runs with cwd anchored to the workspace root so the script works from any
    invocation directory (pre-commit hooks, cron, direct calls).
    """
    tests_dir = WORKSPACE_ROOT / "tests" / "integration"
    # Additional test files outside tests/integration/ registered here.
    extra_test_files = [
        WORKSPACE_ROOT / "tests" / "test_calibrate_parser.py",
    ]
    cmd = [sys.executable, "-m", "pytest", str(tests_dir)]
    cmd.extend(str(f) for f in extra_test_files if f.exists())
    if not quiet:
        cmd.append("-v")
    if with_coverage:
        cmd.extend(["--cov=scripts.sentinel", "--cov-report=term"])

    print(f"{BOLD}Running sentinel integration tests{RESET}")
    print(f"{GRAY}Command: {' '.join(cmd)}{RESET}")
    print(f"{GRAY}cwd: {WORKSPACE_ROOT}{RESET}\n")

    # Probe FIRST. The `except FileNotFoundError` that stood here could never
    # fire -- the missing thing is pytest, not sys.executable -- so a box without
    # pytest got exit 1 and the words "One or more tests failed", pointing triage
    # at the tests instead of at the environment.
    if importlib.util.find_spec("pytest") is None:
        print(f"{RED}pytest not installed. Run: pip install pytest pytest-asyncio pytest-cov{RESET}")
        return 2

    result = subprocess.run(cmd, check=False, cwd=str(WORKSPACE_ROOT))

    print()
    if result.returncode == 0:
        print(f"{GREEN}{BOLD}[PASS] All integration tests passed.{RESET}")
    elif result.returncode == 1:
        print(f"{RED}{BOLD}[FAIL] One or more tests failed.{RESET}")
    else:
        meaning = PYTEST_EXIT_MEANING.get(result.returncode,
                                          "unrecognised pytest exit code")
        print(f"{RED}{BOLD}[ERROR] pytest exited {result.returncode}: "
              f"{meaning}.{RESET}")
        print(f"{YELLOW}The suite did not complete. No pass/fail result was "
              f"measured - treat this as an environment problem, not a test "
              f"failure.{RESET}")

    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose per-test output")
    parser.add_argument("--no-cov", action="store_true", help="Skip coverage measurement")
    args = parser.parse_args()

    return run_tests(quiet=args.quiet, with_coverage=not args.no_cov)


if __name__ == "__main__":
    sys.exit(main())
