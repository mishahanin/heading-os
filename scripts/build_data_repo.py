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

from scripts.utils.atomic import atomic_write_text
from scripts.utils.clone_guard import require_main_clone
from scripts.utils.colors import BOLD, GREEN, RED, RESET, YELLOW
from scripts.utils.paths import DATA_SCHEMA_VERSION
from scripts.utils.workspace import get_routing_destination, get_workspace_root

def _tracked_files(root: Path) -> list[str]:
    # `-z`, because `core.quotepath=false` does not cover the class it looks
    # like it covers. That setting suppresses quoting for NON-ASCII bytes only;
    # git quotes and C-escapes a path holding a CONTROL character whatever it
    # says. MEASURED 2026-08-30 in a scratch repository on this machine (ext4,
    # git 2.43.0): a tracked `crm/contacts/leak\na.md` came back from this exact
    # invocation as the literal `"crm/contacts/leak\\na.md"`, double quotes
    # included, and `get_routing_destination` answered `engine` for that string
    # where the real name answers `private`. A tab, a CR, a vertical tab, a `"`
    # and a backslash in a filename all quote the same way. Under `-z` every one
    # of them came back verbatim, and the flag above became a no-op (measured:
    # byte-identical output with and without it, kept only to match
    # `scripts/utils/commit_source.py`).
    #
    # Here the misread routes a private file OUT of the data overlay: it lands
    # in the engine bucket, is never copied, and nothing reports a skip. A real
    # newline additionally survives `.splitlines()` as two half-paths.
    #
    # Bytes, decoded here, rather than any form of subprocess text mode. Two
    # separate reasons, and the second only showed up under measurement.
    #
    # `text=True` with no `encoding=` decodes through the host locale, so the
    # same repository answers differently on a non-UTF-8 machine.
    #
    # Naming an `encoding=` does NOT fix it, because text mode also turns on
    # UNIVERSAL NEWLINES and `subprocess` exposes no `newline=` knob to switch
    # that off. MEASURED 2026-08-30: two tracked files, `docs/leak\rc.md` and
    # `docs/leak\r\nd.md`, came back from `ls-files -z` as the bytes
    # `b'docs/leak\r\nd.md\x00docs/leak\rc.md\x00'` and decoded through
    # `encoding="utf-8", errors="surrogateescape"` to
    # `'docs/leak\nd.md\x00docs/leak\nc.md\x00'` - every CR silently rewritten
    # to LF, and the CRLF name a byte shorter than the file it names. That is
    # the same failure as the quoting, arriving by another door: a path that
    # matches no routing rule and opens no file.
    #
    # `surrogateescape` carries a path that is not valid UTF-8 through to
    # `os.fsencode` intact rather than raising.
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files", "-z"],
        cwd=str(root), capture_output=True, check=True,
    ).stdout.decode("utf-8", "surrogateescape")
    # No `.strip()` filter: a filename may legally begin or end with whitespace,
    # and trimming it produces a path that matches neither a routing rule nor
    # anything on disk. Only the empty trailing entry `-z` leaves is dropped.
    return [entry for entry in out.split("\0") if entry]


def partition(root: Path) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {"engine": [], "private": [], "corporate": []}
    for rel in _tracked_files(root):
        buckets[get_routing_destination(rel)].append(rel)
    return buckets


def main() -> int:
    require_main_clone(__file__)
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

    # Schema marker for the engine's compatibility handshake. Atomic (tmp +
    # os.replace), per the workspace's no-non-atomic-state-writes rule and to
    # match `build_engine_repo.py`, which routes its equivalent marker the same
    # way. A plain `write_text` interrupted mid-call leaves a zero-byte or
    # partial version string, and this file is the ONLY handshake signal in the
    # overlay: there is no second source for a reader to fall back to.
    atomic_write_text(target / ".schema-version", f"{DATA_SCHEMA_VERSION}\n")

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
