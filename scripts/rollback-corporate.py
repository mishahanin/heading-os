#!/usr/bin/env python3
"""R16 Layer 2 -- one-command rollback of the corporate `main` branch.

If something broke that the canary's smoke + eval missed, this reverts `main` to
the previous BUILD and pushes; execs pull the reverted state on their next hourly
sync. The bad commit stays on `staging` for investigation, never re-propagated.

Mechanism: a FORWARD revert (`git revert --no-edit HEAD`) -- not a hard reset --
so no history is rewritten and GitHub branch protection on `main` is respected.
This assumes the promote was `--ff-only` and that the latest publish landed as a
single commit (the normal publish-corporate flow): then HEAD~1 is the previous
build. If HEAD~1 still carries the current build (a multi-commit staging push),
the rollback refuses and points to manual recovery -- fail-closed, never guess.

Usage:
    python scripts/rollback-corporate.py [--dry-run]

Exit codes: 0 ok | 3 not-admin | 4 corp-repo-missing | 6 not-on-main
            13 no-previous-build | 14 revert-conflict | 15 push-failed
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_workspace_root, get_corporate_repo_path  # noqa: E402
from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.git_push import supervised_push  # noqa: E402

WORKSPACE_ROOT = get_workspace_root()


def verify_admin_identity(workspace_root: Path | None = None) -> None:
    root = workspace_root or WORKSPACE_ROOT
    identity_path = root / ".workspace-identity.json"
    if not identity_path.exists():
        print(f"{RED}ERROR: .workspace-identity.json missing.{RESET}", file=sys.stderr)
        sys.exit(3)
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{RED}ERROR: .workspace-identity.json invalid JSON: {exc}{RESET}", file=sys.stderr)
        sys.exit(3)
    if identity.get("role") != "admin":
        print(f"{RED}ERROR: CEO-only. role={identity.get('role')}{RESET}", file=sys.stderr)
        sys.exit(3)


def _git(args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=check)


def current_branch(corp: Path) -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], corp).stdout.strip()


def _build_at(corp: Path, ref: str):
    """Read the BUILD.json `build` integer at a git ref (None if absent/unreadable)."""
    res = _git(["show", f"{ref}:BUILD.json"], corp, check=False)
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout).get("build")
    except json.JSONDecodeError:
        return None


def validate_rollback_target(current_build, prev_build) -> tuple[bool, str]:
    """Pure check: can we roll back from current_build to prev_build?
    Refuses when there is no distinct previous build (fail-closed)."""
    if prev_build is None:
        return False, "no previous commit / BUILD.json at HEAD~1"
    if current_build is not None and prev_build == current_build:
        return False, (f"HEAD~1 still carries build {prev_build} (multi-commit push); "
                       f"the simple HEAD revert would not restore the previous build -- "
                       f"recover manually")
    return True, f"build {current_build} -> {prev_build}"


def do_rollback(corp: Path) -> int:
    rev = _git(["revert", "--no-edit", "HEAD"], corp, check=False)
    if rev.returncode != 0:
        print(f"{RED}ERROR: revert failed (conflict):\n{rev.stderr}{RESET}", file=sys.stderr)
        _git(["revert", "--abort"], corp, check=False)
        return 14
    # Supervised + verified push: the rollback only takes effect once the revert
    # lands on origin/main (execs pull it next sync). A bare push that exits 0
    # without advancing the ref would report ROLLBACK COMPLETE while execs keep
    # the bad build; the watchdog also bounds an indefinite network stall.
    v = supervised_push(corp, stall_window=120, label="rollback-push")
    if v["state"] != "ok":
        print(f"{RED}ERROR: push {v['state']}: {v['reason']}{RESET}", file=sys.stderr)
        return 15
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rollback corporate main to the previous BUILD (R16 Layer 2).")
    ap.add_argument("--dry-run", action="store_true", help="show the target, do not revert")
    args = ap.parse_args(argv)

    verify_admin_identity()
    corp = get_corporate_repo_path()
    if not corp.exists() or not (corp / ".git").exists():
        print(f"{RED}ERROR: corporate repo not found at {corp}.{RESET}", file=sys.stderr)
        return 4

    _git(["fetch", "origin", "main"], corp, check=False)
    branch = current_branch(corp)
    if branch != "main":
        print(f"{RED}ERROR: corporate repo is on '{branch}', not 'main'. Checkout main first.{RESET}",
              file=sys.stderr)
        return 6

    current_build = _build_at(corp, "HEAD")
    prev_build = _build_at(corp, "HEAD~1")
    ok, detail = validate_rollback_target(current_build, prev_build)

    print(f"\n{BOLD}Corporate rollback{RESET}")
    print(f"  current build: {current_build}")
    print(f"  target  build: {prev_build}")
    print(f"  {detail}")

    if not ok:
        print(f"{RED}ERROR: cannot roll back -- {detail}.{RESET}", file=sys.stderr)
        return 13

    if args.dry_run:
        print(f"{GRAY}--dry-run: would `git revert --no-edit HEAD` and push origin/main.{RESET}")
        return 0

    if input(f"\nRoll back main to build {prev_build}? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Aborted.")
        return 1

    rc = do_rollback(corp)
    if rc == 0:
        print(f"\n{GREEN}{BOLD}ROLLBACK COMPLETE{RESET}  main reverted to build {prev_build}, "
              f"pushed origin/main.")
        print(f"{YELLOW}The reverted change remains on staging for investigation.{RESET}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
