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

Three layers. The PreToolUse deny is a CONVENIENCE: it sees Write, Edit,
MultiEdit, and NotebookEdit tool calls only, so a shell `sed -i` walks past it.
`verify` is the GUARANTEE, because it recomputes digests from disk. The test gate
is what makes the guarantee FIRE: tests/conftest.py runs it at pytest session
start and scripts/run-tests.py runs it before the suite, and an unrun verify is
worth nothing.

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

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))
from scripts.utils.canopus_freeze import (  # noqa: E402
    ANCHOR_MISSING,
    ANCHOR_NONE,
    ANCHOR_PREFIX,
    ANCHOR_RECORDED,
    ATTESTED,
    LOCK_HELD,
    LOCK_UNCONFIRMED,
    LOSS_OF_LOCK,
    NOT_ATTESTED,
    FreezeCorrupt,
    FreezeError,
    anchor_state,
    append_history,
    attestation_state,
    build_manifest,
    clear_freeze,
    lock_state,
    read_attestation,
    read_freeze,
    validate_anchor_path,
    verify_manifest,
    write_freeze,
)
from scripts.utils.colors import BOLD, GREEN, RED, RESET, YELLOW  # noqa: E402

# The gate script every root must carry. A tree without it has no place where the
# freeze is ever checked, so a freeze taken against it is inert by construction.
GATE_SCRIPT = Path("scripts") / "run-tests.py"


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


def _resolve_root(args) -> Path:
    """Resolve --root and refuse a tree that has no test gate.

    --root defaults to the engine root rather than the shell's cwd. Defaulting
    to the cwd meant `cd tests && python ../scripts/canopus.py freeze ...`
    printed a root hash, exited 0, and wrote the state to tests/.canopus/ —
    where neither the PreToolUse dispatcher nor the gate ever looks. The
    operator was told the lock was on while it was inert, which is the worst
    thing a discipline tool can do.
    """
    root = Path(args.root).resolve()
    if not (root / GATE_SCRIPT).is_file():
        raise FreezeError(
            f"{root} has no {GATE_SCRIPT.as_posix()}; a tree with no test gate "
            f"cannot enforce a freeze, so freezing it would be inert"
        )
    return root


def _under_root(raw: str, root: Path) -> Path:
    """Resolve a CLI path argument against --root, not against the shell's cwd.

    --root exists so the tree being frozen need not be the cwd. Resolving
    relative arguments against the cwd would make `--root ../other freeze
    tests/x.py` fail with "outside the working tree" for a legal invocation.
    """
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else root / candidate


def _print_attestation(root: Path, recomputed_root: str) -> None:
    """The second axis: did the frozen tests actually RUN, and pass?

    The lock line answers "did the contract move". It cannot answer "did the
    contract run": -k, --deselect, --ignore and a bare path argument all reach
    green with every frozen byte intact.

    Reports only, and deliberately: it can never change an exit code, because
    the gate that would act on it runs at pytest session start, before the run
    it would attest has finished. Making it fatal would fail every run on its
    own missing record.
    """
    record = read_attestation(root)
    state, reason = attestation_state(record, recomputed_root)
    if state == ATTESTED:
        counts = [
            entry for entry in (record.get("frozen_tests") or {}).values()
            if isinstance(entry, dict)
        ]
        passed = sum(entry.get("passed", 0) for entry in counts)
        skipped = sum(entry.get("skipped", 0) for entry in counts)
        tail = f", {skipped} skipped" if skipped else ""
        print(f"{GREEN}{BOLD}{ATTESTED}{RESET}  {passed} frozen tests passed, none "
              f"deselected, at "
              f"{record.get('attested_at') or 'an unrecorded time'}{tail}")
        return
    print(f"{YELLOW}{BOLD}{NOT_ATTESTED}{RESET}  {reason}")
    listed = (record or {}).get("reasons")
    if isinstance(listed, list):
        for line in listed[:5]:
            print(f"  reason   {line}")


def cmd_freeze(args) -> int:
    root = _resolve_root(args)
    if read_freeze(root) is not None:
        print("canopus: a freeze is already active; run `release` first "
              "(changing a contract reopens the approval gate)", file=sys.stderr)
        return 1
    if not args.paths and not args.content:
        print("canopus: at least one path is required, positionally or via "
              "--content", file=sys.stderr)
        return 1
    manifest = build_manifest(
        [_under_root(p, root) for p in args.paths],
        root,
        label=args.label,
        frozen_at=datetime.now(timezone.utc).isoformat(),
        anchor=_under_root(args.anchor, root),
        content_only=[_under_root(p, root) for p in args.content],
    )
    manifest["git_sha"] = _git_sha(root)
    write_freeze(root, manifest)
    append_history(root, "freeze", digest=manifest["root"], label=manifest["label"])
    _print_root(manifest["root"], manifest)
    print(f"\nPaste this line into {manifest['anchor']} and commit it:\n")
    print(f"    {ANCHOR_PREFIX} {manifest['root']}\n")
    print("Until it is there, verify reports LOCK UNCONFIRMED.")
    return 0


def cmd_verify(args) -> int:
    root = _resolve_root(args)
    manifest = read_freeze(root)
    if manifest is None:
        print("canopus: no active freeze; nothing to verify", file=sys.stderr)
        return 1

    report = verify_manifest(manifest, root)
    override = (
        str(validate_anchor_path(_under_root(args.anchor, root), root))
        if args.anchor else None
    )
    anchor, status, value = anchor_state(manifest, override)
    state = lock_state(report, status, value)

    _print_root(report["recomputed_root"], manifest)

    if state == LOCK_HELD:
        print(f"{GREEN}{BOLD}{LOCK_HELD}{RESET}  matches the hash recorded in {anchor}")
        _print_attestation(root, report["recomputed_root"])
        return 0
    if state == LOCK_UNCONFIRMED:
        detail = ("no anchor was recorded at freeze time" if status == ANCHOR_NONE
                  else f"{anchor} carries no {ANCHOR_PREFIX} line yet")
        print(f"{YELLOW}{BOLD}{LOCK_UNCONFIRMED}{RESET}  {detail}. Nothing changed "
              f"since the last check, which is NOT the same as 'this is the "
              f"approved contract'.")
        _print_attestation(root, report["recomputed_root"])
        return 0

    print(f"{RED}{BOLD}{LOSS_OF_LOCK}{RESET}")
    for rel in report["changed"]:
        print(f"  changed  {rel}\n           expected {manifest['files'][rel]}")
    for rel in report["added"]:
        print(f"  added    {rel}")
    for rel in report["removed"]:
        print(f"  removed  {rel}")
    if status == ANCHOR_MISSING:
        print(f"  anchor   {anchor} is gone")
    elif status == ANCHOR_RECORDED and value != report["recomputed_root"]:
        print(f"  anchor   {anchor} records {value}")
    _print_attestation(root, report["recomputed_root"])
    print("A contract that is genuinely wrong reopens the approval gate. "
          "It is never edited in place.")
    append_history(root, "verify_fail", digest=report["recomputed_root"],
                   label=manifest["label"], reason=state)
    return 1


def cmd_release(args) -> int:
    root = _resolve_root(args)
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
    root = _resolve_root(args)
    manifest = read_freeze(root)
    if manifest is None:
        print("canopus: no active freeze")
        return 0
    report = verify_manifest(manifest, root)
    anchor, status, value = anchor_state(manifest)
    state = lock_state(report, status, value)
    _print_root(manifest["root"], manifest)
    # The lock line, not just the attestation line. An earlier revision paid for
    # the full verify here and then printed only the manifest's STORED root, so
    # `status` on a moved contract was byte-identical to `status` on an intact
    # one -- the operator was told the lock was on while it was broken, which is
    # the one failure this whole tool exists to prevent. Reporting only: the
    # exit code stays 0 so `status` remains a description of state, and `verify`
    # remains the command that fails.
    colour = {LOCK_HELD: GREEN, LOCK_UNCONFIRMED: YELLOW}.get(state, RED)
    tail = ("  run `canopus verify` for the per-file report"
            if state == LOSS_OF_LOCK else "")
    print(f"{colour}{BOLD}{state}{RESET}{tail}")
    _print_attestation(root, report["recomputed_root"])
    print(f"frozen at {manifest['frozen_at']}")
    print(f"git sha   {manifest.get('git_sha') or '(not a git working tree)'}")
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
    parser.add_argument("--root", default=str(ENGINE_ROOT),
                        help="working tree root (default: this script's own repository "
                             "root, NOT the shell's cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    freeze = sub.add_parser("freeze", help="lock a set of paths")
    freeze.add_argument("paths", nargs="*", default=[],
                        help="files or directories to freeze recursively")
    freeze.add_argument("--content", action="append", default=[], metavar="FILE",
                        dest="content",
                        help="freeze this file's BYTES only, with no composition guard "
                             "on its parent directory (use for the enforcer files)")
    freeze.add_argument("--label", required=True, help="short name for this build")
    # Required, because an anchorless freeze can only ever report LOCK
    # UNCONFIRMED. It still catches a later edit, but it is the one route to a
    # PASSING gate that never leaves the engine clone: release, edit the
    # contract, re-freeze, amber, exit 0. With an anchor that same sequence
    # fails, because the artifact still holds the previously approved hash.
    # Re-baselining is exactly what the anchor exists to make visible.
    freeze.add_argument("--anchor", required=True,
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
    except OSError as exc:
        # Same posture as the test gate: an unreadable member (permissions, a
        # vanished mount) fails the command, it does not traceback. The exit
        # code already failed closed; the layer billed as the guarantee should
        # not present a raw stack trace while doing so.
        print(f"canopus: the frozen contract could not be read, so it cannot be "
              f"verified: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
