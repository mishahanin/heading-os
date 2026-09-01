#!/usr/bin/env python3
"""Every file that can write the operator's private overlay, and whether git knows it.

Usage:
    python scripts/overlay-writer-census.py
    python scripts/overlay-writer-census.py --json
    python scripts/overlay-writer-census.py --untracked-only

Why this exists. `scripts/utils/overlay_write_guard.py` can now arm in any
process, and the question that decides what it should REFUSE is: what
distinguishes a legitimate write to the operator's data from a destructive one?

The candidate answer is "the code making the write lives in a git-tracked
workspace file". Every legitimate writer observed so far is tracked; the write
that destroyed a real operator workbook on 2026-08-31 came from
`.tmp/frozen/behaviour.py`, which is not and cannot be, because `.tmp/` is
ignored.

Running tools one at a time samples that answer. This asks it exhaustively:
static, over every Python file under the swept roots, so the report covers files
no dynamic run happened to reach. A candidate is a file that BOTH reaches a
data-root resolver (transitively, per `scripts/utils/resolver_closure.py`) and
calls a write primitive.

What this does NOT establish, stated plainly because the rule it feeds is a
refusal:

* Reaching a resolver and calling a write primitive does not prove the file ever
  writes the overlay. The two may be in unrelated code paths. This over-reports
  by construction, which is the safe direction for a census.
* It cannot see a write made by a subprocess, by a compiled extension, or through
  a path built by string concatenation rather than a resolver call.
* "Tracked" is asked of git for the file's own path. A tracked file that `exec`s
  untracked code would pass, and the guard's caller frame would name the tracked
  file. That is a real hole in the candidate rule, and it is reported here rather
  than discovered later.
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.resolver_closure import (  # noqa: E402
    ENGINE_ROOT,
    derived_resolvers,
    module_reaches_resolver,
    module_reaches_write,
)

def swept_roots() -> tuple[Path, ...]:
    """Where to look for candidate writers, in BOTH repos.

    The DATA overlay's `admin/` carries the operator's provisioning tooling and
    `outputs/documents/` carries per-letter renderers; both write the overlay and
    both are committed, in that repo. A sweep of the engine alone cannot see them
    and therefore cannot answer whether a tracked-file rule is safe.
    """
    roots = [
        ENGINE_ROOT / "scripts",
        ENGINE_ROOT / ".claude" / "hooks",
        ENGINE_ROOT / ".claude" / "skills",
        ENGINE_ROOT / "tests",
    ]
    overlay = ENGINE_ROOT.parent / ".heading-os-data"
    if overlay.is_dir():
        roots.append(overlay)
    return tuple(roots)

# A walk that matches nothing passes everything. Set below the real count so
# ordinary churn does not trip it, and far above zero so a broken glob does.
MIN_FILES_SWEPT = 300


def _overlay_root() -> Path | None:
    """The private DATA overlay beside the engine, if it is there.

    Derived structurally, like the guard's own resolver, so no environment
    variable can move this census onto a different tree than the guard watches.
    """
    sibling = ENGINE_ROOT.parent / ".heading-os-data"
    return sibling if sibling.is_dir() else None


def _repos() -> list[Path]:
    repos = [ENGINE_ROOT]
    overlay = _overlay_root()
    if overlay is not None and (overlay / ".git").exists():
        repos.append(overlay)
    return repos


def tracked_paths() -> frozenset[str]:
    """Every path git tracks in BOTH repos, as absolute strings.

    BOTH, because the DATA overlay is a separate repository and it tracks
    Python that legitimately writes the overlay. A census that asked only the
    engine reported the operator's own committed provisioning tools as untracked
    on 2026-08-31, which would have argued for a rule that blocks them.

    One `git ls-files` per repo rather than one per candidate: the per-file form
    was measured at roughly 400 subprocesses for the engine tree alone.
    """
    out: set[str] = set()
    for repo in _repos():
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            capture_output=True, cwd=str(repo), check=True,
        )
        out |= {
            str(repo / part.decode("utf-8", "surrogateescape"))
            for part in result.stdout.split(b"\0") if part
        }
    return frozenset(out)


def _display(path: Path) -> str:
    """Repo-relative where possible, so output stays readable across two repos."""
    for repo, label in ((ENGINE_ROOT, "engine"), (_overlay_root(), "DATA")):
        if repo is None:
            continue
        try:
            return f"{label}:{path.relative_to(repo).as_posix()}"
        except ValueError:
            continue
    return str(path)


def sweep():
    """(candidates, swept_count). Each candidate is a dict, ready for JSON."""
    resolvers = derived_resolvers()
    tracked = tracked_paths()
    candidates, swept = [], 0

    for root in swept_roots():
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if ".venv" in path.parts or "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError):
                continue
            swept += 1
            if not module_reaches_resolver(tree, resolvers):
                continue
            writes = module_reaches_write(tree)
            if not writes:
                continue
            # Absolute on both sides: two repos means a repo-relative path is
            # ambiguous, and `scripts/x.py` exists in the engine while
            # `outputs/.../scripts/x.py` exists in the overlay.
            candidates.append({
                "path": _display(path),
                "tracked": str(path) in tracked,
                "writes": sorted(writes),
            })
    return candidates, swept


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--untracked-only", action="store_true",
                        help="print only the candidates git does not track")
    args = parser.parse_args()

    candidates, swept = sweep()
    untracked = [c for c in candidates if not c["tracked"]]

    if args.json:
        print(json.dumps({
            "swept": swept,
            "candidates": len(candidates),
            "untracked": untracked,
            "resolvers": len(derived_resolvers()),
        }, indent=2))
        return 0

    if swept < MIN_FILES_SWEPT:
        print(f"{RED}the sweep read only {swept} files (floor {MIN_FILES_SWEPT}); "
              f"the glob is broken and this report means nothing{RESET}")
        return 1

    print(f"{BOLD}Overlay writer census{RESET}")
    print(f"{GRAY}{swept} python files swept under "
          f"{', '.join(_display(r) for r in swept_roots() if r.is_dir())}{RESET}")
    print(f"{GRAY}{len(_repos())} git repo(s) asked what they track{RESET}")
    print(f"{GRAY}{len(derived_resolvers())} data-root resolvers derived from "
          f"scripts/utils/paths.py + workspace.py{RESET}")
    print()
    print(f"  candidates (reach a resolver AND call a write primitive): "
          f"{BOLD}{len(candidates)}{RESET}")
    print(f"  of those, git-tracked   : {GREEN}{len(candidates) - len(untracked)}{RESET}")
    print(f"  of those, NOT tracked   : "
          f"{(RED if untracked else GREEN)}{len(untracked)}{RESET}")
    print()

    if untracked:
        print(f"{YELLOW}These would be REFUSED by a tracked-file rule:{RESET}")
        for c in untracked:
            print(f"  {c['path']}  {GRAY}({', '.join(c['writes'][:5])}){RESET}")
        print()
        print(f"{GRAY}Each one needs a judgement before the rule is enforced: is it "
              f"a probe (refuse it) or legitimate operator tooling that simply is "
              f"not committed (then the rule is wrong)?{RESET}")
    else:
        print(f"{GREEN}Every candidate writer is git-tracked.{RESET}")
        print(f"{GRAY}A tracked-file rule would refuse none of them, and would "
              f"refuse anything run from .tmp/, /tmp or an uncommitted scratch "
              f"file.{RESET}")

    if args.untracked_only:
        return 1 if untracked else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
