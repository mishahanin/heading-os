#!/usr/bin/env python3
"""Clear the dep-update-pending marker after a successful pip install.

Usage:
    python scripts/clear-dep-marker.py

Run this after `pip install -r corporate/requirements.txt` succeeds, to
dismiss the session-start banner. Manual clear is the contract: the
mechanism never auto-clears on sync to avoid masking install failures.

Spec: docs/superpowers/specs/2026-04-27-layered-requirements-distribution-design.md
"""

import sys
from pathlib import Path

# Workspace imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, YELLOW, RESET


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    cwd = Path.cwd()
    marker = cwd / ".sync" / "dep-update-pending.json"

    if not marker.exists():
        print(f"{YELLOW}Nothing to clear:{RESET} {marker} does not exist.")
        return 0

    marker.unlink()
    print(f"{GREEN}Cleared{RESET} {marker}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
