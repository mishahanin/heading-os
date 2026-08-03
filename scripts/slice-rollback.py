#!/usr/bin/env python3
"""Put the tree back where the freeze found it, and delete nothing doing it.

Recovery from a slice that failed halfway is manual git surgery today. That is
tolerable while a human is watching and becomes a real hazard the moment the
unattended loop runs: a build that fails at 03:00 leaves a half-written tree
nobody is awake to read, and the next thing to touch it is another automated
step. See `docs/superpowers/specs/2026-08-01-canopus-v2-design.md` §6 A10.

    python scripts/slice-rollback.py            # the plan, changes nothing
    python scripts/slice-rollback.py --apply    # do it
    python scripts/slice-rollback.py --json

Two properties this holds on purpose:

**Nothing is deleted.** Every file it replaces is copied aside first, under
`.logs/rollback/<timestamp>/`, and the path is printed. A recovery tool is
exactly where deletion gets excused as tidiness, and the operator's standing
instruction is that nothing goes without his word.

**Untracked files are named, never moved.** Whether a new file belongs to the
failed slice or to something else entirely is not knowable from here, so the
command reports what it found and leaves it in place. Guessing would be the one
way this tool could destroy work it was written to protect.
"""
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.paths import get_workspace_root, log_dir


def _git(args, root: Path):
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True)


def _read_freeze(root: Path):
    """(manifest, error). Never raises: this runs when things are already bad.

    Falls back to the raw JSON when strict validation refuses the manifest, and
    that fallback is the point rather than a shortcut. Strict validation belongs
    to `canopus verify`, whose job is to decide whether a freeze can be trusted.
    This command's job is to get a half-written tree back, and a slice that
    failed badly enough to need it is exactly the slice whose manifest may be
    the thing that broke. A recovery tool that refuses on a schema mismatch is
    useless in the only situation it exists for.

    Only three fields are read: the label, the commit to restore from, and the
    frozen path list. None of them require the manifest to be valid.
    """
    try:
        from scripts.utils.canopus_freeze import freeze_state_path, read_freeze
    except Exception as exc:
        return None, f"canopus unavailable: {type(exc).__name__}: {exc}"
    state = freeze_state_path(root)
    if not state.exists():
        return None, "no freeze is held, so there is no state to return to"
    try:
        return read_freeze(root), None
    except Exception as strict_error:
        try:
            raw = json.loads(state.read_text(encoding="utf-8"))
        except Exception as exc:
            return None, f"the freeze file is present but unreadable: {exc}"
        if not isinstance(raw, dict) or not raw.get("git_sha"):
            return None, (f"the freeze file is unusable ({strict_error}) and "
                          f"carries no commit to restore from")
        print(f"{YELLOW}The freeze manifest does not validate ({strict_error}). "
              f"Reading it anyway: a broken manifest is often what a failed slice "
              f"leaves behind.{RESET}", file=sys.stderr)
        return raw, None


def _slug(label: str) -> str:
    """A label safe to put in a directory name.

    The manifest is read raw when strict validation refuses it, which is the
    whole point of this command — so the label is untrusted string data, not a
    schema-checked field. Before this, a label carrying `../` sent the preserved
    copies somewhere other than the `saved_to` path the command PRINTS: measured
    2026-08-01, label `../../escape` reported
    `.logs/rollback/<stamp>-../../escape` while the bytes landed in
    `.logs/rollback/escape`. For a tool whose one promise is "nothing is deleted
    and the path is printed", printing the wrong path is the failure.
    """
    safe = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in str(label))
    safe = safe.strip("-.") or "unlabelled"
    return safe[:64]


def _contained(root: Path, base: Path, rel: str):
    """(source, destination) for a frozen path, or None when it escapes.

    Applies the prefix check the workspace security rule requires of any path
    built from data we did not author: resolve, then confirm the result is still
    under the tree it is supposed to be under. An absolute or `../` entry in a
    manifest's file list would otherwise read and write outside the workspace.
    """
    try:
        source = (root / rel).resolve()
        destination = (base / rel).resolve()
        source.relative_to(root.resolve())
        destination.relative_to(base.resolve())
    except (ValueError, OSError):
        return None
    return source, destination


def _frozen_paths(manifest) -> list:
    """Every path the freeze recorded bytes for, contract and enforcer alike.

    Both maps, because this reader asks what a rollback must consider and the
    answer does not depend on which of the two hashes a path feeds. The split
    made an enforcer edit cheap to RE-PIN; it did not make it invisible to a
    rollback.
    """
    paths: set = set()
    for key in ("files", "content"):
        recorded = manifest.get(key) or {}
        paths.update(recorded if isinstance(recorded, dict) else (recorded or []))
    return sorted(paths)


def _resolves(root: Path, sha: str) -> bool:
    """Whether the freeze's commit is reachable in this clone."""
    return _git(["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
                root).returncode == 0


def _drifted(root: Path, sha: str, paths) -> list:
    """Frozen paths whose working copy differs from the freeze's commit.

    `git diff --quiet` answers 0 for same, 1 for differs and >1 for an ERROR,
    so treating every non-zero code as drift made an unreadable commit look like
    total drift — and an unreachable `git_sha` is exactly the broken-manifest
    case this command exists for. The caller checks `_resolves` first; this
    keeps the distinction anyway, because a per-path error is not evidence of a
    change.
    """
    out = []
    for rel in paths:
        proc = _git(["diff", "--quiet", sha, "--", rel], root)
        if proc.returncode == 1:
            out.append(rel)
        elif proc.returncode > 1:
            print(f"{YELLOW}Could not compare {rel} against {sha[:12]}: "
                  f"{proc.stderr.strip()}{RESET}", file=sys.stderr)
    return out


def _untracked(root: Path) -> list:
    proc = _git(["ls-files", "--others", "--exclude-standard"], root)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Return the tree to the held freeze, keeping what it replaces.")
    parser.add_argument("--apply", action="store_true",
                        help="actually restore; without it this only prints the plan")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    root = get_workspace_root()
    manifest, error = _read_freeze(root)
    if manifest is None:
        if args.as_json:
            json.dump({"error": error, "applied": False}, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            print(f"{RED}Cannot roll back: {error}.{RESET}")
        return 2

    label = manifest.get("label") or "(unlabelled)"
    sha = manifest.get("git_sha") or ""
    paths = _frozen_paths(manifest)
    if sha and not _resolves(root, sha):
        reason = (f"the freeze names commit {sha[:12]}, which this clone cannot "
                  f"resolve; there is nothing to restore from")
        if args.as_json:
            json.dump({"error": reason, "label": label, "git_sha": sha,
                       "frozen_paths": paths, "applied": False},
                      sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            print(f"{RED}Cannot roll back: {reason}.{RESET}")
        return 2
    drifted = _drifted(root, sha, paths) if sha else []
    untracked = _untracked(root)

    saved_to = None
    if args.apply and drifted:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        saved_to = log_dir("rollback", f"{stamp}-{_slug(label)}")
        for rel in drifted:
            contained = _contained(root, saved_to, rel)
            if contained is None:
                print(f"{YELLOW}Refusing to touch {rel}: it resolves outside the "
                      f"workspace.{RESET}", file=sys.stderr)
                continue
            source, destination = contained
            if not source.is_file():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        restore = _git(["checkout", sha, "--", *drifted], root)
        if restore.returncode != 0:
            if args.as_json:
                json.dump({"error": restore.stderr.strip(), "applied": False,
                           "saved_to": str(saved_to)}, sys.stdout, indent=2)
                sys.stdout.write("\n")
            else:
                print(f"{RED}Restore failed: {restore.stderr.strip()}{RESET}")
                print(f"{GRAY}Your files are still at {saved_to}.{RESET}")
            return 2

    if args.as_json:
        json.dump({"label": label, "git_sha": sha, "frozen_paths": paths,
                   "drifted": drifted, "untracked": untracked,
                   "applied": bool(args.apply and drifted),
                   "saved_to": str(saved_to) if saved_to else None},
                  sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print(f"{BOLD}Slice{RESET} {label}  {GRAY}frozen at {sha[:12] or '?'}, "
          f"{len(paths)} path(s) under the freeze{RESET}")
    if not drifted:
        print(f"{GREEN}Nothing to roll back: every frozen path matches the "
              f"freeze.{RESET}")
    else:
        verb = "Restored" if args.apply else "Would restore"
        print(f"{YELLOW}{verb} {len(drifted)} drifted path(s):{RESET}")
        for rel in drifted:
            print(f"  {rel}")
        if saved_to:
            print(f"{GRAY}Replaced versions kept at {saved_to}{RESET}")
        elif not args.apply:
            print(f"{GRAY}Nothing was changed. Re-run with --apply to do it.{RESET}")
    if untracked:
        print()
        print(f"{GRAY}{len(untracked)} untracked file(s) left alone (whether they "
              f"belong to this slice is not knowable from here):{RESET}")
        for rel in untracked[:20]:
            print(f"  {GRAY}{rel}{RESET}")
        if len(untracked) > 20:
            print(f"  {GRAY}... and {len(untracked) - 20} more{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
