#!/usr/bin/env python3
"""Deterministic git push that never relies on a wall-clock guess.

The engine repo's ``pre-push`` hook runs a ~2.5-minute regression gate, so a
plain ``timeout 90 git push`` looks like a network hang when it is really the
test gate running (the 2026-06-20 misdiagnosis). This wrapper drives the push
through ``scripts/utils/supervise.run_supervised``: it blocks until the push
*verifiably* finishes, declaring HUNG only on inactivity (no output + no CPU
across the process tree) — never on elapsed time — and verifies the branch
actually advanced (``ahead/behind == 0 0``) before reporting success. An exit
code of 0 alone is not trusted.

Usage:
  python scripts/safe-push.py --repo engine          # push the engine repo
  python scripts/safe-push.py --repo data            # push the data overlay
  python scripts/safe-push.py --repo all             # engine, then data
  python scripts/safe-push.py --repo engine --json   # machine-readable verdict
  python scripts/safe-push.py --repo engine --stall-window 180

Auth: reads GH_TOKEN from the engine ``.env`` (same token as the ``git pushgh``
alias) and feeds it to git via a credential helper through the child *env* — the
token never appears on the command line. Verify state lives in a live JSON
status file under ``<engine>/.push-state/`` (gitignored), so the run is fully
observable when launched in the background.

Exit codes: 0 all repos pushed (verified). 1 a test gate / push failed.
2 a run hung (killed). 3 auth/config problem. 4 push reported success but the
branch did not advance (postcondition mismatch).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.git_push import load_gh_token, supervised_push
from scripts.utils.workspace import get_data_root, get_workspace_root

# state -> exit code
_EXIT = {"ok": 0, "failed": 1, "hung": 2, "postcondition_failed": 4}


def _repos() -> dict[str, Path]:
    return {"engine": get_workspace_root(), "data": get_data_root()}


def _push_one(name: str, repo: Path, token: str, *, branch: str,
              remote: str, stall_window: float, status_dir: Path) -> dict:
    status_path = status_dir / f"{name}.status.json"
    verdict = supervised_push(
        repo, remote=remote, branch=branch, token=token,
        stall_window=stall_window, status_path=str(status_path),
        label=f"push:{name}",
    )
    verdict["repo"] = name
    return verdict


def _print_verdict(v: dict) -> None:
    state = v["state"]
    color = {"ok": GREEN, "failed": RED, "hung": RED,
             "postcondition_failed": YELLOW}.get(state, RED)
    icon = {"ok": "PUSHED", "failed": "FAILED", "hung": "HUNG",
            "postcondition_failed": "NOT-ADVANCED"}.get(state, state.upper())
    print(f"\n{BOLD}{color}[{icon}]{RESET} {v['repo']} "
          f"{GRAY}({v['elapsed_s']}s, exit {v['exit_code']}){RESET}")
    print(f"  {v['reason']}")
    if state != "ok" and v.get("tail"):
        print(f"{GRAY}  --- last output ---{RESET}")
        for line in v["tail"].splitlines()[-12:]:
            print(f"{GRAY}  | {line}{RESET}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic supervised git push.")
    ap.add_argument("--repo", choices=["engine", "data", "all"], required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--stall-window", type=float, default=120.0,
                    help="seconds of zero progress before declaring HUNG")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    token = load_gh_token()
    if not token:
        msg = "no GH_TOKEN in engine .env — cannot authenticate push"
        print(f"{RED}auth error:{RESET} {msg}", file=sys.stderr)
        if args.json:
            print(json.dumps({"state": "auth_error", "reason": msg}))
        return 3

    status_dir = get_workspace_root() / ".push-state"
    repos = _repos()
    targets = ["engine", "data"] if args.repo == "all" else [args.repo]

    verdicts = []
    for name in targets:
        if not args.json:
            print(f"{CYAN}supervised push -> {name}{RESET} "
                  f"{GRAY}(stall-window {args.stall_window:.0f}s; HUNG only on "
                  f"no output + no CPU, never on elapsed time){RESET}")
        v = _push_one(name, repos[name], token, branch=args.branch,
                      remote=args.remote, stall_window=args.stall_window,
                      status_dir=status_dir)
        verdicts.append(v)
        if not args.json:
            _print_verdict(v)
        # MUST-finish gate: never proceed to the next repo unless this one is
        # verifiably pushed.
        if v["state"] != "ok":
            break

    if args.json:
        print(json.dumps(verdicts, indent=2))

    worst = max((_EXIT.get(v["state"], 1) for v in verdicts), default=1)
    if not args.json:
        ok = all(v["state"] == "ok" for v in verdicts) and len(verdicts) == len(targets)
        print(f"\n{BOLD}{'ALL PUSHED & VERIFIED' if ok else 'STOPPED — not all verified'}{RESET}")
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
