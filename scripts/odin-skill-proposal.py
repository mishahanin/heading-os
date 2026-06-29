#!/usr/bin/env python3
"""odin-skill-proposal.py - the principle -> skill rewrite loop CLI (R6, CEO-only).

Proposes a reflection-derived how-to principle as a checklist-step edit to a
target skill, emitted as a PROPOSED unified diff. It NEVER edits the skill file;
the CEO applies the proposal by hand. Console-first, browser-free, read-only
with respect to skills and the brain.

Usage:
  python scripts/odin-skill-proposal.py --principle gate-product-exposure-on-signed-mnda --skill proposal
  python scripts/odin-skill-proposal.py --principle <slug> --skill meeting-prep --section "Phase 1"
  python scripts/odin-skill-proposal.py --principle <slug> --skill proposal --json
  python scripts/odin-skill-proposal.py --principle <slug> --skill proposal --write-artifact

Exit codes: 0 proposal produced, 1 ineligible/not-found (with a plain reason),
2 usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import odin_skill_proposal as osp
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import display_path, get_outputs_dir, get_workspace_root

ROOT = get_workspace_root()          # ENGINE root - locates skill files under .claude/skills


def _print_diff(unified_diff: str) -> None:
    for line in unified_diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            print(f"{GREEN}{line}{RESET}")
        elif line.startswith("-") and not line.startswith("---"):
            print(f"{RED}{line}{RESET}")
        elif line.startswith("@@"):
            print(f"{CYAN}{line}{RESET}")
        else:
            print(f"{GRAY}{line}{RESET}")


def _write_artifact(result: dict) -> Path:
    out_dir = get_outputs_dir() / "operations" / "odin" / "skill-proposals"
    out_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    name = f"{date}_skill-proposal_{result['skill_name']}_{result['principle_slug']}.md"
    path = out_dir / name
    md = (
        f"# Odin skill-proposal: {result['principle_slug']} -> /{result['skill_name']}\n\n"
        f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n"
        f"**Proposal only.** Odin never edits a skill file. Review, then apply by hand if you accept it.\n\n"
        f"## Rationale\n\n{result['rationale']}\n\n"
        f"## Target section\n\n{result.get('target_section') or '(none found - choose placement manually)'}\n\n"
        f"## Proposed step\n\n```\n{result['proposed_step']}\n```\n\n"
        f"## Proposed diff (display only - not applied)\n\n```diff\n{result['unified_diff']}```\n"
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(md, encoding="utf-8")
    os.replace(tmp, path)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Propose a how-to principle as a skill checklist step (R6).")
    ap.add_argument("--principle", required=True, help="principle slug or path")
    ap.add_argument("--skill", required=True, help="target skill name (e.g. proposal, meeting-prep)")
    ap.add_argument("--section", default=None, help="explicit target heading to insert under")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--write-artifact", action="store_true",
                    help="save the proposal markdown under outputs/operations/odin/skill-proposals/")
    args = ap.parse_args()

    result = osp.build_proposal(args.principle, args.skill,
                                workspace_root=ROOT, section=args.section)

    if not result.get("ok"):
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"{RED}ineligible{RESET}: {result.get('error')}", file=sys.stderr)
        return 1

    artifact_path = None
    if args.write_artifact:
        artifact_path = _write_artifact(result)

    if args.json:
        out = dict(result)
        if artifact_path is not None:
            out["artifact"] = display_path(artifact_path)
        print(json.dumps(out, indent=2))
        return 0

    print(f"{BOLD}Proposal: {CYAN}{result['principle_slug']}{RESET}{BOLD} -> /{result['skill_name']}{RESET}")
    print(f"{GRAY}{result['rationale']}{RESET}\n")
    print(f"{BOLD}Proposed step:{RESET} {result['proposed_step']}\n")
    if result["unified_diff"]:
        print(f"{BOLD}Proposed diff (display only - NOT applied):{RESET}")
        _print_diff(result["unified_diff"])
    else:
        print(f"{YELLOW}No `##` heading found to attach under - choose placement manually.{RESET}")
    if artifact_path is not None:
        print(f"\n{GREEN}wrote{RESET} {GRAY}{display_path(artifact_path)}{RESET}")
    print(f"\n{GRAY}Odin never edits the skill - apply this by hand if you accept it.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
