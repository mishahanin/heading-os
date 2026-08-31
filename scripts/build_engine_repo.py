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

from scripts.utils.atomic import atomic_write_text
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import (
    get_outputs_dir,
    get_routing_destination,
    get_workspace_root,
)

# Data-path tokens (mirror leak-guard) used only for a belt-and-braces assertion
# that nothing engine-routed is real private data (examples/ is the allowed exception).
#
# Matched by `_suspicious_engine` with a plain string `startswith`, NOT by path
# segment, so a token may end mid-filename. That is what makes the last entry
# possible and is why it is here rather than in the routing map: the map's resolver
# compares whole path segments and has no way to span `fireside-schedule.<cycle>.json`.
#
# `auto-memory/` joined the list on 2026-08-30 alongside its routing rule. The two
# are deliberate duplicates: the map is the classification, this is the refusal that
# still fires if a future edit drops the rule.
# The annotation is repeated on EVERY line, not written once above the tuple.
# `leak-guard: ok` suppresses the LINE it sits on. This was a one-line tuple with
# one trailing annotation until 2026-08-30; splitting it for readability moved
# each token onto its own line and left four of them uncovered, which blocked a
# commit. A suppression that does not survive a re-wrap is a suppression that
# expires on the next reformat.
_DATA_TOKENS = (
    "crm/contacts",                # leak-guard: ok (audit token, not a path)
    "knowledge/odin-brain",        # leak-guard: ok (audit token, not a path)
    "threads/",                    # leak-guard: ok (audit token, not a path)
    "outputs/",                    # leak-guard: ok (audit token, not a path)
    "auto-memory/",                # leak-guard: ok (audit token, not a path)
    # every dated roster archive, not just today's
    "config/fireside-schedule.",   # leak-guard: ok (audit token, not a path)
)


def _tracked_files(root: Path) -> list[str]:
    # core.quotepath=false: emit real UTF-8 paths instead of octal-escaped, quoted
    # ones. Without this, a non-ASCII data path (e.g. Cyrillic-named PDFs under
    # datastore/books) arrives as `"datastore/..."` with a leading quote, fails to
    # match its private/corporate rule, and silently mis-routes to engine.
    #
    # `-z`, because that flag covers less than the comment above implies. It
    # suppresses quoting for NON-ASCII bytes only; git quotes and C-escapes a
    # path holding a CONTROL character whatever it says, so the mis-route the
    # comment describes stayed open for exactly those names. MEASURED 2026-08-30
    # in a scratch repository on this machine (ext4, git 2.43.0): a tracked
    # `crm/contacts/leak\na.md` came back from this exact invocation as the
    # literal `"crm/contacts/leak\\na.md"`, double quotes included,
    # `get_routing_destination` answered `engine` for it where the real name
    # answers `private`, and `_suspicious_engine(['"crm/contacts/leak\\na.md"'])`
    # returned `[]` - the leading quote defeats its `startswith` check too, so
    # the belt-and-braces refusal below never fires and the build prints
    # "routing clean". A tab, a CR, a vertical tab, a `"` and a backslash in a
    # filename all quote the same way. Under `-z` every one of them came back
    # verbatim, and `core.quotepath=false` became a no-op (measured:
    # byte-identical output with and without it, kept for continuity with
    # `scripts/utils/commit_source.py`). A real newline additionally survives
    # `.splitlines()` as two half-paths.
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
    # Atomic (tmp + os.replace), per the workspace's no-non-atomic-state-writes
    # rule. A plain write_text left a truncated manifest behind on a crash or a
    # concurrent read, and the provenance comment directly above is what other
    # tooling trusts this file to be.
    src_manifest = get_outputs_dir() / "operations" / "workspace" / "engine-build-manifest.json"
    atomic_write_text(
        src_manifest,
        json.dumps({"engine_count": len(engine), "copied": copied,
                    "target": str(target)}, indent=2) + "\n",
    )

    print(f"{GREEN}  built: {copied} files copied, fresh git history, no remote.{RESET}")
    print(f"  next: cd {target} && HEADING_OS_ENGINE_REPO=1 "
          f"python scripts/leak-guard.py check-staged --files $(git ls-files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
