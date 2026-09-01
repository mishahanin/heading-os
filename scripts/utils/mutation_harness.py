#!/usr/bin/env python3
"""Run a mutation set against a test file, with the child bounded.

Library module (snake_case per the workspace naming convention). Import it
from a scratch harness instead of re-writing the loop each time::

    from scripts.utils.mutation_harness import run_mutations
    raise SystemExit(run_mutations(ROOT, TESTS, MUTATIONS))

Why this exists, and why the bounds are not optional
----------------------------------------------------
Every scratch harness so far spawned ``pytest`` with a bare
``subprocess.run(...)`` -- no timeout, no memory cap. On 2026-08-24 one
mutation turned a paging loop in ``scripts/gmail-reader.py`` into an endless
one. The stub server answered forever, the pytest child grew to 47 GB against
a 48 GB WSL allocation, and the kernel OOM-killer took the child and then the
whole ``init.scope`` with it: the agent session, the terminal manager, and
every shell. ``/tmp`` was wiped by the distro re-init that followed, taking 58
generated audit reports with it.

That is the shape of the risk. A mutation exists precisely to break the code,
and "break" includes "never return" and "allocate without bound". A harness
that does not bound its child is a harness that can take the machine down.

Two bounds, both applied to the child:

* **Wall clock** -- ``timeout`` seconds per run. A timeout counts as CAUGHT:
  the mutation changed observable behaviour, which is exactly what a mutation
  test is looking for. It is reported as ``caught (timeout)`` so a hang is
  never confused with a clean assertion failure.
* **Address space** -- ``RLIMIT_AS`` on the child only, via ``preexec_fn``.
  The child gets MemoryError instead of the machine getting an OOM-killer.
  POSIX only; on other platforms the wall clock is the only bound and
  ``run_mutations`` says so once.

The baseline run gets the same bounds. A baseline that hangs is a broken
harness, not a finding.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover - POSIX is the only platform CI runs
    # `resource` is POSIX-only, so a bare `import resource` made this module
    # unimportable on precisely the platforms the docstring above promises to
    # degrade for, and the `os.name != "posix"` branch in `run_mutations` was
    # dead code that could never print its note. MEASURED 2026-08-30 by hiding
    # `resource` from the import system: `import scripts.utils.mutation_harness`
    # raised ModuleNotFoundError. The address-space bound is now optional; the
    # wall clock, which needs nothing, still applies.
    resource = None

DEFAULT_TIMEOUT_S = 300
DEFAULT_MEMORY_LIMIT_GB = 4

#: How long to wait for another harness to release the same source file.
#: Generous, because the holder is running a whole pytest invocation under it.
LOCK_WAIT_S = 1800
LOCK_POLL_S = 0.5


class MutationUnsafe(RuntimeError):
    """The tree cannot be left in a state this module is willing to vouch for.

    Raised rather than returned, and never caught inside this module, because
    every caller of `run_mutations` is an audit harness whose whole output is a
    claim about a file it restored. A verdict printed over a tree the harness
    could not restore is worse than no verdict.
    """


@contextlib.contextmanager
def _restore_on_sigterm():
    """Turn SIGTERM into an exception, so the restore `finally` actually runs.

    Python's default SIGTERM disposition terminates the process immediately.
    `finally` does NOT run, so a harness killed by a wrapping `timeout`, by a
    supervisor, or by `kill` leaves its mutation in the working tree with no
    message printed at all.

    MEASURED 2026-09-01: an audit batch was killed mid-window by the Bash tool's
    own 120-second default, and `scripts/run-tests.py` was left holding
    `return 0` in place of `return proc.returncode` - the push gate reporting
    success over a red suite. The only reason it was recovered is that the
    backup carries the pid, so the agent could tell its own leftover from a
    peer's live window. Nothing printed; the next run reported ANCHOR MISSING
    and was nearly read as a peer's damage.

    SIGINT already raises `KeyboardInterrupt`, so it was never affected. This
    brings SIGTERM to the same footing. SIGKILL cannot be caught by anything and
    stays the case the pid-named backup exists for.

    Signal handlers can only be installed from the main thread; a harness driven
    from a worker thread degrades to the previous behaviour and says so once.
    """
    def _raise(signum, _frame):
        raise KeyboardInterrupt(f"terminated by signal {signum}")

    try:
        previous = signal.signal(signal.SIGTERM, _raise)
    except ValueError:  # not the main thread
        print("note: not on the main thread, so a SIGTERM will not run the "
              "restore; a `.mutbak.<pid>` beside the source is the sign",
              file=sys.stderr)
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


@contextlib.contextmanager
def _target_lock(root: Path, rel: str, *, wait: float = LOCK_WAIT_S):
    """Serialise every harness that mutates the same source file.

    Two agents mutating one file is not a slow path, it is data loss, and it
    happened twice on 2026-09-01. The backup path was `<target>.mutbak` with no
    process in it, so with harness A and harness B on the same file the order
    that destroys work is:

        A copies the clean file to .mutbak
        A writes its mutation
        B copies the file -- now MUTATED -- over the same .mutbak
        A's finally moves .mutbak back, so the file keeps A's mutation
        B's finally finds no .mutbak at all

    `scripts/utils/workspace.py` was found that afternoon with a peer's mutation
    still applied, no `.mutbak` beside it, and its mtime untouched, which is
    exactly this: `copy2` and `move` both preserve mtime, so neither `ls` nor a
    glance at `git status` showed anything. It read as a live regression in the
    data-root seam.

    Unlike `checkpoint_paths.file_lock`, this one does NOT proceed unlocked when
    the wait expires. That primitive serves hooks with a turn budget, where
    racing beats hanging. Here, proceeding unlocked is the failure being
    prevented, so a timeout raises.

    The lock file lives under `.tmp/`, which is gitignored, so no sidecar can
    reach a commit (`tests/test_lock_sidecars_are_never_tracked.py`).
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - POSIX is the only platform CI runs
        # No interlock available. Say so rather than pretending: a silent
        # no-op here restores the exact race described above.
        print(f"note: no file locking on this platform; {rel} is not "
              "protected from a concurrent harness", file=sys.stderr)
        yield
        return

    slug = rel.replace("/", "__").replace("\\", "__")
    lock_path = root / ".tmp" / "mutation-locks" / f"{slug}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait
    with open(lock_path, "a+") as handle:  # noqa: SIM115 - closed by the with
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise MutationUnsafe(
                        f"another harness has held {rel} for {wait:.0f}s. "
                        f"Refusing to mutate it concurrently: that is how a "
                        f"peer's mutation gets left in the tree.") from None
                time.sleep(LOCK_POLL_S)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_atomic(path: Path, text: str) -> None:
    """Replace `path`'s contents indivisibly, preserving its mode.

    A plain `path.write_text(...)` truncates first and writes second, so a write
    that fails partway leaves the file short, and on a FULL DISK it leaves the
    file EMPTY. That is not hypothetical: on 2026-09-01 the filesystem hit 100%
    while agents were mutating, and `scripts/utils/workspace.py` was left at
    zero bytes, which made every test in the repository uncollectable for every
    agent at once. An empty Python file still compiles, so the sweep that
    followed reported all 454 changed files fine.

    The temp file is created in the target's own directory so `os.replace` is a
    same-filesystem rename, which is the part that cannot half-happen.
    """
    mode = path.stat().st_mode
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".muttmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        # BaseException, not Exception: a Ctrl-C between mkstemp and replace
        # otherwise orphans the scratch file beside the source it was named for.
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _limit_child(memory_limit_bytes: int):
    """preexec_fn that caps the child's address space. POSIX only."""

    def _apply():
        resource.setrlimit(resource.RLIMIT_AS,
                           (memory_limit_bytes, memory_limit_bytes))

    return _apply


def _clear_pycache(root: Path) -> None:
    """Stale bytecode makes a real mutation look like a caught one.

    `find` is used where it exists because it walks a repo with a `.venv` in it
    far faster than Python can. Where it does not, the walk happens here rather
    than not at all: a silent no-op wipe is the stale-bytecode trap this
    function exists to close.
    """
    if shutil.which("find"):
        subprocess.run(["find", str(root), "-name", "__pycache__", "-type", "d",
                        "-exec", "rm", "-rf", "{}", "+"], capture_output=True)
        return
    for cache in Path(root).rglob("__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)


def run_tests(root: Path, tests, *, timeout: int, memory_limit_bytes: int,
              python: str | None = None, clear_cache: bool = True):
    """Run the tests once. Returns "pass" | "fail" | "timeout".

    `python` defaults to the repo venv, which is what the workspace runs; a
    caller passes it explicitly only to test this module itself.

    The child ALWAYS runs with `PYTHONDONTWRITEBYTECODE`, so no run of this
    function can leave a `.pyc` behind for a later run to read. Combined with
    `run_mutations` wiping once before it starts, that means no cached bytecode
    exists at any point in a mutation loop -- which is a stronger guarantee
    than the old wipe-before-every-run, and about a third faster, since the
    wipe walked the whole repo and forced a full recompile each time.

    `clear_cache=False` skips the walk for a caller that has already done it.
    """
    if clear_cache:
        _clear_pycache(root)
    test_args = [tests] if isinstance(tests, str) else list(tests)
    kwargs = {}
    if os.name == "posix" and resource is not None and memory_limit_bytes:
        kwargs["preexec_fn"] = _limit_child(memory_limit_bytes)
    try:
        proc = subprocess.run(
            [python or str(root / ".venv/bin/python"), "-m", "pytest", *test_args,
             "-q", "-x", "--no-header"],
            cwd=str(root), capture_output=True, text=True, timeout=timeout,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            **kwargs)
    except subprocess.TimeoutExpired:
        return "timeout"
    return "pass" if proc.returncode == 0 else "fail"


def run_mutations(root, tests, mutations, *, timeout: int = DEFAULT_TIMEOUT_S,
                  memory_limit_gb: int = DEFAULT_MEMORY_LIMIT_GB,
                  python: str | None = None) -> int:
    """Apply each mutation, run the tests, restore. Returns a process exit code.

    `mutations` is a sequence of ``(tag, relative_path, old, new)``. `old` must
    appear in the file EXACTLY ONCE; a missing or ambiguous anchor is reported
    and counted as a survivor, because a mutation that never applied, or applied
    somewhere other than where its author aimed it, proved nothing.

    Restoration happens in a ``finally``, the backup is written before the edit,
    and the restore is VERIFIED against the backup's digest before the backup is
    removed. A restore that fails or lands wrong raises `MutationUnsafe` and
    leaves the backup on disk. If this process is killed outright, a
    ``.mutbak.<pid>`` next to a source file is the sign: copy it back before
    trusting the tree.

    Each mutation holds an exclusive lock on its target for the whole
    backup-mutate-run-restore window, so two harnesses cannot interleave on one
    file. See `_target_lock` for the sequence that made this necessary.
    """
    root = Path(root)
    limit = memory_limit_gb * 1024 ** 3
    if os.name != "posix" or resource is None:
        print("note: no address-space limit on this platform; the wall clock "
              f"({timeout}s) is the only bound", file=sys.stderr)

    # ONE wipe, here, before anything runs. Nothing in the loop below writes
    # bytecode (see `run_tests`), so after this line no `.pyc` exists for any
    # run to read -- the guarantee the per-run wipe was reaching for, made once
    # instead of once per mutation.
    _clear_pycache(root)
    baseline = run_tests(root, tests, timeout=timeout, memory_limit_bytes=limit,
                         python=python, clear_cache=False)
    if baseline != "pass":
        print(f"BASELINE {baseline.upper()}")
        return 2
    print("baseline green", flush=True)

    survivors = []
    for tag, rel, old, new in mutations:
        with _restore_on_sigterm(), _target_lock(root, rel):
            _run_one(root, rel, tag, old, new, tests, survivors,
                     timeout=timeout, limit=limit, python=python)

    _clear_pycache(root)
    print(f"\n{len(mutations) - len(survivors)}/{len(mutations)} caught")
    for tag, why in survivors:
        print(f"  SURVIVOR {tag}: {why}")
    return 1 if survivors else 0


def _run_one(root, rel, tag, old, new, tests, survivors, *, timeout, limit,
             python):
    """One mutation, under the caller's lock. Restores or raises, never both."""
    target = root / rel
    # The PID is in the name so two harnesses cannot share, and therefore
    # cannot clobber, one backup. The lock already serialises them; this is the
    # second layer, for the case where the lock is unavailable (non-POSIX) or a
    # previous run died holding one.
    backup = target.with_suffix(f"{target.suffix}.mutbak.{os.getpid()}")
    shutil.copy2(target, backup)
    original = _digest(backup)
    if original != _digest(target):
        raise MutationUnsafe(
            f"{rel} changed between the backup and its verification. Another "
            f"writer is active in this tree; refusing to mutate.")
    try:
        text = target.read_text(encoding="utf-8")
        occurrences = text.count(old)
        if occurrences == 0:
            print(f"{tag:5} {rel:42} ANCHOR MISSING", flush=True)
            survivors.append((tag, "anchor missing"))
            return
        if occurrences > 1:
            # `replace(old, new, 1)` patches the FIRST match, which for an
            # anchor that is not unique is whichever function happens to
            # come first in the file. Measured 2026-08-26: three mutations
            # aimed at `_install_systemd_user_timer` and `_get_json` landed
            # in `_install_windows_task` and `_post_json` instead, so the
            # target code was never mutated and all three were reported as
            # SURVIVED. That reads as a test gap in code that is in fact
            # untested by the mutation, which is the worse of the two
            # errors: it sends the reader to weaken a guard that was fine.
            print(f"{tag:5} {rel:42} ANCHOR AMBIGUOUS ({occurrences}x)",
                  flush=True)
            survivors.append((tag, f"anchor matches {occurrences} places"))
            return
        _write_atomic(target, text.replace(old, new, 1))
        outcome = run_tests(root, tests, timeout=timeout,
                            memory_limit_bytes=limit, python=python,
                            clear_cache=False)
        label = {"fail": "caught", "timeout": "caught (timeout)",
                 "pass": "SURVIVED"}[outcome]
        print(f"{tag:5} {rel:42} {label}", flush=True)
        if outcome == "pass":
            survivors.append((tag, rel))
    finally:
        # A restore is a WRITE, and a write can fail. Until 2026-09-01 this was
        # a bare `shutil.move` whose success was assumed, and the assumption is
        # the whole reason a mutation survived into the working tree unnoticed.
        # `copy2` then `unlink` rather than `move`, so a failure leaves the
        # backup on disk to restore by hand instead of consuming it.
        try:
            shutil.copy2(backup, target)
            restored = _digest(target)
        except OSError as exc:
            raise MutationUnsafe(
                f"could not restore {rel}: {exc}. The backup is still at "
                f"{backup}; copy it back before trusting this tree.") from exc
        if restored != original:
            raise MutationUnsafe(
                f"{rel} does not match its backup after restore. The mutation "
                f"is still in the tree. The backup is at {backup}.")
        backup.unlink()
