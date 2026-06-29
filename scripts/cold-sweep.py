#!/usr/bin/env python3
"""Cold-Sweep manual-run CLI (R2; daemon-free deposit since 2026-06-27).

Thin wrapper around ``cold_sweep_core``: builds Action Queue cards from CRM
health and deposits them IN-PROCESS via ``action_queue.append_cards`` (the sole
dedup authority). No bridge daemon, no HTTP - the deposit works with the daemon
down, consistent with the terminal-native Action Queue redesign.

Root discipline (two-repo topology): the card build shells out to
``scripts/crm-health.py`` under the ENGINE root (get_workspace_root); the queue
store lives under the DATA root (get_data_root), passed to append_cards.

Usage:
    python scripts/cold-sweep.py            # build + deposit
    python scripts/cold-sweep.py --dry-run  # build + print, do not deposit

Exit codes: 0 ok, 1 error.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import cold_sweep_core
from scripts.bridge_daemon.sources.action_queue import append_cards
from scripts.utils.colors import GRAY, GREEN, RESET
from scripts.utils.workspace import get_data_root, get_workspace_root


def main() -> int:
    ap = argparse.ArgumentParser(description="Cold-Sweep: route overdue CRM contacts into the Action Queue.")
    ap.add_argument("--dry-run", action="store_true", help="build + print cards, do not deposit")
    args = ap.parse_args()

    engine_root = get_workspace_root()  # locates scripts/crm-health.py
    cards = cold_sweep_core.run(engine_root)
    if not cards:
        print(f"{GRAY}no overdue contacts to route{RESET}")
        return 0

    if args.dry_run:
        print(json.dumps(cards, indent=2, ensure_ascii=False))
        print(f"{GRAY}(dry-run) {len(cards)} card(s) built, not deposited{RESET}")
        return 0

    # Daemon-free deposit: append directly to the queue under the DATA root.
    result = append_cards(get_data_root(), cards)
    print(f"{GREEN}deposited{RESET} added={result.get('added', 0)} "
          f"skipped={result.get('skipped', 0)} (dedup/cooldown applied by append_cards)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
