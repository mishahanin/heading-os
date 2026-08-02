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
import sys
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


def _imported_modules(tree: ast.AST, rel: str = "") -> list[str]:
    """Every dotted name an AST imports, in BOTH readings of `from X import y`.

    Following only `node.module` is a real escape rather than a hypothetical
    one: `from scripts.utils import denial_log` yields the package
    `scripts.utils`, which is not a file, so the store would fall out of the
    closure entirely. The enforcer-set guard in tests/test_canopus_freeze.py
    had to learn this the same way.
    """
    package = ".".join(rel[: -len(".py")].split("/")[:-1]) if rel.endswith(".py") else ""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # A relative import carries its depth in `node.level` and a module
            # name that is bare or absent. Returning `node.module` verbatim
            # yielded `denial_log`, which never equals `scripts.utils.denial_log`,
            # so every relative reader was invisible. Measured: 106 relative
            # imports under scripts/, and the largest package there is written
            # that way.
            base = package
            if node.level:
                parts = package.split(".") if package else []
                base = ".".join(parts[: len(parts) - (node.level - 1)]) if parts else ""
            prefix = f"{base}.{node.module}" if node.level and node.module else (
                base if node.level else node.module
            )
            if not prefix:
                continue
            found.append(prefix)
            found.extend(f"{prefix}.{alias.name}" for alias in node.names)
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
        for dotted in _imported_modules(tree, rel):
            if dotted.split(".")[0] not in _FIRST_PARTY_ROOTS:
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

    The binding is RESOLVED rather than name-matched, which closes two measured
    holes at once. Matching the bare name missed `log_denial as ld` and refused
    a contract that followed the discipline under an alias; it also accepted a
    call to a locally DEFINED function that merely shares the name, which is a
    fixture inventing its own writer and passing for the real one.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False

    bound: set[str] = set()
    shadowed: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            bound.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == writer
            )
        elif (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == writer):
            shadowed.add(node.name)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in bound and func.id not in shadowed:
            return True
        # `denial_log.log_denial(...)`: the attribute names the real symbol on
        # the real module, so no local binding is needed to trust it.
        if isinstance(func, ast.Attribute) and func.attr == writer:
            return True
    return False


# ============================================================
# The refusal
# ============================================================


def _contract_sources(contract_paths, root: Path) -> list[tuple[str, str]]:
    """(file name, source text) for every contract source file.

    `conftest.py` is collected alongside `test_*.py` on purpose. It is the
    canonical pytest home for a fixture, so a contract that does exactly what
    this module demands -- mint its records through the real writer -- and puts
    that fixture where pytest expects it would otherwise be refused for it.

    Collected for BOTH call shapes, which is the whole of finding H1. The freeze
    gate passes directories; the attestation gate passes the frozen file list,
    and that list is filtered by pytest's `python_files` patterns, so it never
    contains a conftest. Collecting it only on the directory branch made the two
    gates disagree about the same contract: clean at freeze, falsely accused at
    attestation. A file input now also picks up the conftest beside it.
    """
    out: list[tuple[str, str]] = []
    seen: set[Path] = set()
    for raw in contract_paths:
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        if path.is_dir():
            files = sorted(path.rglob("test_*.py")) + sorted(path.rglob("conftest.py"))
        else:
            files = [path, path.parent / "conftest.py"]
        for candidate in files:
            if candidate.is_file() and candidate not in seen:
                seen.add(candidate)
                out.append((candidate.name, candidate.read_text(encoding="utf-8")))
    return out


def _modules_reading(store: str, candidates: list[str], root: Path) -> list[str]:
    """Of `candidates`, those that import a name FROM `store`.

    Reachability is not use, and the difference is the whole of finding H3.
    `scripts/canopus.py` reaches `denial_log.py` in three hops through
    `gate_yield`, and a contract that merely imports canopus was told "the code
    under test reads denial_log" -- false, on the most common contract shape in
    this workspace, and this module's own docstring says what a gate that
    accuses falsely becomes. So the accusation now needs a module the contract
    IMPORTS DIRECTLY to read the store itself.

    The trade is named rather than hidden: a module that delegates its store
    reading to a helper the contract does not import escapes this check. That
    is the quiet direction, and a false accusation is not.
    """
    store_module = store[: -len(".py")].replace("/", ".")
    reading: list[str] = []
    for rel in candidates:
        source = root / rel
        if not source.is_file():
            continue
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (SyntaxError, ValueError, OSError):
            continue
        for dotted in _imported_modules(tree, rel):
            if dotted == store_module or dotted.startswith(store_module + "."):
                reading.append(rel)
                break
    return reading


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

        parts: list[str] = []
        for store, writer in RECORD_STORES.items():
            readers = _modules_reading(store, entry, root)
            if not readers:
                continue
            if any(calls_writer(text, writer) for _, text in sources):
                continue
            parts.append(
                f"{readers[0]} reads {store} but no test in the contract calls "
                f"{writer}(), so every fixture for that store is invented and "
                f"nothing compares it to the shape the writer emits"
            )
        return "; ".join(parts)
    except Exception as exc:  # noqa: BLE001 - totality IS the requirement
        # Total, and NOT silent. Refusing nothing is the requirement; saying
        # nothing is a separate choice, and the wrong one: a fault in here
        # leaves the gate quietly toothless, which is the exact posture this
        # module exists to remove from somewhere else. Both siblings that share
        # this shape bind and report, and the workspace rule forbids a handler
        # that neither logs nor re-raises.
        print(
            f"canopus: the production-shape check faulted and refused nothing "
            f"({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        return ""
