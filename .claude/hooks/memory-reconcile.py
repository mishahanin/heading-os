#!/usr/bin/env python3
"""SessionStart hook + CLI: reconcile the native harness memory store with the
canonical data-root auto-memory.

Why this exists
---------------
Claude Code's native file-memory feature loads `MEMORY.md` + individual memories
from `~/.claude/projects/<cwd-hash>/memory/` -- a store keyed to the session's
LAUNCH DIRECTORY. After the HEADING OS engine/data split, the same data is reached
from two launch paths (the transitional `ceo-main`, and the engine clone
`.heading-os`), each of which hashes to a DIFFERENT native store. A memory written
or seeded under one launch path is invisible from the other, so a fresh session in
the new ecosystem loaded an empty/stale store (symptom: wrong name, missing facts).

The canonical, durable home for memory is DATA: `<data-root>/auto-memory/` (lives in
the data repo, survives, indexed by memory-index). The native per-launch store is a
runtime cache. This hook keeps the two in sync, both directions, newest-wins, at every
SessionStart -- so whatever directory a session launches from, its native store is
seeded from canonical, and any memory written during a session is persisted back to
canonical for the next launch (from any path).

No symlinks (CEO directive): the bridge is an explicit per-clone reconcile, not a
filesystem link. Deletions are NOT propagated (a file present on only one side is
copied to the other, never deleted) -- this fails safe against accidental mass-loss;
prune a retired memory on both sides by hand.

Usage:
    # SessionStart hook (reads hook JSON on stdin; resolves native store from
    # transcript_path, canonical from get_data_root()):
    python3 .claude/hooks/memory-reconcile.py

    # CLI (explicit dirs -- used for one-off cutover seeding and tests):
    python3 .claude/hooks/memory-reconcile.py --native DIR --canonical DIR [--quiet]
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def reconcile(dir_a: Path, dir_b: Path) -> tuple[int, int]:
    """Bidirectional newest-wins sync of *.md between two memory dirs.

    Returns (a_updated, b_updated). copy2 preserves mtime so newest-wins is stable
    across repeated runs (an unchanged pair never re-copies). Deletions are never
    propagated.

    An EXACT mtime tie between two differing files goes to `dir_b`, which main()
    always passes as the canonical data-root store. A tie is not a race between
    two edits: a memory edited natively during a session carries the wall clock
    of that edit and is strictly newer. The tie arises when a pair copy2 seeded
    from one mtime then diverged WITHOUT the clock moving, which is what an
    access-metadata bump does by design (see scripts/utils/memory_touch.py --
    it restores mtime so a bump cannot masquerade as a content edit). Resolving
    that toward the durable store keeps the bump; resolving it toward the
    per-launch cache would discard the counter at every SessionStart.
    """
    dir_a.mkdir(parents=True, exist_ok=True)
    dir_b.mkdir(parents=True, exist_ok=True)
    names = {p.name for p in dir_a.glob("*.md")} | {p.name for p in dir_b.glob("*.md")}
    a_upd = b_upd = 0
    for name in sorted(names):
        fa, fb = dir_a / name, dir_b / name
        # Per-entry, not per-run. One unreadable file, a directory that happens
        # to end in `.md`, or a file that vanishes between exists() and
        # read_bytes() used to raise straight out of this loop; main() caught it
        # once and returned, so a single bad entry left every REMAINING memory
        # unsynced. Found by the 2026-08-23 audit. Skipping the entry and
        # continuing syncs the other N-1; failing the run syncs none of them.
        try:
            if fa.exists() and not fb.exists():
                shutil.copy2(fa, fb)
                b_upd += 1
            elif fb.exists() and not fa.exists():
                shutil.copy2(fb, fa)
                a_upd += 1
            else:
                if fa.read_bytes() == fb.read_bytes():
                    continue
                if fa.stat().st_mtime > fb.stat().st_mtime:
                    shutil.copy2(fa, fb)
                    b_upd += 1
                else:
                    shutil.copy2(fb, fa)
                    a_upd += 1
        except OSError as exc:
            print(f"[memory-reconcile] skipped {name}: {exc}", file=sys.stderr)
    return a_upd, b_upd


def _native_from_hook(data: dict) -> Path | None:
    """Resolve the native harness memory dir from SessionStart hook input.

    Prefer transcript_path (authoritative: its parent IS the project dir). Fall back
    to deriving the project-hash from cwd the way Claude Code does (each '/' and '.'
    in the absolute path becomes '-').

    The fallback is POSIX-only, and says so rather than guessing. On Windows
    `Path(cwd).resolve()` yields `C:\\Users\\...`: the backslashes and the drive
    colon are not covered by the two replacements, so the computed slug never
    matched a real store and the hook reconciled against an invented directory,
    creating it. Found by the 2026-08-23 audit.

    Returning None is the right answer there, not a repaired guess. The caller
    already treats None as "nothing to reconcile" and exits 0, and the correct
    Windows slug format is not something this file can verify. transcript_path
    is present in practice, so the fallback is the rare path either way.
    """
    tp = data.get("transcript_path")
    if tp:
        return Path(tp).expanduser().parent / "memory"
    if os.name != "posix":
        print("[memory-reconcile] no transcript_path and the cwd-slug fallback is "
              "POSIX-only; skipping rather than guessing a store path",
              file=sys.stderr)
        return None
    cwd = data.get("cwd") or os.getcwd()
    slug = str(Path(cwd).resolve()).replace("/", "-").replace(".", "-")
    return Path.home() / ".claude" / "projects" / slug / "memory"


def main() -> int:
    ap = argparse.ArgumentParser(description="Reconcile native harness memory with canonical data auto-memory.")
    ap.add_argument("--native", help="native harness memory dir (CLI mode)")
    ap.add_argument("--canonical", help="canonical data auto-memory dir (CLI mode)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.native and args.canonical:
        native = Path(args.native).expanduser()
        canonical = Path(args.canonical).expanduser()
    else:
        # Hook mode: read SessionStart JSON on stdin.
        try:
            data = json.loads(sys.stdin.read() or "{}")
        except (json.JSONDecodeError, ValueError):
            data = {}
        # A payload that is valid JSON but not an object still reaches `.get`.
        # `[]`, `"x"`, `3` and `null` all parse, then raise an uncaught
        # AttributeError. Swept 2026-08-23 across every stdin hook: six crashed
        # on all four shapes. Same defect checkpoint-inject.py fixed on
        # 2026-08-20; the sweep is how the rest were found.
        if not isinstance(data, dict):
            data = {}
        native = _native_from_hook(data)
        try:
            from scripts.utils.workspace import get_data_root
            canonical = get_data_root() / "auto-memory"
        except Exception as e:  # never break the session over a memory sync
            print(f"[memory-reconcile] data-root resolve failed: {e}", file=sys.stderr)
            return 0
        if native is None:
            return 0

    try:
        a_upd, b_upd = reconcile(native, canonical)
    except OSError as e:
        print(f"[memory-reconcile] failed: {e}", file=sys.stderr)
        return 0

    if not args.quiet and (a_upd or b_upd):
        print(f"[memory-reconcile] native +{a_upd}, canonical +{b_upd}  "
              f"({native} <-> {canonical})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
