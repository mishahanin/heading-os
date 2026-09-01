"""Unit tests for atomic state file writes, including the failure path.

*The clause that did not cover an interrupt.* `atomic_write_text` cleaned up
its scratch file under `except Exception`, and `KeyboardInterrupt` and
`SystemExit` do not derive from `Exception`. A Ctrl-C or a shutdown arriving
between the `mkstemp` and the `os.replace` therefore left an orphan
`tmpXXXXXXXX` in `.daemon-state/` forever, owned by nothing and named after
nothing. Narrowing the clause the other way, from `Exception` to `OSError`,
was measured on 2026-08-31:

    owner tests/bridge/test_atomic.py: 5 passed in 0.82s
    tests/bridge                     : 1312 passed, 1 skipped in 50.79s
    VERDICT: SURVIVED

The five tests in this file all drove the SUCCESS path, so the `except`
clause had no test of its own at any width. The failure path was covered
only indirectly, by `test_atomic_config.py`, and only for `OSError`, which is
why a narrowing to `OSError` survived and an interrupt was never considered.
The sibling helper `scripts/utils/crm_autolog.atomic_write` already catches
`BaseException` with a comment saying why; this copy had not.

The target file itself is never at risk either way, because `os.replace` is
what makes a write visible, and `test_atomic_config.py` proves the old
content survives a failed replace. This file's new cases are about the
orphan and about which exception types reach the cleanup.
"""
import os
from pathlib import Path

import pytest

from scripts.bridge_daemon._atomic import atomic_write_text


def test_atomic_write_creates_parent_dirs(tmp_path):
    """atomic_write_text creates parent directories if missing."""
    target = tmp_path / "nested" / "dir" / "state.txt"
    atomic_write_text(target, "hello")
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_replaces_existing_file(tmp_path):
    """Subsequent writes replace the file (last writer wins)."""
    target = tmp_path / "state.txt"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")
    assert target.read_text(encoding="utf-8") == "second"


def test_atomic_write_no_tmpfile_orphans(tmp_path):
    """After a successful write, no tempfile orphans remain in the parent dir."""
    target = tmp_path / "state.txt"
    atomic_write_text(target, "x")
    # Only the target should exist; no tmpXXXX siblings.
    siblings = list(target.parent.iterdir())
    assert siblings == [target]


def test_atomic_write_default_mode_is_owner_only_on_posix(tmp_path):
    """On POSIX, default mode is 0o600 (owner read/write only)."""
    target = tmp_path / "token"
    atomic_write_text(target, "secret")
    if os.name == "posix":
        st = target.stat()
        assert (st.st_mode & 0o777) == 0o600
    # On Windows, chmod has limited semantics; just verify the file exists.
    assert target.exists()


def test_atomic_write_explicit_mode_0644(tmp_path):
    """Explicit mode=0o644 is honored on POSIX."""
    target = tmp_path / "port"
    atomic_write_text(target, "31415", mode=0o644)
    if os.name == "posix":
        st = target.stat()
        assert (st.st_mode & 0o777) == 0o644
    assert target.exists()


# --- the failure path: whatever interrupts the write, no orphan survives ---

# BaseException members first, because they are the ones `except Exception`
# never caught. `RuntimeError` and `MemoryError` are there because the clause
# was also narrower than `Exception` in an earlier measured mutation
# (`except OSError`), which nothing detected.
INTERRUPTS = [
    pytest.param(KeyboardInterrupt, id="keyboard-interrupt"),
    pytest.param(SystemExit, id="system-exit"),
    pytest.param(GeneratorExit, id="generator-exit"),
    pytest.param(OSError, id="os-error"),
    pytest.param(RuntimeError, id="runtime-error"),
    pytest.param(MemoryError, id="memory-error"),
]


@pytest.mark.parametrize("exc", INTERRUPTS)
def test_no_orphan_survives_an_interrupted_replace(tmp_path, monkeypatch, exc):
    """The scratch file is removed whatever stopped the write."""
    import scripts.bridge_daemon._atomic as atomic_mod

    def _boom(src, dst):
        raise exc("interrupted")

    monkeypatch.setattr(atomic_mod.os, "replace", _boom)
    target = tmp_path / "token"
    with pytest.raises(exc):
        atomic_write_text(target, "secret")
    leftovers = [p.name for p in tmp_path.iterdir() if p != target]
    assert leftovers == [], f"orphan scratch file after {exc.__name__}: {leftovers}"


@pytest.mark.parametrize("exc", INTERRUPTS)
def test_the_interrupt_is_re_raised_not_swallowed(tmp_path, monkeypatch, exc):
    """Cleanup must not become a silent success.

    `pytest.raises` above already covers this, but only while the cleanup and
    the re-raise sit in the same clause. They are two separate statements and
    a `return` in place of the `raise` would leave every caller believing the
    state file was written.
    """
    import scripts.bridge_daemon._atomic as atomic_mod

    monkeypatch.setattr(atomic_mod.os, "replace",
                        lambda src, dst: (_ for _ in ()).throw(exc("interrupted")))
    target = tmp_path / "token"
    with pytest.raises(exc, match="interrupted"):
        atomic_write_text(target, "secret")
    assert not target.exists(), "the target was created despite the failure"


@pytest.mark.parametrize("exc", INTERRUPTS)
def test_an_interrupted_rewrite_leaves_the_old_content_intact(tmp_path,
                                                              monkeypatch, exc):
    """Atomicity, stated as the property rather than as a tmp-file count.

    None of the five original tests distinguished this helper from
    `path.write_text(content)`: they asserted the final content, the modes,
    and the absence of siblings on the SUCCESS path only, all of which a
    plain in-place write satisfies. The old bytes surviving a failed write is
    the property the whole tmp-plus-rename dance exists to provide.
    """
    import scripts.bridge_daemon._atomic as atomic_mod

    target = tmp_path / "heartbeat.json"
    atomic_write_text(target, '{"pid": 1}')

    def _boom(src, dst):
        raise exc("interrupted")

    monkeypatch.setattr(atomic_mod.os, "replace", _boom)
    with pytest.raises(exc):
        atomic_write_text(target, '{"pid": 2, "half')
    assert target.read_text(encoding="utf-8") == '{"pid": 1}', (
        "the previous content was clobbered before the replace, so this is "
        "not an atomic write")


def test_a_failure_before_the_replace_also_cleans_up(tmp_path, monkeypatch):
    """The window is wider than `os.replace`.

    Patching `os.replace` only exercises the last statement in the `try`. A
    write that fails while the content is being flushed (a full disk is the
    ordinary case) must clean up too, and it enters the same clause from a
    different line.
    """
    import scripts.bridge_daemon._atomic as atomic_mod

    real_fdopen = atomic_mod.os.fdopen

    class _FailingHandle:
        def __init__(self, fh):
            self._fh = fh

        def write(self, _text):
            raise OSError(28, "No space left on device")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._fh.close()
            return False

    monkeypatch.setattr(atomic_mod.os, "fdopen",
                        lambda fd, *a, **k: _FailingHandle(real_fdopen(fd, *a, **k)))
    target = tmp_path / "state.txt"
    with pytest.raises(OSError):
        atomic_write_text(target, "content")
    assert list(tmp_path.iterdir()) == [], (
        f"orphan after a failed write: {[p.name for p in tmp_path.iterdir()]}")
