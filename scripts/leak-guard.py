#!/usr/bin/env python3
"""Leak guard for the HEADING OS engine/data boundary.

Two checks (HEADING OS spec Section 6):
  check-paths   Lint engine source for hardcoded data-path literals authored
                outside the get_*_dir() seam (scripts/utils/workspace.py).
  check-staged  Fail if any staged file routes to private/corporate while in
                the engine repo (gated by an engine-repo marker; inert on ceo-main).

Usage:
  python scripts/leak-guard.py check-paths  --files a.py b.py
  python scripts/leak-guard.py check-staged --files a.md b.md
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_data_root, get_routing_destination, get_workspace_root

# Data-path tokens that must never be hardcoded as string literals in engine code.
# Kept narrow on purpose: directory roots that resolve to private/corporate data.
DATA_PATH_TOKENS = [
    "crm/contacts",
    "knowledge/odin-brain",
    "outputs/",
    "threads/",
    "datastore/operations/tribe/fireside-state",
]

# Files allowed to contain these literals (the seam owns the canonical paths).
# Only .py/.sh members matter — the suffix filter below skips everything else,
# so non-code files do not need listing here (review finding L1).
SEAM_ALLOWLIST = {
    "scripts/utils/workspace.py",
    "scripts/leak-guard.py",
    "scripts/init-data.py",  # owns the canonical data-tree definition (scaffolds it from scratch)
}

# Match a quoted literal that STARTS with a data token — i.e. a path literal
# like "crm/contacts/..." — not a URL or log message that merely CONTAINS the
# substring ("https://x/outputs/y", "writing outputs/report"). Anchoring the
# token to the opening quote removes the URL / log-message false positives
# (review finding M4). A literal that builds a path from a token still starts
# with it: root / "crm/contacts" -> the literal is "crm/contacts".
_LITERAL_RE = re.compile(r"""['"]((?:%s)[^'"]*)['"]""" % "|".join(
    re.escape(t) for t in DATA_PATH_TOKENS
))


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(get_workspace_root()))
    except ValueError:
        return str(path)


def check_paths(files) -> int:
    violations = []
    for f in files:
        p = Path(f)
        rel = _rel(p)
        if rel in SEAM_ALLOWLIST:
            continue
        if p.suffix not in {".py", ".sh"}:
            continue
        # Only lint actual engine code. Test files legitimately embed data-path
        # literals as fixtures; archived scripts under scripts/archive/ are inert
        # dead code (never run, retained for history); and a .py that itself routes
        # to private/corporate (e.g. a throwaway build script inside outputs/) is
        # not shippable engine code — linting any of these for "engine must not
        # hardcode data paths" is wrong.
        if (
            rel.startswith("tests/")
            or rel.startswith("scripts/archive/")
            or get_routing_destination(rel) != "engine"
        ):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            # Inline suppression for legitimate relative-path usages (comparison
            # keys, prefix patterns, f-string display, log/error/regex strings)
            # that are NOT absolute-path construction. Annotate with a reason:
            #   "crm/contacts/",  # leak-guard: ok (relative prefix match)
            if "leak-guard: ok" in line:
                continue
            m = _LITERAL_RE.search(line)
            if m:
                violations.append((rel, n, m.group(1)))
    if violations:
        print("BLOCKED - hardcoded data-path literal(s) outside the get_*_dir() seam:")
        for rel, n, lit in violations:
            print(f"  {rel}:{n}  \"{lit}\"  -> use a get_*_dir() helper from scripts/utils/workspace.py")
        return 1
    return 0


def _in_engine_repo() -> bool:
    """True when this clone is the split-topology engine (data lives in a sibling).

    Auto-detected from the data-root seam: when get_data_root() resolves to a
    DIFFERENT path than the workspace root, we are in the two-part topology and the
    working tree is the engine -- which must stay code-only. The legacy
    HEADING_OS_ENGINE_REPO=1 env var still forces-on as an explicit override, but is
    no longer the SOLE trigger: relying on a hand-set env var is exactly why this
    guard sat inert while four private specs leaked (2026-06-22). Pre-cutover single
    repo (data_root == workspace_root) -> inert, since data is legitimately tracked.
    """
    import os

    if os.environ.get("HEADING_OS_ENGINE_REPO") == "1":
        return True
    try:
        return get_data_root() != get_workspace_root()
    except Exception:
        # Fail-closed: if the seam cannot resolve, assume engine and enforce.
        return True


def check_staged(files) -> int:
    """Fail if a staged file routes to private/corporate, but only in the engine repo.

    Active whenever this clone is the split-topology engine (auto-detected via the
    data-root seam, or forced by HEADING_OS_ENGINE_REPO=1). Inert on a pre-cutover
    single repo where data files are legitimately tracked.
    """
    if not _in_engine_repo():
        return 0
    leaked = []
    for f in files:
        rel = f.replace("\\", "/").lstrip("/")
        if get_routing_destination(rel) in {"private", "corporate"}:
            leaked.append(rel)
    if leaked:
        print("BLOCKED - non-engine content staged into the engine repo:")
        for rel in leaked:
            print(f"  {rel}  -> routes to '{get_routing_destination(rel)}'; belongs in the data/corporate repo")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="HEADING OS leak guard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cp = sub.add_parser("check-paths")
    cp.add_argument("--files", nargs="*", default=[])
    cs = sub.add_parser("check-staged")
    cs.add_argument("--files", nargs="*", default=[])
    args = ap.parse_args()
    if args.cmd == "check-paths":
        return check_paths(args.files)
    if args.cmd == "check-staged":
        return check_staged(args.files)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
