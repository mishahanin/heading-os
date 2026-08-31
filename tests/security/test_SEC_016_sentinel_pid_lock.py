#!/usr/bin/env python3
"""SEC-016: the PID-file lock that stops a second Sentinel instance.

What this file used to do, and why it was replaced
--------------------------------------------------
The whole file was one assertion over the SOURCE TEXT of ``scripts/sentinel.py``::

    has_lock = "LOCK_EX" in content or "LK_NBLCK" in content or "flock" in content
    assert has_lock or (has_fcntl or has_msvcrt)

MEASURED 2026-08-31: copy ``scripts/sentinel.py`` to scratch, delete the only
two lines that lock anything -- ``import fcntl`` and
``fcntl.flock(self._pid_file_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)`` -- and
replay that assertion. It PASSES. ``"flock"`` still occurs in three comments
(the ones explaining the ``open(..., "a+")`` trap, the named Windows gap, and
the advisory ``--status`` check), and ``"msvcrt"`` occurs in a fourth. So the
test rewarded the file for DOCUMENTING its own trap and would have gone on
passing over a daemon with no second-instance guard at all. A source-text grep
is not a guard.

It also had no negative case. Nothing ever started a second instance and
watched it be refused, though the assertion message said "to prevent duplicate
instances".

What replaces it
----------------
Two binds, neither of which can be satisfied by a comment.

* ``test_the_pid_lock_is_bound_in_sentinel_start`` reads the AST, not the text,
  and pins the names it walks: class ``Sentinel``, method ``start``, the
  attribute ``_pid_file_handle``, and the ``fcntl`` flag names resolved through
  ``getattr(fcntl, ...)`` on the real stdlib module. Rename any of them and this
  file goes red rather than silently matching nothing.

* ``test_a_second_instance_is_refused_the_lock`` is the negative case. It takes
  the flag expression OUT of the production call, applies it to a real temp
  file, and then launches a REAL second process that tries to take the same
  lock. That process must be refused. The positive control immediately after it
  -- the same child acquiring once the lock is released -- is what stops the
  refusal from being green because the child was simply broken.

Together they close both directions: delete the flock and the child acquires;
weaken ``LOCK_EX`` to ``LOCK_SH`` and the child acquires; drop ``LOCK_NB`` and
the child blocks until the harness times it out.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap

import pytest

from tests.security.conftest import read_file_content

# The child gets LOCK_NB from production, so it answers immediately. Anything
# approaching this bound means the non-blocking flag stopped being passed.
CHILD_TIMEOUT_S = 15

# A real second process. It is deliberately NOT a re-implementation of the
# daemon: the lock flags are handed in from the production call site, so this
# child measures whatever `Sentinel.start` actually asks the kernel for.
_CHILD = textwrap.dedent(
    """
    import fcntl, sys
    path, flags = sys.argv[1], int(sys.argv[2])
    handle = open(path, "a+")
    try:
        fcntl.flock(handle, flags)
    except OSError:
        print("REFUSED")
        sys.exit(3)
    print("ACQUIRED")
    sys.exit(0)
    """
)


def _sentinel_start(scripts_dir):
    """Return the AST of `Sentinel.start`, pinning every name on the way in."""
    tree = ast.parse(read_file_content(scripts_dir / "sentinel.py"))

    classes = [n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "Sentinel"]
    assert len(classes) == 1, (
        "expected exactly one `class Sentinel` in scripts/sentinel.py, found "
        f"{len(classes)}. This test walks that class by name; a rename must "
        "fail here rather than let the walk match nothing and pass."
    )

    starts = [n for n in classes[0].body
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "start"]
    assert len(starts) == 1, (
        "expected exactly one `Sentinel.start`, found "
        f"{len(starts)}. That method is where the PID file is written and "
        "locked; if it moved, this test must be pointed at the new home."
    )
    return starts[0]


def _pid_handle_open(start):
    """The `self._pid_file_handle = open(...)` statement inside `start`."""
    opens = []
    for node in ast.walk(start):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Attribute)
                    and target.attr == "_pid_file_handle"
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "open"):
                opens.append(node.value)
    assert len(opens) == 1, (
        "expected exactly one `self._pid_file_handle = open(...)` in "
        f"Sentinel.start, found {len(opens)}. The lock below is taken on that "
        "handle, so the attribute name is load-bearing for this whole file."
    )
    return opens[0]


def _flock_calls(start):
    calls = [n for n in ast.walk(start)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "flock"]
    assert len(calls) >= 1, (
        "Sentinel.start contains no call to flock(). Nothing stops a second "
        "Sentinel from starting beside a healthy one: both would poll the same "
        "sources, double-notify, race on state.json and the Telethon SQLite "
        "session, and leave --stop pointing at whichever PID was written last."
    )
    return calls


def _lock_flags(flag_expr):
    """Resolve the production flag expression through the real fcntl module.

    Deliberately not `eval`. Every flag name is looked up with `getattr` on the
    stdlib module, so a name that does not exist there is a failure here rather
    than a silently-zero mask.
    """
    fcntl = pytest.importorskip("fcntl")
    names = [n.attr for n in ast.walk(flag_expr)
             if isinstance(n, ast.Attribute)
             and isinstance(n.value, ast.Name)
             and n.value.id == "fcntl"]
    assert len(names) >= 2, (
        f"expected at least two fcntl flags in the lock call, found {names}. "
        "An exclusive, non-blocking lock needs both LOCK_EX and LOCK_NB."
    )
    flags = 0
    for name in names:
        assert hasattr(fcntl, name), f"fcntl has no attribute {name!r}"
        flags |= getattr(fcntl, name)
    return names, flags


@pytest.mark.skipif(sys.platform == "win32",
                    reason="scripts/sentinel.py takes this lock under "
                           "`if sys.platform != 'win32'`; the Windows gap is "
                           "named in the source and is not what SEC-016 covers")
def test_the_pid_lock_is_bound_in_sentinel_start(scripts_dir):
    """The lock is an exclusive non-blocking flock on the PID-file handle.

    Every name below is pinned, so a rename fails the suite instead of turning
    the walk into a no-op that reports green.
    """
    fcntl = pytest.importorskip("fcntl")
    start = _sentinel_start(scripts_dir)
    _pid_handle_open(start)  # pins the attribute the lock is taken on

    calls = _flock_calls(start)
    locked_on_pid_handle = [
        c for c in calls
        if c.args and isinstance(c.args[0], ast.Attribute)
        and c.args[0].attr == "_pid_file_handle"
    ]
    assert len(locked_on_pid_handle) >= 1, (
        "flock() is called in Sentinel.start but not on "
        "`self._pid_file_handle`. Locking some other descriptor leaves the PID "
        f"file unguarded. Saw: {[ast.unparse(c) for c in calls]}"
    )

    for call in locked_on_pid_handle:
        assert len(call.args) == 2, (
            f"flock() needs a flags argument: {ast.unparse(call)}"
        )
        names, flags = _lock_flags(call.args[1])
        assert "LOCK_EX" in names, (
            f"the PID lock must be EXCLUSIVE, got {names}. A shared lock lets a "
            "second instance take the same lock and start anyway."
        )
        assert "LOCK_NB" in names, (
            f"the PID lock must be NON-BLOCKING, got {names}. Without LOCK_NB "
            "the losing instance hangs on the lock forever instead of exiting, "
            "so the operator sees a started-but-silent daemon."
        )
        assert flags == (fcntl.LOCK_EX | fcntl.LOCK_NB), (
            f"unexpected resolved flag mask {flags} from {names}"
        )


@pytest.mark.skipif(sys.platform == "win32",
                    reason="fcntl.flock is POSIX-only; scripts/sentinel.py "
                           "guards the same call with the same platform test")
def test_the_pid_file_open_never_truncates(scripts_dir):
    """`open(PID_FILE, "w")` truncates at open(2), BEFORE the lock can refuse.

    The losing instance would empty the live daemon's PID file and then exit on
    the lock, and the write-back never runs on that branch. `--status` then
    printed UNKNOWN and `--stop` deleted the file without signalling anything.
    """
    start = _sentinel_start(scripts_dir)
    call = _pid_handle_open(start)

    assert len(call.args) == 2, (
        f"expected `open(PID_FILE, <mode>)`, got {ast.unparse(call)}"
    )
    mode = call.args[1]
    assert isinstance(mode, ast.Constant) and isinstance(mode.value, str), (
        f"the open mode must be a literal this test can read: {ast.unparse(call)}"
    )
    assert "w" not in mode.value, (
        f"open mode {mode.value!r} truncates at open(2), before the flock below "
        "can refuse. A second instance would empty the running daemon's PID "
        "file on its way out."
    )
    assert "a" in mode.value or "x" in mode.value, (
        f"open mode {mode.value!r} cannot create the PID file on a clean boot."
    )


@pytest.mark.skipif(sys.platform == "win32",
                    reason="fcntl.flock is POSIX-only; scripts/sentinel.py "
                           "guards the same call with the same platform test")
def test_a_second_instance_is_refused_the_lock(scripts_dir, tmp_path):
    """The negative case this file was always supposed to make.

    A real second PROCESS attempts the production lock on a PID file that is
    already held, and must be refused. The positive control at the bottom is
    what keeps this honest: if the child were simply broken, it would fail to
    acquire the released lock too, and this test would be green over nothing.
    """
    fcntl = pytest.importorskip("fcntl")
    start = _sentinel_start(scripts_dir)
    call = _flock_calls(start)[0]
    names, flags = _lock_flags(call.args[1])

    pid_file = tmp_path / "sentinel.pid"
    # `with` both mirrors the mode production opens with and releases the lock:
    # closing the descriptor drops any flock held on it, which is what makes the
    # positive control below a genuinely unlocked file.
    with open(pid_file, "a+") as holder:
        fcntl.flock(holder, flags)

        try:
            refused = subprocess.run(
                [sys.executable, "-c", _CHILD, str(pid_file), str(flags)],
                capture_output=True, text=True, timeout=CHILD_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            pytest.fail(
                f"the second instance BLOCKED for {CHILD_TIMEOUT_S}s on the "
                f"lock instead of being refused. Flags taken from production "
                f"were {names}; a non-blocking lock needs LOCK_NB."
            )

        assert refused.returncode == 3, (
            "a second Sentinel instance was NOT refused the PID lock "
            f"(exit {refused.returncode}, stdout {refused.stdout!r}). Two "
            "daemons would poll the same sources and double-notify. Production "
            f"flags were {names}."
        )
        assert "REFUSED" in refused.stdout

    # Positive control, ON the other side of the line: with the lock released
    # the same child must succeed. Without this, a child that always errored
    # would satisfy the refusal above.
    allowed = subprocess.run(
        [sys.executable, "-c", _CHILD, str(pid_file), str(flags)],
        capture_output=True, text=True, timeout=CHILD_TIMEOUT_S)
    assert allowed.returncode == 0 and "ACQUIRED" in allowed.stdout, (
        "the child could not take the lock even after it was released "
        f"(exit {allowed.returncode}, stdout {allowed.stdout!r}, "
        f"stderr {allowed.stderr!r}). The refusal above therefore proves "
        "nothing about the lock."
    )
