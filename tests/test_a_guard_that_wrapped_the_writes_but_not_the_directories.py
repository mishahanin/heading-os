#!/usr/bin/env python3
"""The overlay write guard must refuse EVERY primitive that can create a path.

MEASURED 2026-08-31. `tests/conftest.py`'s `_install_overlay_write_guard()`
wrapped `builtins.open`, `io.open`, `os.replace`, `os.rename`, `os.remove` and
`os.unlink`, and nothing else. So a test that reached `Path.write_text` failed
loudly, while a test that reached `mkdir` or `touch` planted a real directory or
file in the operator's private data **in total silence**. `git status` does not
show an empty directory either, so nothing downstream would have surfaced it.

An AST audit the same day found 31 test-reachable modules that resolve the data
root at import time; **17 of them bite through exactly that gap.**

The fix was applied in two rounds, and the second round is the lesson. Wrapping
`os.mkdir`, `os.makedirs` and `os.rmdir` closed the directory calls, and a
by-hand probe then showed `Path.touch()` STILL wrote a file into the real
overlay: `Path.touch` never goes through `builtins.open`, it calls `os.open`
with `O_CREAT` directly. The probe left a real `PROBE-t` in the operator's data
before anyone noticed. Wrapping the pretty name and missing the primitive
underneath is how a guard reads complete and is not.

This file is the regression test for both rounds. It drives the guard by hand
rather than relying on the session-installed one, so it measures the mechanism
instead of the environment.

The guard itself moved out of `tests/conftest.py` into
`scripts/utils/overlay_write_guard.py` on the same day, with no change to what it
refuses. The account above is of where it lived when it was measured.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import overlay_write_guard as cf  # noqa: E402

# Every primitive that can bring a path into existence, or take one out. Named
# individually rather than as a count, so a reader can see what is covered.
CREATING_PRIMITIVES = (
    "os.mkdir", "os.makedirs", "Path.mkdir", "Path.touch",
    "os.open(O_CREAT)", "write_text", "os.replace", "os.rename",
)
DESTROYING_PRIMITIVES = ("os.rmdir", "os.remove", "os.unlink")


@pytest.fixture
def armed(tmp_path, monkeypatch):
    """The real guard, aimed at a pretend overlay under tmp_path.

    Aimed at tmp_path and NOT at the operator's live tree on purpose: a test
    that proves a guard by attempting real writes into real private data is one
    bug away from being the incident it is testing for. That already happened
    once here, with a by-hand probe.
    """
    pretend = tmp_path / "pretend-overlay"
    (pretend / "auto-memory").mkdir(parents=True)
    (pretend / "auto-memory" / "MEMORY.md").write_text("index\n", encoding="utf-8")
    (pretend / "spare-dir").mkdir()
    (pretend / "spare-file").write_text("x\n", encoding="utf-8")
    # Built BEFORE the guard arms, because building it afterwards is itself a
    # refused write. The read-only case below needs a database that exists.
    import sqlite3
    sqlite3.connect(pretend / "existing.db").close()

    monkeypatch.setattr(cf, "_OVERLAY_PREFIXES", (str(pretend) + os.sep,))
    restore = cf._install_overlay_write_guard()
    try:
        yield pretend
    finally:
        restore()


def _attempts(overlay: Path):
    """{name: callable} covering every primitive named in the constants above."""
    return {
        "os.mkdir": lambda: os.mkdir(overlay / "new-dir"),
        "os.makedirs": lambda: os.makedirs(overlay / "a" / "b"),
        "Path.mkdir": lambda: (overlay / "p-dir").mkdir(),
        "Path.touch": lambda: (overlay / "p-file").touch(),
        "os.open(O_CREAT)": lambda: os.open(
            overlay / "o-file", os.O_CREAT | os.O_WRONLY),
        "write_text": lambda: (overlay / "w-file").write_text("x", encoding="utf-8"),
        "os.replace": lambda: os.replace(overlay / "spare-file", overlay / "moved"),
        "os.rename": lambda: os.rename(overlay / "spare-file", overlay / "moved2"),
        "os.rmdir": lambda: os.rmdir(overlay / "spare-dir"),
        "os.remove": lambda: os.remove(overlay / "spare-file"),
        "os.unlink": lambda: (overlay / "spare-file").unlink(),
    }


def test_the_set_under_test_is_the_set_this_file_names():
    """A case list that drifts from the constants tests fewer things than it says."""
    covered = set(_attempts(Path("/nonexistent")))
    named = set(CREATING_PRIMITIVES) | set(DESTROYING_PRIMITIVES)
    assert covered == named, (
        f"the attempt table and the named primitives disagree: "
        f"only in table {sorted(covered - named)}, only named {sorted(named - covered)}")


@pytest.mark.parametrize("primitive", CREATING_PRIMITIVES + DESTROYING_PRIMITIVES)
def test_every_path_primitive_is_refused_inside_the_overlay(armed, primitive):
    """One case per primitive, so a regression names the one that reopened."""
    attempt = _attempts(armed)[primitive]
    # The exact exception, not a blind `Exception`. A primitive that reached the
    # filesystem and failed on its own (a missing parent, a permission error)
    # would satisfy a blind catch and read as "the guard refused it".
    with pytest.raises(cf.OverlayWriteRefused):
        attempt()


def test_a_refused_attempt_leaves_nothing_behind(armed):
    """Refusing after the write has landed is not refusing.

    `Path.touch` is why this is a separate assertion: the round-one guard let it
    through entirely, and the way that surfaced was a real file appearing in the
    operator's overlay, not an exception anybody saw.
    """
    before = {p.relative_to(armed).as_posix() for p in armed.rglob("*")}
    for attempt in _attempts(armed).values():
        with pytest.raises(cf.OverlayWriteRefused):
            attempt()
    after = {p.relative_to(armed).as_posix() for p in armed.rglob("*")}
    assert after == before, (
        f"a refused attempt still changed the tree. Appeared: "
        f"{sorted(after - before)}. Vanished: {sorted(before - after)}")


def test_reads_inside_the_overlay_still_work(armed):
    """The negative case. A guard that refuses reads would be caught by half the
    suite going red, and someone would then loosen it in a hurry."""
    assert (armed / "auto-memory" / "MEMORY.md").read_text(encoding="utf-8") == "index\n"
    assert os.path.isdir(armed / "auto-memory")
    fd = os.open(armed / "auto-memory" / "MEMORY.md", os.O_RDONLY)
    os.close(fd)


def test_writes_outside_the_overlay_still_work(armed, tmp_path):
    """The other negative case, and the one that matters most in practice: the
    suite writes constantly under tmp_path, and a guard scoped too widely would
    make the whole thing unusable."""
    elsewhere = tmp_path / "not-the-overlay"
    elsewhere.mkdir()
    (elsewhere / "f").touch()
    (elsewhere / "g").write_text("x", encoding="utf-8")
    os.replace(elsewhere / "g", elsewhere / "h")
    (elsewhere / "f").unlink()
    (elsewhere / "h").unlink()
    elsewhere.rmdir()
    assert not elsewhere.exists()


def test_a_write_only_database_connection_is_refused(armed):
    """`sqlite3.connect` opens its file in C and never reaches `os.open`.

    MEASURED 2026-08-31: with every other primitive wrapped, it created a real
    database in the operator's overlay and reported ALLOWED.
    """
    import sqlite3

    with pytest.raises(cf.OverlayWriteRefused):
        sqlite3.connect(armed / "new.db")
    assert not (armed / "new.db").exists(), "refused, and yet the file appeared"

    existing = armed / "auto-memory" / "MEMORY.md"
    with pytest.raises(cf.OverlayWriteRefused):
        sqlite3.connect(existing)


def test_a_read_only_database_connection_is_allowed(armed):
    """The negative case, and it is load-bearing rather than decorative.

    `scripts/utils/sqlite_uri.read_only_uri()` opens databases with `?mode=ro`
    and `uri=True` on purpose, and its callers (the Chromium and Firefox cookie
    readers, the CodeGraph symbol source, and the retired
    `.claude/hooks/memory-inject.py` before them) depend on that working.
    Refusing it would be the over-friction that gets a guard switched off, so
    the guard reads the mode.
    """
    import sqlite3

    db = armed / "existing.db"
    conn = sqlite3.connect(f"{db.absolute().as_uri()}?mode=ro", uri=True)
    conn.close()
    assert sqlite3.connect(":memory:") is not None


def test_a_child_process_that_could_reach_the_overlay_is_recorded_not_refused(
        monkeypatch, tmp_path):
    """A child writes outside this interpreter, so nothing here can stop it.

    Recording is the whole point: it turns "the overlay changed and nothing can
    say who" into a named suspect. Refusing instead would change what a run is
    permitted to do, and could hide a real defect behind a harness error.

    Deliberately WITHOUT the `armed` fixture. The recorder is session-level and
    production installs exactly one guard; `armed` would install a second whose
    `_GuardedPopen` subclasses the first, so every spawn records twice. Measured:
    one `run` produced two records under the nested fixture. Testing the nested
    shape would measure the fixture, not the code that ships.
    """
    import subprocess
    import sys

    monkeypatch.setattr(cf, "_CHILD_SPAWNS", [])
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_x.py::test_y (call)")
    # The suite pins HEADING_OS_DATA at the real overlay, and `armed` aims the
    # guard at a pretend one, so an inheriting child would look SAFE under this
    # fixture. Removing the variable is what makes it a genuine suspect: a child
    # with nothing pinned resolves the operator's own tree.
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)

    subprocess.run([sys.executable, "-c", "pass"], check=True)
    assert len(cf._CHILD_SPAWNS) == 1, "an inheriting child was not recorded"
    assert cf._CHILD_SPAWNS[0][0] == "tests/test_x.py::test_y", (
        "the record does not name the test that spawned it, which is the only "
        "thing it exists to add")

    # A real directory the test owns, not a literal under /tmp: the point is
    # only that the pin resolves somewhere OTHER than an overlay prefix.
    elsewhere = tmp_path / "pinned-elsewhere"
    elsewhere.mkdir()
    subprocess.run([sys.executable, "-c", "pass"], check=True,
                   env={**os.environ, "HEADING_OS_DATA": str(elsewhere)})
    assert len(cf._CHILD_SPAWNS) == 1, (
        "a child pinned away from the live overlay was recorded as a suspect; "
        "a suspect list that names everything names nothing")

    subprocess.Popen([sys.executable, "-c", "pass"]).wait()
    assert len(cf._CHILD_SPAWNS) == 2, "a direct Popen was not recorded"


def test_run_and_popen_do_not_double_count(monkeypatch):
    """`subprocess.run` builds a `Popen`, so wrapping both counted every run
    twice. Measured: four spawns produced five records.

    No `armed` fixture, for the reason given above: nesting two guards
    reintroduces the double count this test exists to forbid."""
    import subprocess
    import sys

    monkeypatch.setattr(cf, "_CHILD_SPAWNS", [])
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    subprocess.run([sys.executable, "-c", "pass"], check=True)
    assert len(cf._CHILD_SPAWNS) == 1, (
        f"one `run` produced {len(cf._CHILD_SPAWNS)} records; only the Popen "
        f"primitive should be wrapped, never the wrapper over it")


def _primitive_table():
    import builtins
    import io
    import sqlite3
    import subprocess

    return {
        "builtins.open": builtins.open, "io.open": io.open,
        "os.replace": os.replace, "os.rename": os.rename,
        "os.remove": os.remove, "os.unlink": os.unlink,
        "os.mkdir": os.mkdir, "os.makedirs": os.makedirs,
        "os.rmdir": os.rmdir, "os.open": os.open,
        "sqlite3.connect": sqlite3.connect, "subprocess.Popen": subprocess.Popen,
    }


def test_arming_wraps_every_primitive_and_restore_puts_each_one_back(monkeypatch, tmp_path):
    """A guard that does not restore poisons every later test in the worker.

    Same family as the `sys.path` leak measured the same day: an interpreter-wide
    rebind left in place is invisible in file order and only red under a shuffle.

    This compares object identity against the state BEFORE arming, never against
    "unwrapped". `pytest_sessionstart` installs the real guard for the whole
    session, so the primitives are ALREADY the guard's wrappers when this test
    starts. The first draft of this test asserted they were bare afterwards and
    went red for that reason - the assertion was wrong, not the guard.
    """
    pretend = tmp_path / "nested-overlay"
    pretend.mkdir()
    monkeypatch.setattr(cf, "_OVERLAY_PREFIXES", (str(pretend) + os.sep,))

    before = _primitive_table()
    restore = cf._install_overlay_write_guard()
    during = _primitive_table()
    try:
        changed = [n for n in before if during[n] is not before[n]]
        assert sorted(changed) == sorted(before), (
            f"arming left these primitives untouched, so nothing guards them: "
            f"{sorted(set(before) - set(changed))}")
    finally:
        restore()

    after = _primitive_table()
    leaked = [n for n in before if after[n] is not before[n]]
    assert not leaked, (
        f"restore() did not put these back: {leaked}. Every test that runs after "
        f"this one in this worker inherits the stale wrapper.")
