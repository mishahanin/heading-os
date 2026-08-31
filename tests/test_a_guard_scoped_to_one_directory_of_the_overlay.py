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

The guard moved out of `tests/conftest.py` into
`scripts/utils/overlay_write_guard.py` on 2026-08-31, with no change to what it
refuses. The dates above are of where it lived at the time.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GUARD = ROOT / "scripts" / "utils" / "overlay_write_guard.py"


@pytest.fixture(scope="module")
def cf():
    """The guard as a FRESH module, so replacing `_watched_roots` cannot
    repoint the copy this session armed over the operator's real overlay."""
    spec = importlib.util.spec_from_file_location("overlay_guard_scope_copy", GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.modules["overlay_guard_scope_copy"] = module
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
    assert "1 file(s) rewrote" in complaints[0]
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
    assert "1 file(s) appeared" in complaint
    assert "2026-08-27_probe.md" in complaint


def test_a_deleted_file_is_reported(cf):
    before = _snap(**{"auto-memory index": {"MEMORY.md": 20828}})
    after = _snap(**{"auto-memory index": {}})
    (complaint,) = cf.watch_complaints(before, after)
    assert "1 file(s) vanished" in complaint


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

def test_there_is_no_list_of_interesting_directories_any_more(cf):
    """The scope was a list of two, and the hazard was never a list.

    Both earlier versions of this guard named the directories they had already
    been burned by. On 2026-08-29 the third was found the same way: a mutation
    run rewrote the email-intel state file in the live overlay and the guard was
    silent, because that path was not one of the two. A list cannot be right
    here; only the whole overlay can.
    """
    assert not hasattr(cf, "_WATCH_DIRS"), (
        "an allowlist of watched directories came back; the unit is the overlay")


@pytest.mark.parametrize("name", [
    ".git", ".memory-index", ".memory-index-code", ".codegraph", ".sessions",
])
def test_only_rebuildable_or_runtime_trees_are_left_out(cf, name):
    """Each exclusion carries a reason, and none of them is operator data."""
    assert name in cf._UNWATCHED
    assert cf._UNWATCHED[name].strip(), f"{name} is excluded with no reason given"


@pytest.mark.parametrize("rel", [
    "outputs/operations/email-intelligence/state.json",
    "crm/contacts/a-person.md",
    "threads/business/a-thread.md",
    "auto-memory/MEMORY.md",
    "context/pipeline.md",
    "knowledge/a-note.md",
])
def test_a_write_anywhere_in_the_overlay_is_reported(cf, monkeypatch, tmp_path, rel):
    """Drive the real snapshot over a fake overlay, one write at a time."""
    # One knob. `_watched_roots()` is the single place the snapshot asks which
    # roots exist, so faking it drives the real walk over a pretend overlay
    # without faking the two resolvers behind it -- and without the structural
    # root (the operator's actual data) joining the result.
    monkeypatch.setattr(cf, "_watched_roots", lambda: {"operator overlay": tmp_path})
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("original\n", encoding="utf-8")

    before = cf._watch_snapshot()
    target.write_text("REWRITTEN BY A TEST RUN\n" * 4, encoding="utf-8")
    complaints = cf.watch_complaints(before, cf._watch_snapshot())

    assert complaints, f"{rel} was rewritten and nothing said so"
    assert rel in complaints[0]


def test_the_snapshot_records_a_size_for_every_file(cf, monkeypatch, tmp_path):
    """A name-only watch is how the memory index was lost: same name, 20 bytes."""
    # One knob. `_watched_roots()` is the single place the snapshot asks which
    # roots exist, so faking it drives the real walk over a pretend overlay
    # without faking the two resolvers behind it -- and without the structural
    # root (the operator's actual data) joining the result.
    monkeypatch.setattr(cf, "_watched_roots", lambda: {"operator overlay": tmp_path})
    (tmp_path / "auto-memory").mkdir()
    (tmp_path / "auto-memory" / "MEMORY.md").write_text("x" * 500, encoding="utf-8")

    snapshot = cf._watch_snapshot()
    assert list(snapshot) == ["operator overlay"]
    _directory, entries = snapshot["operator overlay"]
    assert entries == {"auto-memory/MEMORY.md": 500}


def test_an_excluded_tree_is_not_snapshotted(cf, monkeypatch, tmp_path):
    """Otherwise a rebuilt index fails an honest run and the guard gets removed."""
    # One knob. `_watched_roots()` is the single place the snapshot asks which
    # roots exist, so faking it drives the real walk over a pretend overlay
    # without faking the two resolvers behind it -- and without the structural
    # root (the operator's actual data) joining the result.
    monkeypatch.setattr(cf, "_watched_roots", lambda: {"operator overlay": tmp_path})
    (tmp_path / ".memory-index").mkdir()
    (tmp_path / ".memory-index" / "index.db").write_bytes(b"0" * 10)

    _directory, entries = cf._watch_snapshot()["operator overlay"]
    assert entries == {}


def test_a_clone_with_no_overlay_watches_nothing(cf, monkeypatch, tmp_path):
    """CI has no data overlay, so the guard must cost nothing and claim nothing."""
    monkeypatch.setattr(cf, "_watched_roots", dict)
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
