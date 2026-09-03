#!/usr/bin/env python3
"""Install the data overlay's pre-commit guard. Once, from HELM.

The guard refuses a commit to the private data overlay made from a YARD
worktree. It is the SECOND of two independent mechanisms for the one rule
("files into the overlay yes, git in it no"); the first is
`check_yard_write_guard` in `.claude/hooks/_dispatch.py`, which is structural
and needs no installation at all.

Why this is its own command, run by hand, and not a step in the YARD bootstrap:

  * The overlay is a SHARED resource outside every worktree. A bootstrap that
    writes there runs on every worktree creation, so it would rewrite a shared
    file several times a day for no reason.
  * An existing `pre-commit` must never be overwritten. "Additions only" is a
    hard constraint of the HELM/YARD change, and a draft of this step did
    `mv tmp pre-commit` unconditionally, which destroys whatever was there.

So: an absent hook is written, our own hook is refreshed, and a hook belonging
to somebody else is left alone with the fragment printed for manual merging.

Usage:
    python scripts/install-data-overlay-guard.py            # install / refresh
    python scripts/install-data-overlay-guard.py --check    # report only
    python scripts/install-data-overlay-guard.py --print    # emit the body

Exit codes: 0 installed / already correct, 1 a foreign hook is in the way or
`--check` found the guard missing, 2 this was not run from HELM.

Tests: tests/test_a_data_overlay_guard_that_overwrote_what_was_there.py
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.clone_guard import require_main_clone
from scripts.utils.colors import BOLD, CYAN, GREEN, RED, RESET, YELLOW
from scripts.utils.paths import assert_data_root_external

# The marker that identifies OUR hook. Deliberately the variable name the hook
# tests, so a hook that carries it is a hook that does this job.
MARKER = "HEADING_OS_YARD"

BODY_PATH = (Path(__file__).resolve().parent / "herdr" / "heading-os-yard"
             / "data-overlay-pre-commit")


def hook_body() -> str:
    return BODY_PATH.read_text(encoding="utf-8")


def classify(hook: Path) -> str:
    """One of: 'absent', 'ours', 'foreign'."""
    if not hook.exists():
        return "absent"
    try:
        text = hook.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"{RED}Cannot read {hook}: {exc}{RESET}", file=sys.stderr)
        raise
    return "ours" if MARKER in text else "foreign"


def install(hook: Path) -> None:
    """Write the hook atomically, then make it executable.

    Temp file plus `os.replace`, per the workspace's atomic-state-write rule:
    a half-written pre-commit hook is a repository that cannot commit at all.
    """
    tmp = hook.with_suffix(".tmp")
    tmp.write_text(hook_body(), encoding="utf-8")
    tmp.chmod(0o755)
    os.replace(tmp, hook)


def main() -> int:
    require_main_clone(__file__)

    parser = argparse.ArgumentParser(
        description="Install the data overlay's YARD commit guard, from HELM.")
    parser.add_argument("--check", action="store_true",
                        help="report the state and change nothing")
    parser.add_argument("--print", dest="print_body", action="store_true",
                        help="print the hook body and exit")
    args = parser.parse_args()

    if args.print_body:
        print(hook_body(), end="")
        return 0

    data_root = assert_data_root_external()
    hooks_dir = data_root / ".git" / "hooks"
    hook = hooks_dir / "pre-commit"
    state = classify(hook)

    if args.check:
        if state == "ours":
            print(f"{GREEN}armed{RESET}  {hook}")
            return 0
        if state == "absent":
            print(f"{YELLOW}missing{RESET}  no pre-commit at {hook}")
        else:
            print(f"{YELLOW}foreign{RESET}  a pre-commit exists at {hook} and "
                  f"does not carry the {MARKER} guard")
        return 1

    if state == "foreign":
        print(
            f"{RED}{BOLD}REFUSED{RESET}: {hook} already exists and is not this "
            f"guard.\n"
            f"Nothing was changed. Additions only is a hard constraint here, "
            f"and overwriting somebody else's commit hook is not an addition.\n\n"
            f"Merge this in by hand, near the top of that file:\n\n"
            f"{CYAN}{hook_body()}{RESET}",
            file=sys.stderr,
        )
        return 1

    hooks_dir.mkdir(parents=True, exist_ok=True)
    install(hook)
    verb = "refreshed" if state == "ours" else "installed"
    print(f"{GREEN}{verb}{RESET}  {hook}")
    print(f"  A commit to the data overlay from a YARD is now refused by git "
          f"itself, as well as by the PreToolUse wall.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
