#!/usr/bin/env python3
"""Refuse a contract whose fixtures cannot carry the shape the writer emits.

The fifth planning-gate rule says a fixture must produce the shape the real
source produces. Until this module it was prose the author had to remember, and
the one time it was forgotten it cost the standard its most expensive failure:
the gate-yield report shipped useless for half its mechanisms because every
fixture in a 23-test frozen contract stamped an ISO string while the denial log
writes `time.time()` floats.

The witness is the WRITER, not the live file. A test that reads live mutable
state is a bad test and this workspace deliberately does not write them; a
fixture minted by the real writer carries the real shape by construction and
stays hermetic. So the rule this module enforces is:

    if the code under test reads a record store, at least one test in the
    contract must build its fixtures by CALLING that store's writer.

It buys the floor, not the ceiling. One writer call satisfies it while the rest
of a contract hand-authors, and the refusal text says so rather than overselling
the guarantee. The floor is what was missing: the count that shipped the defect
was zero, not one.

Consumed by: scripts/canopus.py (approve / freeze / attestation).
"""

from __future__ import annotations

import ast
from pathlib import Path

# ============================================================
# Registry
# ============================================================

# Store module (repo-relative) -> the writer whose call mints a real record.
#
# A store absent from this table is unguarded. That is a hole, and it is
# deliberately a hole with ONE name and ONE place to fix rather than a
# heuristic: inferring stores by shape would produce false accusations, and a
# gate that accuses falsely is a gate people learn to disable.
RECORD_STORES: dict[str, str] = {
    "scripts/utils/denial_log.py": "log_denial",
}

# Directories whose modules are first-party. An import outside these is a
# third-party or stdlib name and the closure stops there.
_FIRST_PARTY_ROOTS = ("scripts", "tests")


# ============================================================
# Import closure
# ============================================================


def _module_to_path(dotted: str) -> str:
    return dotted.replace(".", "/") + ".py"


def _imported_modules(tree: ast.AST) -> list[str]:
    """Every dotted name an AST imports, in BOTH readings of `from X import y`.

    Following only `node.module` is a real escape rather than a hypothetical
    one: `from scripts.utils import denial_log` yields the package
    `scripts.utils`, which is not a file, so the store would fall out of the
    closure entirely. The enforcer-set guard in tests/test_canopus_freeze.py
    had to learn this the same way.
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
            found.extend(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
    return found


def first_party_closure(entry_points: list[str], root: Path) -> set[str]:
    """Transitive first-party import closure of `entry_points` under `root`.

    A module that does not exist is stepped over rather than raised on: at
    freeze time the code under test is absent by construction, and a walk that
    aborted there would make the check unusable exactly when it is wanted.
    """
    seen: set[str] = set()
    queue = list(entry_points)
    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        source = root / rel
        if not source.is_file():
            continue
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (SyntaxError, ValueError, OSError):
            continue
        for dotted in _imported_modules(tree):
            if not dotted.split(".")[0] in _FIRST_PARTY_ROOTS:
                continue
            candidate = _module_to_path(dotted)
            if candidate not in seen:
                queue.append(candidate)
    return seen


# ============================================================
# Writer detection
# ============================================================


def calls_writer(source: str, writer: str) -> bool:
    """True when `source` CALLS `writer`, not merely when it names it.

    The distinction is the whole point. A substring match answers True for a
    docstring that mentions the writer, which would let a comment satisfy the
    gate. It is not a theoretical concern: the blob that misled the author of
    this module names `log_denial` in two docstrings and calls it never.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == writer:
            return True
        if isinstance(func, ast.Attribute) and func.attr == writer:
            return True
    return False


# ============================================================
# The refusal
# ============================================================


def _contract_sources(contract_paths, root: Path) -> list[tuple[str, str]]:
    """(repo-relative name, source text) for every contract test file."""
    out: list[tuple[str, str]] = []
    for raw in contract_paths:
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        files = sorted(path.rglob("test_*.py")) if path.is_dir() else [path]
        for candidate in files:
            if candidate.is_file():
                out.append((candidate.name, candidate.read_text(encoding="utf-8")))
    return out


def shape_refusal(contract_paths, root: Path) -> str:
    """The refusal, or "" when the contract is clean or nothing was measurable.

    Total by construction, like its sibling gates: an internal fault refuses
    nothing. A check that turns a bug in itself into a wall no slice can pass
    is worse than the gap it was built to close, and unparseable input is a
    fault rather than evidence of a violation.
    """
    try:
        root = Path(root)
        sources = _contract_sources(contract_paths, root)
        if not sources:
            return ""

        trees = []
        for _, text in sources:
            try:
                trees.append(ast.parse(text))
            except (SyntaxError, ValueError):
                # The contract cannot be understood, so it cannot be accused.
                return ""

        entry: list[str] = []
        for tree in trees:
            for dotted in _imported_modules(tree):
                if dotted.split(".")[0] in _FIRST_PARTY_ROOTS:
                    entry.append(_module_to_path(dotted))

        closure = first_party_closure(entry, root)

        parts: list[str] = []
        for store, writer in RECORD_STORES.items():
            if store not in closure:
                continue
            if any(calls_writer(text, writer) for _, text in sources):
                continue
            parts.append(
                f"the code under test reads {store} but no test in the contract "
                f"calls {writer}(), so every fixture for that store is invented "
                f"and nothing compares it to the shape the writer emits"
            )
        return "; ".join(parts)
    except Exception:  # noqa: BLE001 - totality IS the requirement
        return ""
