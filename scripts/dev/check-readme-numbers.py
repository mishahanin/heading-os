#!/usr/bin/env python3
"""Keep the README / docs front-door "By the numbers" block honest (F-8.3).

README.md, docs/index.html, ROADMAP.md and SECURITY.md each carry a "By the
numbers" block whose figures must come from CI, not from a hand-typed guess.
This guard re-derives the one figure that actually drifts (the security-test
count) and asserts all four front doors agree with it and with each other; it
also cross-checks the enforcement-layer count across them against the
architectural constant.

These two paragraphs said "two front doors" and named only README and
docs/index.html. ROADMAP.md was added to `FRONT_DOORS` after it drifted to 554
against a real 563, and the guard has failed the run on it ever since. A reader
of the old text would conclude ROADMAP was unchecked and "fix" a failing guard
by reverting ROADMAP's number instead of believing the failure.

SECURITY.md joined `FRONT_DOORS` on 2026-09-01 and this text was not updated
with it, which turned `tests/test_a_guard_that_was_green_over_an_absent_tree.py::
test_the_docstring_names_every_front_door` red. That test exists precisely
because the paragraph above describes a reader being misled by a stale list, so
the same omission recurring one door later is the argument for the test rather
than an inconvenience from it. Name the file here in the SAME change that adds
it to `FRONT_DOORS`.

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

# The engine/data enforcement layers, COUNTED from the numbered table in
# docs/engine-data-segregation-contract.md, which is the only page that
# enumerates them rather than quoting a numeral.
#
# This was `EXPECTED_LAYERS = 6`, described in the docstring below as "a fixed
# architectural constant, not a fluctuating count". It fluctuated: the content
# guard became layer 7 on 2026-08-31, the contract and SECURITY.md were updated,
# and this guard went on asserting 6 against the two front doors that still said
# 6 -- so the guard reported "in sync" across a set that disagreed with the
# authority. A count a human must remember to update in two places is the defect.
_LAYER_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|", re.MULTILINE)


def derive_layer_count() -> int:
    """Count the numbered rows of the contract's enforcement-layer table.

    Refuses rather than guesses: the rows must be numbered 1..N with no gap, and
    there must be at least the six that existed in 2026-06. A table that stops
    parsing (a reformat, a blank line orphaning a row) collapses the count, and a
    silent low number here would quietly demand that every front door shrink.
    """
    contract = ROOT / "docs" / "engine-data-segregation-contract.md"
    text = contract.read_text(encoding="utf-8")
    heading = text.find("## The ")
    body = text[heading:] if heading != -1 else text
    numbers = [int(m) for m in _LAYER_ROW_RE.findall(body)]
    if numbers != list(range(1, len(numbers) + 1)):
        raise SystemExit(
            f"{contract.relative_to(ROOT)}: enforcement-layer rows are not numbered "
            f"1..N with no gap: {numbers}. Fix the table; this guard reads it as the "
            f"authority for every other page's numeral."
        )
    if len(numbers) < 6:
        raise SystemExit(
            f"{contract.relative_to(ROOT)}: parsed only {len(numbers)} enforcement-layer "
            f"rows; the contract has held at least 6 since 2026-06. The table almost "
            f"certainly stopped parsing rather than shrinking."
        )
    return len(numbers)


EXPECTED_LAYERS = derive_layer_count()

# Front-door pages that must carry matching figures.
#
# ROADMAP.md joined the list on 2026-08-23. It was the one page quoting the
# security-test count that nothing checked, and it had drifted: 554 against a
# real 563, while the README beside it was right. A number that only one guarded
# page carries is a number that will disagree with its unguarded twin.
# SECURITY.md joined on 2026-09-01, for the layer count what ROADMAP was for the
# test count: the one top-level page carrying an enforcement-layer numeral that
# nothing checked. The contract gained layer 7 on 2026-08-31 and three pages had
# to be found by hand afterwards; this one was not among them because no guard
# named it. It agrees today, which is the state a drift starts from.
FRONT_DOORS = [ROOT / "README.md", ROOT / "docs" / "index.html", ROOT / "ROADMAP.md",
               ROOT / "SECURITY.md"]

_SEC_RE = re.compile(r"(\d+)\s+security tests", re.IGNORECASE)
# Digits OR the spelled word. README.md:62 wrote "six enforcement layers" in
# prose while README.md:73 wrote "6" in a bullet, and a digits-only pattern read
# only the bullet -- so the same page could carry two different counts and pass.
_LAYER_WORDS = {
    "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
# The NOUN varies too. Measured 2026-08-31: with the pattern pinned to
# "enforcement layers", three pages carried a stale six in wordings it could not
# see -- "six mechanical layers" on README.md and docs/index.html, and "the six
# layers" in the contract's own change-control clause. All three sat beside a
# corrected seven and the guard reported the set in sync. An adjective is the
# cheapest thing for a writer to vary, so it must be the cheapest thing for the
# pattern to allow.
_LAYER_RE = re.compile(
    r"(\d+|" + "|".join(_LAYER_WORDS) + r")\s+(?:\w+\s+)?layers", re.IGNORECASE
)
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
        # BOTH streams. pytest writes its own diagnosis to stdout and leaves
        # stderr empty, so a message carrying stderr alone reports a failure it
        # cannot explain. MEASURED 2026-09-02: this guard failed with exit 1 and
        # printed "--- stderr tail ---" followed by nothing, and the sentence
        # naming the cause was sitting unread in stdout.
        raise SystemExit(
            f"{RED}pytest collection of tests/security failed (exit {proc.returncode}). "
            f"Cannot derive the security-test count.{RESET}\n"
            f"--- stdout tail ---\n{proc.stdout[-1500:]}\n"
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

    Decoded from bytes, not through subprocess text mode, which turns on
    universal newlines and rewrites every CR byte to LF (no `newline=` knob
    exists on `subprocess`). MEASURED 2026-08-30: two tracked files differing
    only by that byte come back as one name. Here that name reaches
    `count_trigger_cases`, whose `read_text` then raises FileNotFoundError and
    takes the whole guard down over a filename that is not the one on disk.

    That decode fix removed one CAUSE of an unopenable name; it left the general
    case, which is a path that vanishes between this listing and the read. As of
    2026-09-01 `count_trigger_cases` handles it: it re-reads once and then exits
    with a message naming the file, instead of a bare FileNotFoundError
    traceback. It still refuses rather than skipping, because the figure it
    produces is a COUNT -- see that function's docstring for why.
    """
    repo = ROOT if root is None else root
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", ".claude/skills/*/triggers.json"],
        capture_output=True, check=True,
    ).stdout.decode("utf-8", "surrogateescape")
    return sorted(repo / rel for rel in out.split("\0") if rel)


def count_trigger_cases(paths) -> int:
    """Total cases across ``paths``, reading each file the way the harness does.

    `scripts/utils/router_payload.load_triggers` returns the parsed document
    itself and requires a JSON array, so the case count of one skill is the
    length of that array. Anything else raises here rather than counting zero:
    a malformed file that silently contributed nothing would move the documented
    figure for a reason no reader could see.

    A path that VANISHED between `tracked_trigger_files` and this read gets the
    same refusal, for the same reason, and deliberately NOT the skip-and-warn
    that `scripts/utils/repo_files.read_sources` gives a per-file scanner. What
    this function returns is a COUNT that the guard then asserts against a
    sentence in `docs/EXTENDING.md`. Dropping one file lowers the count by that
    file's cases, and the guard would then either fail while naming the wrong
    problem ("says 730, the tree holds 718") or, if the page happened to be
    stale by the same amount, agree with a number nobody can reproduce. A
    warning beside a wrong number is still a wrong number.

    The read is retried once. That recovers a writer's unlink-and-rewrite
    window and nothing else; a file that is genuinely gone is still gone on the
    second look, and that case exits naming the file rather than raising a bare
    FileNotFoundError traceback out of the guard.
    """
    total = 0
    for path in paths:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            try:
                raw = path.read_text(encoding="utf-8")
            except FileNotFoundError as e:
                raise SystemExit(
                    f"{RED}{path}: listed by `git ls-files` but not readable "
                    f"({e.strerror}). The trigger-corpus count cannot be "
                    f"derived over a corpus that changed underneath it; "
                    f"re-run once the tree is quiet.{RESET}"
                ) from e
        data = json.loads(raw)
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
    values = {_LAYER_WORDS.get(m.lower()) or int(m) for m in matches}
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
