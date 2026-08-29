#!/usr/bin/env python3
"""Keep the README / docs front-door "By the numbers" block honest (F-8.3).

README.md, docs/index.html and ROADMAP.md each carry a "By the numbers" block
whose figures must come from CI, not from a hand-typed guess. This guard
re-derives the one figure that actually drifts (the security-test count) and
asserts all three front doors agree with it and with each other; it also
cross-checks the enforcement-layer count across them against the architectural
constant.

These two paragraphs said "two front doors" and named only README and
docs/index.html. ROADMAP.md was added to `FRONT_DOORS` after it drifted to 554
against a real 563, and the guard has failed the run on it ever since. A reader
of the old text would conclude ROADMAP was unchecked and "fix" a failing guard
by reverting ROADMAP's number instead of believing the failure.

Derived vs asserted:
  * security-test count -- DERIVED by collecting ``tests/security`` (the exact suite
    the CI ``security-tests`` job runs). This number grows as security tests land, so
    a stale README is caught here.
  * enforcement-layer count -- a fixed architectural constant (the six engine/data
    layers enumerated in docs/SECURITY-MODEL.md). Not re-derivable from a fluctuating
    source, so this guard instead asserts every page in `FRONT_DOORS` agrees with
    the constant and with the others, catching an accidental divergence between
    the front doors.
  * trigger-corpus figures -- DERIVED by counting the tracked
    ``.claude/skills/*/triggers.json`` files and the cases inside them, with the
    same "the file IS the array" reading `scripts/utils/router_payload.load_triggers`
    uses. docs/EXTENDING.md quotes both, and nothing checked them: MEASURED
    2026-08-29 the page said 69 files / 710 cases against a real 70 / 730, and it
    dated the figures to 2026-08-03 while asserting them in the present tense. This
    guard grew past the README front doors on that date, because a number a human
    must remember to update is the defect, and re-typing today's number only
    resets the clock.

Exit 0 when every figure matches; exit 1 (with a diff) on any mismatch.

Usage:
    python scripts/dev/check-readme-numbers.py            # check, exit non-zero on mismatch
    python scripts/dev/check-readme-numbers.py --quiet    # only print on mismatch

Tests: tests/test_a_guard_that_was_green_over_an_absent_tree.py,
       tests/test_a_trigger_count_nothing_recounted.py
"""
from __future__ import annotations

import argparse
import json
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
#
# ROADMAP.md joined the list on 2026-08-23. It was the one page quoting the
# security-test count that nothing checked, and it had drifted: 554 against a
# real 563, while the README beside it was right. A number that only one guarded
# page carries is a number that will disagree with its unguarded twin.
FRONT_DOORS = [ROOT / "README.md", ROOT / "docs" / "index.html", ROOT / "ROADMAP.md"]

_SEC_RE = re.compile(r"(\d+)\s+security tests", re.IGNORECASE)
_LAYER_RE = re.compile(r"(\d+)\s+enforcement layers", re.IGNORECASE)
_COLLECTED_RE = re.compile(r"(\d+)\s+tests?\s+collected")

# The two sentences on docs/EXTENDING.md that carry the trigger-corpus figures.
# Each pattern is anchored on the surrounding words, not on a bare number, so a
# reworded paragraph fails loudly here instead of silently going unchecked.
_TRIGGER_FILES_RE = re.compile(r"(\d+)\s+routing-sensitive skills carry")
_TRIGGER_CASES_RE = re.compile(r"they hold (\d+) cases")


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


def tracked_trigger_files(root: Path | None = None) -> list[Path]:
    """Every tracked `.claude/skills/*/triggers.json`, as absolute paths.

    `git ls-files` rather than a glob: an untracked or ignored scratch file is
    not part of the engine anyone clones, and counting it would put a figure in
    the page that no other machine can reproduce. `-z` because git C-quotes a
    non-ASCII path, and a quoted name is not a file this guard can open.
    """
    repo = ROOT if root is None else root
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", ".claude/skills/*/triggers.json"],
        capture_output=True, text=True, check=True,
    ).stdout
    return sorted(repo / rel for rel in out.split("\0") if rel)


def count_trigger_cases(paths) -> int:
    """Total cases across ``paths``, reading each file the way the harness does.

    `scripts/utils/router_payload.load_triggers` returns the parsed document
    itself and requires a JSON array, so the case count of one skill is the
    length of that array. Anything else raises here rather than counting zero:
    a malformed file that silently contributed nothing would move the documented
    figure for a reason no reader could see.
    """
    total = 0
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SystemExit(
                f"{RED}{path}: triggers.json must be a JSON array of cases.{RESET}"
            )
        total += len(data)
    return total


def trigger_figure_problems(text: str, page_label: str, files: int, cases: int) -> list[str]:
    """Every disagreement between ``text`` and the derived (files, cases) pair.

    Pure, so it can be measured on synthetic input in both directions. A missing
    sentence is a problem, not a pass: this page is the only one quoting these
    figures, so an unanchored guard here checks nothing at all.
    """
    problems: list[str] = []
    for pattern, derived, label in (
        (_TRIGGER_FILES_RE, files, "skills carrying triggers.json"),
        (_TRIGGER_CASES_RE, cases, "trigger cases"),
    ):
        found = {int(m) for m in pattern.findall(text)}
        if not found:
            problems.append(
                f"{page_label}: no '{label}' figure matched {pattern.pattern!r}; "
                f"the sentence was reworded and this guard now checks nothing"
            )
            continue
        if len(found) > 1:
            problems.append(
                f"{page_label}: inconsistent '{label}' figures {sorted(found)} "
                f"within the same page"
            )
            continue
        stated = found.pop()
        if stated != derived:
            problems.append(f"{page_label}: says {stated} {label}, the tree holds {derived}")
    return problems


def check_trigger_figures(root: Path | None = None) -> tuple[list[str], int, int]:
    """Derive the two trigger figures and hold `docs/EXTENDING.md` against them.

    One seam for the whole check, in the same shape as `derive_security_test_count`,
    so a caller driving `main()` over a synthetic tree can substitute it. The page
    is resolved from ``root`` rather than from an import-time constant, for the
    same reason.
    """
    repo = ROOT if root is None else root
    page = repo / "docs" / "EXTENDING.md"
    files = tracked_trigger_files(repo)
    cases = count_trigger_cases(files)
    problems = trigger_figure_problems(
        page.read_text(encoding="utf-8"), str(page.relative_to(repo)), len(files), cases
    )
    return problems, len(files), cases


def _extract(pattern: re.Pattern[str], text: str, path: Path, label: str) -> int | None:
    """The figure this page carries, or None if it does not carry one.

    A missing figure is not a defect. ROADMAP.md quotes the security-test count
    and never mentions enforcement layers, and demanding both would make the
    page serve the guard rather than the reader. What IS a defect is a page
    listed here that carries neither figure, and `main` refuses that separately:
    a watched page checked for nothing reads as covered while covering nothing.
    """
    matches = pattern.findall(text)
    if not matches:
        return None
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
        if sec is None and layers is None:
            problems.append(
                f"{rel}: carries neither figure, so listing it here checks nothing. "
                f"Add a figure or drop the page from FRONT_DOORS."
            )
        if sec is not None and sec != derived_sec:
            problems.append(f"{rel}: says {sec} security tests, CI collects {derived_sec}")
        if layers is not None and layers != EXPECTED_LAYERS:
            problems.append(f"{rel}: says {layers} enforcement layers, expected {EXPECTED_LAYERS}")

    trigger_problems, derived_files, derived_cases = check_trigger_figures()
    problems += trigger_problems

    watched = [*FRONT_DOORS, ROOT / "docs" / "EXTENDING.md"]
    if problems:
        print(f"{RED}{BOLD}Documented numbers out of sync:{RESET}", file=sys.stderr)
        for p in problems:
            print(f"  {RED}- {p}{RESET}", file=sys.stderr)
        print(
            f"\nFix the figure(s) in {', '.join(str(p.relative_to(ROOT)) for p in watched)} "
            f"to match, then re-run this guard.",
            file=sys.stderr,
        )
        return 1

    if not args.quiet:
        print(
            f"{GREEN}Documented numbers in sync: {derived_sec} security tests, "
            f"{EXPECTED_LAYERS} enforcement layers, "
            f"{derived_files} skills carrying triggers.json holding "
            f"{derived_cases} cases, across "
            f"{', '.join(str(p.relative_to(ROOT)) for p in watched)}.{RESET}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
