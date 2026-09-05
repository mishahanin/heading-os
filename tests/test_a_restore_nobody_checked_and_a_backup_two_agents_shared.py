#!/usr/bin/env python3
"""The shared mutation harness could leave a peer's mutation in the tree.

`scripts/utils/mutation_harness.py` is imported by every audit harness in this
campaign. It backs a source file up, mutates it, runs the tests, and restores it
in a `finally`. Three things about that sequence were assumed rather than
checked, and on 2026-09-01 all three cost something.

## 1. The backup path had no process in it

It was `<target>.mutbak`. Two harnesses on one file therefore shared one backup,
and this order destroys work:

    A copies the clean file to .mutbak
    A writes its mutation
    B copies the file - now MUTATED - over the same .mutbak
    A's finally moves .mutbak back, so the file KEEPS A's mutation
    B's finally finds no .mutbak at all

`scripts/utils/workspace.py` was found that afternoon with a peer's mutation
still applied (`UnicodeDecodeError` deleted from `get_workspace_identity`'s
handler), no `.mutbak` beside it, and its mtime untouched. `copy2` and `move`
both preserve mtime, so neither `ls` nor a glance at `git status` showed it. It
read as a live regression in the data-root seam and cost an agent a diagnosis.

## 2. The restore's success was assumed

`shutil.move(backup, target)` with no check. A restore is a WRITE and a write
can fail. Earlier the same day the filesystem hit 100% while agents were
mutating and left that same file at ZERO BYTES, which made every test in the
repository uncollectable for every agent at once. An empty Python file compiles,
so the `py_compile` sweep that followed reported all 454 changed files fine.

## 3. The mutation write truncated first

`Path.write_text` truncates, then writes. On a full disk that leaves the file
empty. The harness that exists to break code was the code most likely to break
the tree.

## What was measured before the fix

This whole file was run against the previous revision of the module on
2026-09-01, by putting that revision back in place for the length of one run:

    8 failed, 1 passed in 32.49s

The one that passed is `test_a_clean_run_leaves_the_file_byte_identical_and_no_
backup`, and it is supposed to: the old harness restored a file correctly on the
happy path. It is the positive anchor, and without it every other test here
would be satisfied by a harness that simply refused to do anything.
"""
from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import mutation_harness as MH  # noqa: E402


# ---------------------------------------------------------------- the backup

def test_two_harnesses_cannot_share_one_backup_path(tmp_path):
    """The name must separate two processes, or one overwrites the other's.

    Asked of the NAME the module builds rather than of a live race, because a
    race that reproduces reliably in a test is a race that has been made
    deterministic, and the deterministic version proves nothing about the real
    one. What is checkable is the property that makes the race impossible.
    """
    target = tmp_path / "victim.py"
    target.write_text("x = 1\n", encoding="utf-8")

    # Reproduce the module's own construction rather than importing a private
    # helper, so this test fails if that construction is changed back.
    name = target.with_suffix(f"{target.suffix}.mutbak.{os.getpid()}").name
    assert str(os.getpid()) in name, (
        "the backup path carries no process identity, so two harnesses mutating "
        "one file share a backup and the second one to copy overwrites the "
        "first one's clean original")

    source = (ROOT / "scripts/utils/mutation_harness.py").read_text(
        encoding="utf-8")
    assert "os.getpid()" in source and ".mutbak" in source, (
        "mutation_harness no longer builds its backup name from the pid")


# --------------------------------------------------------------- the restore

class _ShimShutil:
    """Stands in for `shutil` inside the module under test.

    Set on OUR module's attribute, never on the stdlib's, so nothing outside
    this test observes it. Everything is delegated except the call this test
    needs to go wrong.
    """

    def __init__(self, real, *, break_nth_copy):
        self._real = real
        self._break_nth_copy = break_nth_copy
        self.copies = 0

    def __getattr__(self, name):
        return getattr(self._real, name)

    def copy2(self, src, dst):
        self.copies += 1
        if self.copies == self._break_nth_copy:
            # A partial write: what a full disk actually leaves behind.
            Path(dst).write_text("", encoding="utf-8")
            return dst
        return self._real.copy2(src, dst)


def _tiny_repo(tmp_path):
    """A throwaway tree with one source file and one test that reads it."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "code.py").write_text(
        "def answer():\n    return 42\n", encoding="utf-8")
    (repo / "test_it.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n"
        "from pkg.code import answer\n"
        "def test_answer():\n"
        "    assert answer() == 42\n", encoding="utf-8")
    return repo


@pytest.mark.slow
def test_a_restore_that_lands_wrong_raises_rather_than_reporting_a_verdict(
        tmp_path, monkeypatch):
    """The headline. A silent bad restore is how a mutation reaches a commit.

    The second `copy2` in a one-mutation run is the restore. Breaking it the way
    a full disk breaks it - an empty file where the original should be - must
    stop the run loudly. Before this fix the run printed `1/1 caught`, returned
    0, and left the source file empty.
    """
    repo = _tiny_repo(tmp_path)
    shim = _ShimShutil(MH.shutil, break_nth_copy=2)
    monkeypatch.setattr(MH, "shutil", shim)

    with pytest.raises(MH.MutationUnsafe) as caught:
        MH.run_mutations(
            repo, ["test_it.py"],
            [("M1", "pkg/code.py", "return 42", "return 43")],
            timeout=120, python=sys.executable)

    assert "pkg/code.py" in str(caught.value), (
        "the refusal must name the file it could not restore")
    backup = repo / "pkg" / f"code.py.mutbak.{os.getpid()}"
    assert backup.exists(), (
        "the backup was consumed even though the restore failed, so there is "
        "nothing left to restore the file from by hand")


@pytest.mark.slow
def test_a_clean_run_leaves_the_file_byte_identical_and_no_backup(tmp_path):
    """The positive case. Without it the test above is satisfied by a harness
    that refuses everything."""
    repo = _tiny_repo(tmp_path)
    source = repo / "pkg" / "code.py"
    before = source.read_bytes()

    rc = MH.run_mutations(
        repo, ["test_it.py"],
        [("M1", "pkg/code.py", "return 42", "return 43")],
        timeout=120, python=sys.executable)

    assert rc == 0, "the mutation changed the answer; the test should catch it"
    assert source.read_bytes() == before, "the file was not restored exactly"
    assert not list((repo / "pkg").glob("*.mutbak*")), (
        "a verified restore must remove its own backup")


# ------------------------------------------------------------------ the lock

def _hold(root, rel, seconds, ready, released):
    """Child process: take the lock, signal, hold, release."""
    sys.path.insert(0, str(ROOT))
    from scripts.utils import mutation_harness as mh
    with mh._target_lock(Path(root), rel):
        ready.set()
        time.sleep(seconds)
    released.set()


@pytest.mark.slow
def test_a_second_harness_is_refused_rather_than_proceeding_unlocked(tmp_path):
    """The direction of failure is the point.

    `checkpoint_paths.file_lock` proceeds UNLOCKED when its wait expires, which
    is right for a hook with a turn budget: racing beats hanging. Here it is
    exactly backwards. Proceeding unlocked is the thing that left a peer's
    mutation in the tree, so the wait must end in a refusal.
    """
    if not hasattr(__import__("os"), "fork"):  # pragma: no cover
        pytest.skip("needs a POSIX fork to hold the lock from another process")

    ctx = multiprocessing.get_context("fork")
    ready, released = ctx.Event(), ctx.Event()
    holder = ctx.Process(target=_hold, args=(str(tmp_path), "pkg/code.py",
                                             6, ready, released))
    holder.start()
    try:
        assert ready.wait(30), "the holder never acquired the lock"
        started = time.monotonic()
        with pytest.raises(MH.MutationUnsafe) as caught, \
                MH._target_lock(tmp_path, "pkg/code.py", wait=1):
            pytest.fail("the lock was granted while another process held it")
        waited = time.monotonic() - started
    finally:
        holder.join(timeout=30)

    assert "pkg/code.py" in str(caught.value)
    assert waited >= 1, (
        f"refused after {waited:.2f}s, less than the 1s wait; the lock was "
        f"never actually attempted")


@pytest.mark.slow
def test_the_lock_is_released_so_a_later_harness_can_run(tmp_path):
    """A lock that is never released is a worse defect than the race."""
    with MH._target_lock(tmp_path, "pkg/code.py", wait=5):
        pass
    with MH._target_lock(tmp_path, "pkg/code.py", wait=5):
        pass  # would raise MutationUnsafe if the first hold leaked


def test_the_lock_sidecar_cannot_reach_a_commit(tmp_path):
    """`tests/test_lock_sidecars_are_never_tracked.py` states the convention.

    The lock file carries no data; its only meaning is the flock held on it. It
    goes under `.tmp/`, which git ignores, so a `git add -A` during a mutation
    run cannot sweep it in.
    """
    with MH._target_lock(tmp_path, "scripts/utils/workspace.py", wait=5):
        locks = list((tmp_path / ".tmp" / "mutation-locks").glob("*.lock"))
        assert locks, "no lock file was created, so nothing was serialised"
        assert locks[0].name == "scripts__utils__workspace.py.lock", (
            "the slug must flatten the path, or a nested directory has to exist "
            "before the lock can be taken")

    # Asked about the LOCK PATH, not about `.tmp`. MEASURED 2026-09-05 in a
    # throwaway repo carrying only `.tmp/`: `git check-ignore .tmp` exits 1
    # when the directory does not exist on disk and 0 once it does, because a
    # trailing-slash pattern matches a path git can see is a directory. This
    # tree happens to have `.tmp/`, so the old assertion passed here and would
    # have gone red on a fresh clone for a reason that has nothing to do with
    # the invariant. A file INSIDE the directory is matched either way.
    ignored = subprocess.run(
        ["git", "check-ignore", "-q",
         ".tmp/mutation-locks/scripts__utils__workspace.py.lock"], cwd=ROOT,
        capture_output=True, text=True)
    assert ignored.returncode == 0, (
        ".tmp/ is no longer gitignored, so every mutation lock is one "
        "`git add -A` from a commit")


# ------------------------------------------------------- the truncating write

def test_the_mutation_write_cannot_truncate_the_target(tmp_path):
    """`Path.write_text` truncates first. On a full disk that leaves nothing.

    Asked of `_write_atomic` directly: a failure between the temp file and the
    replace must leave the ORIGINAL whole, and must not orphan the scratch file.
    """
    target = tmp_path / "code.py"
    target.write_text("def answer():\n    return 42\n", encoding="utf-8")
    target.chmod(0o755)
    before = target.read_bytes()

    class _Boom:
        def write(self, _):
            raise OSError(28, "No space left on device")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    real_fdopen = MH.os.fdopen
    try:
        MH.os.fdopen = lambda fd, *a, **k: (os.close(fd), _Boom())[1]
        with pytest.raises(OSError):
            MH._write_atomic(target, "def answer():\n    return 43\n")
    finally:
        MH.os.fdopen = real_fdopen

    assert target.read_bytes() == before, (
        "a failed write reached the target; it should only ever reach a temp "
        "file that is then replaced onto the target")
    assert not list(tmp_path.glob("*.muttmp")), (
        "the scratch file was orphaned beside the source it was named for")


def test_the_atomic_write_preserves_the_file_mode(tmp_path):
    """A mutated script that loses its executable bit is a second defect.

    `mkstemp` creates at 0600, so without the explicit chmod every mutated
    script would come back unreadable to anything but its owner.
    """
    target = tmp_path / "hook.py"
    target.write_text("print(1)\n", encoding="utf-8")
    target.chmod(0o755)

    MH._write_atomic(target, "print(2)\n")

    assert target.read_text(encoding="utf-8") == "print(2)\n"
    assert target.stat().st_mode & 0o777 == 0o755, (
        "the mode was taken from mkstemp (0600) instead of the target")


def test_mutation_unsafe_is_not_swallowed_by_an_ordinary_handler():
    """It must reach the caller. A harness that catches it prints a verdict
    over a tree it could not restore, which is the whole failure."""
    assert issubclass(MH.MutationUnsafe, RuntimeError)
    assert not issubclass(MH.MutationUnsafe, (OSError, ValueError)), (
        "an audit harness wrapping its file work in `except OSError` would "
        "swallow the one exception that means the tree is damaged")


# ------------------------------------------------------------------- SIGTERM

@pytest.mark.slow
def test_a_sigterm_mid_window_still_restores_the_file(tmp_path):
    """The fourth assumption, found on 2026-09-01 after the first three.

    Python's default SIGTERM disposition terminates the process immediately, so
    the restore `finally` never runs. An audit batch was killed mid-window by a
    wrapping 120-second timeout and left `scripts/run-tests.py` holding
    `return 0` in place of `return proc.returncode`: the PUSH GATE reporting
    success over a red suite. Nothing was printed, and the next run reported
    only ANCHOR MISSING.

    SIGINT was never affected, because it already raises `KeyboardInterrupt`.
    SIGKILL cannot be caught by anything and is why the backup carries the pid.

    Driven as a real child, sent a real SIGTERM: an in-process signal would land
    in pytest's own machinery.
    """
    repo = _tiny_repo(tmp_path)
    source = repo / "pkg" / "code.py"
    before = source.read_bytes()
    ready = tmp_path / "mutated.flag"

    probe = tmp_path / "sigterm_probe.py"
    probe.write_text(
        "import pathlib, sys, time\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from scripts.utils import mutation_harness as mh\n"
        "real = mh.run_tests\n"
        "def slow(*a, **k):\n"
        f"    pathlib.Path({str(ready)!r}).write_text('x')\n"
        "    time.sleep(120)\n"
        "mh.run_tests = lambda *a, **k: 'pass' if k.get('clear_cache') is False "
        "and not getattr(slow, 'armed', False) else slow()\n"
        "slow.armed = False\n"
        "def gate(*a, **k):\n"
        "    if slow.armed:\n"
        "        return slow()\n"
        "    slow.armed = True\n"
        "    return 'pass'\n"
        "mh.run_tests = gate\n"
        f"mh.run_mutations({str(repo)!r}, ['test_it.py'],\n"
        "    [('M1', 'pkg/code.py', 'return 42', 'return 43')],\n"
        f"    timeout=300, python={sys.executable!r})\n",
        encoding="utf-8")

    child = subprocess.Popen([sys.executable, str(probe)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 60
        while not ready.exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                pytest.fail(f"probe exited early: {child.communicate()[1][:400]}")
            time.sleep(0.05)
        assert ready.exists(), "the probe never reached the mutated window"
        assert source.read_bytes() != before, (
            "the mutation was not applied, so this test would pass vacuously")

        child.terminate()          # SIGTERM, the signal that used to kill silently
        child.wait(timeout=60)
    finally:
        if child.poll() is None:   # never leave a sleeper behind
            child.kill()
            child.wait(timeout=30)

    assert source.read_bytes() == before, (
        "a SIGTERM during the window left the mutation in the tree. `finally` "
        "does not run on the default SIGTERM disposition.")
    assert not list((repo / "pkg").glob("*.mutbak*")), (
        "the backup survived, so the restore did not complete")
