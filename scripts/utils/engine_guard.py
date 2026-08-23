#!/usr/bin/env python3
"""Engine/data leak detector -- single source of truth for "is this file allowed
in the engine clone?".

HEADING OS engine/data separation invariant: the engine repo (.heading-os) is
code only; every file that routes `private` or `corporate` belongs in the DATA
overlay (.heading-os-data) or the corporate repo, never tracked in the engine.

This module holds the PURE detector and a repo-scanning helper so that every
enforcement layer shares ONE implementation:

  * tests/test_engine_tree_clean.py   -- the pre-commit (always_run) + pre-push
                                          suite assertion that the tree is clean;
  * scripts/push-all.py               -- the UNBYPASSABLE push-time wall (pure
                                          code on the sanctioned push path, no
                                          skip flag), so a `--no-verify` commit
                                          or an un-armed pre-push hook still
                                          cannot ship a data artifact.

Why a shared module: the detector previously lived only inside the test file, so
the unbypassable push path could not reuse it without importing from tests. The
2026-06-22 `docs/superpowers/` leak survived for exactly this reason -- the only
routing check ran at layers that `--no-verify` skips. Centralising the logic lets
the push wall enforce the same invariant the test asserts.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.workspace import get_routing_destination

# Routing destinations that must NEVER appear in the engine clone.
DATA_DESTINATIONS = frozenset({"private", "corporate"})


def find_data_artifacts(rel_paths, routing_fn=get_routing_destination) -> list[str]:
    """Pure core: given workspace-relative paths, return every one whose routing
    destination is private/corporate -- a data-class artifact that must not sit in
    the engine clone, regardless of its top-level directory.

    Filtering is by routing destination ONLY, never by a top-level-name allowlist:
    classification carve-outs (e.g. ``datastore/brand/templates/`` routes ENGINE)
    legitimately share a top-level name with data dirs and must NOT be flagged,
    while a private-routed file under an otherwise-engine top level (the
    ``docs/superpowers/`` leak: top-level ``docs``, route ``private``) MUST be.
    A fixed allowlist gets this wrong in both directions; the destination check
    alone is the complete invariant.
    """
    flagged = []
    for rel in rel_paths:
        norm = rel.replace("\\", "/").lstrip("/")
        if not norm:
            continue
        if routing_fn(norm) in DATA_DESTINATIONS:
            flagged.append(norm)
    return flagged


def repo_carried_paths(root: Path) -> list[str]:
    """All files git would carry from ``root``: tracked + untracked-not-ignored.

    Respects .gitignore so build/venv noise is excluded, and is the exact set that
    could leak into the repo on the next commit/push.

    `-z` is load-bearing, not tidiness. Without it git C-quotes any path holding a
    non-ASCII byte, so `crm/contacts/иван.md` arrives wrapped in a double quote
    with each Cyrillic byte written as a backslash escape. The leading quote breaks
    the `crm/contacts/` prefix, the routing lookup falls through to the
    `engine` default, and the wall clears exactly the file it exists to stop. This
    is a bilingual RU/EN workspace, so such a filename is ordinary. NUL-terminated
    output is never quoted and never escaped.
    """
    paths: list[str] = []
    for args in (
        ["git", "ls-files", "-z"],
        ["git", "ls-files", "-z", "--others", "--exclude-standard"],
    ):
        out = subprocess.run(
            args, cwd=str(root), capture_output=True, text=True, check=True
        ).stdout
        paths.extend(entry for entry in out.split("\0") if entry.strip())
    return paths


def scan_engine_repo(root: Path) -> list[str]:
    """Scan an engine clone working tree and return every data-class artifact in it.

    Empty list == clean. The repo-level entry point both the test and the push
    wall call.
    """
    return find_data_artifacts(repo_carried_paths(Path(root)))
