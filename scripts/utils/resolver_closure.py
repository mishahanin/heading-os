"""Which functions can reach the operator's data root, derived and never typed.

Two seeds actually read `HEADING_OS_DATA`: `paths.get_data_root` and
`paths.env_data_root`. Everything else that resolves a private path does so by
calling something that calls something that calls one of those. This module
computes that closure from the source, so a new helper in `scripts/utils/paths.py`
is dangerous the moment it is written rather than the moment somebody remembers
to add it to a list.

A hand-maintained list of dangerous names is the defect this replaces. The
workspace has found that shape more than once: a list is correct on the day it is
written and silently incomplete afterwards.

The same closure is computed inside
`tests/test_a_tracked_dir_list_frozen_before_any_test_could_move_it.py`, which
predates this module and asks a different question with it (which module-level
NAMES are frozen at import). `tests/test_the_two_resolver_closures_agree.py`
fails if the two ever disagree, so the duplication cannot drift unnoticed. It is
duplication all the same, and consolidating it belongs in whichever change next
touches that test file.
"""
from __future__ import annotations

import ast
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[2]

RESOLVER_SOURCES = (
    ENGINE_ROOT / "scripts" / "utils" / "paths.py",
    ENGINE_ROOT / "scripts" / "utils" / "workspace.py",
)

# The only two functions that read the environment variable. Everything in the
# returned set is reachable from one of them.
SEEDS = frozenset({"get_data_root", "env_data_root"})


def called_name(node: ast.Call) -> str | None:
    """The bare name a call resolves to, ignoring what it is attached to.

    `get_data_root()`, `paths.get_data_root()` and `self.get_data_root()` all
    answer `get_data_root`. Deliberately loose: a false positive costs one extra
    name in a set, a false negative costs a resolver nobody guards.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _calls_by_function(tree: ast.AST) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        named = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                name = called_name(sub)
                if name:
                    named.add(name)
        out.setdefault(node.name, set()).update(named)
    return out


def _close(calls: dict[str, set[str]], seeds: frozenset[str]) -> frozenset[str]:
    reached = set(seeds) | {f for f, named in calls.items() if named & seeds}
    changed = True
    while changed:
        changed = False
        for func, named in calls.items():
            if func not in reached and (named & reached):
                reached.add(func)
                changed = True
    return frozenset(reached)


def derived_resolvers(sources=RESOLVER_SOURCES, seeds=SEEDS) -> frozenset[str]:
    """Every function in the path modules that transitively reaches a seed."""
    calls: dict[str, set[str]] = {}
    for src in sources:
        tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        for func, named in _calls_by_function(tree).items():
            calls.setdefault(func, set()).update(named)
    return _close(calls, seeds)


def module_reaches_resolver(tree: ast.AST, resolvers: frozenset[str]) -> bool:
    """True when ANY call anywhere in the module names a dangerous resolver.

    Whole-module, not per-function, because the question here is "can this file
    produce a path into the operator's overlay at all", not "which of its
    functions does".
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = called_name(node)
            if name and name in resolvers:
                return True
    return False


# ------------------------------------------------------------
# Write primitives
# ------------------------------------------------------------
#
# The same eleven the guard wraps, plus the pathlib and shutil spellings that
# reach them. Asked as "does this file contain a call by this name", which is
# loose on purpose: this is a census that feeds a human decision, so a file
# listed and then dismissed costs a glance, and a file missed costs the whole
# point of the census.
WRITE_CALL_NAMES = frozenset({
    "open", "write_text", "write_bytes", "write", "writelines",
    "touch", "mkdir", "makedirs", "replace", "rename", "remove", "unlink",
    "rmdir", "rmtree", "copy", "copy2", "copyfile", "copytree", "move",
    "save", "dump", "to_csv", "to_excel", "connect", "savefig",
})


def module_reaches_write(tree: ast.AST, names=WRITE_CALL_NAMES) -> frozenset[str]:
    """Which write-primitive names this module calls. Empty means none."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = called_name(node)
            if name and name in names:
                found.add(name)
    return frozenset(found)
