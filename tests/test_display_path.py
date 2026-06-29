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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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
