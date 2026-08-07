"""The retrieval bump must not make the reconcile hook revert a real edit.

`.claude/hooks/memory-reconcile.py` resolves a canonical-vs-native conflict
NEWEST-WINS on mtime, and `atomic_write_text` ends in `os.replace`, which
stamps a new mtime. Once the bump moved from a hand-invoked skill to every
user prompt, that combination meant: edit a memory in the native harness store
during a session, have the retriever surface it, and the bump on the canonical
copy makes the canonical side look newer — so the next SessionStart copies the
STALE canonical text over the real edit. Content lost on the assistant's own
initiative, in the mechanism whose whole premise is that memory is never lost.

Run: .venv/bin/python -m pytest tests/test_memory_reconcile_bump.py
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from scripts.utils.memory_touch import touch_if_stale

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "memory-reconcile.py"


def load_hook():
    spec = importlib.util.spec_from_file_location("memory_reconcile_mod", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fact(body: str) -> str:
    return (
        "---\n"
        "name: example-fact\n"
        "description: fixture fact\n"
        "metadata:\n"
        "  node_type: memory\n"
        "  type: feedback\n"
        "  access_count: 3\n"
        "---\n\n"
        f"{body}\n"
    )


@pytest.fixture
def stores(tmp_path: Path) -> tuple[Path, Path]:
    """A canonical and a native store holding the same memory, same mtime."""
    canonical = tmp_path / "auto-memory"
    native = tmp_path / "native-memory"
    canonical.mkdir()
    native.mkdir()
    stamp = 1_700_000_000
    for d in (canonical, native):
        f = d / "example-fact.md"
        f.write_text(_fact("The original fact."), encoding="utf-8")
        os.utime(f, (stamp, stamp))
    return canonical, native


def test_a_bump_does_not_revert_a_newer_native_edit(stores):
    canonical, native = stores
    mod = load_hook()

    # A session edits the memory in the native harness store. Real content
    # change, so it legitimately carries a newer mtime.
    native_file = native / "example-fact.md"
    native_file.write_text(_fact("The CORRECTED fact."), encoding="utf-8")
    os.utime(native_file, (1_700_000_600, 1_700_000_600))

    # The retriever then surfaces the memory and the canonical copy is bumped.
    assert touch_if_stale("example-fact.md", canonical, "2026-08-08") == 4

    mod.reconcile(native, canonical)

    assert "CORRECTED" in native_file.read_text(encoding="utf-8")
    assert "CORRECTED" in (canonical / "example-fact.md").read_text(encoding="utf-8")


def test_the_bump_still_propagates_when_nothing_else_changed(stores):
    """The guard above must not be bought by making the bump invisible: with no
    competing edit, the bumped canonical copy still reaches the native store."""
    canonical, native = stores
    mod = load_hook()

    assert touch_if_stale("example-fact.md", canonical, "2026-08-08") == 4
    mod.reconcile(native, canonical)

    assert "access_count: 4" in (native / "example-fact.md").read_text(encoding="utf-8")
