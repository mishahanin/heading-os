"""No module in the script tree may take a standard-library module's name.

Python puts the executed script's OWN directory first on `sys.path`. So the
moment anyone runs `python scripts/utils/paths.py`, every file in
`scripts/utils/` outranks the standard library for the rest of that process --
including for the standard library's own internal imports.

That is not theoretical. `scripts/utils/operator.py` broke every direct run of
every module in that directory on the service VM:

    import logging -> re -> enum -> `from operator import or_`
      -> scripts/utils/operator.py -> `from functools import lru_cache`
      -> functools -> collections -> `from operator import eq`
      -> ImportError: cannot import name 'eq' from partially initialized
         module 'operator'

`scripts/utils/html.py` was worse and quieter: it could not import its own
dependency, because `from html.parser import HTMLParser` resolved back to
itself ("'html' is not a package").

The trap hides well. It never fires through the normal package path
(`from scripts.utils.x import y`), so every daemon and every test stayed green.
And it did not reproduce on the development laptop at all, because the distro's
`.pth` files import `operator` during interpreter startup, so the name was
already in `sys.modules` before any workspace file could claim it. A bare venv
on the server had no such head start. An environment-dependent import failure
is the kind of thing a guard should catch, not a person.

The four offenders were renamed on 2026-08-09: `operator` -> `operator_identity`,
`html` -> `html_text`, `trace` -> `tracing`, `venv` -> `venv_guard`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.repo_files import tracked_paths

ROOT = Path(__file__).resolve().parent.parent

# Directories whose contents can become `sys.path[0]`: any directory holding a
# Python file someone might execute directly, plus every package beside it.
# Each carries its OWN floor. A single floor over the union was satisfied by
# `scripts/` alone (386 files against a floor of 250), so `.claude/hooks/` could
# contribute zero modules and the gate would still report green over a tree it
# had stopped reading. Counts measured 2026-09-01; the floors sit well under
# them so retiring a batch of files does not fail this test.
_SCANNED_TREES = {"scripts": 250, ".claude/hooks": 10}

_STDLIB = set(sys.stdlib_module_names)


def _tree_files(tree: str) -> list[Path]:
    """Every tracked `.py` under one scanned tree.

    Through git, not a bare `rglob`: an agent worktree or a scratch copy under
    an ignored path would otherwise join the corpus and make both the floor and
    the failure message meaningless (`tests/test_a_walker_that_never_asked_git.py`).
    """
    return [p for p in tracked_paths((f"{tree}/**/*.py", f"{tree}/*.py"))
            if "__pycache__" not in p.parts]


def _module_files() -> list[Path]:
    return sorted({p for tree in _SCANNED_TREES for p in _tree_files(tree)})


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_module_shadows_the_standard_library(path):
    assert path.stem not in _STDLIB, (
        f"{path.relative_to(ROOT)} takes the name of the standard-library module "
        f"{path.stem!r}. Running any file in {path.parent.relative_to(ROOT)}/ directly "
        f"puts that directory first on sys.path, so this file would answer the "
        f"standard library's own imports of {path.stem!r} -- and the failure surfaces "
        f"only on machines where {path.stem!r} is not already loaded at startup. "
        f"Give it a name of its own (operator -> operator_identity, "
        f"html -> html_text, trace -> tracing, venv -> venv_guard)."
    )


@pytest.mark.parametrize("tree,floor", sorted(_SCANNED_TREES.items()))
def test_the_scan_still_finds_the_modules(tree, floor):
    """An empty parametrize is one silent skip, not a failure, so this gate
    would report green over zero modules. Every tree it reads is engine-only,
    so an empty result means the glob or the layout moved.

    Per tree, not over the union. Measured 2026-09-01: scripts 386,
    .claude/hooks 17. Under the old single floor of 250 over the union,
    `.claude/hooks/` could drop to zero files and this test still passed.
    """
    found = _tree_files(tree)

    assert len(found) >= floor, (
        f"only {len(found)} modules under {tree}/ reached the shadowing gate "
        f"(floor {floor})")
