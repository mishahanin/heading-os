#!/usr/bin/env python3
"""Version-sync guard (F-1.1).

`pyproject.toml` is the single source of truth for the engine version. This
check asserts four surfaces agree with it — three human-facing, one generated:

  * README.md § Status — the `vX.Y.Z` token under the "## Status" heading.
  * CHANGELOG.md      — the newest real release heading `## [X.Y.Z]`
                        (the `## [Unreleased]` section is ignored).
  * ROADMAP.md        — the `vX.Y.Z` token in the "HEADING OS is" preamble.
  * uv.lock           — the `version` of the `[[package]]` entry whose `name`
                        matches `project.name`. Generated, never hand-edited,
                        which is why it drifts unseen.

Exit 0 when all agree; exit 1 with a diff-style message otherwise. Wired into
`.pre-commit-config.yaml` (files: README.md|CHANGELOG.md|ROADMAP.md|
pyproject.toml|uv.lock) and the CI `guards` job, so version drift across the
five files can never re-appear.

The lock surface was added 2026-09-03. MEASURED 2026-09-03 on `main`: `uv.lock`
held `version = "0.13.0"` for `heading-os-engine` while `pyproject.toml`
declared `0.14.0`, and this script exited 0 printing "Version in sync: 0.14.0". The drift had survived the v0.14.0 release and nothing in the
tree noticed, because this guard read three markdown files and the lock was not
one of them. It is not a cosmetic mismatch: step 4 of the YARD bootstrap runs
`uv sync`, which silently rewrites the stale version, so every new worktree
opened with a dirty tree and its operator had to work out whether the edit was
theirs.

Both numbers in the first paragraph used to read "three human-facing surfaces"
and "the four files"; adding a fourth surface made each of them wrong, so they
are restated here rather than left to rot.

Usage:
    python scripts/check-version-sync.py [--quiet]

Tests: tests/test_a_heading_match_that_was_never_anchored.py
       tests/test_a_version_gate_that_never_read_the_lockfile.py
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402

_SEMVER = r"\d+\.\d+\.\d+"


def _pyproject_version(root: Path) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def _pyproject_name(root: Path) -> str:
    """The distribution name, read rather than assumed.

    `uv.lock` lists every dependency, so finding "the" version in it means
    finding OUR entry among them. Hardcoding `heading-os-engine` here would
    make the guard silently stop checking anything the day the project is
    renamed: no entry would match, and a `None` would be read as drift against
    a file nobody had touched. The name comes from the same source of truth as
    the version.
    """
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["name"]


def _readme_status_version(root: Path) -> str | None:
    text = (root / "README.md").read_text(encoding="utf-8")
    # Anchored to line start, like `_changelog_latest_version` below. Without
    # `^` and MULTILINE, `re.search` matched the substring "## Status" INSIDE a
    # deeper heading - `### Status` contains it from its second `#` - and
    # inside prose that merely names the section. The first such match won, so
    # the guard could compare pyproject against a subsection's stale token and
    # report drift on a correct README, or pass while the real `## Status`
    # heading had drifted. The docstring always scoped this to the `## Status`
    # HEADING; the regex did not.
    # The terminator needs no `^`: it already consumes the newline, and a
    # position just past `\n` IS a line start under MULTILINE, so the anchor
    # there could never change a match. Only the LEADING one does work.
    m = re.search(r"^##\s+Status\b(.*?)(?:\n##\s|\Z)", text,
                  re.DOTALL | re.MULTILINE)
    if not m:
        return None
    v = re.search(rf"`v({_SEMVER})`", m.group(1))
    return v.group(1) if v else None


def _changelog_latest_version(root: Path) -> str | None:
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    for m in re.finditer(rf"^##\s+\[({_SEMVER})\]", text, re.MULTILINE):
        return m.group(1)  # first real release heading, top-down
    return None


def _roadmap_version(root: Path) -> str | None:
    """The `vX.Y.Z` token in the ROADMAP preamble.

    Added after the sentence "HEADING OS is `v0.3.0`" survived six releases on
    the public landing path. Nothing checked it, and a reader who takes the
    roadmap at its word reads a project two quarters behind the code.
    """
    text = (root / "ROADMAP.md").read_text(encoding="utf-8")
    m = re.search(rf"HEADING OS is `v({_SEMVER})`", text)
    return m.group(1) if m else None


def _uv_lock_version(root: Path, package: str) -> str | None:
    """The locked version of THIS package inside `uv.lock`.

    Returns None when the lock is absent or carries no entry for `package`.
    Both read as drift at the call site, which is the safe direction: a guard
    whose input collapsed must report rather than pass, or the silence looks
    exactly like agreement.
    """
    path = root / "uv.lock"
    if not path.exists():
        return None
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for entry in data.get("package", []):
        if entry.get("name") == package:
            return entry.get("version")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assert version parity across README, CHANGELOG, pyproject "
                    "and ROADMAP.")
    parser.add_argument("--quiet", action="store_true", help="print only on failure")
    args = parser.parse_args()

    root = get_workspace_root()
    truth = _pyproject_version(root)
    package = _pyproject_name(root)
    readme = _readme_status_version(root)
    changelog = _changelog_latest_version(root)
    roadmap = _roadmap_version(root)
    uv_lock = _uv_lock_version(root, package)

    problems = []
    if readme != truth:
        problems.append(f"  README.md § Status : {readme!r:>10}  != pyproject {truth!r}")
    if changelog != truth:
        problems.append(f"  CHANGELOG.md latest: {changelog!r:>10}  != pyproject {truth!r}")
    if roadmap != truth:
        problems.append(f"  ROADMAP.md preamble: {roadmap!r:>10}  != pyproject {truth!r}")
    if uv_lock != truth:
        problems.append(f"  uv.lock {package}   : {uv_lock!r:>10}  != pyproject {truth!r}")

    if problems:
        print(f"{RED}Version drift (source of truth = pyproject.toml {truth}):{RESET}")
        print("\n".join(problems))
        print(
            f"{YELLOW}Fix: align README § Status, the newest CHANGELOG heading, and the "
            f"ROADMAP preamble to {truth}; re-lock with `uv lock` for uv.lock.{RESET}"
        )
        return 1

    if not args.quiet:
        print(f"{GREEN}Version in sync:{RESET} {truth} "
              f"(README, CHANGELOG, ROADMAP, uv.lock, pyproject agree)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
