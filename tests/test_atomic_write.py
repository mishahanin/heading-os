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


def test_no_tmpfile_orphans_on_base_exception(tmp_path, monkeypatch):
    """A KeyboardInterrupt between mkstemp and replace must still clean up.

    `except Exception` does not catch KeyboardInterrupt or SystemExit, so a
    Ctrl-C or an interpreter shutdown landing inside the write left a scratch
    file named `tmpXXXXXXXX` beside the target, owned by nothing and never
    collected. The three sibling copies of this helper
    (`scripts/bridge_daemon/_atomic.py`, `scripts/utils/crm_autolog.atomic_write`,
    and the eval-viewer's private copy) all catch BaseException for exactly this
    reason; this one, with the most callers of the four, kept the narrow clause
    until 2026-09-01. Nothing here witnessed it: every orphan check above raises
    OSError, which `except Exception` catches.
    """
    import scripts.utils.atomic as atomic_mod

    def _interrupt(src, dst):
        raise KeyboardInterrupt("ctrl-c")

    monkeypatch.setattr(atomic_mod.os, "replace", _interrupt)
    target = tmp_path / "state.json"
    with pytest.raises(KeyboardInterrupt):
        atomic_write_text(target, "content")
    leftovers = list(tmp_path.iterdir())
    assert leftovers == [], f"orphan tmp files left after KeyboardInterrupt: {leftovers}"


def test_tmpfile_is_replaced_from_the_targets_own_directory(tmp_path, monkeypatch):
    """The scratch file must live in the target's directory, not the system temp dir.

    os.replace is only atomic within one filesystem. Dropping the `dir=` argument
    from mkstemp sends the scratch file to /tmp, and os.replace onto a target on
    any other mount raises OSError(EXDEV), while every other test in this file
    stays green, because the orphan checks scan `tmp_path`, which such a scratch
    file never enters. This asserts the VALUE handed to os.replace, so a scratch
    path that merely happens to sit on the same device cannot satisfy it.
    """
    import scripts.utils.atomic as atomic_mod

    seen: list[tuple[Path, Path]] = []
    real_replace = atomic_mod.os.replace

    def _record(src, dst):
        seen.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(atomic_mod.os, "replace", _record)
    target = tmp_path / "nested" / "state.json"
    atomic_write_text(target, "x")

    assert seen, "os.replace was never called; the write was not atomic"
    assert len(seen) == 1, f"expected exactly one replace, saw {len(seen)}: {seen}"
    src, dst = seen[0]
    assert dst == target, f"replaced onto {dst}, not the target {target}"
    assert src.parent == target.parent, (
        f"scratch file {src} is not in the target's directory {target.parent}; "
        "os.replace is not atomic across filesystems"
    )


# ---------------------------------------------------------------------------
# A chmod that fails must be SAID, not swallowed
# ---------------------------------------------------------------------------

def test_a_chmod_that_fails_is_reported_and_the_write_still_lands(
        tmp_path, monkeypatch, capsys):
    """`except OSError: pass` on the mode set, inside a credential writer.

    Reported by a shard auditor 2026-09-01 and fixed the same day. The SWALLOW
    is deliberate and stays: `mkstemp` creates at 0o600, so a failed chmod
    leaves the file NARROWER than requested, never wider, and refusing the
    whole write over a tightening that already holds would turn a cosmetic
    failure into a lost record.

    The SILENCE was not deliberate. `except OSError: pass` is the shape the
    workspace security rule forbids outright, and the callers that pass `mode`
    explicitly are the ones writing CREDENTIALS at 0o600. On a filesystem that
    refuses chmod, a mounted share or a container volume, the requested mode
    would go unapplied with nothing anywhere saying so.

    MEASURED with `os.chmod` raising `PermissionError("read-only share")`:
    before, silence; after, one stderr line naming the path, the requested
    mode, and the fact that 0o600 is what the file keeps.

    Three jaws, because the fix must not have traded one defect for another:
    the record must still be on disk, its CONTENT must be intact, and the
    warning must actually appear.
    """
    from scripts.utils import atomic

    target = tmp_path / "credentials.json"
    payload = '{"token": "not-a-real-secret"}'

    def refuse(*_a, **_k):
        raise PermissionError("read-only share")

    monkeypatch.setattr(atomic.os, "chmod", refuse)
    atomic.atomic_write_text(target, payload, mode=0o600)
    err = capsys.readouterr().err

    assert target.exists(), (
        "a failed chmod lost the write entirely, which is worse than the "
        "wrong mode: the swallow exists precisely so the record survives")
    assert target.read_text(encoding="utf-8") == payload, (
        "the record on disk is not what was handed in")
    assert "could not set mode" in err and str(target) in err, (
        f"the failed chmod was not reported on any stream, so a credential "
        f"written with the wrong permissions says nothing: {err!r}")


def test_a_healthy_write_says_nothing_at_all(tmp_path, capsys):
    """The anchor. A warning on every write is noise that hides the real one."""
    from scripts.utils import atomic

    target = tmp_path / "ordinary.json"
    atomic.atomic_write_text(target, "{}", mode=0o600)

    assert capsys.readouterr().err == "", (
        "a healthy atomic write printed a warning")
