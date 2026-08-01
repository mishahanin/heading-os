#!/usr/bin/env python3
"""Refuse a commit that touches the enforcement surface with no freeze held.

The teeth behind `scripts/utils/slice_depth.py`. A classifier nothing calls is
advice, and THE LAW says advice dies; this is what makes the answer bind.

Invoked by pre-commit with the staged filenames on argv:

    python3 scripts/depth-gate.py <file> [<file> ...]

Deliberately bypassable. `git commit --no-verify` skips it, and it is NOT
promoted to the push wall. Depth is a process discipline, not a leak wall: the
push-time scans that ARE unbypassable exist to stop data leaving the machine,
and locking the operator out of an emergency fix to his own hooks would trade a
real risk for a procedural one.

The escape is an environment variable carrying a STATED REASON:

    HEADING_OS_DEPTH_OVERRIDE="hotfix, the gate itself is wedged" git commit ...

An override with no reason still refuses. Both the refusal and the override are
counted through the denial log, so "we overrode it every time" becomes a readable
fact rather than folklore. An escape that costs nothing to use is not an escape,
it is the default.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.denial_log import log_denial
from scripts.utils.paths import get_workspace_root
from scripts.utils.slice_depth import DEPTH_FULL, classify

OVERRIDE_ENV = "HEADING_OS_DEPTH_OVERRIDE"


def _freeze_state(root: Path):
    """Return (held, manifest).

    Held-ness is answered by the freeze file EXISTING, not by the manifest
    parsing. The gate's question is "is a slice in flight", and a corrupt
    manifest is a different problem with its own owner: `canopus verify` reports
    it, and the freeze gate inside `tests/conftest.py` refuses to run the suite
    at all while it reads red. Demanding a valid parse here would mean that the
    one commit able to REPAIR a corrupt freeze is the commit this gate blocks,
    which is a lockout dressed as strictness.

    The manifest is still read, guarded, because `classify` uses it to treat a
    frozen path as load-bearing. Failing to parse costs that refinement and
    nothing else.
    """
    try:
        from scripts.utils.canopus_freeze import freeze_state_path, read_freeze
    except Exception as exc:
        print(f"{YELLOW}depth-gate: canopus unavailable ({type(exc).__name__}: {exc}); "
              f"treating the freeze as not held{RESET}", file=sys.stderr)
        return False, None

    held = freeze_state_path(root).exists()
    try:
        manifest = read_freeze(root)
    except Exception as exc:
        print(f"{YELLOW}depth-gate: a freeze file is present but unreadable "
              f"({type(exc).__name__}: {exc}); `canopus verify` owns that. "
              f"Treating the slice as in flight.{RESET}", file=sys.stderr)
        manifest = None
    return held, manifest


def main() -> int:
    paths = [p for p in sys.argv[1:] if p.strip()]
    if not paths:
        return 0

    root = get_workspace_root()
    held, manifest = _freeze_state(root)
    result = classify(paths, freeze=manifest)

    if result["depth"] != DEPTH_FULL:
        return 0
    if held:
        # Full depth AND a freeze held is the correct state, not a problem: it
        # is what a slice in flight looks like.
        return 0

    triggers = result["triggers"]
    override = (os.environ.get(OVERRIDE_ENV) or "").strip()
    if override:
        for trigger in triggers:
            log_denial(mechanism="depth-gate:override", action="commit",
                       path=trigger["path"], reason=override)
        print(f"{YELLOW}depth-gate: OVERRIDDEN{RESET} {GRAY}({override}){RESET}")
        print(f"{GRAY}  Recorded. Read the overrides with: "
              f"python scripts/denials.py --detail{RESET}")
        return 0

    for trigger in triggers:
        log_denial(mechanism="depth-gate", action="commit", path=trigger["path"],
                   reason=f"full depth required: {trigger['kind']} ({trigger['rule']})")

    print(f"{RED}{BOLD}BLOCKED{RESET} {RED}- this change takes FULL depth and no "
          f"Canopus freeze is held.{RESET}")
    for trigger in triggers:
        print(f"  {RED}{trigger['path']}{RESET}  {GRAY}{trigger['kind']} "
              f"({trigger['rule']}){RESET}")
    print()
    print(f"{GRAY}These paths hold the workspace together, so a change to them "
          f"carries the whole lifecycle{RESET}")
    print(f"{GRAY}however small the diff is. Calibration only ever removes "
          f"ceremony from work that touches none of them.{RESET}")
    print()
    print(f"{BOLD}To proceed properly:{RESET} write the test that decides, then")
    print(f"  {GREEN}python scripts/canopus.py approve --label <slice> --anchor "
          f"<gate artifact> --contract <dir>{RESET}")
    print(f"  {GRAY}commit the gate artifact, then the same command with "
          f"'freeze'.{RESET}")
    print()
    print(f"{BOLD}If this is genuinely an emergency:{RESET}")
    print(f'  {YELLOW}{OVERRIDE_ENV}="why" git commit ...{RESET}'
          f"  {GRAY}(recorded, and an empty reason still refuses){RESET}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
