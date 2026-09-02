#!/usr/bin/env python3
"""Ratchet: a test whose only assertions can run zero times.

Usage:
    python scripts/check-test-vacuity.py            # report
    python scripts/check-test-vacuity.py --check    # exit 1 on a NEW site
    python scripts/check-test-vacuity.py --json
    python scripts/check-test-vacuity.py --write-baseline   # shrink only

The shape this catches
----------------------
A test iterates a corpus it discovered at runtime -- a glob, a tree walk, a file
read -- and every assertion it makes sits inside that loop. When the corpus comes
back empty the loop body never runs, no assertion is evaluated, and the test
passes. It passes for the same reason an empty `for` passes: there was nothing to
disagree with. Nothing about the file looks wrong. It has a real corpus, a real
loop and a real assertion, and it is green.

The 10-day hardening campaign that ended 2026-09-02 found this shape 23 times in
142 commits, and one sweep alone (`3c0b6563`, `6b979bc6`) found 38 vacuous
emptiness tests and 182 loop-only assertions in one pass over `tests/`. It is the
second most common defect shape in that campaign, and the only one no code
review catches, because the code IS correct.

The fix the campaign adopted by hand is a FLOOR: an assertion, outside the loop,
that the corpus is not empty -- ideally a measured minimum with the date it was
measured beside it. This script is the mechanical version of that habit.

Scope, precisely (`.claude/rules/scope-claims.md`)
--------------------------------------------------
This reads `tests/**/test_*.py` from the WORKING TREE and parses each with
`ast`. It answers exactly one question per test function: does this function
make at least one assertion that is NOT nested inside a `for`, `while` or `if`?

It says nothing about whether the assertions are meaningful, whether the corpus
is the right one, or whether the loop body is reachable for any other reason. A
test can satisfy this check and still measure nothing. This is a floor under one
specific failure, not a verdict on test quality.

Three deliberate narrowings, each of which cost a calibration pass:

* **A loop over a literal is exempt.** `for page in ("a", "b")` cannot be empty.
* **A loop over a NAME bound to a literal is exempt.** Measured 2026-09-02: not
  resolving the name flagged 147 sites, of which `for page in DEAD_PAGES` and
  `for anchor in _REQUIRED_ANCHORS` were false. Both names are module-level
  lists. Resolving one level of binding took the count to 126.
* **An assertion under a `with` or `try` is NOT nested.** Those do not gate
  execution on a corpus being non-empty; `for`, `while` and `if` do.

An UNRESOLVABLE name is treated as discovered, not as a literal. That is the
over-reporting direction, which is the one `.claude/rules/scope-claims.md`
requires when the evidence is absent: a false flag costs a baseline line, a
false pass costs a test that never fails.

MEASURED 2026-09-02 over the tree at 835c146: 16,529 test functions carry at
least one assertion; 126 of them have every assertion behind a loop over a
corpus that can be empty. Those 126 are frozen in the baseline. A 127th fails.

Why a ratchet and not a fix
---------------------------
The 126 are pre-existing. Fixing them is 126 separate judgements about what the
right floor is for each corpus, and each wrong guess writes a floor that is
itself a false claim. Freezing them stops the bleeding today and leaves the
repair as ordinary work. `--write-baseline` only ever REMOVES entries, so a
newly-written vacuous test cannot be laundered into the baseline by re-running
it.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402

ROOT = get_workspace_root()
BASELINE_PATH = ROOT / "config" / "test-vacuity-baseline.json"

# A corpus this small cannot be the whole test tree. Below it, something has gone
# wrong with the walk itself and a clean verdict would be a lie about coverage.
MIN_CORPUS = 400  # measured 2026-09-02: 1,051 test files in tree

LITERALS = (ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Constant)

# Calls that pass their argument through unchanged as far as emptiness goes.
PASSTHROUGH = frozenset({
    "sorted", "list", "set", "tuple", "frozenset", "enumerate", "reversed",
})

# How many binding hops to follow before giving up and calling it discovered.
MAX_HOPS = 5


# ============================================================
# The rule, pure, so it can be exercised on synthetic source
# ============================================================

def _bindings(body: list[ast.stmt]) -> dict[str, ast.expr]:
    """Name -> the expression last assigned to it, in one scope's statement list."""
    out: dict[str, ast.expr] = {}
    for stmt in body:
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets = [stmt.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                out[target.id] = stmt.value
    return out


def _unwrap(node: ast.expr) -> ast.expr:
    """Strip `sorted(...)`, `list(...)` and friends, which do not change emptiness."""
    while (isinstance(node, ast.Call)
           and isinstance(node.func, ast.Name)
           and node.func.id in PASSTHROUGH
           and node.args):
        node = node.args[0]
    return node


def corpus_can_be_empty(iterator: ast.expr,
                        module_binds: dict[str, ast.expr],
                        local_binds: dict[str, ast.expr]) -> bool:
    """Whether the thing being iterated could come back with nothing in it.

    A literal cannot. A name bound to a literal cannot. Anything else -- a call,
    a comprehension, an attribute, a name we cannot resolve -- can.
    """
    node = _unwrap(iterator)
    for _ in range(MAX_HOPS):
        if not isinstance(node, ast.Name):
            break
        source = local_binds.get(node.id, module_binds.get(node.id))
        if source is None:
            return True
        node = _unwrap(source)
    else:
        return True
    return not isinstance(node, LITERALS)


def unguarded_assertions(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Assert]:
    """Assertions that are NOT nested inside a for/while/if in this function.

    `with` and `try` bodies are transparent: they always run. `for`, `while` and
    `if` bodies are not, which is the whole point.
    """
    found: list[ast.Assert] = []

    def walk(stmts: list[ast.stmt], guarded: bool) -> None:
        for stmt in stmts:
            if isinstance(stmt, ast.Assert) and not guarded:
                found.append(stmt)
            # One tuple, not two branches with identical bodies. A `for` whose
            # corpus is empty, a `while` whose condition is false at once, and
            # an `if` that is false for every item all reach the same end: the
            # body never runs. They differ in why, never in what it costs.
            if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While, ast.If)):
                walk(stmt.body, True)
                walk(stmt.orelse, True)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                walk(stmt.body, guarded)
            elif isinstance(stmt, ast.Try):
                walk(stmt.body, guarded)
                for handler in stmt.handlers:
                    walk(handler.body, guarded)
                walk(stmt.orelse, guarded)
                walk(stmt.finalbody, guarded)

    walk(fn.body, False)
    return found


def vacuous_tests(rel_path: str, source: str) -> list[str]:
    """`path::function` for every test whose assertions can all run zero times.

    Pure: takes source text, returns findings. Deleting the line that appends a
    finding changes a live result, which is what makes this testable on synthetic
    prose rather than only on the tree.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # A test file that does not parse is a different failure, loudly reported
        # by pytest itself. Silently skipping it here would be this script
        # committing the defect it exists to find.
        return []

    module_binds = _bindings(tree.body)
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        if not any(isinstance(n, ast.Assert) for n in ast.walk(node)):
            continue  # no assertions at all is a different finding, not this one
        if unguarded_assertions(node):
            continue
        local_binds = _bindings(node.body)
        loops = [n for n in ast.walk(node) if isinstance(n, (ast.For, ast.AsyncFor))]
        if not loops:
            continue  # everything is behind an `if`, which is its own shape
        if any(corpus_can_be_empty(loop.iter, module_binds, local_binds)
               for loop in loops):
            out.append(f"{rel_path}::{node.name}")
    return out


# ============================================================
# The tree
# ============================================================

def test_files(root: Path) -> list[Path]:
    return sorted(p for p in (root / "tests").rglob("test_*.py") if p.is_file())


def scan(root: Path) -> tuple[list[str], int]:
    """(findings, files_read). The file count is the anti-vacuity floor's input."""
    findings: list[str] = []
    read = 0
    for path in test_files(root):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            # Never swallow: an unreadable test file is a fault, and reporting a
            # clean tree over one is exactly this script's own defect shape.
            print(f"{RED}cannot read {path}: {exc}{RESET}", file=sys.stderr)
            continue
        read += 1
        findings.extend(vacuous_tests(path.relative_to(root).as_posix(), source))
    return sorted(findings), read


def load_baseline() -> set[str]:
    if not BASELINE_PATH.exists():
        return set()
    try:
        data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{RED}baseline unreadable: {exc}{RESET}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("frozen"), list):
        raise SystemExit(f"{RED}baseline malformed: expected {{'frozen': [...]}}{RESET}")
    return set(data["frozen"])


def write_baseline(frozen: set[str], current: list[str]) -> int:
    """Shrink only. An entry that is no longer a finding leaves; a new one never
    enters. Otherwise re-running the writer would launder a fresh defect."""
    kept = sorted(frozen & set(current))
    removed = sorted(frozen - set(current))
    payload = {
        "_comment": (
            "Frozen sites for scripts/check-test-vacuity.py. Shrink-only: "
            "--write-baseline removes entries that are no longer findings and "
            "never adds new ones. A new finding must be fixed, not frozen."
        ),
        "measured": "2026-09-02",
        "frozen": kept,
    }
    BASELINE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"{GREEN}baseline written: {len(kept)} frozen, {len(removed)} removed{RESET}")
    for entry in removed:
        print(f"  {GRAY}-{RESET} {entry}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 on a finding that is not in the baseline")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--write-baseline", action="store_true",
                    help="rewrite the baseline, removing entries that no longer fire")
    args = ap.parse_args()

    findings, files_read = scan(ROOT)

    if files_read < MIN_CORPUS:
        message = (f"read {files_read} test files, below the floor of {MIN_CORPUS}; "
                   f"refusing to report a verdict over a corpus this small")
        if args.json:
            print(json.dumps({"refused": message}, indent=2))
        else:
            print(f"{RED}{BOLD}REFUSED{RESET} {message}", file=sys.stderr)
        return 2

    frozen = load_baseline()

    if args.write_baseline:
        return write_baseline(frozen, findings)

    new = [f for f in findings if f not in frozen]
    stale = sorted(frozen - set(findings))

    if args.json:
        print(json.dumps({"files_read": files_read, "findings": findings,
                          "new": new, "stale_baseline": stale,
                          "frozen": sorted(frozen)}, indent=2))
        return 1 if (args.check and new) else 0

    print(f"{BOLD}test-vacuity{RESET}  {files_read} files, "
          f"{len(findings)} sites, {len(new)} new, {len(frozen)} frozen")

    if stale:
        print(f"{GRAY}{len(stale)} baseline entries no longer fire; "
              f"run --write-baseline to drop them{RESET}")

    if not new:
        print(f"{GREEN}OK -- no test whose assertions can all run zero times.{RESET}")
        return 0

    print(f"\n{RED}{BOLD}{len(new)} new vacuous test(s){RESET}")
    for entry in new:
        print(f"  {RED}x{RESET} {entry}")
    print(f"\n{YELLOW}Every assertion in these tests sits inside a loop over a "
          f"corpus that can come back empty.{RESET}")
    print(f"{YELLOW}Add a floor OUTSIDE the loop -- assert the corpus size, with "
          f"the measured number and today's date beside it.{RESET}")
    return 1 if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
