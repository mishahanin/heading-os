#!/usr/bin/env python3
"""sync-all-execs.py -- RETIRED stub (no central exec-sync driver, by design).

This was the parallel per-exec workspace-sync driver: it ran
`python scripts/workspace-sync.py --pull-only` inside each `../31c-workspace-{slug}/`
sibling, keyed off the legacy `config/exec-registry.json`. Both the destructive
`workspace-sync.py` engine and that legacy fleet model are retired -- see
`plans/2026-06-26-retire-workspace-sync-disk-import.md`.

In the HEADING OS three-repo model a clean exec deploy pulls code with a plain
`git pull` on its engine clone, pushes data with `scripts/push-all.py`, and
recovers any prior on-disk records once with `scripts/import-legacy-records.py`.
There is no central CEO-driven pull of exec workspaces anymore.

Ongoing exec sync is per-machine by design (git pull for code + corporate via
scripts/sync-corporate.py, push-all.py for data); there is deliberately NO central
CEO-driven driver, and this stub will not become one (deferral lifted 2026-06-26).
It keeps the old CLI surface so any lingering caller exits cleanly with a clear
message rather than crashing on a missing script.

Usage (all flags accepted, all no-ops):
    python scripts/sync-all-execs.py [--slug S] [--dry-run] [--max-workers N]
                                     [--timeout S] [--json]
Exit code is always 0 -- a no-op is not a failure.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GRAY, YELLOW, RESET

_RETIRED_MSG = (
    "sync-all-execs.py is retired. Central CEO-driven exec sync no longer exists; "
    "a clean exec deploy uses `git pull` (code), `push-all.py` (data), and "
    "`import-legacy-records.py` (first-run records). See "
    "plans/2026-06-26-retire-workspace-sync-disk-import.md."
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RETIRED: parallel per-exec workspace sync driver (no-op stub).",
    )
    parser.add_argument("--slug", help="(ignored) retained for CLI compatibility.")
    parser.add_argument("--dry-run", action="store_true", help="(ignored)")
    parser.add_argument("--max-workers", type=int, default=5, help="(ignored)")
    parser.add_argument("--timeout", type=int, default=600, help="(ignored)")
    parser.add_argument("--json", action="store_true",
                        help="Emit a machine-readable retired marker.")
    args = parser.parse_args()

    if args.json:
        print(json.dumps({"status": "retired", "results": [], "total_elapsed_s": 0.0,
                          "message": _RETIRED_MSG}, indent=2))
    else:
        print(f"{YELLOW}{_RETIRED_MSG}{RESET}")
        print(f"{GRAY}No exec workspaces were synced (intentional no-op).{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
