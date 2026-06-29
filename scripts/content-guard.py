#!/usr/bin/env python3
"""Engine CONTENT-leak gate: scan engine-routed files for real-entity tokens.

The routing guards (leak-guard, engine_guard, the push wall) check WHERE a file
goes; this one checks WHAT is inside an engine-routed file. It builds a real-entity
denylist from the private DATA overlay (scripts/utils/content_denylist.py) and
refuses any engine-routed file that carries a real person slug/name, handle,
e-mail, Telegram ID, or a curated company/event/codename.

On a public clone / CI the DATA overlay is absent: the denylist is empty and the
gate no-ops (the only machine that authors AND pushes engine files -- the
operator's -- has the overlay). Annotate a genuine false positive inline with
``content-guard: ok <reason>`` to suppress one line (mirrors ``leak-guard: ok``).

Usage:
  python scripts/content-guard.py --all                 # scan whole engine surface
  python scripts/content-guard.py --files a.py b.md      # scan specific files
  python scripts/content-guard.py --stdin               # newline-delimited paths on stdin

Exit: 0 clean, 1 leak(s) found, 2 internal error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.content_denylist import build_denylist
from scripts.utils.engine_guard import repo_carried_paths
from scripts.utils.workspace import get_data_root, get_routing_destination, get_workspace_root

# Suffixes that are never prose/code we can scan as text.
_BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".pptx", ".docx", ".xlsx",
    ".woff", ".woff2", ".ttf", ".otf", ".ico", ".zip", ".gz", ".db", ".sqlite",
    ".pyc", ".lock",
}


def _engine_text_files(root: Path, candidates) -> list[str]:
    """Keep only engine-routed, non-binary files that exist."""
    out = []
    for rel in candidates:
        rel = rel.replace("\\", "/").lstrip("/")
        if not rel:
            continue
        if get_routing_destination(rel) != "engine":
            continue
        p = root / rel
        if not p.is_file() or p.suffix.lower() in _BINARY_SUFFIXES:
            continue
        out.append(rel)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="HEADING OS engine content-leak gate")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="scan the whole engine surface")
    g.add_argument("--files", nargs="*", help="scan these paths")
    g.add_argument("--stdin", action="store_true", help="read newline-delimited paths from stdin")
    ap.add_argument("--data-root", help="override the DATA overlay path (default: get_data_root())")
    ap.add_argument("--strict", action="store_true",
                    help="also flag bare name-words split from person slugs (noisy; deep-audit only)")
    ap.add_argument("--quiet", action="store_true", help="print nothing on a clean result")
    args = ap.parse_args()

    root = get_workspace_root()

    if args.data_root:
        data_root = Path(args.data_root)
    else:
        try:
            data_root = get_data_root()
        except Exception:
            data_root = None
    # In the pre-cutover single repo (data == engine) the overlay IS the repo, so
    # every real entity would flag the repo against itself. No-op in that mode.
    if data_root is not None and Path(data_root) == root:
        if not args.quiet:
            print(f"{GRAY}content-guard: data root == engine (single repo); skipped.{RESET}")
        return 0

    dl = build_denylist(data_root, strict=args.strict)
    if dl.degraded or not dl.tokens:
        if not args.quiet:
            print(f"{GRAY}content-guard: denylist unavailable (no DATA overlay); skipped.{RESET}")
        return 0

    if args.all:
        candidates = repo_carried_paths(root)
    elif args.stdin:
        candidates = [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
    else:
        candidates = args.files or []

    files = _engine_text_files(root, candidates)

    findings: list[tuple[str, int, str, str]] = []
    for rel in files:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, matched, category in dl.scan_text(text):
            findings.append((rel, lineno, matched, category))

    if findings:
        print(f"{RED}{BOLD}BLOCKED — real-entity content in engine-routed file(s):{RESET}")
        for rel, lineno, matched, category in findings:
            print(f"  {RED}{rel}:{lineno}{RESET}  \"{matched}\"  {GRAY}[{category}]{RESET}")
        print(f"{GRAY}The engine ships no real data. Genericize to a placeholder, move the "
              f"value to the private DATA overlay, or — if it is a true false positive — "
              f"annotate the line with `content-guard: ok <reason>`.{RESET}")
        return 1

    if not args.quiet:
        scope = "engine surface" if args.all else f"{len(files)} file(s)"
        print(f"{GREEN}content-guard: clean{RESET} {GRAY}({scope}; "
              f"{len(dl.tokens)} denylist tokens){RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
