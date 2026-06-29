"""Tests for scripts.utils.atomic — shared atomic write helper."""
import os
import stat
from pathlib import Path

import pytest

from scripts.utils.atomic import atomic_write_text


def test_creates_parent_dirs(tmp_path):
    target = tmp_path / "a" / "b" / "state.json"
    atomic_write_text(target, '{"ok": true}')
    assert target.read_text(encoding="utf-8") == '{"ok": true}'


def test_replaces_existing_file(tmp_path):
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


def test_no_tmpfile_orphans_on_success(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_text(target, "content")
    files = list(tmp_path.iterdir())
    assert files == [target], f"orphan tmp files left: {files}"


def test_no_tmpfile_orphans_on_failure(tmp_path, monkeypatch):
    """If os.replace raises, the tmp file must be cleaned up."""
    import scripts.utils.atomic as atomic_mod

    def _bad_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(atomic_mod.os, "replace", _bad_replace)
    target = tmp_path / "state.json"
    with pytest.raises(OSError, match="disk full"):
        atomic_write_text(target, "content")
    tmp_files = [f for f in tmp_path.iterdir() if f != target]
    assert tmp_files == [], f"orphan tmp files left after failure: {tmp_files}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits not meaningful on Windows")
def test_default_mode_is_0o644(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_text(target, "x")
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o644, f"expected 0o644, got {oct(mode)}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits not meaningful on Windows")
def test_explicit_mode_0o600(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_text(target, "x", mode=0o600)
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
