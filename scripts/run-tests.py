#!/usr/bin/env python3
"""The single test-gate entry point. Both push-all.py and .githooks/pre-push call this.

Default mode runs the regression suite (everything EXCEPT the acceptance gates).
--acceptance runs only the A+ sign-off gates (the findings-registry zero-open
check, etc.).

NEITHER mode measures coverage. The docstring said "with the coverage floor from
pyproject" until 2026-08-30, and both halves of that were false: `pyproject.toml`
carries no coverage addopts, and `build_command` attaches no `--cov` argument in
either mode. The floor lives on the unit-tests step of .github/workflows/ci.yml
(see COVERAGE_FLOOR below), so a reader who trusted this line believed pushing
through this gate enforced the ratchet, and it never did.

Usage:
  python scripts/run-tests.py            # regression gate (pre-push)
  python scripts/run-tests.py --acceptance   # A+ sign-off gates
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.venv_guard import ensure_venv  # noqa: E402

ensure_venv()  # pytest/pytest-cov live only in .venv; re-exec if launched elsewhere

from scripts.utils.canopus_contract import pytest_child_env  # noqa: E402
from scripts.utils.colors import GREEN, RED, RESET


# Coverage floor. Phase 0 baseline was 27.03% (floor held at 25 as a churn buffer
# through Phases 0-3). Phase 4 ratchets it to 27 — one point below the achieved
# 27.57% after the Phase 3 regression tests landed, keeping a thin churn buffer.
# It only ever moves up; this is the real no-regression guarantee.
#
# ENFORCED IN CI, NOT HERE (2026-08-20). The floor used to ride on this pre-push
# gate, where it could not do its job and cost real time: measured on the
# operator's box, the regression run took 124.7s mean WITH coverage and 86.8s
# WITHOUT — 37.9s on every push — while printing "Total coverage: 43.44%" against
# a floor of 27, so the gate could never fail on it. The floor now lives on the
# unit-tests step of .github/workflows/ci.yml, which runs on every push and PR
# and is the surface a regression would actually have to get past. This constant
# stays the documented home of the ratchet: raise it here, and raise the
# --cov-fail-under in ci.yml in the same change.
#
# 27 -> 35 in the same move, so the documented ratchet is not stale on the day it
# lands: ci.yml enforces 35, and a constant that says 27 while the gate says 35
# reads as a floor nobody kept. 35 is itself deliberately under the measured 43%
# (re-measured 2026-08-20 on the exact CI selection: 53052 statements, 30088
# missed, TOTAL 43%) because the runner has no private data overlay, no marp-cli
# and no LFS fixtures, so it covers strictly less than this box does.
#
# NOTHING IN THIS FILE READS THIS CONSTANT ANY MORE. It is documentation until a
# test ties it to the ci.yml value; see the finding filed with this change.
COVERAGE_FLOOR = 35


def build_command(acceptance: bool) -> list[str]:
    """Return the pytest argv for the requested mode.

    Regression mode (default) runs everything except acceptance gates. Acceptance
    mode runs only the A+ sign-off gates. Neither measures coverage: the floor is
    enforced on the CI unit-tests step (see COVERAGE_FLOOR above), which is where
    a coverage regression can actually be stopped. Keeping it out of pyproject
    addopts also means single-file `pytest tests/x.py` runs are never blocked by
    partial coverage.
    """
    # -n auto: distribute the regression suite across all CPU cores (pytest-xdist).
    # The serial gate was the slow part of every engine push (~4 min); parallel
    # collapses it to ~1 min on a multi-core box. Acceptance gates stay serial --
    # they are few and some assert ordered, single-writer behaviour.
    base = [sys.executable, "-m", "pytest", "-q"]
    if acceptance:
        return base + ["-m", "acceptance"]
    return base + ["-n", "auto", "-m", "not acceptance"]


def child_env() -> dict[str, str]:
    """The environment the pytest child gets: ours, minus everything PYTEST_.

    The scrub itself lives in canopus_contract.pytest_child_env, with the
    measurement behind it, because the contract child has to get the identical
    treatment: the two runs are read against each other, so a discipline applied
    to one of them alone makes the reading a photograph of the shell the operator
    happened to be standing in.

    CANOPUS_LAUNCHER is stamped so a reader of a child's environment can tell
    which launcher produced it. It is provenance and not a verdict.
    """
    return pytest_child_env(CANOPUS_LAUNCHER="run-tests")


def main() -> int:
    ap = argparse.ArgumentParser(description="HEADING OS test gate.")
    ap.add_argument("--acceptance", action="store_true",
                    help="run only the A+ sign-off gates instead of the regression suite")
    args = ap.parse_args()
    root = Path(__file__).resolve().parent.parent
    cmd = build_command(args.acceptance)
    proc = subprocess.run(cmd, cwd=str(root), env=child_env())
    if proc.returncode == 0:
        print(f"{GREEN}test gate: PASS{RESET}")
    else:
        print(f"{RED}test gate: FAIL (pytest exit {proc.returncode}){RESET}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
