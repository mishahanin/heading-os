#!/usr/bin/env python3
"""SEC-011: the shutdown handler is registered after the object it closes over.

`main()` defines `signal_handler` as a closure over `sentinel`, so registering
it before `sentinel = Sentinel(...)` runs would leave SIGINT/SIGTERM pointing at
a handler that raises `NameError` mid-shutdown: the daemon would refuse to stop
cleanly at exactly the moment someone is trying to stop it.

What this file used to do, and why it was replaced
--------------------------------------------------
It compared LINE NUMBERS in the source text against a hardcoded constant::

    if 'signal.signal(' in stripped and first_signal_line is None:
        if i > 2200:                       # "the main() function area"
            first_signal_line = i
    ...
    assert first_signal_line > sentinel_creation_line

Three things were wrong with that, all of the same kind -- it measured the
right thing only by luck:

* ``2200`` is a magic number with no relationship to ``main``. Today the two
  statements sit at 3293 and 3303 and the file is 3315 lines long. Any growth
  above the daemon class moves that boundary under the test.
* ``'signal.signal(' in stripped`` is a substring match over raw text, so a
  COMMENT mentioning ``signal.signal(`` would be picked up as a registration.
* Nothing required the two statements to be in the same function. Move
  ``sentinel = Sentinel(...)`` into a factory and leave ``signal.signal`` in
  ``main`` and the line comparison still passes, while the closure it is
  supposed to protect no longer exists.

The rewrite below reads the AST, resolves each statement to its ENCLOSING
function, and requires both to be `main` before it compares anything. Comments
are invisible to a parser and there is no line-number constant left.
"""

from __future__ import annotations

import ast

import pytest

from tests.security.conftest import read_file_content


def _module(scripts_dir):
    return ast.parse(read_file_content(scripts_dir / "sentinel.py"))


def _enclosing_function(tree, node):
    """Name of the innermost function containing `node`, or None at module level.

    Innermost matters: `signal_handler` is nested inside `main`, so a naive
    outermost walk would report the wrong owner for anything inside it.
    """
    owner = None
    for candidate in ast.walk(tree):
        if not isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        contains = any(child is node for child in ast.walk(candidate))
        if contains and (owner is None or candidate.lineno > owner.lineno):
            owner = candidate
    return owner.name if owner is not None else None


def _sentinel_constructions(tree):
    """`sentinel = Sentinel(...)` assignments, matched structurally."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "Sentinel"):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "sentinel" in names:
            found.append(node)
    return found


def _signal_registrations(tree):
    """`signal.signal(...)` calls, matched structurally, never as text."""
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "signal"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "signal"]


def test_the_names_this_file_walks_still_exist(scripts_dir):
    """The binder pin. A walk that matches nothing must fail here, not pass.

    Every assertion in this file is a filter over the AST, and an empty filter
    result is indistinguishable from a clean result unless something demands a
    minimum. This is that demand.
    """
    tree = _module(scripts_dir)

    mains = [n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == "main"]
    assert len(mains) == 1, (
        f"expected exactly one module-level `main` in sentinel.py, found "
        f"{len(mains)}. SEC-011 is a claim about what main() does in what order."
    )

    classes = [n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "Sentinel"]
    assert len(classes) == 1, (
        f"expected exactly one `class Sentinel`, found {len(classes)}"
    )

    assert len(_sentinel_constructions(tree)) == 1, (
        "expected exactly one `sentinel = Sentinel(...)`; this file compares "
        "its position against the signal registrations, so two of them would "
        "make the comparison meaningless."
    )
    assert len(_signal_registrations(tree)) >= 1, (
        "found no `signal.signal(...)` call in sentinel.py. Either the daemon "
        "stopped installing a shutdown handler, or the call was renamed and "
        "this test is now walking for something that does not exist."
    )


def test_signal_handler_registered_after_sentinel_creation(scripts_dir):
    """Both statements live in `main`, and the object is built first."""
    tree = _module(scripts_dir)

    creation = _sentinel_constructions(tree)[0]
    registrations = _signal_registrations(tree)

    creation_owner = _enclosing_function(tree, creation)
    assert creation_owner == "main", (
        f"`sentinel = Sentinel(...)` is in {creation_owner!r}, not `main`. The "
        "SEC-011 ordering is only meaningful when the construction and the "
        "registration share a scope: the handler is a closure over `sentinel`."
    )

    for call in registrations:
        owner = _enclosing_function(tree, call)
        assert owner == "main", (
            f"`{ast.unparse(call)}` is registered in {owner!r}, not `main`. A "
            "handler installed outside main cannot close over the `sentinel` "
            "built inside it."
        )
        assert call.lineno > creation.lineno, (
            f"`{ast.unparse(call)}` at line {call.lineno} runs BEFORE "
            f"`sentinel = Sentinel(...)` at line {creation.lineno}. The handler "
            "closes over `sentinel`, so a signal arriving in that window raises "
            "NameError instead of shutting the daemon down."
        )


def test_the_handler_that_is_registered_closes_over_the_sentinel(scripts_dir):
    """Ordering alone is not the invariant; the handler must USE the object.

    Registering an unrelated function after the construction would satisfy a
    pure line-order check while leaving SIGTERM unable to stop the daemon. This
    asserts the registered callable is a function defined in `main` that reads
    `sentinel`.
    """
    tree = _module(scripts_dir)
    main = next(n for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == "main")

    nested = {n.name: n for n in ast.walk(main)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n is not main}

    registrations = _signal_registrations(tree)
    checked = 0
    for call in registrations:
        assert len(call.args) == 2, (
            f"expected `signal.signal(sig, handler)`: {ast.unparse(call)}"
        )
        handler = call.args[1]
        assert isinstance(handler, ast.Name), (
            f"the handler must be a named function this test can resolve: "
            f"{ast.unparse(call)}"
        )
        assert handler.id in nested, (
            f"`{handler.id}` is not defined inside main(); it cannot be the "
            "closure over `sentinel` that SEC-011 is about."
        )
        body = nested[handler.id]
        reads = {n.id for n in ast.walk(body) if isinstance(n, ast.Name)}
        assert "sentinel" in reads, (
            f"the registered handler `{handler.id}` never mentions `sentinel`, "
            "so ordering it after the construction protects nothing. It cannot "
            "stop the daemon."
        )
        checked += 1

    assert checked >= 2, (
        f"only {checked} signal registration(s) checked. Both SIGINT and "
        "SIGTERM must reach the shutdown handler: systemd sends SIGTERM, and a "
        "daemon that only handles SIGINT is killed rather than stopped."
    )
