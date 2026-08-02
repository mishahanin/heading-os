#!/usr/bin/env python3
"""How much process does this change carry? Ask before you start, not after.

The console read path over `scripts/utils/slice_depth.py`. With no arguments it
classifies what is staged plus what is modified, which is the question actually
being asked: how deep is the thing I am about to commit.

Usage:
    python scripts/slice-depth.py                     # the current change
    python scripts/slice-depth.py --files a.py b.md   # named paths
    python scripts/slice-depth.py --range main..HEAD  # a commit range
    python scripts/slice-depth.py --json
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.paths import get_workspace_root
from scripts.utils.slice_depth import DEPTH_FULL, DEPTH_LIGHT, classify

_COLOUR = {DEPTH_FULL: RED, "standard": YELLOW, DEPTH_LIGHT: GREEN}

_WHAT_IT_MEANS = {
    "full": "all thirteen moments, both approvals, frozen contract, adversarial review",
    "standard": "test before the code, machine verdict, one approval at the end",
    "light": "ordinary commit checks; no contract, no approval gate",
}


def _git(args, root: Path) -> list:
    proc = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _current_change(root: Path) -> list:
    """Staged plus unstaged tracked edits: what a commit now would carry."""
    paths = set(_git(["diff", "--cached", "--name-only"], root))
    paths.update(_git(["diff", "--name-only"], root))
    return sorted(paths)


def _freeze_state(root: Path):
    """(held, manifest), matching `scripts/depth-gate.py` exactly.

    Held-ness is the freeze FILE existing, not the manifest parsing. If the two
    disagreed, an operator debugging a refusal would read "freeze held: no" here
    while the gate that refused him read "held". Same question, same answer.
    """
    try:
        from scripts.utils.canopus_freeze import freeze_state_path, read_freeze
    except Exception as exc:
        print(f"{GRAY}(canopus unavailable: {exc}){RESET}", file=sys.stderr)
        return False, None
    held = freeze_state_path(root).exists()
    try:
        return held, read_freeze(root)
    except Exception as exc:
        print(f"{GRAY}(freeze file present but unreadable: {exc}){RESET}", file=sys.stderr)
        return held, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify a change's Canopus depth.")
    parser.add_argument("--files", nargs="*", help="classify these paths")
    parser.add_argument("--range", help="classify a commit range, e.g. main..HEAD")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    root = get_workspace_root()
    if args.files:
        paths, source = list(args.files), "named paths"
    elif args.range:
        paths = _git(["diff", "--name-only", args.range], root)
        source = f"range {args.range}"
    else:
        paths = _current_change(root)
        source = "the current change (staged + modified)"

    held, manifest = _freeze_state(root)
    result = classify(paths, freeze=manifest, root=root)
    depth = result["depth"]

    if args.as_json:
        json.dump({"depth": depth, "reason": result["reason"],
                   "triggers": result["triggers"], "paths": paths,
                   "freeze_held": held, "source": source},
                  sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    colour = _COLOUR.get(depth, "")
    print(f"{BOLD}Depth: {colour}{depth}{RESET}  "
          f"{GRAY}{len(paths)} path(s), {source}{RESET}")
    print(f"{GRAY}{_WHAT_IT_MEANS.get(depth, '')}{RESET}")
    if result["triggers"]:
        print()
        for trigger in result["triggers"]:
            print(f"  {colour}{trigger['path']}{RESET}  {GRAY}{trigger['kind']} "
                  f"({trigger['rule']}){RESET}")
    if depth == DEPTH_FULL:
        print()
        print(f"{GRAY}Freeze held: {'yes' if held else 'NO'}{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
