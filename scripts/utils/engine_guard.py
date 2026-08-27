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

# The bundled demo tree is a CLOSED MANIFEST, not a directory anything may write
# into. Operator law, 2026-08-26: no data from the DATA repository may ever sit in
# the engine, and everything under `examples/` must be invented.
#
# Why a manifest rather than a routing rule. `config/routing-map.yaml` carries no
# entry for `examples/`, so every path under it falls through to the `engine`
# default. Measured 2026-08-26, all of these PASSED the wall:
# `examples/crm/contacts/real-person.md`, `examples/outputs/operations/x.md`,
# `examples/knowledge/secret.md`, a private-thread path under `examples/`, and
# `examples/state/mail-bodies.json`. Adding per-path routing rules cannot fix it,
# because the shipped demo files live at those very prefixes and would be flagged
# alongside the leaks.
#
# The route in is not hypothetical. With no overlay `get_data_root()` answers
# `<workspace_root>/examples`, so any tool that writes to the data root writes
# INSIDE the engine clone. A worktree run on 2026-08-26 produced
# `examples/datastore/` and `examples/outputs/operations/` this way, untracked and
# invisible to every gate.
#
# Adding a demo file is therefore a deliberate act: put it here, and it gets read.
DEMO_ROOT = "examples/"
DEMO_MANIFEST = frozenset({
    "examples/.schema-version",
    "examples/README.md",
    "examples/context/EXAMPLE-people.md",
    "examples/crm/contacts/EXAMPLE-contact.md",
    "examples/knowledge/EXAMPLE-note.md",
    "examples/outputs/.gitkeep",
    "examples/threads/business/EXAMPLE-thread.md",
})


# Suffixes that are never prose or code a content gate can read as text. A file
# with one of these is skipped deliberately and silently; anything NOT on this
# list that fails to decode is a gap in coverage the caller must refuse over.
#
# `.bin` earns its place: `tests/integration/fixtures/unsupported.bin` is a
# committed engine fixture, and while the suffix was missing every sweep tried to
# decode it, fell into the unreadable branch, and called the result clean.
BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".pptx", ".docx", ".xlsx",
    ".woff", ".woff2", ".ttf", ".otf", ".ico", ".zip", ".gz", ".db", ".sqlite",
    ".pyc", ".lock", ".bin",
})


def engine_text_files(root: Path, candidates) -> list[str]:
    """Of `candidates`, the engine-routed, non-binary files that exist on disk.

    Both content gates select their targets with this one function, and the
    duplication it replaces is the reason it exists. `content-guard.py` and
    `push-all.engine_content_scan` each carried their own copy of this filter and
    their own `except (OSError, UnicodeDecodeError): continue`. The CLI copy was
    fixed on 2026-08-14 to record and refuse over what it could not read; the
    copy inside the UNBYPASSABLE push wall was not, and stayed silent for eleven
    days. The bypassable layer was strictly stronger than the last one.

    Order is preserved, so a caller that wants a stable report sorts its input.
    """
    out: list[str] = []
    for rel in candidates:
        rel = rel.replace("\\", "/").lstrip("/")
        if not rel:
            continue
        if get_routing_destination(rel) != "engine":
            continue
        p = root / rel
        if not p.is_file() or p.suffix.lower() in BINARY_SUFFIXES:
            continue
        out.append(rel)
    return out


def find_data_artifacts(rel_paths, routing_fn=get_routing_destination) -> list[str]:
    """Pure core: given workspace-relative paths, return every one whose routing
    destination is private/corporate -- a data-class artifact that must not sit in
    the engine clone, regardless of its top-level directory.

    Filtering is by routing destination ONLY, never by a top-level-name allowlist:
    classification carve-outs (e.g. ``datastore/brand/templates/`` routes ENGINE)
    legitimately share a top-level name with data dirs and must NOT be flagged,
    while a private-routed file under an otherwise-engine top level (the
    ``docs/superpowers/`` leak: top-level ``docs``, route ``private``) MUST be.
    A fixed allowlist gets this wrong in both directions.

    TWO rules, not one. This docstring said "the destination check alone is the
    complete invariant" until 2026-08-26, when a measurement refuted it: the
    routing map has no entry for ``examples/``, so everything under the bundled
    demo tree falls to the ``engine`` default and passed, including a contacts
    file and a captured-mail-bodies file. The demo tree is therefore checked
    against ``DEMO_MANIFEST`` as well, and anything under it that is not on the
    manifest is a data artifact whatever its route says.
    """
    flagged = []
    for rel in rel_paths:
        norm = rel.replace("\\", "/").lstrip("/")
        if not norm:
            continue
        if norm.startswith(DEMO_ROOT) and norm not in DEMO_MANIFEST:
            flagged.append(norm)
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
