#!/usr/bin/env python3
"""Unit tests for scripts.utils.workspace.display_path -- the systemic data-root
seam helper.

After the engine/data split a workspace file may live under the ENGINE root, the
DATA root, or the corporate root. display_path() must resolve a readable relative
path against whichever root actually contains the file, and degrade to the
absolute path rather than raise ValueError (the bug class that hit
knowledge-health, capture-design-exemplars, odin-skill-proposal, council-aggregate).

Standalone-runnable, plain asserts.
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import workspace as _workspace
from scripts.utils.workspace import display_path, get_data_root, get_workspace_root


def test_data_root_path_is_relative():
    p = get_data_root() / "knowledge" / "technology" / "example.md"
    assert display_path(p) == "knowledge/technology/example.md"


def test_engine_root_path_is_relative():
    p = get_workspace_root() / "scripts" / "knowledge-health.py"
    assert display_path(p) == "scripts/knowledge-health.py"


def test_unrelated_path_falls_back_to_absolute():
    p = Path("/tmp/definitely/outside/any/workspace/root/x.md")  # noqa: S108 test fixture for fallback path
    # Must not raise; returns the path as a string.
    assert display_path(p) == str(p)


def test_accepts_string_input():
    p = get_data_root() / "outputs" / "operations" / "council" / "x.md"
    assert display_path(str(p)) == "outputs/operations/council/x.md"


def test_corporate_root_path_is_relative():
    """The third leg of the resolver, which nothing else reaches.

    display_path() tries data, engine, then corporate. MEASURED 2026-09-01:
    deleting `get_corporate_root` from that tuple left every test in this file
    and every other test in the repository that names `display_path` green.

    It survives because in BOTH shipped topologies the third getter is shadowed
    by one of the first two: on a CEO workspace `get_corporate_root()` returns
    the data root, and on an exec workspace it returns
    `<engine>/.corporate-repo`, which is under the engine root and so already
    matched. The leg is therefore unreachable today, and this test says so
    rather than implying it guards a live path. What it does guard is the
    CONTRACT the docstring states, so a `get_corporate_root()` that later
    resolves outside both roots is resolved rather than printed absolute.
    """
    original = _workspace.get_corporate_root
    with tempfile.TemporaryDirectory() as raw:
        outside = Path(raw).resolve()
        _workspace.get_corporate_root = lambda: outside
        try:
            got = display_path(outside / "datastore" / "brand" / "logo.svg")
        finally:
            _workspace.get_corporate_root = original
    assert got == "datastore/brand/logo.svg", got


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  [OK ] {name}")
            except AssertionError as e:
                failures += 1
                print(f"  [FAIL] {name}: {e}")
    sys.exit(1 if failures else 0)
