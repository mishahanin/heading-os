#!/usr/bin/env python3
"""A test run wrote the operator's live data three times, and twice silently.

The suite has had a guard since 2026-08-27, and it has been widened twice, each
time to the directory that had just been damaged:

* 114 probe archives in the handoff directory, and the shared `.latest` pointers
  aimed at one of them. The guard was written, scoped to that directory.
* A mutation run put a `MEMORY.md` writer back into `thread.py open`; the CLI
  tests did not pin `HEADING_OS_DATA`, and the operator's 20 KB memory index
  became a 20-byte stub. The guard grew a second directory. Its own comment said
  "the hazard is the whole overlay" and then watched two.
* 2026-08-29: the shard-64 mutation harness reverted `StateManager.__init__` in
  `scripts/email-intelligence.py` to its import-time default and ran the
  email-intel tests in the main tree. Four runs of `main()` rewrote
  `outputs/operations/email-intelligence/state.json`. Nothing complained,
  because that path was not one of the two.

Measured the same day, writing into a fake overlay: of four writes, three drew
no complaint at all.

Two halves now, and neither replaces the other.

The SNAPSHOT covers the whole overlay and runs at session start and finish. It
is the only half that can see a CHILD process, which is how the handoff probes
arrived. It is a post-mortem: it cannot name the test.

The WRITE GUARD wraps the write primitives, so an in-process write raises where
it happens and the traceback names the test. It checks a substring of the path
before any resolve, because it runs on every `open()` in a suite of this size.
A relative path or a symlink walks past it, and that is deliberate rather than
overlooked: the accidents all look like an absolute path built from
`get_data_root()`, and the snapshot covers what the substring misses.
"""
import builtins
import importlib.util
import io
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GUARD = ROOT / "scripts" / "utils" / "overlay_write_guard.py"


@pytest.fixture(scope="module")
def cf():
    """The guard as a FRESH module, not the one this session armed.

    A fresh copy is the point. These tests replace `_OVERLAY_PREFIXES` to aim
    the guard at a tmp_path; doing that to the live module would take the real
    overlay out of guard for the length of the test.
    """
    spec = importlib.util.spec_from_file_location("overlay_guard_write_copy", GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.modules["overlay_guard_write_copy"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def armed(cf, monkeypatch, tmp_path):
    """The guard armed over a pretend overlay, and disarmed afterwards.

    The pretend overlay is a real directory, so an unguarded write would land
    somewhere harmless and the test would fail on the missing exception rather
    than on a crash.
    """
    overlay = tmp_path / "pretend-overlay"
    (overlay / "outputs" / "operations").mkdir(parents=True)
    # Seeded before the guard arms, so a read test has something real to read.
    (overlay / "outputs" / "readable.md").write_text("operator data\n", encoding="utf-8")
    monkeypatch.setattr(cf, "_OVERLAY_PREFIXES", (f"{overlay}{os.sep}",))
    restore = cf._install_overlay_write_guard()
    try:
        yield overlay
    finally:
        restore()


# ============================================================
# 1. Every way a write reaches the disk
# ============================================================

def test_opening_an_overlay_file_for_writing_is_refused(cf, armed):
    with pytest.raises(cf.OverlayWriteRefused):
        open(armed / "outputs" / "notes.md", "w").close()


@pytest.mark.parametrize("mode", ["w", "a", "x", "wb", "r+", "w+b"])
def test_every_write_mode_is_refused(cf, armed, mode):
    with pytest.raises(cf.OverlayWriteRefused):
        open(armed / "outputs" / "notes.md", mode).close()


def test_path_write_text_is_refused(cf, armed):
    """pathlib reaches `io.open`, not `builtins.open`. Both names are rebound."""
    with pytest.raises(cf.OverlayWriteRefused):
        (armed / "outputs" / "notes.md").write_text("x", encoding="utf-8")


def test_path_write_bytes_is_refused(cf, armed):
    with pytest.raises(cf.OverlayWriteRefused):
        (armed / "outputs" / "notes.md").write_bytes(b"x")


def test_the_atomic_write_pattern_is_refused(cf, armed, tmp_path):
    """`.tmp` then `os.replace` is the workspace's own convention for a durable
    write, so a guard that only wraps `open` misses every careful writer."""
    scratch = tmp_path / "scratch.json"
    scratch.write_text("{}", encoding="utf-8")
    with pytest.raises(cf.OverlayWriteRefused):
        os.replace(scratch, armed / "outputs" / "state.json")


def test_renaming_onto_an_overlay_path_is_refused(cf, armed, tmp_path):
    scratch = tmp_path / "scratch2.json"
    scratch.write_text("{}", encoding="utf-8")
    with pytest.raises(cf.OverlayWriteRefused):
        os.rename(scratch, armed / "outputs" / "state.json")


@pytest.mark.parametrize("remover", ["remove", "unlink"])
def test_deleting_an_overlay_file_is_refused(cf, armed, remover):
    victim = armed / "outputs" / "keep-me.md"
    getattr(os, remover)  # the guarded one
    with pytest.raises(cf.OverlayWriteRefused):
        getattr(os, remover)(victim)


# ============================================================
# 2. What the guard must NOT refuse
# ============================================================

def test_reading_the_overlay_is_allowed(armed):
    """Tests read the operator's real config and fixtures all the time. A guard
    that blocked reads would be removed within a day, so it must not."""
    target = armed / "outputs" / "readable.md"
    assert target.read_text(encoding="utf-8") == "operator data\n"
    with open(target) as handle:
        assert handle.read() == "operator data\n"


def test_writing_outside_the_overlay_is_allowed(armed, tmp_path):
    elsewhere = tmp_path / "somewhere-else.md"
    elsewhere.write_text("fine\n", encoding="utf-8")
    assert elsewhere.read_text(encoding="utf-8") == "fine\n"


def test_a_clone_with_no_overlay_arms_nothing(cf, monkeypatch, tmp_path):
    """CI has no overlay. The guard must cost nothing and claim nothing there."""
    monkeypatch.setattr(cf, "_OVERLAY_PREFIXES", ())
    cf._refuse_overlay_path(tmp_path / "anything.md", "write")   # must not raise


# ============================================================
# 3. The message has to be actionable
# ============================================================

def test_the_refusal_names_the_path_and_the_fix(cf, armed):
    with pytest.raises(cf.OverlayWriteRefused) as excinfo:
        (armed / "outputs" / "notes.md").write_text("x", encoding="utf-8")
    message = str(excinfo.value)
    assert "notes.md" in message
    assert "HEADING_OS_DATA" in message, "the message has to say what to do"


# ============================================================
# 4. The primitives are put back
# ============================================================

def test_the_guard_restores_every_primitive_it_wrapped(cf, monkeypatch, tmp_path):
    monkeypatch.setattr(cf, "_OVERLAY_PREFIXES", (f"{tmp_path}{os.sep}",))
    before = (builtins.open, io.open, os.replace, os.rename, os.remove, os.unlink)
    restore = cf._install_overlay_write_guard()
    assert builtins.open is not before[0], "the guard did not arm"
    restore()
    assert (builtins.open, io.open, os.replace, os.rename,
            os.remove, os.unlink) == before


def test_builtins_open_and_io_open_stay_the_same_object(cf, monkeypatch, tmp_path):
    """If they diverge, one of the two routes to a file is unguarded."""
    monkeypatch.setattr(cf, "_OVERLAY_PREFIXES", (f"{tmp_path}{os.sep}",))
    restore = cf._install_overlay_write_guard()
    try:
        assert builtins.open is io.open
    finally:
        restore()


# ============================================================
# 5. The guard is actually armed in THIS session, not merely defined
# ============================================================

def test_the_running_session_has_the_guard_armed_when_an_overlay_exists():
    """A guard nobody installs refuses nothing. This reads the LIVE
    `scripts/utils/overlay_write_guard.py` that pytest's conftest armed, not a
    fresh copy of it."""
    live = sys.modules.get("scripts.utils.overlay_write_guard")
    assert live is not None, (
        "the guard module is not in sys.modules, so this test cannot see the "
        "guard this session armed and must not pass quietly")
    conftest = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    assert conftest is not None and conftest._guard is live, (
        "the root conftest holds a DIFFERENT guard object than the one imported "
        "here, so every assertion below would be about a module nobody armed")
    if not live._watched_roots():
        pytest.skip("this clone has no private overlay, so nothing to guard")
    assert live._OVERLAY_PREFIXES, "an overlay is present and the guard is not armed"
    for prefix in live._OVERLAY_PREFIXES:
        with pytest.raises(live.OverlayWriteRefused):
            live._refuse_overlay_path(
                Path(prefix) / "outputs" / "never-written.md", "write")

    # A refusal function nobody wrapped the primitives with refuses nothing, and
    # that is the shape the whole file is about: the rule existed, and the place
    # it had to be applied did not have it. Asserted on the LIVE objects rather
    # than by attempting a real write, because if the guard is missing the
    # attempt would put a file in the operator's overlay to prove it.
    assert callable(live._RESTORE_WRITE_GUARD), (
        "the session never installed the write guard")
    assert builtins.open.__name__ == "guarded_open", (
        "builtins.open is unwrapped in a session that has an overlay")
    assert io.open is builtins.open, "the pathlib route is unwrapped"
    for name in ("replace", "rename", "remove", "unlink"):
        assert getattr(os, name).__name__.startswith("guarded_"), name
