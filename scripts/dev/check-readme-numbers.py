#!/usr/bin/env python3
"""Keep the README / docs front-door "By the numbers" block honest (F-8.3).

The README and docs/index.html each carry a "By the numbers" block whose figures
must come from CI, not from a hand-typed guess. This guard re-derives the one
figure that actually drifts (the security-test count) and asserts the two front
doors agree with it and with each other; it also cross-checks the enforcement-layer
count between the two pages against the architectural constant.

Derived vs asserted:
  * security-test count -- DERIVED by collecting ``tests/security`` (the exact suite
    the CI ``security-tests`` job runs). This number grows as security tests land, so
    a stale README is caught here.
  * enforcement-layer count -- a fixed architectural constant (the six engine/data
    layers enumerated in docs/SECURITY-MODEL.md). Not re-derivable from a fluctuating
    source, so this guard instead asserts README and docs/index.html agree with each
    other and with the constant, catching an accidental divergence between the two
    front doors.

Exit 0 when every figure matches; exit 1 (with a diff) on any mismatch.

Usage:
    python scripts/dev/check-readme-numbers.py            # check, exit non-zero on mismatch
    python scripts/dev/check-readme-numbers.py --quiet    # only print on mismatch
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.colors import BOLD, GREEN, RED, RESET  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402

ROOT = get_workspace_root()

# The six engine/data enforcement layers enumerated in docs/SECURITY-MODEL.md and the
# README security bullet. A fixed architectural constant, not a fluctuating count.
EXPECTED_LAYERS = 6

# Front-door pages that must carry matching figures.
FRONT_DOORS = [ROOT / "README.md", ROOT / "docs" / "index.html"]

_SEC_RE = re.compile(r"(\d+)\s+security tests", re.IGNORECASE)
_LAYER_RE = re.compile(r"(\d+)\s+enforcement layers", re.IGNORECASE)
_COLLECTED_RE = re.compile(r"(\d+)\s+tests?\s+collected")


def derive_security_test_count() -> int:
    """Collect tests/security and return the number of collected test items."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/security",
         "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"{RED}pytest collection of tests/security failed (exit {proc.returncode}). "
            f"Cannot derive the security-test count.{RESET}\n"
            f"--- stderr tail ---\n{proc.stderr[-1500:]}"
        )
    matches = _COLLECTED_RE.findall(proc.stdout)
    if not matches:
        raise SystemExit(
            f"{RED}could not parse a 'N tests collected' line from pytest output.{RESET}\n"
            f"--- stdout tail ---\n{proc.stdout[-1500:]}"
        )
    return int(matches[-1])


def _extract(pattern: re.Pattern[str], text: str, path: Path, label: str) -> int:
    matches = pattern.findall(text)
    if not matches:
        raise SystemExit(f"{RED}{path.relative_to(ROOT)}: no '{label}' figure found.{RESET}")
    values = {int(m) for m in matches}
    if len(values) > 1:
        raise SystemExit(
            f"{RED}{path.relative_to(ROOT)}: inconsistent '{label}' figures {sorted(values)} "
            f"within the same page.{RESET}"
        )
    return values.pop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Check README/docs front-door numbers against CI facts")
    parser.add_argument("--quiet", action="store_true", help="Only print on mismatch")
    args = parser.parse_args()

    derived_sec = derive_security_test_count()

    problems: list[str] = []
    for path in FRONT_DOORS:
        text = path.read_text(encoding="utf-8")
        sec = _extract(_SEC_RE, text, path, "security tests")
        layers = _extract(_LAYER_RE, text, path, "enforcement layers")
        rel = path.relative_to(ROOT)
        if sec != derived_sec:
            problems.append(f"{rel}: says {sec} security tests, CI collects {derived_sec}")
        if layers != EXPECTED_LAYERS:
            problems.append(f"{rel}: says {layers} enforcement layers, expected {EXPECTED_LAYERS}")

    if problems:
        print(f"{RED}{BOLD}README numbers out of sync:{RESET}", file=sys.stderr)
        for p in problems:
            print(f"  {RED}- {p}{RESET}", file=sys.stderr)
        print(
            f"\nFix the figure(s) in {', '.join(str(p.relative_to(ROOT)) for p in FRONT_DOORS)} "
            f"to match, then re-run this guard.",
            file=sys.stderr,
        )
        return 1

    if not args.quiet:
        print(
            f"{GREEN}README numbers in sync: {derived_sec} security tests, "
            f"{EXPECTED_LAYERS} enforcement layers (README + docs/index.html).{RESET}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
