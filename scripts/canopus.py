#!/usr/bin/env python3
"""Canopus freeze CLI: lock the test contract before a build, verify it after.

The Canopus standard freezes the definition of done before any code exists, so
the builder cannot move the target it is measured against.

    python scripts/canopus.py freeze tests/test_thing.py --label my-slice \\
        --anchor ../my-notes-repo/plans/2026-07-25-pre-impl-my-slice.md
    python scripts/canopus.py verify
    python scripts/canopus.py status
    python scripts/canopus.py release --reason "slice shipped"
    python scripts/canopus.py release --force --reason "manifest damaged"

Three layers. The PreToolUse deny is a CONVENIENCE: it sees Write/Edit tool calls
only, so a shell `sed -i` walks past it. `verify` is the GUARANTEE, because it
recomputes digests from disk. The test gate in scripts/run-tests.py is what makes
the guarantee FIRE, because it runs `verify` before the suite and an unrun verify
is worth nothing.

The expected root hash lives in a committed artifact OUTSIDE the working tree,
and `verify` reads it from there. Nobody types it and nobody compares it by eye.
Point --anchor at a sibling repository with its own history, so a build reaching
for the anchor leaves a commit in a repository it had no reason to touch. That is
not containment; it is a passive, permanent trap that does not depend on anyone
being alert.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.canopus_freeze import (  # noqa: E402
    ANCHOR_PREFIX,
    LOCK_HELD,
    LOCK_UNCONFIRMED,
    LOSS_OF_LOCK,
    FreezeCorrupt,
    FreezeError,
    append_history,
    build_manifest,
    clear_freeze,
    lock_state,
    read_anchor,
    read_freeze,
    verify_manifest,
    write_freeze,
)
from scripts.utils.colors import BOLD, GREEN, RED, RESET, YELLOW  # noqa: E402


def _git_sha(root: Path) -> str:
    """Current HEAD, or "" outside a repository. A secondary anchor only."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"canopus: could not record a git sha: {exc}", file=sys.stderr)
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _print_root(digest: str, manifest: dict) -> None:
    count = len(manifest["files"])
    noun = "file" if count == 1 else "files"
    print(f"{BOLD}CANOPUS  root {digest}{RESET}  "
          f"(label: {manifest['label']}, {count} {noun})")


def _anchor_state(manifest: dict, override: str | None):
    anchor = override or manifest.get("anchor") or ""
    if not anchor:
        return anchor, "none", None
    status, value = read_anchor(Path(anchor))
    return anchor, status, value


def _under_root(raw: str, root: Path) -> Path:
    """Resolve a CLI path argument against --root, not against the shell's cwd.

    --root exists so the tree being frozen need not be the cwd. Resolving
    relative arguments against the cwd would make `--root ../other freeze
    tests/x.py` fail with "outside the working tree" for a legal invocation.
    """
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else root / candidate


def cmd_freeze(args) -> int:
    root = Path(args.root).resolve()
    if read_freeze(root) is not None:
        print("canopus: a freeze is already active; run `release` first "
              "(changing a contract reopens the approval gate)", file=sys.stderr)
        return 1
    manifest = build_manifest(
        [_under_root(p, root) for p in args.paths],
        root,
        label=args.label,
        frozen_at=datetime.now(timezone.utc).isoformat(),
        anchor=Path(args.anchor) if args.anchor else None,
    )
    manifest["git_sha"] = _git_sha(root)
    write_freeze(root, manifest)
    append_history(root, "freeze", digest=manifest["root"], label=manifest["label"])
    _print_root(manifest["root"], manifest)
    if manifest["anchor"]:
        print(f"\nPaste this line into {manifest['anchor']} and commit it:\n")
        print(f"    {ANCHOR_PREFIX} {manifest['root']}\n")
        print("Until it is there, verify reports LOCK UNCONFIRMED.")
    else:
        print("\nNo anchor recorded. verify will only ever report LOCK UNCONFIRMED; "
              "re-freeze with --anchor to get a checkable lock.")
    return 0


def cmd_verify(args) -> int:
    root = Path(args.root).resolve()
    manifest = read_freeze(root)
    if manifest is None:
        print("canopus: no active freeze; nothing to verify", file=sys.stderr)
        return 1

    report = verify_manifest(manifest, root)
    anchor, status, value = _anchor_state(manifest, args.anchor)
    state = lock_state(report, status, value)

    _print_root(report["recomputed_root"], manifest)

    if state == LOCK_HELD:
        print(f"{GREEN}{BOLD}{LOCK_HELD}{RESET}  matches the hash recorded in {anchor}")
        return 0
    if state == LOCK_UNCONFIRMED:
        detail = ("no anchor was recorded at freeze time" if status == "none"
                  else f"{anchor} carries no {ANCHOR_PREFIX} line yet")
        print(f"{YELLOW}{BOLD}{LOCK_UNCONFIRMED}{RESET}  {detail}. Nothing changed "
              f"since the last check, which is NOT the same as 'this is the "
              f"approved contract'.")
        return 0

    print(f"{RED}{BOLD}{LOSS_OF_LOCK}{RESET}")
    for rel in report["changed"]:
        print(f"  changed  {rel}\n           expected {manifest['files'][rel]}")
    for rel in report["added"]:
        print(f"  added    {rel}")
    for rel in report["removed"]:
        print(f"  removed  {rel}")
    if status == "missing":
        print(f"  anchor   {anchor} is gone")
    elif status == "recorded" and value != report["recomputed_root"]:
        print(f"  anchor   {anchor} records {value}")
    print("A contract that is genuinely wrong reopens the approval gate. "
          "It is never edited in place.")
    append_history(root, "verify_fail", digest=report["recomputed_root"],
                   label=manifest["label"], reason=state)
    return 1


def cmd_release(args) -> int:
    root = Path(args.root).resolve()
    if args.force:
        # Never parses the manifest: this is the escape FROM an unparseable one.
        append_history(root, "force_release", digest="", label="", reason=args.reason)
        clear_freeze(root)
        print("Force-released and logged. A forced release is a normal, recorded "
              "event; deleting the file by hand is not.")
        return 0
    manifest = read_freeze(root)
    if manifest is None:
        print("canopus: no active freeze to release", file=sys.stderr)
        return 1
    append_history(root, "release", digest=manifest["root"],
                   label=manifest["label"], reason=args.reason)
    clear_freeze(root)
    print(f"Released {manifest['root']} (label: {manifest['label']})")
    return 0


def cmd_status(args) -> int:
    root = Path(args.root).resolve()
    manifest = read_freeze(root)
    if manifest is None:
        print("canopus: no active freeze")
        return 0
    _print_root(manifest["root"], manifest)
    print(f"frozen at {manifest['frozen_at']}")
    print(f"git sha   {manifest.get('git_sha') or '(not a git working tree)'}")
    anchor, status, value = _anchor_state(manifest, None)
    print(f"anchor    {anchor or '(none)'}  [{status}]")
    for rel in manifest["files"]:
        print(f"  file  {rel}")
    for rel, entry in manifest["dirs"].items():
        print(f"  dir   {rel}/  ({entry['mode']})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="canopus",
        description="Freeze the test contract so the builder cannot move the target.",
    )
    parser.add_argument("--root", default=".",
                        help="working tree root (default: current directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    freeze = sub.add_parser("freeze", help="lock a set of paths")
    freeze.add_argument("paths", nargs="+", help="files or directories to freeze")
    freeze.add_argument("--label", required=True, help="short name for this build")
    freeze.add_argument("--anchor", default=None,
                        help="committed artifact OUTSIDE this tree that records the root hash")
    freeze.set_defaults(func=cmd_freeze)

    verify = sub.add_parser("verify", help="recompute and compare against the anchor")
    verify.add_argument("--anchor", default=None,
                        help="override the anchor path recorded in the manifest")
    verify.set_defaults(func=cmd_verify)

    release = sub.add_parser("release", help="clear the active freeze")
    release.add_argument("--reason", default="", help="why the freeze is being released")
    release.add_argument("--force", action="store_true",
                         help="clear a damaged manifest without parsing it; logged")
    release.set_defaults(func=cmd_release)

    status = sub.add_parser("status", help="show the active freeze")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FreezeCorrupt as exc:
        print(f"canopus: {exc}\n"
              f"         Every write is denied while the manifest is damaged. "
              f"Clear it with: python scripts/canopus.py release --force "
              f"--reason \"<why>\"",
              file=sys.stderr)
        return 1
    except FreezeError as exc:
        print(f"canopus: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
