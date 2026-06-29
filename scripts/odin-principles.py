#!/usr/bin/env python3
"""odin-principles.py - deal-side principle retrieval CLI (R9, CEO-only).

Maps a contact's (relationship_type, stage) - or an ad-hoc keyword set - to the
relevant Odin principles, by intersecting the existing `keywords` taxonomy. The
console-first path: this drives retrieval end to end from the terminal and chat,
before any skill or dashboard touches it. Read-only; never writes the brain or a
contact.

Usage:
  python scripts/odin-principles.py --type partner
  python scripts/odin-principles.py --type prospect --stage Negotiation
  python scripts/odin-principles.py --keywords negotiation,persuasion --json
  python scripts/odin-principles.py --type partner --limit 3

Brain absent (any exec workspace) -> a clean stderr message and exit 0.
Exit codes: 0 (incl. clean brain-absent degrade), 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RESET, YELLOW
from scripts.utils.odin_principles import principles_for_domains, relevant_principles_for
from scripts.utils.workspace import get_knowledge_dir


def main() -> int:
    ap = argparse.ArgumentParser(description="Retrieve Odin principles relevant to a deal (R9).")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--type", help="relationship_type (e.g. prospect, partner, investor-active)")
    group.add_argument("--keywords", help="comma-separated relationship-domain keywords")
    ap.add_argument("--stage", default=None, help="pipeline stage (e.g. Negotiation, Proposal)")
    ap.add_argument("--limit", type=int, default=5, help="max principles to return (default 5)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    brain_root = get_knowledge_dir() / "odin-brain"
    if not (brain_root / "principles").exists():
        # Clean console-first degrade: the brain is not present (any exec workspace).
        print("Odin brain not present on this workspace - principle citation unavailable",
              file=sys.stderr)
        if args.json:
            print(json.dumps([]))
        return 0

    if args.keywords:
        domains = [k.strip() for k in args.keywords.split(",") if k.strip()]
        results = principles_for_domains(domains, limit=args.limit, brain_root=brain_root)
    else:
        results = relevant_principles_for(args.type, args.stage, limit=args.limit, brain_root=brain_root)

    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    if not results:
        print(f"{YELLOW}No Odin principle matched this relationship domain.{RESET}")
        return 0
    label = args.keywords or (f"{args.type}" + (f" / {args.stage}" if args.stage else ""))
    print(f"{BOLD}Relevant Odin principles for {CYAN}{label}{RESET}{BOLD}:{RESET}")
    for r in results:
        dom = ", ".join(r["matched_domains"])
        print(f"  {GREEN}{r['slug']}{RESET}  {GRAY}[{dom}] ({r['confidence']}){RESET}")
        print(f"      {r['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
