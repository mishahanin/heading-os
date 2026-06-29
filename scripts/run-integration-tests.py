#!/usr/bin/env python3
"""Run the integration and parser test suites with coverage reporting.

Suites included:
    tests/integration/          -- sentinel integration tests (14 tests)
    tests/test_calibrate_parser.py -- calibrate JSONL parser tests (CEO-only)

Usage:
    python scripts/run-integration-tests.py              # full run, terminal output
    python scripts/run-integration-tests.py --quiet      # suppress verbose output
    python scripts/run-integration-tests.py --no-cov     # skip coverage measurement

Exit codes:
    0 - all tests passed
    1 - one or more tests failed
    2 - pytest collection error or infrastructure issue
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Bootstrap path so we can import from scripts.utils.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root
from scripts.utils.colors import BOLD, GREEN, GRAY, RED, RESET, YELLOW

WORKSPACE_ROOT = get_workspace_root()


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

    try:
        result = subprocess.run(cmd, check=False, cwd=str(WORKSPACE_ROOT))
    except FileNotFoundError:
        print(f"{RED}pytest not installed. Run: pip install pytest pytest-asyncio pytest-cov{RESET}")
        return 2

    print()
    if result.returncode == 0:
        print(f"{GREEN}{BOLD}[PASS] All integration tests passed.{RESET}")
    elif result.returncode == 1:
        print(f"{RED}{BOLD}[FAIL] One or more tests failed.{RESET}")
    else:
        print(f"{YELLOW}{BOLD}[WARN] pytest exited with code {result.returncode}{RESET}")

    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose per-test output")
    parser.add_argument("--no-cov", action="store_true", help="Skip coverage measurement")
    args = parser.parse_args()

    return run_tests(quiet=args.quiet, with_coverage=not args.no_cov)


if __name__ == "__main__":
    sys.exit(main())
