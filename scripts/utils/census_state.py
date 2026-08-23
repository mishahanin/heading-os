#!/usr/bin/env python3
"""What makes two /census runs comparable, defined once.

The acceptance rule compares numbers produced days apart: the mechanical ceiling
measured on 2026-08-13, and the primitive's answers scored afterwards. That
comparison is only meaningful if both ran against the same world, so four values
are pinned and any divergence refuses the comparison rather than reporting a
difference that is really a difference of corpus.

Why four and not one. The corpus SHA alone misses three real ways the world
moves underneath a run: `today` enters several oracles directly (a thread goes
stale by the calendar, not by an edit), the retrieved side depends on the index
config, and the index is rebuilt without the SHA moving at all.

This lives in `scripts/utils/` rather than inside `census-bench.py` because the
engine WRITES a state into its answers file and the scorer READS one, and a
comparison the whole acceptance rests on must not exist in two copies. The copy
that stops being updated is the one someone reads.
"""
from __future__ import annotations

import hashlib
import subprocess  # nosec B404 - fixed argv git reads, never shell=True
from datetime import date, datetime
from pathlib import Path

PINNED_KEYS = ("corpus_sha", "corpus_content_sha256", "today",
               "index_config_sha256", "index_built")

# Not every pin governs every comparison, and treating them as one set makes a
# guard refuse comparisons it has no business refusing.
#
# ORACLE_PINS are the inputs the code-computed truth actually depends on: the
# corpus bytes, and the date several oracles read directly. Two answer sets
# graded against oracles are comparable exactly when these match.
#
# RETRIEVAL_PINS govern the retrieved side. They matter when comparing two
# RETRIEVAL measurements, and not at all when grading answers a traversal
# produced by reading files - /census never touches the index. Discovered on the
# first scored run, where a rebuilt index alone made a valid acceptance report
# NOT-COMPARABLE.
#
# `corpus_content_sha256` is in ORACLE_PINS and `corpus_sha` alone is not enough:
# the commit is not the bytes, and on a live operator workspace the tree is
# dirty nearly always. See `corpus_digest`.
ORACLE_PINS = ("corpus_sha", "corpus_content_sha256", "today")
RETRIEVAL_PINS = ("index_config_sha256", "index_built")


def git_head(path: Path) -> tuple[str, bool]:
    """(HEAD sha, working tree dirty). Unknown-and-dirty on any failure.

    Fail-toward-dirty is deliberate: an unreadable repository must not report a
    clean, comparable state it cannot establish.
    """
    try:
        head = subprocess.run(  # nosec B603 B607 - fixed argv, no shell
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        dirty = subprocess.run(  # nosec B603 B607 - fixed argv, no shell
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", True
    # Check the EXIT CODES, not just stdout. `except OSError` alone only covers a
    # missing git binary. When git ran and refused -- not a repository, a broken
    # .git, a repo with no commit yet -- stdout was empty or, for `rev-parse
    # HEAD` on an empty repo, the literal string "HEAD"; `bool("".strip())` then
    # read as CLEAN and the function returned a state it could not establish as
    # comparable. That is the exact opposite of the fail-toward-dirty promise
    # three lines above.
    if head.returncode != 0 or dirty.returncode != 0:
        return "unknown", True
    return head.stdout.strip() or "unknown", bool(dirty.stdout.strip())


# Files the corpus digest covers. The same set the engine's window check walks,
# so "the corpus" means one thing in both places.
CORPUS_SUFFIXES = (".md", ".json", ".yaml", ".yml", ".txt")

# Directories excluded from the digest: rebuilt artefacts, not corpus content.
DIGEST_SKIP_DIRS = frozenset({".git", ".memory-index", "node_modules", "__pycache__"})

def digest_scopes() -> tuple[str, ...]:
    """The subtrees whose bytes can move a truth, resolved through the data seam.

    These are the four the oracles read, and deliberately NOT the whole data
    overlay: `outputs/` is where this very benchmark writes its reports, so
    digesting it made the instrument perturb its own measurement. `--baseline`
    wrote a report, the digest moved, and the scoring run that followed was
    refused as NOT-COMPARABLE against the baseline it had just produced.
    Measured 2026-08-13.

    Derived from `get_*_dir()` rather than written as literals, so a workspace
    that lays its data out differently digests ITS directories, not this one's.
    """
    from scripts.utils.workspace import (
        get_auto_memory_dir,
        get_context_dir,
        get_crm_contacts_dir,
        get_threads_dir,
    )

    threads = get_threads_dir()
    root = threads.parent.resolve()
    names = []
    for directory in (threads / "business", get_crm_contacts_dir(),
                      get_context_dir(), get_auto_memory_dir()):
        try:
            names.append(directory.resolve().relative_to(root).as_posix())
        except (ValueError, OSError):
            # A scope outside the corpus root cannot be addressed relative to it.
            # Skipping it silently would shrink the digest's coverage without
            # saying so, so it is named instead and hashed as unreachable.
            names.append(f"<unreachable:{directory.name}>")
    return tuple(names)


def corpus_digest(corpus_root: Path, scopes: tuple[str, ...] | None = None) -> str:
    """A content hash of the corpus, for when git HEAD cannot answer the question.

    `git_head` establishes WHICH COMMIT, and the acceptance needs WHICH BYTES.
    They part company the moment the tree is dirty, which for a live operator
    workspace is almost always: on 2026-08-13 a `/thread log` written between two
    scoring runs moved `oracle_agg_09`'s truth from 8 threads to 9 -- the log text
    named a country -- while `corpus_sha` stayed byte-identical and
    `states_comparable` reported True. A correct answer was graded wrong and the
    guard whose whole job is that comparison said nothing.

    Costs one walk of ~1.4 MB. Paths are relative and sorted, so the digest is
    stable across machines and across the order the filesystem hands files back.
    """
    digest = hashlib.sha256()
    root = corpus_root.resolve()
    scopes = digest_scopes() if scopes is None else scopes
    # A scope that does not exist is hashed as absent rather than skipped: the
    # difference between "empty" and "gone" is a difference of corpus.
    for scope in scopes:
        base = root / scope
        digest.update(f"[{scope}]".encode())
        if not base.is_dir():
            digest.update(b"<absent>\0")
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix.lower() not in CORPUS_SUFFIXES or not path.is_file():
                continue
            if DIGEST_SKIP_DIRS.intersection(path.relative_to(root).parts):
                continue
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            try:
                digest.update(path.read_bytes())
            except OSError:
                # A file the walk saw but cannot read changes the digest rather
                # than being skipped: silence here would report two different
                # corpora as the same one, which is what this closes.
                digest.update(b"<unreadable>")
            digest.update(b"\0")
    return digest.hexdigest()[:16]


def run_state(corpus_root: Path, engine_root: Path, today: date, tz=None) -> dict:
    """The pinned values, plus the dirty flag for the reader's judgement."""
    sha, dirty = git_head(corpus_root)
    config_path = engine_root / "config" / "memory-index.yaml"
    config_hash = (
        hashlib.sha256(config_path.read_bytes()).hexdigest()[:16]
        if config_path.exists() else "absent"
    )
    index_db = corpus_root / ".memory-index" / "index.db"
    return {
        "corpus_sha": sha,
        "corpus_dirty": dirty,
        "corpus_content_sha256": corpus_digest(corpus_root),
        "today": today.isoformat(),
        "index_config_sha256": config_hash,
        "index_built": (
            datetime.fromtimestamp(index_db.stat().st_mtime, tz=tz).isoformat()
            if index_db.exists() else "absent"
        ),
    }


def states_comparable(a: dict, b: dict,
                      pins: tuple[str, ...] = PINNED_KEYS) -> tuple[bool, list[str]]:
    """Which of the given pinned values diverge between two runs."""
    diverged = [k for k in pins if a.get(k) != b.get(k)]
    return (not diverged), diverged
