#!/usr/bin/env python3
"""canary-smoke.py -- Deterministic post-sync smoke tests on the canary exec workspace.

Runs after the canary's hourly corporate sync completes. Validates the just-pulled
staging build with four static checks (no API calls, milliseconds latency, $0 cost).
Writes status JSON to the corporate repo's `staging` branch so the CEO's ceo-main
can read canary health locally without contacting the canary machine.

This script is corporate-classified and ships to every exec workspace. On non-canary
workspaces (default) it exits 0 with a skip message - the M6 guard from the
2026-05-15 design doc. The actual canary wiring (Alex's .workspace-identity.json
canary=true, his scheduled task invoking this script post-sync) lands in the
canary-side session that follows this CEO-side infrastructure work.

Layer 1 scope (this script): smoke tests + status JSON write/push.
Layer 3 scope (later): canary-eval.py augments this status JSON with eval results.
Until Layer 3 lands, eval_* fields are placeholders.

Usage:
    python scripts/canary-smoke.py             # Run on canary; exit 0 on non-canary
    python scripts/canary-smoke.py --dry-run   # Run checks; print payload; no commit
    python scripts/canary-smoke.py --force     # Bypass canary guard (testing only)
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import (
    get_corporate_repo_path,
    get_workspace_identity,
    get_workspace_root,
)
from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW


# Four deterministic checks. All paths are relative to the workspace root.
SMOKE_CHECKS = [
    ("skill-router-sync", ["scripts/check-skill-router-sync.py"]),
    ("crm-schema", ["scripts/validate-crm-schema.py"]),
    (
        "workspace-health",
        ["scripts/workspace-health.py",
         "--section", "refs", "--section", "context", "--section", "counts"],
    ),
    (
        "sanitize-corporate-architecture",
        ["scripts/sanitize-text.py",
         "corporate/reference/workspace-architecture.md", "--scan"],
    ),
]


def run_check(workspace_root: Path, cmd_args: list, timeout: int = 300) -> tuple:
    """Run a single check command. Returns (success, summary_string).

    summary_string is "ok" on success; otherwise an exit-code + last-output excerpt.
    """
    cmd = [sys.executable] + cmd_args
    try:
        result = subprocess.run(
            cmd, cwd=str(workspace_root),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return False, f"command not found: {e}"

    if result.returncode == 0:
        return True, "ok"

    output = (result.stdout + result.stderr).strip()
    if len(output) > 400:
        output = "..." + output[-400:]
    return False, f"exit {result.returncode}: {output}"


def read_build_json(corp_repo: Path) -> dict:
    """Return parsed BUILD.json from corp_repo, or empty dict on missing/invalid."""
    path = corp_repo / "BUILD.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get_latest_staging_commit(corp_repo: Path) -> str:
    """Return SHA of latest staging commit reachable from local clone, or ''."""
    for ref in ("refs/remotes/origin/staging", "refs/heads/staging", "HEAD"):
        try:
            result = subprocess.run(
                ["git", "rev-parse", ref], cwd=str(corp_repo),
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return ""


def current_branch(corp_repo: Path) -> str:
    """Return the corporate clone's current branch name, or ''."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(corp_repo),
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def ensure_on_staging(corp_repo: Path) -> tuple:
    """Ensure the canary's corporate clone is checked out on `staging`.

    Replaces the retired `workspace-sync.py --branch` auto-track (removed with the
    sync engine, 2026-06-26): in the git-native model the canary's branch is set
    by a plain fetch + checkout here, right before the smoke checks read it.

    Best-effort and non-fatal. On any git failure it returns ``(False, reason)``
    and the caller proceeds -- ``commit_and_push`` still refuses to push if the
    clone did not actually land on ``staging``. A plain checkout (never ``-B``)
    means a local-only commit or a dirty tree is reported, never silently
    discarded. Returns ``(ok, message)``.
    """
    fetch = subprocess.run(
        ["git", "fetch", "origin", "staging"], cwd=str(corp_repo),
        capture_output=True, text=True, timeout=60,
    )
    if fetch.returncode != 0:
        return False, f"git fetch origin staging failed: {fetch.stderr.strip()[:200]}"

    if current_branch(corp_repo) == "staging":
        return True, "already on staging"

    has_local = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "refs/heads/staging"],
        cwd=str(corp_repo), capture_output=True, text=True, timeout=10,
    ).returncode == 0
    cmd = (
        ["git", "checkout", "staging"] if has_local
        else ["git", "checkout", "-b", "staging", "--track", "origin/staging"]
    )
    checkout = subprocess.run(cmd, cwd=str(corp_repo), capture_output=True, text=True, timeout=30)
    if checkout.returncode != 0:
        return False, f"git checkout staging failed: {checkout.stderr.strip()[:200]}"
    return True, "switched to staging"


def write_status(corp_repo: Path, slug: str, payload: dict) -> Path:
    """Write status/canary-{slug}.json into the corporate clone atomically.

    Uses write-to-tmp + os.replace() per the global "no non-atomic state writes"
    rule. A partial write would otherwise leave a corrupt JSON for the CEO
    dashboard to parse on a concurrent read.
    """
    status_dir = corp_repo / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    status_file = status_dir / f"canary-{slug}.json"
    tmp = status_file.with_suffix(status_file.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, status_file)
    return status_file


def commit_and_push(corp_repo: Path, status_file: Path, slug: str) -> tuple:
    """Stage, commit, push the status file to origin/staging.

    Returns (success, message). Refuses to push if the clone is not on staging
    (defensive - ensure_on_staging() should already have switched it).
    """
    branch = current_branch(corp_repo)
    if branch != "staging":
        return False, f"corporate clone is on '{branch}', refusing to push status"

    pull = subprocess.run(
        ["git", "pull", "--rebase", "origin", "staging"], cwd=str(corp_repo),
        capture_output=True, text=True, timeout=60,
    )
    if pull.returncode != 0:
        return False, f"git pull --rebase failed: {pull.stderr.strip()[:300]}"

    rel = status_file.relative_to(corp_repo).as_posix()
    add = subprocess.run(
        ["git", "add", rel], cwd=str(corp_repo),
        capture_output=True, text=True, timeout=15,
    )
    if add.returncode != 0:
        return False, f"git add failed: {add.stderr.strip()[:300]}"

    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=str(corp_repo),
    )
    if diff.returncode == 0:
        return True, "status unchanged - nothing to commit"

    commit = subprocess.run(
        ["git", "commit", "-m", f"canary status: {slug}"], cwd=str(corp_repo),
        capture_output=True, text=True, timeout=30,
    )
    if commit.returncode != 0:
        return False, f"git commit failed: {commit.stderr.strip()[:300]}"

    push = subprocess.run(
        ["git", "push", "origin", "staging"], cwd=str(corp_repo),
        capture_output=True, text=True, timeout=120,
    )
    if push.returncode != 0:
        return False, f"git push failed: {push.stderr.strip()[:300]}"

    return True, "pushed to origin/staging"


def main():
    parser = argparse.ArgumentParser(
        description="Canary post-sync smoke tests + status transport.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run checks and print status payload; do not write/commit/push",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Bypass the canary guard (for testing on non-canary workspaces)",
    )
    args = parser.parse_args()

    workspace_root = get_workspace_root()
    identity = get_workspace_identity()

    # M6 guard: hard-skip on non-canary workspaces. Layer 1 ships this script
    # to every exec via corporate sync; only the designated canary should run it.
    if not args.force and not identity.get("canary", False):
        print("Not a canary workspace -- skipping.")
        sys.exit(0)

    slug = identity.get("slug", "unknown")
    print(f"{BOLD}Canary smoke run{RESET} (slug: {slug})")

    corp_repo = get_corporate_repo_path()
    if not corp_repo.exists():
        print(f"{RED}Corporate repo not found at {corp_repo}{RESET}")
        sys.exit(1)

    # Ensure the canary clone tracks `staging` before the checks read the branch
    # (replaces the retired workspace-sync.py --branch auto-track). Only the real
    # canary mutates its branch; a --force test run on a non-canary clone skips
    # this so it never retargets someone else's corporate clone.
    if identity.get("canary", False):
        ok, msg = ensure_on_staging(corp_repo)
        print(f"  branch: {msg}" if ok else f"  {YELLOW}branch: {msg}{RESET}")

    failures = []
    for check_id, cmd_args in SMOKE_CHECKS:
        print(f"  -> {check_id} ... ", end="", flush=True)
        success, summary = run_check(workspace_root, cmd_args)
        if success:
            print(f"{GREEN}ok{RESET}")
        else:
            print(f"{RED}FAIL{RESET}")
            print(f"     {GRAY}{summary}{RESET}")
            failures.append({"check": check_id, "summary": summary})

    smoke_status = "healthy" if not failures else "canary-blocked"

    build_data = read_build_json(corp_repo)
    payload = {
        "slug": slug,
        "last_pull_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "latest_staging_commit": get_latest_staging_commit(corp_repo),
        "smoke_status": smoke_status,
        "smoke_failures": failures,
        # Eval fields stubbed for Layer 1. Layer 3's canary-eval.py overwrites these
        # before the status push when it runs in tandem with this script.
        "eval_status": "eval-unavailable",
        "eval_pass_count": 0,
        "eval_total_count": 0,
        "eval_failures": [],
        "month_to_date_spend_usd": 0.0,
        "canary_build": build_data.get("build", 0),
    }

    print()
    print(f"{BOLD}Status:{RESET} {smoke_status} ({len(failures)} failure(s))")

    if args.dry_run:
        print("DRY-RUN -- status payload:")
        print(json.dumps(payload, indent=2))
        sys.exit(0 if smoke_status == "healthy" else 1)

    status_file = write_status(corp_repo, slug, payload)
    print(f"Wrote {status_file.relative_to(corp_repo).as_posix()}")

    success, message = commit_and_push(corp_repo, status_file, slug)
    if success:
        print(f"{GREEN}{message}{RESET}")
    else:
        print(f"{YELLOW}status not pushed: {message}{RESET}")

    # Exit code reflects smoke status only. Push failures are surfaced but do not
    # propagate to the exit code - the canary's hourly job should not be marked
    # failed just because origin was momentarily unreachable.
    sys.exit(0 if smoke_status == "healthy" else 1)


if __name__ == "__main__":
    main()
