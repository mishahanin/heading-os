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
    if joined == root:
        # `copy_includes` rmtree_force()s a directory include before it
        # copies into it, so an include that RESOLVES TO THE ROOT deletes
        # the destination itself, `.git` and all. MEASURED 2026-08-29:
        # `_contained(dest, ".")` and `_contained(dest, "")` both returned
        # `dest`, and the `joined != root` clause below was what let them.
        # `main`'s `(dest / '.git').exists()` check runs BEFORE this, so it
        # cannot save the clone. No live manifest entry resolves here; one
        # hand edit does.
        raise ValueError(
            f"manifest include {rel!r} IS {base}; a directory include is "
            f"deleted before it is copied, so this would delete the "
            f"destination root")
    if root not in joined.parents:
        raise ValueError(f"manifest include {rel!r} escapes {base}")
    return joined


def downstream_dest(workspace: Path, downstream_repo: str) -> Path:
    """The sibling clone named by the manifest, refused unless it IS a sibling.

    `_contained` exists in this file because manifest values are hand-maintained
    and were trusted; `downstream_repo` comes from the same manifest, in the same
    run, and was joined onto `workspace.parent` with nothing checking it at all.
    `Path('/a/b').parent / '/etc/x'` is `/etc/x`, and `.. / ..` walks out just as
    freely, so a typo'd or mis-templated manifest pointed the whole publish at an
    arbitrary directory that `copy_includes` then rmtree's and overwrites.

    Defence in depth, not an attacker boundary: the manifest is the operator's
    own private config data. The asymmetry is what makes it worth closing.
    """
    if downstream_repo != Path(downstream_repo).name or downstream_repo in ("", ".", ".."):
        raise ValueError(
            f"manifest downstream_repo {downstream_repo!r} must be a plain "
            f"directory name (a sibling of {workspace}), not a path")
    return _contained(workspace.parent, downstream_repo)


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
            # A marker holding valid JSON that is not an object (`[1]`)
            # raised AttributeError on `.get`, which is neither
            # JSONDecodeError nor ValueError, so it walked through the
            # handler written for exactly a corrupted marker. It landed
            # after copy_includes had already rmtree'd and rewritten the
            # mirror, leaving the downstream clone dirty and uncommitted.
            data = json.loads(marker.read_text(encoding="utf-8"))
            build = int(data.get("build", 0)) + 1 if isinstance(data, dict) else 1
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

    The listing exit code is checked because it was not. `git ls-files` writes
    nothing to stdout when it fails, so a failed call parsed to an empty list,
    fell into the `not files` shortcut, and reported the mirror clean without
    ever starting the scanner. main()'s `(dest / '.git').exists()` does not stand
    in front of that: a `.git` GITFILE whose gitdir has been removed satisfies
    the check while every git call under it exits 128. An empty list is only a
    clean verdict when git succeeded in producing it.

    The listing is decoded from BYTES rather than read through subprocess text
    mode. Text mode turns on universal newlines and rewrites every CR byte to
    LF, and `subprocess` offers no `newline=` knob to switch it off, so the `-z`
    here closes only the quoting half of the problem. MEASURED 2026-08-30: two
    tracked files whose names differ only by that byte come back as two names in
    bytes mode and as one under `text=True`. A mistranslated name is a file the
    scanner cannot open, published unscanned - the leak this gate exists for.
    """
    listing = subprocess.run(
        ["git", "-C", str(dest), "ls-files", "-z", "--cached", "--others",
         "--exclude-standard"],
        capture_output=True)
    if listing.returncode != 0:
        sys.stderr.write(listing.stderr.decode("utf-8", "replace"))
        print(f"{RED}REFUSING TO PUBLISH -- cannot list the files to scan in "
              f"{dest} (git ls-files exit {listing.returncode}).{RESET}")
        return False
    decoded = listing.stdout.decode("utf-8", "surrogateescape")
    files = [f for f in decoded.split("\0") if f]
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


PUBLISH_BRANCH = "main"


def branch_objection(dest: Path) -> str | None:
    """The reason this clone must not be published from, or None.

    `publish` commits onto whatever HEAD points at, then pushes and reports
    `origin/main` unconditionally. MEASURED 2026-08-30 on a scratch clone
    checked out to `scratch`: the build commit landed on `scratch`, `main`
    stayed empty, the run exited 0, and it closed by advising
    `git push origin main` -- a push of a branch that does not carry the
    build. With --push the supervised push targets `main` all the same.

    `push-all.py` already treats exactly this as worth gating
    (`branch is '...', expected 'main'`); this publication path had no
    equivalent. Called BEFORE `copy_includes`, so a refusal leaves the
    downstream clone untouched rather than rmtree'd and rewritten.
    """
    proc = git(dest, "rev-parse", "--abbrev-ref", "HEAD")
    if proc.returncode != 0:
        return (f"cannot read the checked-out branch of {dest} "
                f"(git rev-parse exit {proc.returncode}): "
                f"{proc.stderr.strip() or 'no stderr'}")
    branch = proc.stdout.strip()
    if not branch:
        return f"git rev-parse named no branch for {dest}"
    if branch == "HEAD":
        return (f"{dest} has a DETACHED HEAD; a build commit made here reaches "
                f"no branch at all, and the push targets {PUBLISH_BRANCH!r}")
    if branch != PUBLISH_BRANCH:
        return (f"{dest} is on branch {branch!r}, not {PUBLISH_BRANCH!r}; the "
                f"build commit would land on {branch!r} while the run reports "
                f"and pushes {PUBLISH_BRANCH!r}")
    return None


def publish(dest: Path, push: bool) -> int:
    status = git(dest, "status", "--porcelain")
    # A failed `git status` prints nothing to stdout, so the emptiness test below
    # read it as "nothing changed" and returned success. The copy had already
    # landed in the mirror, and the run said 0 over a repo it could not read.
    if status.returncode != 0:
        sys.stderr.write(status.stderr)
        print(f"{RED}Cannot read the downstream repo at {dest} "
              f"(git status exit {status.returncode}).{RESET}")
        return 1
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
    try:
        dest = downstream_dest(workspace, downstream_repo)
    except ValueError as exc:
        print(f"{RED}{exc}{RESET}")
        return 1
    if not (dest / ".git").exists():
        print(f"{RED}Downstream service-host repo clone not found at {dest}{RESET}")
        print(f"{GRAY}Create the GitHub repo and clone it there (as a sibling dir) first.{RESET}")
        return 1
    objection = branch_objection(dest)
    if objection is not None:
        print(f"{RED}REFUSING TO PUBLISH -- {objection}.{RESET}")
        print(f"{GRAY}Check out {PUBLISH_BRANCH} in the downstream clone:  "
              f"git -C {dest} checkout {PUBLISH_BRANCH}{RESET}")
        return 1

    print(f"{BOLD}Publishing ceo-main -> {dest}{RESET}")
    copy_includes(workspace, dest, includes, exclude_names)
    return publish(dest, args.push)


if __name__ == "__main__":
    sys.exit(main())
