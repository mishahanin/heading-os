#!/usr/bin/env python3
"""Every path the engine writes under `.cache/` is gitignored, by derivation.

WHAT HAPPENED, 2026-09-05. Two new stores landed in this repository on the same
day. `.cache/content-verdicts.db` got its four `.gitignore` entries (the file and
its `-journal`, `-wal`, `-shm` siblings) because the author remembered.
`.cache/nightly-refresh/last-run.json` got none, and was the only untracked,
non-ignored path under `.cache/` when the day ended. Nothing failed. Nothing
said anything.

WHY THAT MATTERS HERE SPECIFICALLY. `scripts/push-all.py` stages with
`git add -A`, and this repository is PUBLIC. A run record is not a secret, so
this is not a leak; it is machine-local state landing in a public tree, where it
then differs per clone and churns after every night. The next such file might
not be as harmless: the content-verdict store's key is a digest of the private
denylist, and it is ignored today only because someone thought of it.

THE CORPUS COMES FROM THE CODE, NOT FROM THE DISK, and that is the whole design
of this file. A test that walked the real `.cache/` directory would be green on
any machine where the nightly had not run yet, which is `a-guard-is-green-over-an-
empty-corpus` and is the failure this file must not reproduce. It reads the
SOURCE, so a store that has never been written once is still in scope the moment
its path is declared.

TWO SPELLINGS, and missing the second would have missed the interesting case.
Both appear in this tree:

    RECORD_REL = Path(".cache/nightly-refresh/last-run.json")   # a literal
    STORE_PATH = ROOT / ".cache" / "content-verdicts.db"        # a Path join

A regex for `".cache/` finds the first and not the second, so the two verdict
stores, the ones whose keys derive from private data, would have been invisible
to it. This walks the AST and handles the join.

Run: .venv/bin/python -m pytest \\
     tests/test_a_cache_store_whose_ignore_rule_was_remembered.py -q
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: Where engine code that writes a cache actually lives. `tests/` is excluded on
#: purpose: a test naming a scratch `.cache/...` path under `tmp_path` is not
#: making a claim about this repository's working tree.
SOURCE_DIRS = ("scripts", ".claude")

#: SQLite writes these beside any database it opens. They are the reason the
#: rule is four lines rather than one, and forgetting them leaves the working
#: tree dirty at exactly the moment a store is being written, which is the
#: moment `git add -A` runs during a push.
SQLITE_SIDECARS = ("-journal", "-wal", "-shm")

#: A floor on what the scan must find, asserted before any verdict is read from
#: it. Not the exact count, which would turn every new cache into a failing test
#: for the wrong reason; a floor, which only fails when the scanner itself has
#: stopped seeing. MEASURED 2026-09-05: the scan finds 6.
MINIMUM_EXPECTED = 5


def _joined_segments(node: ast.AST) -> list[str] | None:
    """The string segments of a `a / "b" / "c"` chain, innermost first.

    None when the expression is not a chain of `/` over constants and names,
    which is every expression this file has no opinion about.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _joined_segments(node.left)
        right = _joined_segments(node.right)
        if right is None:
            return None
        # An unrecognised left operand (`ROOT`, `get_workspace_root()`) is fine:
        # the segments to the RIGHT of it are still the relative path, which is
        # what `git check-ignore` needs.
        return (left or []) + right
    return None


def _cache_paths_in(source: str) -> set[str]:
    """Every `.cache/...` relative path this source declares."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    found: set[str] = set()

    for node in ast.walk(tree):
        # Spelling one: a literal that already carries the whole path.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value.startswith(".cache/") and len(value) > len(".cache/"):
                found.add(value.rstrip("/"))
        # Spelling two: a `/` chain with ".cache" as one of its segments.
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            segments = _joined_segments(node)
            if segments and ".cache" in segments:
                index = segments.index(".cache")
                tail = [s for s in segments[index:] if s and "*" not in s]
                if len(tail) > 1:
                    found.add("/".join(tail))

    return found


#: The scan parses every `.py` under `scripts/` and `.claude/`, which is the
#: expensive part of this file and identical for all four callers. MEASURED
#: 2026-09-05: three unmemoised scans made this file 40.09 s; one makes it
#: 3.21 s, of which 2.48 s is the single scan itself. The
#: cache is module-level rather than a fixture because the corpus is the source
#: tree, which no test here mutates.
_SCAN_CACHE: dict[str, list[str]] | None = None


def _scan() -> dict[str, list[str]]:
    """`{relative cache path: [files that declare it]}` across the engine."""
    global _SCAN_CACHE
    if _SCAN_CACHE is not None:
        return _SCAN_CACHE
    declared: dict[str, list[str]] = {}
    for directory in SOURCE_DIRS:
        base = ROOT / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                # Never silent: an unreadable source is a file this scan did not
                # cover, and a scan that quietly covers less is the defect this
                # file exists to prevent.
                pytest.fail(f"could not read {path.relative_to(ROOT)}")
            for rel in _cache_paths_in(source):
                declared.setdefault(rel, []).append(
                    str(path.relative_to(ROOT)))
    _SCAN_CACHE = declared
    return declared


def _ignored(rel: str) -> bool:
    """Whether git ignores `rel`, whether or not it exists on disk."""
    proc = subprocess.run(
        ["git", "check-ignore", "-q", "--", rel],
        cwd=str(ROOT), capture_output=True)
    # 0 ignored, 1 not ignored, 128 an error worth failing on rather than
    # reading as "not ignored", which would be a false alarm, or as "ignored",
    # which would be a silent pass.
    if proc.returncode not in (0, 1):
        pytest.fail(f"git check-ignore failed on {rel!r}: "
                    f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.returncode == 0


# ============================================================
# The scan measures something, before anything is concluded from it
# ============================================================

def test_the_scan_finds_the_cache_paths_this_repository_declares():
    """The control. Every assertion below is over this set, so an empty or
    shrunken one would make all of them pass while measuring nothing."""
    declared = _scan()

    assert len(declared) >= MINIMUM_EXPECTED, (
        f"the scan found {len(declared)} cache path(s), fewer than the "
        f"{MINIMUM_EXPECTED} known to exist. Either the paths moved or the "
        f"scanner stopped seeing them; do not lower this number to make it "
        f"pass. Found: {sorted(declared)}")


def test_both_spellings_are_covered():
    """The scan sees a Path JOIN, not only a literal.

    `STORE_PATH = ROOT / ".cache" / "content-verdicts.db"` is the shape a regex
    over `".cache/` misses, and it is the shape the two verdict stores use, so a
    scanner blind to it would be blind to the stores whose keys derive from the
    private denylist.
    """
    literal = _cache_paths_in('X = Path(".cache/nightly-refresh/last-run.json")')
    joined = _cache_paths_in('X = ROOT / ".cache" / "content-verdicts.db"')

    assert literal == {".cache/nightly-refresh/last-run.json"}, literal
    assert joined == {".cache/content-verdicts.db"}, joined


# ============================================================
# The rule
# ============================================================

def test_every_declared_cache_path_is_gitignored():
    """THE GUARD. A new store ships with its ignore rule or this goes red.

    `push-all.py` stages with `git add -A` and this repository is public, so an
    un-ignored cache path is machine-local state committed to a public tree.
    """
    declared = _scan()
    missing = {rel: sorted(set(who)) for rel, who in declared.items()
               if not _ignored(rel)}

    assert not missing, (
        "these paths are written under .cache/ and are NOT gitignored, so "
        "`git add -A` in push-all.py commits them into a PUBLIC repository:\n"
        + "\n".join(f"  {rel}  (declared in {', '.join(who)})"
                    for rel, who in sorted(missing.items()))
        + "\nAdd each to .gitignore. For a `.db`, add its -journal, -wal and "
          "-shm siblings too.")


def test_a_sqlite_store_also_ignores_its_three_sidecars():
    """The four-line rule, which is where the remembering actually fails.

    SQLite writes `-journal`, `-wal` and `-shm` beside the database. Ignoring
    only the `.db` leaves the tree dirty exactly while the store is being
    written, which is when a push runs `git add -A`.
    """
    declared = _scan()
    databases = [rel for rel in declared if rel.endswith(".db")]

    assert databases, (
        "no `.db` under .cache/ was found, so this test proved nothing. It is "
        "asserted because zero databases is its passing value.")

    missing = [f"{rel}{suffix}"
               for rel in databases for suffix in SQLITE_SIDECARS
               if not _ignored(f"{rel}{suffix}")]

    assert not missing, (
        "these SQLite sidecar paths are not gitignored:\n  "
        + "\n  ".join(missing))


def test_the_ignore_check_can_fail(tmp_path):
    """The negative direction, without which every pass above is unfalsifiable.

    A path nothing ignores must answer False. Written against a path INSIDE the
    repository so it exercises the same rules, and named so that no plausible
    future `.gitignore` entry silences it by accident.
    """
    sentinel = "docs/a-file-no-gitignore-rule-names-2026-09-05.md"

    assert not _ignored(sentinel), (
        f"{sentinel} reports as ignored, so `_ignored` cannot distinguish an "
        f"ignored path from an un-ignored one and every assertion in this file "
        f"is vacuous")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
