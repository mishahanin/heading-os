#!/usr/bin/env python3
"""SEC-015: `run_cycle` must persist state in a `finally`, not on the happy path.

A cycle that raises halfway leaves the sentinel's dedupe state unwritten, so the
next cycle re-notifies everything it already sent. The control is that the save
sits in a `finally` attached to the cycle body.

Until 2026-08-27 this file tested a substring. It scanned the lines between
`async def run_cycle` and the next `async def` for the text `finally:` and
asserted it appeared - nothing more. A `finally:` that closed a file, logged a
line, or did anything at all other than save the state passed it, and so would
a `finally:` belonging to a nested helper that happened to sit in that line
range. The control SEC-015 names was never checked.

Now parsed. `ast` was already imported and unused.
"""

import ast

from tests.security.conftest import read_file_content


def _run_cycle(scripts_dir) -> ast.AsyncFunctionDef:
    tree = ast.parse(read_file_content(scripts_dir / "sentinel.py"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_cycle":
            return node
    raise AssertionError("run_cycle is gone; this control needs re-aiming")


def _saves_state(body) -> bool:
    """True when `body` calls something.save() on a `state` attribute."""
    for node in body:
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            if isinstance(fn, ast.Attribute) and fn.attr == "save" \
                    and isinstance(fn.value, ast.Attribute) \
                    and fn.value.attr == "state":
                return True
    return False


def test_run_cycle_saves_state_in_a_finally(scripts_dir):
    fn = _run_cycle(scripts_dir)
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try) and n.finalbody]
    assert tries, (
        "run_cycle has no try/finally at all, so a cycle that raises mid-way "
        "leaves the dedupe state unwritten and the next cycle re-notifies"
    )
    assert any(_saves_state(t.finalbody) for t in tries), (
        "run_cycle has a `finally`, but nothing in it saves state. The "
        "substring this test used to look for was exactly that: a `finally:` "
        "with no idea what was inside it."
    )


def test_the_save_is_not_only_on_the_happy_path(scripts_dir):
    """A save in the try body as well is fine; a save ONLY there is the defect."""
    fn = _run_cycle(scripts_dir)
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try) and n.finalbody]
    guarded = [t for t in tries if _saves_state(t.finalbody)]
    assert guarded, "no try/finally in run_cycle saves state"
    for t in guarded:
        assert t.body, "the guarded try has an empty body; it protects nothing"
