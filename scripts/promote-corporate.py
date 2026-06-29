#!/usr/bin/env python3
"""R16 Layer 2 -- gated promotion of the corporate `staging` branch to `main`.

The two-stage propagation (design: plans/2026-05-15-corporate-staging-branch-and-canary-exec.md)
routes every CEO publish to `staging` first, where the canary exec soaks and
smoke-tests it. This script is the gate from `staging` to `main`:

  1. resolve the canary's status (status/canary-{slug}.json in the corporate repo)
  2. soak gate    -- >= SOAK_HOURS since the latest staging commit (M1; soak resets per commit)
  3. freshness    -- the canary pulled the latest staging commit (M3)
  4. smoke gate   -- canary smoke_status == "healthy" (M2)
  5. eval status  -- WARNING only, never blocks (M4)
  6. merge `staging` -> `main` with --ff-only (preserves the canary-tested BUILD.json; H1)
  7. --force bypasses 2-4 with a typed risk-acknowledgement, logged to BUILD.json.history

This NEVER bumps BUILD.json -- the staging build is the canonical, canary-tested
integer and is preserved verbatim through the fast-forward merge.

Usage:
    python scripts/promote-corporate.py [--dry-run] [--force] [--yes] [--no-gpg-sign]

Exit codes: 0 ok | 3 not-admin | 4 corp-repo-missing | 5 config-invalid
            8 smoke-blocked | 9 canary-stale | 10 soak-incomplete | 11 merge-conflict
            12 push-failed | 13 no-canary-configured
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_workspace_root, get_corporate_repo_path  # noqa: E402
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.git_push import supervised_push  # noqa: E402

SOAK_HOURS = 4
WORKSPACE_ROOT = get_workspace_root()

# Gate-result keys that name a blocking condition; the typed --force confirmation
# must match one of these.
BLOCK_REASONS = ("soak-incomplete", "canary-stale", "smoke-blocked")


# ============================================================
# Identity / repo gates
# ============================================================
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


def verify_corporate_repo(corp: Path) -> None:
    if not corp.exists() or not (corp / ".git").exists():
        print(f"{RED}ERROR: corporate repo not found / not a git repo at {corp}.{RESET}",
              file=sys.stderr)
        sys.exit(4)


# ============================================================
# Pure helpers (no git) -- the testable core
# ============================================================
def _parse_iso(s: str):
    """Parse an ISO timestamp to an aware datetime (UTC if naive). None on failure."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def resolve_canary_slug(registry: dict) -> str | None:
    """Return the slug of the entry flagged canary=true, or None."""
    execs = registry.get("executives") or registry.get("execs") or []
    if isinstance(execs, dict):
        execs = list(execs.values())
    for entry in execs:
        if isinstance(entry, dict) and entry.get("canary") is True:
            return entry.get("slug")
    return None


def evaluate_gates(now, latest_commit_at, status: dict, soak_hours: int = SOAK_HOURS) -> dict:
    """Pure gate evaluation. ``now`` and ``latest_commit_at`` are aware datetimes;
    ``status`` is the canary status dict. Returns a result with per-gate booleans,
    a blocked flag, and the list of blocking reason names (subset of BLOCK_REASONS)."""
    commit_dt = latest_commit_at if isinstance(latest_commit_at, datetime) else _parse_iso(latest_commit_at)
    now_dt = now if isinstance(now, datetime) else _parse_iso(now)

    soak = (now_dt - commit_dt) if (now_dt and commit_dt) else None
    soak_hours_elapsed = round(soak.total_seconds() / 3600, 1) if soak else None
    soak_ok = soak is not None and soak >= timedelta(hours=soak_hours)

    last_pull = _parse_iso(status.get("last_pull_at", ""))
    canary_fresh = bool(last_pull and commit_dt and last_pull >= commit_dt)

    smoke_status = status.get("smoke_status")
    smoke_ok = smoke_status == "healthy"

    eval_status = status.get("eval_status", "eval-unavailable")

    reasons = []
    if not soak_ok:
        reasons.append("soak-incomplete")
    if not canary_fresh:
        reasons.append("canary-stale")
    if not smoke_ok:
        reasons.append("smoke-blocked")

    return {
        "soak_hours_elapsed": soak_hours_elapsed,
        "soak_required": soak_hours,
        "soak_ok": soak_ok,
        "canary_fresh": canary_fresh,
        "smoke_ok": smoke_ok,
        "smoke_status": smoke_status,
        "eval_status": eval_status,           # warning-only
        "eval_failures": status.get("eval_failures", []),
        "blocked": bool(reasons),
        "reasons": reasons,
    }


# ============================================================
# Git operations (real)
# ============================================================
def _git(args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=check)


def latest_staging_commit(corp: Path):
    """Return (sha, aware-datetime) of the tip of origin/staging (fetched first)."""
    _git(["fetch", "origin", "staging", "main"], corp, check=False)
    sha = _git(["rev-parse", "origin/staging"], corp).stdout.strip()
    iso = _git(["show", "-s", "--format=%cI", sha], corp).stdout.strip()
    return sha, _parse_iso(iso)


def read_status(corp: Path, slug: str) -> dict:
    path = corp / "status" / f"canary-{slug}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def read_build(corp: Path) -> dict:
    path = corp / "BUILD.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def append_force_history(corp: Path, reason: str, build: dict) -> None:
    """Append a force-promote audit entry to BUILD.json.history."""
    history = build.get("history", [])
    history.append({
        "event": "force-promote",
        "reason": reason,
        "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "build": build.get("build"),
    })
    build["history"] = history
    tmp = corp / "BUILD.json.tmp"
    tmp.write_text(json.dumps(build, indent=4) + "\n", encoding="utf-8")
    tmp.replace(corp / "BUILD.json")


def do_promote(corp: Path, gpg_sign: bool = True) -> int:
    """Fast-forward merge staging -> main and push. Returns an exit code."""
    _git(["checkout", "main"], corp)
    merge = _git(["merge", "origin/staging", "--ff-only"], corp, check=False)
    if merge.returncode != 0:
        print(f"{RED}ERROR: ff-only merge failed (staging is not a fast-forward of main):\n"
              f"{merge.stderr}{RESET}", file=sys.stderr)
        return 11
    # Supervised + verified push: a bare push can exit 0 without advancing
    # origin/main (the documented "bare push silently fails" case), which would
    # report PROMOTION COMPLETE while execs never receive the canary-tested BUILD.
    # The watchdog also bounds an indefinite network stall instead of hanging.
    v = supervised_push(corp, stall_window=120, label="promote-push")
    if v["state"] != "ok":
        print(f"{RED}ERROR: push to origin/main {v['state']}: {v['reason']}{RESET}",
              file=sys.stderr)
        return 12
    return 0


# ============================================================
# CLI
# ============================================================
def _print_report(slug, sha, gates, build):
    print(f"\n{BOLD}Promotion Gate Report{RESET}")
    print(f"  Canary: {slug}   staging tip: {sha[:10]}   build: {build.get('build')}")
    print(f"  Soak: {gates['soak_hours_elapsed']}h (need {gates['soak_required']}h)")

    def mark(ok):
        return f"{GREEN}ok{RESET}" if ok else f"{RED}FAIL{RESET}"

    print(f"  [{mark(gates['soak_ok'])}] soak time")
    print(f"  [{mark(gates['canary_fresh'])}] canary fresh (pulled latest staging)")
    print(f"  [{mark(gates['smoke_ok'])}] smoke tests ({gates['smoke_status']})")
    print(f"  [{YELLOW}warn{RESET}] eval status: {gates['eval_status']} (advisory, never blocks)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Promote corporate staging -> main (R16 Layer 2).")
    ap.add_argument("--dry-run", action="store_true", help="evaluate gates, do not merge")
    ap.add_argument("--force", action="store_true", help="bypass blocking gates with typed confirmation")
    ap.add_argument("--yes", action="store_true", help="skip the final interactive confirm (gates still apply)")
    ap.add_argument("--no-gpg-sign", action="store_true", help="merge without GPG signing (testing/unblocking)")
    args = ap.parse_args(argv)

    verify_admin_identity()
    corp = get_corporate_repo_path()
    verify_corporate_repo(corp)

    try:
        registry = json.loads((WORKSPACE_ROOT / "config" / "exec-registry.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"{RED}ERROR: exec-registry.json unreadable: {exc}{RESET}", file=sys.stderr)
        return 5
    slug = resolve_canary_slug(registry)
    if not slug:
        print(f"{RED}ERROR: no exec is flagged canary=true in exec-registry.json. "
              f"Cannot gate a promotion without a canary.{RESET}", file=sys.stderr)
        return 13

    sha, commit_at = latest_staging_commit(corp)
    status = read_status(corp, slug)
    build = read_build(corp)
    now = datetime.now(timezone.utc).astimezone()
    gates = evaluate_gates(now, commit_at, status)

    _print_report(slug, sha, gates, build)

    if args.dry_run:
        print(f"\n{GRAY}--dry-run: no merge performed.{RESET}")
        return 0

    if gates["blocked"]:
        reasons = ", ".join(gates["reasons"])
        if not args.force:
            print(f"\n{RED}BLOCKED by: {reasons}. Re-run with --force to override.{RESET}",
                  file=sys.stderr)
            code = {"smoke-blocked": 8, "canary-stale": 9, "soak-incomplete": 10}
            return next((code[r] for r in gates["reasons"] if r in code), 8)
        # --force: require typed confirmation of a failing reason.
        primary = gates["reasons"][0]
        print(f"\n{YELLOW}Force-promote despite: {reasons}.{RESET}")
        typed = input(f"Type the failing flag to confirm ({primary}): ").strip()
        if typed not in gates["reasons"]:
            print(f"{RED}Confirmation '{typed}' does not match a failing gate. Aborted.{RESET}",
                  file=sys.stderr)
            return 1
        append_force_history(corp, typed, build)
        print(f"{YELLOW}force-promote logged to BUILD.json.history.{RESET}")
    elif not args.yes:
        if input("\nPromote staging -> main? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    rc = do_promote(corp, gpg_sign=not args.no_gpg_sign)
    if rc == 0:
        print(f"\n{GREEN}{BOLD}PROMOTION COMPLETE{RESET}  build {build.get('build')} "
              f"merged staging -> main (ff-only), pushed origin/main.")
        print(f"{GRAY}Execs receive this on their next hourly sync.{RESET}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
