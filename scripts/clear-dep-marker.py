#!/usr/bin/env python3
"""Clear the dep-update-pending marker after a successful pip install.

Usage:
    python scripts/clear-dep-marker.py

Run this after `pip install -r corporate/requirements.txt` succeeds, to
dismiss the session-start banner. Manual clear is the contract: the
mechanism never auto-clears on sync to avoid masking install failures.

Spec: docs/superpowers/specs/2026-04-27-layered-requirements-distribution-design.md
(data overlay: .heading-os-data/docs/superpowers/specs/2026-04-27-layered-requirements-distribution-design.md;
that tree routes private, so a public clone does not carry it)
"""

import sys
from pathlib import Path

# Workspace imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, YELLOW, RESET
from scripts.utils.workspace import get_workspace_root


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    # The workspace root, not `Path.cwd()`. Every sibling script anchors here;
    # this one did not, so a cron entry, an absolute-path invocation
    # (`python /path/to/scripts/clear-dep-marker.py`) or a run from any
    # subdirectory looked at the wrong place, printed "Nothing to clear" and
    # exited 0 while the real marker survived — and the session-start banner
    # kept firing after a successful install. Wrong-looking-success, and the
    # only hint was the absolute path in the message nobody reads.
    marker = get_workspace_root() / ".sync" / "dep-update-pending.json"

    if not marker.exists():
        print(f"{YELLOW}Nothing to clear:{RESET} {marker} does not exist.")
        return 0

    marker.unlink()
    print(f"{GREEN}Cleared{RESET} {marker}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
