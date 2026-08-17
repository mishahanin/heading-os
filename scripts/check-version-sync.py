#!/usr/bin/env python3
"""Version-sync guard (F-1.1).

`pyproject.toml` is the single source of truth for the engine version. This
check asserts the three human-facing surfaces agree with it:

  * README.md § Status — the `vX.Y.Z` token under the "## Status" heading.
  * CHANGELOG.md      — the newest real release heading `## [X.Y.Z]`
                        (the `## [Unreleased]` section is ignored).
  * ROADMAP.md        — the `vX.Y.Z` token in the "HEADING OS is" preamble.

Exit 0 when all agree; exit 1 with a diff-style message otherwise. Wired into
`.pre-commit-config.yaml` (files: README.md|CHANGELOG.md|ROADMAP.md|
pyproject.toml) and the CI `guards` job, so version drift across the four files
can never re-appear.

Usage:
    python scripts/check-version-sync.py [--quiet]
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


def _readme_status_version(root: Path) -> str | None:
    text = (root / "README.md").read_text(encoding="utf-8")
    m = re.search(r"##\s+Status\b(.*?)(?:\n##\s|\Z)", text, re.DOTALL)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Assert version parity across README/CHANGELOG/pyproject.")
    parser.add_argument("--quiet", action="store_true", help="print only on failure")
    args = parser.parse_args()

    root = get_workspace_root()
    truth = _pyproject_version(root)
    readme = _readme_status_version(root)
    changelog = _changelog_latest_version(root)
    roadmap = _roadmap_version(root)

    problems = []
    if readme != truth:
        problems.append(f"  README.md § Status : {readme!r:>10}  != pyproject {truth!r}")
    if changelog != truth:
        problems.append(f"  CHANGELOG.md latest: {changelog!r:>10}  != pyproject {truth!r}")
    if roadmap != truth:
        problems.append(f"  ROADMAP.md preamble: {roadmap!r:>10}  != pyproject {truth!r}")

    if problems:
        print(f"{RED}Version drift (source of truth = pyproject.toml {truth}):{RESET}")
        print("\n".join(problems))
        print(
            f"{YELLOW}Fix: align README § Status, the newest CHANGELOG heading, and the "
            f"ROADMAP preamble to {truth}.{RESET}"
        )
        return 1

    if not args.quiet:
        print(f"{GREEN}Version in sync:{RESET} {truth} (README, CHANGELOG, ROADMAP, pyproject agree)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
