"""Unit tests for atomic state file writes."""
import os
from pathlib import Path

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
