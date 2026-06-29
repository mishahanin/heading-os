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
    """
    dir_a.mkdir(parents=True, exist_ok=True)
    dir_b.mkdir(parents=True, exist_ok=True)
    names = {p.name for p in dir_a.glob("*.md")} | {p.name for p in dir_b.glob("*.md")}
    a_upd = b_upd = 0
    for name in sorted(names):
        fa, fb = dir_a / name, dir_b / name
        if fa.exists() and not fb.exists():
            shutil.copy2(fa, fb)
            b_upd += 1
        elif fb.exists() and not fa.exists():
            shutil.copy2(fb, fa)
            a_upd += 1
        else:
            if fa.read_bytes() == fb.read_bytes():
                continue
            if fa.stat().st_mtime >= fb.stat().st_mtime:
                shutil.copy2(fa, fb)
                b_upd += 1
            else:
                shutil.copy2(fb, fa)
                a_upd += 1
    return a_upd, b_upd


def _native_from_hook(data: dict) -> Path | None:
    """Resolve the native harness memory dir from SessionStart hook input.

    Prefer transcript_path (authoritative: its parent IS the project dir). Fall back
    to deriving the project-hash from cwd the way Claude Code does (each '/' and '.'
    in the absolute path becomes '-')."""
    tp = data.get("transcript_path")
    if tp:
        return Path(tp).expanduser().parent / "memory"
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
