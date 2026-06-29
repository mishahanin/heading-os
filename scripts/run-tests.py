#!/usr/bin/env python3
"""The single test-gate entry point. Both push-all.py and .githooks/pre-push call this.

Default mode runs the regression suite (everything EXCEPT the acceptance gates),
with the coverage floor from pyproject. --acceptance runs only the A+ sign-off
gates (the findings-registry zero-open check, etc.).

Usage:
  python scripts/run-tests.py            # regression gate (pre-push)
  python scripts/run-tests.py --acceptance   # A+ sign-off gates
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.venv import ensure_venv  # noqa: E402

ensure_venv()  # pytest/pytest-cov live only in .venv; re-exec if launched elsewhere

from scripts.utils.colors import GREEN, RED, RESET


# Coverage floor. Phase 0 baseline was 27.03% (floor held at 25 as a churn buffer
# through Phases 0-3). Phase 4 ratchets it to 27 — one point below the achieved
# 27.57% after the Phase 3 regression tests landed, keeping a thin churn buffer.
# It only ever moves up; this is the real no-regression guarantee.
COVERAGE_FLOOR = 27


def build_command(acceptance: bool) -> list[str]:
    """Return the pytest argv for the requested mode.

    Regression mode (default) runs everything except acceptance gates AND enforces
    the coverage floor across the full suite. Acceptance mode runs only the A+
    sign-off gates with no floor. The floor lives here, not in pyproject addopts,
    so single-file `pytest tests/x.py` runs are never blocked by partial coverage.
    """
    base = [sys.executable, "-m", "pytest", "-q"]
    if acceptance:
        return base + ["-m", "acceptance"]
    return base + ["-m", "not acceptance", "--cov=scripts", f"--cov-fail-under={COVERAGE_FLOOR}"]


def main() -> int:
    ap = argparse.ArgumentParser(description="HEADING OS test gate.")
    ap.add_argument("--acceptance", action="store_true",
                    help="run only the A+ sign-off gates instead of the regression suite")
    args = ap.parse_args()
    root = Path(__file__).resolve().parent.parent
    cmd = build_command(args.acceptance)
    proc = subprocess.run(cmd, cwd=str(root))
    if proc.returncode == 0:
        print(f"{GREEN}test gate: PASS{RESET}")
    else:
        print(f"{RED}test gate: FAIL (pytest exit {proc.returncode}){RESET}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
