#!/usr/bin/env python3
"""Materialise the HEADING OS data overlay (`../.heading-os-data`).

Sibling of `build_engine_repo.py`. Enumerates every TRACKED file in this
workspace, classifies each via the routing map, and copies the `private` AND
`corporate`-routed files into a fresh `../.heading-os-data`, preserving the tree
(Plan 4 D1/M1: corporate content lives in the data overlay and is published OUT
to heading-os-corporate by /publish-corporate). Engine-routed files are never copied.

Writes `.schema-version` (= DATA_SCHEMA_VERSION) so the engine's schema handshake
can detect a stale data format. Fresh git history, no remote, no push.

Snake_case because a test imports its `partition`; also runnable as a CLI.

Usage:
  python scripts/build_data_repo.py --dry-run     # report partition + target, copy nothing
  python scripts/build_data_repo.py               # build ../.heading-os-data (refuses if non-empty)
  python scripts/build_data_repo.py --target DIR  # override target location
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, GREEN, RED, RESET, YELLOW
from scripts.utils.paths import DATA_SCHEMA_VERSION
from scripts.utils.workspace import get_routing_destination, get_workspace_root

# Destinations that belong in the data overlay (everything that is NOT engine).
_DATA_DESTS = {"private", "corporate"}


def _tracked_files(root: Path) -> list[str]:
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the HEADING OS data overlay")
    ap.add_argument("--dry-run", action="store_true", help="Report only; copy nothing.")
    ap.add_argument("--target", help="Target dir (default: ../.heading-os-data).")
    args = ap.parse_args()

    root = get_workspace_root()
    target = Path(args.target).resolve() if args.target else (root.parent / ".heading-os-data")

    buckets = partition(root)
    data_files = sorted(buckets["private"] + buckets["corporate"])

    print(f"{BOLD}HEADING OS data overlay build{RESET}")
    print(f"  source : {root}")
    print(f"  target : {target}")
    print(f"  data   : {len(data_files)}  (private {len(buckets['private'])} + "
          f"corporate {len(buckets['corporate'])})   engine(excluded): {len(buckets['engine'])}")

    if args.dry_run:
        print(f"{YELLOW}  dry-run: nothing copied.{RESET}")
        return 0

    if target.exists() and any(target.iterdir()):
        print(f"{RED}  REFUSING: {target} exists and is non-empty (no clobber).{RESET}")
        return 1
    target.mkdir(parents=True, exist_ok=True)

    import shutil
    copied = 0
    for rel in data_files:
        src = root / rel
        if not src.is_file():
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    # Schema marker for the engine's compatibility handshake.
    (target / ".schema-version").write_text(f"{DATA_SCHEMA_VERSION}\n", encoding="utf-8")

    def _cfg(key: str) -> str:
        r = subprocess.run(["git", "config", key], cwd=str(root),
                            capture_output=True, text=True)
        return r.stdout.strip()

    name = _cfg("user.name") or "HEADING OS"
    email = _cfg("user.email") or "noreply@example.com"
    subprocess.run(["git", "init", "-q"], cwd=str(target), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(target), check=True)
    subprocess.run(
        ["git", "-c", f"user.name={name}", "-c", f"user.email={email}",
         "commit", "-q", "--no-verify",
         "-m", "feat: HEADING OS data overlay — initial import"],
        cwd=str(target), check=True,
    )

    print(f"{GREEN}  built: {copied} files copied, .schema-version={DATA_SCHEMA_VERSION}, "
          f"fresh history, no remote.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
