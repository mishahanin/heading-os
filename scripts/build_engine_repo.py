#!/usr/bin/env python3
"""Materialise the HEADING OS engine working tree (`../.heading-os`).

Enumerates every TRACKED file in this workspace, classifies each via the routing
map (`get_routing_destination`), and copies only the `engine`-routed files into a
fresh sibling `../.heading-os`, preserving the tree. The data tree ships solely as
the bundled `examples/**` (which routes engine) plus any `.gitkeep` markers already
tracked — no private or corporate content is ever copied.

The engine repo is born with fresh history (a brand-new `git init` + single commit);
it shares no history with this workspace, so no private data exists even in the past.
No git remote is added and nothing is pushed — creating/pushing the GitHub repo is a
separate, outward-facing, CEO-gated step.

The leak guard's `check-staged` (run with HEADING_OS_ENGINE_REPO=1) is the structural
post-condition: if any non-engine file slipped in, it fails. Run it after this script.

Snake_case because it is imported by tests (the routing partition is unit-tested);
also runnable as a CLI.

Usage:
  python scripts/build_engine_repo.py --dry-run     # report manifest + target, copy nothing
  python scripts/build_engine_repo.py               # build ../.heading-os (refuses if non-empty)
  python scripts/build_engine_repo.py --target DIR  # override target location
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import (
    get_outputs_dir,
    get_routing_destination,
    get_workspace_root,
)

# Data-path tokens (mirror leak-guard) used only for a belt-and-braces assertion
# that nothing engine-routed is real private data (examples/ is the allowed exception).
_DATA_TOKENS = ("crm/contacts", "knowledge/odin-brain", "threads/", "outputs/")  # leak-guard: ok (audit token list, not path construction)


def _tracked_files(root: Path) -> list[str]:
    # core.quotepath=false: emit real UTF-8 paths instead of octal-escaped, quoted
    # ones. Without this, a non-ASCII data path (e.g. Cyrillic-named PDFs under
    # datastore/books) arrives as `"datastore/..."` with a leading quote, fails to
    # match its private/corporate rule, and silently mis-routes to engine.
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        cwd=str(root), capture_output=True, text=True, check=True,
    ).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def partition(root: Path) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {"engine": [], "private": [], "corporate": []}
    for rel in _tracked_files(root):
        buckets[get_routing_destination(rel)].append(rel)
    return buckets


def _suspicious_engine(engine: list[str]) -> list[str]:
    """Engine-routed files that look like real private data (examples/ excluded)."""
    sus = []
    for rel in engine:
        if rel.startswith("examples/"):
            continue
        if any(rel.startswith(t) or ("/" + t) in rel for t in _DATA_TOKENS):
            sus.append(rel)
    return sus


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the HEADING OS engine working tree")
    ap.add_argument("--dry-run", action="store_true", help="Report only; copy nothing.")
    ap.add_argument("--target", help="Target dir (default: ../.heading-os).")
    args = ap.parse_args()

    root = get_workspace_root()
    target = Path(args.target).resolve() if args.target else (root.parent / ".heading-os")

    buckets = partition(root)
    engine = sorted(buckets["engine"])
    sus = _suspicious_engine(engine)

    print(f"{BOLD}HEADING OS engine build{RESET}")
    print(f"  source : {root}")
    print(f"  target : {target}")
    print(f"  engine : {len(engine)}   private: {len(buckets['private'])}   "
          f"corporate: {len(buckets['corporate'])}")
    if sus:
        print(f"{RED}  REFUSING: {len(sus)} engine-routed file(s) look like real private data:{RESET}")
        for s in sus[:25]:
            print(f"    {s}")
        return 1
    print(f"{GREEN}  routing clean: no real data routes to engine (examples/ scaffolding excepted){RESET}")

    if args.dry_run:
        print(f"{YELLOW}  dry-run: nothing copied.{RESET}")
        return 0

    if target.exists() and any(target.iterdir()):
        print(f"{RED}  REFUSING: {target} exists and is non-empty (no clobber).{RESET}")
        return 1
    target.mkdir(parents=True, exist_ok=True)

    copied = 0
    for rel in engine:
        src = root / rel
        if not src.is_file():
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    # Defense-in-depth: the engine ships NO data, and every data read/write must
    # resolve under the DATA root via the get_*_dir() helpers (enforced by
    # tests/test_data_root_no_bypass.py). But a stray runtime write or an accidental
    # `git add .` could still drop private data into the engine clone. Gitignore the
    # data dirs in the engine so such a write can never be committed or pushed —
    # the engine working tree stays clean by construction. (Appended here, not in
    # the source .gitignore, because ceo-main legitimately tracks these dirs until
    # cutover.)
    _DATA_DIR_IGNORES = [
        "", "# HEADING OS: data dirs never belong in the engine (data lives in",
        "# the .heading-os-data sibling; resolved via get_*_dir()).",
        "/threads/", "/crm/", "/outputs/", "/knowledge/", "/context/", "/plans/",
        "/datastore/", "/_archive/",
    ]
    gi = target / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if "HEADING OS: data dirs never belong in the engine" not in existing:
        with open(gi, "a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n".join(_DATA_DIR_IGNORES) + "\n")

    # Fresh history: brand-new repo, single orphan commit. Carry the author from
    # this workspace's git config so the new repo needs no ambient identity.
    def _cfg(key: str) -> str:
        r = subprocess.run(["git", "config", key], cwd=str(root),
                            capture_output=True, text=True)
        return r.stdout.strip()

    author_name = _cfg("user.name") or "HEADING OS"
    author_email = _cfg("user.email") or "noreply@example.com"
    subprocess.run(["git", "init", "-q"], cwd=str(target), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(target), check=True)
    subprocess.run(
        ["git",
         "-c", f"user.name={author_name}", "-c", f"user.email={author_email}",
         "commit", "-q", "--no-verify",
         "-m", "feat: HEADING OS engine — initial import (fresh history)"],
        cwd=str(target), check=True,
    )

    # Build provenance lives under the outputs tree (data) in the SOURCE workspace,
    # never shipped into the engine tree. Resolve via the data-root seam
    # (get_outputs_dir -> .heading-os-data/outputs). Joining the ENGINE root to a
    # data-dir literal instead would drop the manifest into the engine clone -- the
    # exact seam bypass tests/test_data_root_no_bypass.py forbids, and now covers
    # this file (the former blanket exemption hid this very write, 2026-06-28).
    src_manifest = get_outputs_dir() / "operations" / "workspace" / "engine-build-manifest.json"
    src_manifest.parent.mkdir(parents=True, exist_ok=True)
    src_manifest.write_text(
        json.dumps({"engine_count": len(engine), "copied": copied,
                    "target": str(target)}, indent=2),
        encoding="utf-8",
    )

    print(f"{GREEN}  built: {copied} files copied, fresh git history, no remote.{RESET}")
    print(f"  next: cd {target} && HEADING_OS_ENGINE_REPO=1 "
          f"python scripts/leak-guard.py check-staged --files $(git ls-files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
