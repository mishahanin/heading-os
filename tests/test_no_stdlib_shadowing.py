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

ROOT = Path(__file__).resolve().parent.parent

# Directories whose contents can become `sys.path[0]`: any directory holding a
# Python file someone might execute directly, plus every package beside it.
_SCANNED_TREES = ("scripts", ".claude/hooks")

_STDLIB = set(sys.stdlib_module_names)


def _module_files() -> list[Path]:
    return [
        p
        for tree in _SCANNED_TREES
        for p in sorted((ROOT / tree).rglob("*.py"))
        if "__pycache__" not in p.parts
    ]


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


def test_the_scan_still_finds_the_modules():
    """An empty parametrize is one silent skip, not a failure, so this gate
    would report green over zero modules. Every tree it reads is engine-only,
    so an empty result means the glob or the layout moved. 388 on 2026-08-26.
    """
    found = _module_files()

    assert len(found) >= 250, f"only {len(found)} modules reached the shadowing gate"
