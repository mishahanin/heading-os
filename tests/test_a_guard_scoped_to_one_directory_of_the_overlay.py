#!/usr/bin/env python3
"""The session guard watched one directory while the hazard was the whole overlay.

`tests/conftest.py` gained a session-start/session-finish check on 2026-08-27,
after 114 probe handoffs were found in the operator's live
`outputs/operations/handoff-archive/`. It listed names in that one directory.

Hours later, on the same day, the second instance of the same hazard landed
somewhere else and in a shape the check could not see. A mutation-testing run put
the retired `MEMORY.md` writer back into `thread.py open`, to prove the guard
against it. The CLI tests that call `open` pinned `THREADS_ROOT` but not
`HEADING_OS_DATA`, so the mutant resolved `get_data_root()` to the operator's real
overlay and truncated a 20,828-byte memory index to a 20-byte stub. It was
restored from the session's own copy and verified pointer-for-pointer: 217
pointers, 217 files, no orphan and no dangling link.

Two things were wrong, and both are fixed:

* The guard's SCOPE. One directory, when anything resolving `get_data_root()`
  can write anywhere under the overlay. `auto-memory/` now joins it.
* The guard's SHAPE. It compared the set of names. A truncation in place adds no
  name and removes none, so the destruction above would have passed a set
  comparison cleanly. Sizes are compared too.

The per-test fix is in the fixtures: every thread test now pins
`HEADING_OS_DATA` at a tmp directory, whether or not today's code reaches it.
This file covers the backstop.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONFTEST = ROOT / "tests" / "conftest.py"


@pytest.fixture(scope="module")
def cf():
    """Load conftest.py as a plain module, under a name pytest is not using."""
    spec = importlib.util.spec_from_file_location("conftest_under_test", CONFTEST)
    module = importlib.util.module_from_spec(spec)
    sys.modules["conftest_under_test"] = module
    spec.loader.exec_module(module)
    return module


def _snap(**entries):
    """A snapshot shaped like `_watch_snapshot()` output."""
    return {
        label: (Path(f"/live/{label}"), files)
        for label, files in entries.items()
    }


# ============================================================
# The shape: a rewrite in place must be seen
# ============================================================

def test_a_truncated_file_is_reported(cf):
    """The exact destruction of 2026-08-27: same name, 20,828 bytes to 20."""
    before = _snap(**{"auto-memory index": {"MEMORY.md": 20828}})
    after = _snap(**{"auto-memory index": {"MEMORY.md": 20}})
    complaints = cf.watch_complaints(before, after)
    assert len(complaints) == 1
    assert "rewrote 1 file(s)" in complaints[0]
    assert "MEMORY.md" in complaints[0]


def test_a_set_comparison_alone_would_have_missed_it(cf):
    """States the reason the shape changed, by measuring the old shape."""
    before = _snap(**{"auto-memory index": {"MEMORY.md": 20828}})
    after = _snap(**{"auto-memory index": {"MEMORY.md": 20}})
    assert set(before["auto-memory index"][1]) == set(after["auto-memory index"][1])
    assert cf.watch_complaints(before, after)


def test_an_unchanged_overlay_says_nothing(cf):
    same = _snap(**{"auto-memory index": {"MEMORY.md": 20828, "a.md": 10}})
    assert cf.watch_complaints(same, same) == []


def test_a_new_file_is_reported(cf):
    before = _snap(**{"handoff archive": {}})
    after = _snap(**{"handoff archive": {"2026-08-27_probe.md": None}})
    (complaint,) = cf.watch_complaints(before, after)
    assert "wrote 1 file(s)" in complaint
    assert "2026-08-27_probe.md" in complaint


def test_a_deleted_file_is_reported(cf):
    before = _snap(**{"auto-memory index": {"MEMORY.md": 20828}})
    after = _snap(**{"auto-memory index": {}})
    (complaint,) = cf.watch_complaints(before, after)
    assert "deleted 1 file(s)" in complaint


def test_a_directory_that_vanishes_is_reported(cf):
    """Removing the whole archive must not read as a clean run."""
    before = _snap(**{"auto-memory index": {"MEMORY.md": 20828}})
    (complaint,) = cf.watch_complaints(before, {})
    assert "disappeared during the run" in complaint


def test_every_watched_directory_is_diffed(cf):
    """Two directories, two independent complaints."""
    before = _snap(**{"handoff archive": {}, "auto-memory index": {"MEMORY.md": 20828}})
    after = _snap(**{
        "handoff archive": {"probe.md": None},
        "auto-memory index": {"MEMORY.md": 20},
    })
    complaints = cf.watch_complaints(before, after)
    assert len(complaints) == 2
    assert any("handoff archive" in c for c in complaints)
    assert any("auto-memory index" in c for c in complaints)


# ============================================================
# The scope: the auto-memory index is watched at all
# ============================================================

def test_the_auto_memory_index_is_on_the_watch_list(cf):
    watched = {label: parts for label, parts, _ in cf._WATCH_DIRS}
    assert watched["auto-memory index"] == ("auto-memory",)
    assert watched["handoff archive"] == ("outputs", "operations", "handoff-archive")


def test_the_auto_memory_watch_records_sizes(cf):
    """A name-only watch on this directory would repeat the 2026-08-27 miss."""
    with_size = {label: flag for label, _, flag in cf._WATCH_DIRS}
    assert with_size["auto-memory index"] is True


def test_a_clone_with_no_overlay_watches_nothing(cf, monkeypatch, tmp_path):
    """CI has no data overlay, so the guard must cost nothing and claim nothing."""
    from scripts.utils import paths

    monkeypatch.setattr(paths, "data_overlay_present", lambda: False)
    assert cf._watch_snapshot() == {}
    assert cf.watch_complaints({}, {}) == []


# ============================================================
# The per-test fix: the thread fixtures pin the data root
# ============================================================

@pytest.mark.parametrize("rel", [
    "tests/test_thread_cli.py",
    "tests/test_thread_quiet_period.py",
    "tests/test_a_closed_thread_that_came_back.py",
    "tests/test_a_reopen_that_unmuted_a_quiet_thread.py",
])
def test_every_thread_test_file_pins_the_data_root(rel):
    """Asked of the source because the pin is an ABSENCE of a write.

    A test cannot observe "nothing reached the overlay" without reaching for the
    overlay, which is the thing being prevented. What is checkable is that each
    file sets `HEADING_OS_DATA` where it sets `THREADS_ROOT` or patches
    `_threads_root`.
    """
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert "HEADING_OS_DATA" in src, (
        f"{rel} sets up a threads root without pinning the data root; a mutation "
        f"or a future code path then writes into the operator's live overlay"
    )
