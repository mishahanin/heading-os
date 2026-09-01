#!/usr/bin/env python3
"""Three defects in the guard that protects the operator's live data.

All three were found by audit on 2026-08-31 and all three are MEASURED here.

**F2, the expensive one.** `shutil.rmtree` of a directory inside the protected
overlay deleted every file in it and THEN refused. Measured before the fix, on
a pretend overlay:

    rmtree REFUSED: OverlayWriteRefused
    victim dir still exists: True | children left: []

Both files were already gone. The traceback is indistinguishable from a write
the guard actually prevented, so the guard reported that it had saved data it
had just destroyed.

The cause is not a bug in the prefix test; it is the capability-set
registration, which is correct and stays. Registering the wrappers in
`os.supports_dir_fd` keeps `shutil._use_fd_functions` True, and that routes
`_rmtree_safe_fd` through `os.unlink(entry.name, dir_fd=topfd)` with a RELATIVE
name. The prefix test is a substring test, and `"a.txt"` contains no prefix. So
only the closing full-path `os.rmdir(path)` was ever checked.

**F1.** The wrappers were installed TWICE in every interpreter, three times
under pytest. CPython's `site.py` processes a venv's site-packages twice
(`site.venv()` then `site.main()`), and `known_paths` de-duplicates sys.path
ENTRIES, not `.pth` execution. Measured before the fix, in a plain
`.venv/bin/python`:

    builtins.open layers: 2
    Popen mro: ['_GuardedPopen', '_GuardedPopen', 'Popen', 'object']
    modules bound to overlay_write_guard.py: ['_heading_os_overlay_guard']

One module in `sys.modules`, two layers. The install-once check is a module
global, so copy #2 could not see copy #1's install, and copy #1 was unreachable
for the life of the interpreter: its `restore()` could never be called by
anything. `disarm()` unwound one of three.

**F3.** `arm(MODE_OFF)` on an already-armed module refused EVERYTHING. It set
the mode and returned without clearing the prefixes or the wrappers, and
`_refuse_overlay_path` branched on RECORD and GUARD only before falling through
to an unconditional raise. The one documented escape an operator has when the
guard refuses their work wrongly inverted into a total block.

Why the existing tests could not see any of it: `test_arming_twice_installs_
one_layer_of_wrappers` was written for F1 and took `pristine = builtins.open`
as its baseline, a baseline already three layers deep, so it compared two
wrapped objects. `test_off_mode_installs_nothing_at_all` covers only the
never-armed case. And the directory-wrapper file covers `mkdir`, `Path.touch`
and `os.open` with absolute paths; no test anywhere passed a `dir_fd`.

Two things learned while fixing, both pinned below because both were wrong once:

* A second module copy must TAKE OVER the wrappers, not delegate to them. A
  delegating draft (push mode and prefixes into the owner, leave its wrappers
  live) failed loudly: two copies have two distinct `OverlayWriteRefused`
  CLASSES, so `pytest.raises` matched nothing.
* Taking over must HAND BACK on `disarm()`, and hand back the same objects. A
  draft that re-installed instead read to the identity test as a leftover
  layer, and a draft that snapshotted after the restore handed back the bare
  primitives, leaving the process unguarded for every later test.
"""
from __future__ import annotations

import builtins
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD_SOURCE = ROOT / "scripts" / "utils" / "overlay_write_guard.py"


def _load(name: str):
    """A private copy, so a test never disturbs the live session guard."""
    spec = importlib.util.spec_from_file_location(name, GUARD_SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def armed(tmp_path):
    """A copy armed REFUSE over a pretend overlay under `tmp_path`.

    Armed by hand rather than through `arm()`: `arm()` resolves the REAL data
    root, and a test that pointed this guard at the operator's own overlay
    would be the accident the guard exists to prevent.
    """
    guard = _load("guard_under_test")
    pretend = tmp_path / "pretend"
    (pretend / "victim").mkdir(parents=True)
    # The victim files are written BEFORE arming, and that is not incidental:
    # a guard that refuses its own fixture's setup would make every assertion
    # below pass for the wrong reason.
    for name in ("a.txt", "b.txt", "c.txt"):
        (pretend / "victim" / name).write_bytes(b"operator data\n")
    guard._MODE = guard.MODE_REFUSE
    guard._OVERLAY_PREFIXES = (str(pretend) + os.sep,)
    guard._RESTORE_WRITE_GUARD = guard._install_overlay_write_guard()
    guard._mark_owner(vars(guard))
    try:
        yield guard, pretend
    finally:
        guard.disarm()


# ------------------------------------------------------------
# F2: the delete that happened before the refusal
# ------------------------------------------------------------

def test_rmtree_inside_the_overlay_deletes_nothing(armed):
    """The headline. Refusing AFTER the delete is not refusing."""
    guard, pretend = armed
    victim = pretend / "victim"

    with pytest.raises(guard.OverlayWriteRefused):
        shutil.rmtree(victim)

    survivors = sorted(p.name for p in victim.iterdir())
    assert survivors == ["a.txt", "b.txt", "c.txt"], (
        "rmtree emptied a protected directory and then raised. The refusal is "
        "worthless if the data is already gone: resolve a relative name "
        f"against its dir_fd before the prefix test. Survivors: {survivors}")


def test_the_fd_walk_is_the_one_being_exercised(armed):
    """Without this, the test above could pass over the legacy full-path walk.

    `shutil` chooses its algorithm by identity membership in
    `os.supports_dir_fd`. If the wrappers stopped being registered there the
    fd-relative path would never run, and the test above would be green while
    the defect it names went untested.
    """
    _guard, _pretend = armed
    assert shutil._use_fd_functions is True, (
        "shutil is taking its legacy full-path walk, so no test here reaches "
        "the fd-relative unlink that the F2 defect lived in")


def test_an_unlink_through_a_dir_fd_is_refused(armed):
    """The primitive underneath rmtree, driven directly."""
    guard, pretend = armed
    victim = pretend / "victim"

    fd = os.open(str(victim), os.O_RDONLY)
    try:
        with pytest.raises(guard.OverlayWriteRefused):
            os.unlink("c.txt", dir_fd=fd)
    finally:
        os.close(fd)
    assert (victim / "c.txt").is_file()


def test_a_delete_outside_the_overlay_still_works(tmp_path, armed):
    """The other direction, and it is not optional.

    Over-friction is how a guard gets switched off, after which nothing guards
    the real thing. A fix that refused every `rmtree` in the suite would pass
    every assertion above and be worse than the defect.
    """
    _guard, _pretend = armed
    outside = tmp_path / "outside" / "sub"
    outside.mkdir(parents=True)
    (outside / "x.txt").write_bytes(b"not the operator's\n")

    shutil.rmtree(tmp_path / "outside")
    assert not (tmp_path / "outside").exists()


def test_an_unresolvable_descriptor_is_refused_not_waved_through(armed,
                                                                monkeypatch):
    """When the guard cannot tell, it must not guess in the permissive direction.

    A closed or bogus descriptor cannot be resolved to a directory. The choice
    then is refuse or allow, and allowing means deleting the operator's data
    without being able to say whether it was theirs.
    """
    guard, _pretend = armed
    monkeypatch.setattr(guard.os, "readlink",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("no")))
    with pytest.raises(guard.OverlayWriteRefused):
        guard._refuse_overlay_path("c.txt", "delete", dir_fd=4242)


def test_the_resolver_leaves_an_absolute_path_alone(armed):
    """A dir_fd is ignored for an absolute name, exactly as the kernel does."""
    guard, pretend = armed
    absolute = str(pretend / "victim" / "d.txt")
    assert guard._resolve_dir_fd_path(absolute, 4242) == absolute
    assert guard._resolve_dir_fd_path(absolute, None) == absolute


# ------------------------------------------------------------
# F1: one layer per process, not one per module copy
# ------------------------------------------------------------

def _wrapper_depth(argv_probe: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", argv_probe],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )


LAYER_PROBE = r"""
import builtins, subprocess, sys
def layers(fn):
    seen, count = set(), 0
    while fn is not None and id(fn) not in seen:
        seen.add(id(fn))
        if "guarded" not in getattr(fn, "__name__", ""):
            break
        count += 1
        nxt = None
        for cell in (fn.__closure__ or ()):
            value = cell.cell_contents
            if callable(value) and getattr(value, "__name__", "") in (
                    "open", "guarded_open"):
                nxt = value
        fn = nxt
    return count
popen = [c.__name__ for c in subprocess.Popen.__mro__].count("_GuardedPopen")
print(layers(builtins.open), popen)
"""


def test_the_pth_installs_exactly_one_layer_in_a_real_interpreter():
    """Measured through a real subprocess, because that is where the bug lived.

    An in-process assertion cannot see this: the double execution comes from
    `site.py` running the `.pth` twice during interpreter startup, which has
    already finished by the time any test body runs.
    """
    proc = _wrapper_depth(LAYER_PROBE)
    assert proc.returncode == 0, proc.stderr
    open_layers, popen_layers = (int(x) for x in proc.stdout.split())
    assert open_layers <= 1, (
        f"builtins.open is wrapped {open_layers} times. Every one beyond the "
        "first is unreachable through sys.modules, so its restore() can never "
        "be called and disarm() unwinds one of them")
    assert popen_layers <= 1, (
        f"subprocess.Popen carries {popen_layers} guard classes, so one child "
        "spawn is recorded that many times in the suspect list")


def test_an_exported_off_installs_nothing_at_all():
    """The operator's escape hatch, measured end to end.

    This is also why `arm(MODE_OFF)` does not reach across module copies: the
    `.pth` resolves the same variable before it arms, so the escape works at
    its source.
    """
    proc = subprocess.run(
        [sys.executable, "-c", LAYER_PROBE], capture_output=True, text=True,
        cwd=str(ROOT), timeout=120,
        env={**os.environ, "PYTHONPATH": str(ROOT),
             "HEADING_OS_OVERLAY_GUARD": "off"},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["0", "0"], (
        f"an exported OFF still wrapped something: {proc.stdout!r}")


def test_a_second_copy_takes_over_and_hands_back_the_same_objects(armed):
    """The handover contract, both halves.

    A second copy must end up the sole owner (or two layers accumulate), and
    its `disarm()` must put the FIRST copy's guard back, object for object. A
    draft that re-installed instead of restoring read to the identity test in
    `test_a_guard_that_armed_under_pytest_and_nowhere_else.py` as a leftover
    layer; a draft that snapshotted too late handed back bare primitives and
    left the process unguarded for everything that followed.
    """
    session, pretend = armed
    before_open = builtins.open
    before_popen = subprocess.Popen
    assert session._installed_owner() is vars(session)

    fresh = _load("fresh_copy")
    fresh.arm(fresh.MODE_GUARD, snapshot=False)
    assert fresh._installed_owner() is vars(fresh), (
        "the second copy did not take ownership, so a later arm() will stack "
        "a second layer on top of it")
    assert builtins.open is not before_open

    fresh.disarm()
    assert builtins.open is before_open, (
        "the hand-back produced a different object than it displaced")
    assert subprocess.Popen is before_popen
    assert session._installed_owner() is vars(session), (
        "the ownership marker was not re-stamped, so the next arm() sees an "
        "unowned process and stacks a layer")
    assert (str(pretend) + os.sep,) == session._OVERLAY_PREFIXES, (
        "the displaced copy got its wrappers back but not its prefixes, so it "
        "now watches nothing and refuses nothing")


def test_the_session_guard_still_refuses_after_a_throwaway_cycle(armed):
    """Behaviour, not bookkeeping. The assertions above are all identity."""
    session, pretend = armed
    fresh = _load("fresh_copy_two")
    fresh.arm(fresh.MODE_GUARD, snapshot=False)
    fresh.disarm()

    with pytest.raises(session.OverlayWriteRefused):
        (pretend / "victim" / "after.txt").write_bytes(b"x")


def test_the_snapshot_covers_every_name_the_wrappers_rebind(armed):
    """A name added to the wrappers and forgotten in `_primitive_slots`.

    That is a silently incomplete hand-back, so derive the expected set from
    the restore closure's own free variables rather than listing it here.
    """
    guard, _pretend = armed
    slots = {(getattr(mod, "__name__", str(mod)), attr)
             for mod, attr in guard._primitive_slots()}
    assert ("builtins", "open") in slots
    assert ("io", "open") in slots
    for attr in ("replace", "rename", "remove", "unlink", "mkdir", "makedirs",
                 "rmdir", "open"):
        assert ("os", attr) in slots, f"os.{attr} is wrapped but never snapshotted"
    assert ("sqlite3", "connect") in slots
    assert ("subprocess", "Popen") in slots
    assert len(slots) >= 12, f"the slot list shrank to {len(slots)}"


# ------------------------------------------------------------
# F3: off means off
# ------------------------------------------------------------

def test_off_after_arming_stops_refusing(armed):
    """The escape hatch, in the state that inverted it."""
    guard, pretend = armed
    target = pretend / "victim" / "e.txt"

    with pytest.raises(guard.OverlayWriteRefused):
        target.write_bytes(b"refused while armed")

    guard.arm(guard.MODE_OFF)
    target.write_bytes(b"allowed once off")
    assert target.read_bytes() == b"allowed once off", (
        "arm(MODE_OFF) on an armed module still refused. OFF is the one thing "
        "an operator can do when the guard is wrong about their work")


def test_off_also_clears_the_prefixes_and_the_wrappers(armed):
    """Not just the observable behaviour: the state the next caller reads."""
    guard, _pretend = armed
    guard.arm(guard.MODE_OFF)
    assert guard._MODE == guard.MODE_OFF
    assert guard._OVERLAY_PREFIXES == ()
    assert guard._RESTORE_WRITE_GUARD is None


def test_the_checker_returns_early_on_off_whatever_the_prefixes_say(armed):
    """The belt, for a mode set by hand rather than through `arm()`.

    `_refuse_overlay_path` had no OFF branch at all, which is what made the
    early return in `arm()` load-bearing on its own.
    """
    guard, pretend = armed
    guard._MODE = guard.MODE_OFF
    guard._refuse_overlay_path(str(pretend / "victim" / "f.txt"), "write")
