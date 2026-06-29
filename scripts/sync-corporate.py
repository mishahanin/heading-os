#!/usr/bin/env python3
"""Exec-side corporate-content consumption seam.

In the HEADING OS three-repo model an executive workspace reads corporate content
(datastore, knowledge/shared, the context carve-outs, crm config/aliases/
address-book, daemon base config) directly from a gitignored clone of the
published `heading-os-corporate` repo at ``<workspace>/.corporate-repo/``. This is
the single source of truth: ``get_corporate_root()`` resolves there for execs, so
nothing is copied and there is no stale on-disk duplicate. `git pull` updates it.

This script owns that clone:
  - first run  -> `gh repo clone <org>/heading-os-corporate .corporate-repo/`
  - thereafter -> `git pull --ff-only` inside `.corporate-repo/`

`scripts/setup.py` (onboarding) and the `/sync` skill both call this; it is the
ONE implementation of the corporate clone/pull (no second copy in setup.py).

On the CEO workspace this is a deliberate no-op: the CEO AUTHORS corporate content
in the private data overlay and publishes it UP to heading-os-corporate via
`/publish-corporate`. The CEO never consumes a corporate clone.

Usage:
  python scripts/sync-corporate.py            # clone or ff-only pull
  python scripts/sync-corporate.py --dry-run  # show what would happen
  python scripts/sync-corporate.py --json     # machine-readable result

Exit codes:
  0  success (or CEO no-op)
  1  clone/pull failed (degrades clearly; never silent)
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import GRAY, GREEN, RED, YELLOW, RESET
from scripts.utils.workspace import (
    get_workspace_root,
    is_exec_workspace,
    load_env,
    load_github_org,
)

CORPORATE_REPO = "heading-os-corporate"
CLONE_DIRNAME = ".corporate-repo"


def _result(status: str, action: str, path: str, message: str) -> dict:
    return {"status": status, "action": action, "path": path, "message": message}


def sync_corporate(dry_run: bool = False) -> dict:
    """Clone or fast-forward the corporate content clone. Returns a result dict."""
    root = get_workspace_root()

    # CEO workspace: never consumes a corporate clone (publishes UP instead).
    if not is_exec_workspace():
        return _result(
            "noop", "none", "",
            "CEO workspace — corporate content is authored in the data overlay and "
            "published via /publish-corporate; nothing to consume.",
        )

    clone = root / CLONE_DIRNAME
    org = load_github_org()

    if dry_run:
        action = "pull" if (clone / ".git").is_dir() else "clone"
        return _result(
            "dry-run", action, str(clone),
            f"would {action} {org}/{CORPORATE_REPO} at {clone}",
        )

    # gh reads GH_TOKEN; load the engine .env so a headless run authenticates.
    try:
        load_env(root)
    except Exception as exc:  # noqa: BLE001 - advisory; gh may still use its own auth
        print(f"{GRAY}note: could not load engine .env ({exc}); relying on gh auth.{RESET}")

    if (clone / ".git").is_dir():
        proc = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=str(clone), capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return _result(
                "error", "pull", str(clone),
                f"git pull --ff-only failed: {(proc.stderr or proc.stdout).strip()}",
            )
        return _result(
            "ok", "pull", str(clone),
            (proc.stdout or "").strip() or "already up to date",
        )

    # First run: clone.
    proc = subprocess.run(
        ["gh", "repo", "clone", f"{org}/{CORPORATE_REPO}", str(clone)],
        cwd=str(root), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return _result(
            "error", "clone", str(clone),
            f"gh repo clone {org}/{CORPORATE_REPO} failed: "
            f"{(proc.stderr or proc.stdout).strip()}",
        )
    return _result("ok", "clone", str(clone), f"cloned {org}/{CORPORATE_REPO}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Sync the gitignored heading-os-corporate clone (exec consumption seam).",
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would happen; clone/pull nothing")
    ap.add_argument("--json", action="store_true",
                    help="emit a machine-readable result")
    args = ap.parse_args()

    res = sync_corporate(dry_run=args.dry_run)

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        if res["status"] in ("ok", "dry-run"):
            print(f"{GREEN}{res['action']}{RESET}: {res['message']}")
        elif res["status"] == "noop":
            print(f"{GRAY}{res['message']}{RESET}")
        else:
            print(f"{RED}corporate sync failed — {res['message']}{RESET}")
            print(f"{YELLOW}Corporate content not updated. Check access to "
                  f"{load_github_org()}/{CORPORATE_REPO} and your gh auth.{RESET}")

    return 1 if res["status"] == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
