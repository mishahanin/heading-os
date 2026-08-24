#!/usr/bin/env python3
"""Publish ceo-main code/config to the downstream service-host repo.

Copies the allowlisted paths from the private config/service-manifest.json into
the downstream service-host repo clone (a sibling dir, named by the manifest's
downstream_repo), writes a build marker, and commits. That downstream repo is
the filtered mirror the managed service-host VM pulls from. Allowlist only -
anything not named in the manifest never reaches the VM.

Default behaviour is commit-only, so the changeset can be reviewed before
it leaves the laptop (mirrors the corporate publish convention). Pass
--push to also push to origin/main.

Usage:
    python scripts/publish-service.py            # copy + commit locally
    python scripts/publish-service.py --push     # copy + commit + push
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_workspace_root, get_data_config_dir
from scripts.utils.atomic import atomic_write_text
from scripts.utils.rmtree import rmtree_force
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET
from scripts.utils.git_push import supervised_push

STATIC_IGNORE_PATTERNS = ("__pycache__", "*.pyc", "*.pyo", ".venv*", ".pytest_cache")


def load_manifest(workspace: Path) -> tuple[list[str], list[str], str]:
    # The manifest is per-instance config-DATA (the publish allowlist + the
    # downstream repo name), so it resolves under the data root, not the engine.
    manifest_path = get_data_config_dir() / "service-manifest.json"
    if not manifest_path.exists():
        print(f"{RED}Manifest not found: {manifest_path}{RESET}")
        sys.exit(1)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    includes = manifest.get("include", [])
    exclude_names = manifest.get("exclude_names", [])
    downstream_repo = manifest.get("downstream_repo", "")
    if not includes:
        print(f"{RED}Manifest has no 'include' entries.{RESET}")
        sys.exit(1)
    if not downstream_repo:
        print(f"{RED}Manifest has no 'downstream_repo' (the sibling repo dir name).{RESET}")
        sys.exit(1)
    return includes, exclude_names, downstream_repo


def _contained(base: Path, rel: str) -> Path:
    """`base / rel`, refused unless it stays under `base`.

    `include` entries come from a hand-maintained manifest and were joined onto
    the destination with no normalisation, so `../../x` or an absolute path
    wrote outside the downstream clone -- and because directories are rmtree'd
    before the copy, it was a delete primitive out there too.
    """
    joined = (base / rel).resolve()
    root = base.resolve()
    if joined != root and root not in joined.parents:
        raise ValueError(f"manifest include {rel!r} escapes {base}")
    return joined


def copy_includes(workspace: Path, dest: Path, includes: list[str], exclude_names: list[str]) -> None:
    ignore = shutil.ignore_patterns(*STATIC_IGNORE_PATTERNS, *exclude_names)
    for rel in includes:
        src = _contained(workspace, rel)
        dst = _contained(dest, rel)
        if not src.exists():
            print(f"  {YELLOW}skip (not in source yet): {rel}{RESET}")
            continue
        if src.is_dir():
            if dst.exists():
                rmtree_force(dst)
            shutil.copytree(src, dst, ignore=ignore)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        print(f"  {GREEN}{rel}{RESET}")


def write_build_marker(dest: Path) -> int:
    marker = dest / "SERVICE-BUILD.json"
    build = 1
    if marker.exists():
        try:
            build = int(json.loads(marker.read_text(encoding="utf-8")).get("build", 0)) + 1
        except (json.JSONDecodeError, ValueError):
            build = 1
    # Atomic: a torn SERVICE-BUILD.json makes the NEXT run's json.loads fail,
    # which silently resets the build counter to 1.
    atomic_write_text(
        marker,
        json.dumps(
            {"build": build, "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
            indent=2,
        )
        + "\n",
    )
    return build


SCANNER = Path(__file__).resolve().parent / "secret-scanner.py"


def secret_scan(dest: Path) -> bool:
    """Scan every tracked-or-new file in the downstream clone before committing.

    This is a SECOND publication path out of the workspace and it had no gate at
    all: the manifest is hand-maintained config, so one secret-bearing file
    added to `include` propagated to the VM-pullable mirror with no friction --
    exactly the leak class push-all.py's walls exist to stop.
    """
    listing = subprocess.run(
        ["git", "-C", str(dest), "ls-files", "-z", "--cached", "--others",
         "--exclude-standard"],
        capture_output=True, text=True)
    files = [f for f in listing.stdout.split("\0") if f]
    if not files:
        return True
    proc = subprocess.run(
        [sys.executable, str(SCANNER), "--stdin"],
        cwd=str(dest), input="\n".join(sorted(files)),
        capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        reason = ("secret-like CONTENT in a file about to be published"
                  if proc.returncode == 1 else "secret-scanner error")
        print(f"{RED}REFUSING TO PUBLISH -- {reason}.{RESET}")
        return False
    return True


def git(dest: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(dest), *args], capture_output=True, text=True)


def publish(dest: Path, push: bool) -> int:
    status = git(dest, "status", "--porcelain")
    if not status.stdout.strip():
        print(f"{GRAY}No changes to publish.{RESET}")
        return 0

    print(f"{CYAN}Changed in downstream service-host repo:{RESET}")
    for line in status.stdout.strip().splitlines():
        print(f"  {line}")

    if not secret_scan(dest):
        return 2

    build = write_build_marker(dest)
    git(dest, "add", "-A")
    commit = git(dest, "commit", "-m", f"service-host: publish build {build}")
    if commit.returncode != 0:
        print(f"{RED}Commit failed:{RESET}\n{commit.stderr}")
        return 1
    print(f"{GREEN}Committed build {build}.{RESET}")

    if push:
        # Supervised, not bare. `git push` can exit 0 without advancing the ref
        # -- the failure mode push-all.py documents and guards against -- and
        # this is the second publication path out of the workspace.
        verdict = supervised_push(dest, branch="main", stall_window=120,
                                  label="publish-service")
        if verdict["state"] != "ok":
            print(f"{RED}Push failed ({verdict['state']}):{RESET} {verdict['reason']}")
            return 1
        print(f"{GREEN}Pushed build {build} to origin/main.{RESET}")
    else:
        print(f"{GRAY}Committed locally. Review:  git -C {dest} show{RESET}")
        print(f"{GRAY}Push when ready:           git -C {dest} push origin main{RESET}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish ceo-main code to the downstream service-host repo.")
    parser.add_argument("--push", action="store_true",
                        help="Also push to origin/main (default: commit locally only).")
    args = parser.parse_args()

    workspace = get_workspace_root()
    includes, exclude_names, downstream_repo = load_manifest(workspace)
    dest = workspace.parent / downstream_repo
    if not (dest / ".git").exists():
        print(f"{RED}Downstream service-host repo clone not found at {dest}{RESET}")
        print(f"{GRAY}Create the GitHub repo and clone it there (as a sibling dir) first.{RESET}")
        return 1

    print(f"{BOLD}Publishing ceo-main -> {dest}{RESET}")
    copy_includes(workspace, dest, includes, exclude_names)
    return publish(dest, args.push)


if __name__ == "__main__":
    sys.exit(main())
