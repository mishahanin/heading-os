#!/usr/bin/env python3
"""Three source-side holes in scripts/import-legacy-records.py.

The module docstring leads with "a destination file that already exists is NEVER
overwritten ... Copies go through a UNIQUE temp file in the destination directory
and land via `os.link`, which the filesystem refuses if the destination appeared
in the meantime." On a filesystem WITHOUT hard links - FAT32, exFAT, some network
mounts, all reachable recovery targets - `os.link` raises an errno that is not
EEXIST and the fallback `os.replace` clobbered whatever was there, silently. The
docstring's create-if-absent was gone and nothing said so.

One unreadable source file ended the whole import. `_import_subtree` contained
only FileExistsError, so PermissionError from `shutil.copy2` travelled out
through `main` as a traceback: every remaining file in that subtree and every
later subtree unprocessed, no totals printed, and the per-subtree lines already
on screen reading as success. That is the exact operational failure the docstring
congratulates itself on having removed for the collision case.

Every path-safety guard in the file watches the DESTINATION. Nothing watched the
source, and `is_file()` follows a symlink while `copy2` copies the target's
content - so a link inside the old workspace pointing anywhere had that file's
contents imported into the private data overlay, under a name inside the four
scoped subtrees.
"""
from __future__ import annotations

import errno
import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


@pytest.fixture
def importer(tmp_path, monkeypatch):
    """The importer bound to a throwaway data root, never the live overlay."""
    d = tmp_path / ".heading-os-data"
    d.mkdir(parents=True)
    monkeypatch.setenv("HEADING_OS_DATA", str(d))
    monkeypatch.delenv("THREADS_ROOT", raising=False)
    from scripts.utils import workspace
    workspace._reset_identity_cache()
    spec = importlib.util.spec_from_file_location(
        "import_legacy_records", ROOT / "scripts" / "import-legacy-records.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, argv):
    old = sys.argv
    sys.argv = ["import-legacy-records.py", *argv]
    try:
        return mod.main()
    finally:
        sys.argv = old


def _knowledge_dest():
    from scripts.utils.workspace import get_knowledge_dir
    d = get_knowledge_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def no_hard_links(monkeypatch):
    """A filesystem that refuses `os.link` for a reason other than EEXIST."""
    def refuse(src, dst):
        raise OSError(errno.EPERM, "operation not permitted")
    monkeypatch.setattr(os, "link", refuse)


# ============================================================
# The no-hard-link fallback must not clobber
# ============================================================

def test_the_fallback_does_not_overwrite_an_existing_destination(importer, tmp_path,
                                                                 no_hard_links):
    src = tmp_path / "src.md"
    src.write_text("NEW CONTENT", encoding="utf-8")
    dest = tmp_path / "out" / "dest.md"
    dest.parent.mkdir()
    dest.write_text("SPECTRE DOSSIER, ALREADY HERE", encoding="utf-8")

    with pytest.raises(FileExistsError):
        importer._atomic_copy(src, dest)
    assert dest.read_text(encoding="utf-8") == "SPECTRE DOSSIER, ALREADY HERE"


def test_the_fallback_leaves_no_scratch_file_behind_when_it_refuses(importer,
                                                                    tmp_path,
                                                                    no_hard_links):
    src = tmp_path / "src.md"
    src.write_text("NEW CONTENT", encoding="utf-8")
    dest = tmp_path / "out" / "dest.md"
    dest.parent.mkdir()
    dest.write_text("ALREADY HERE", encoding="utf-8")
    with pytest.raises(FileExistsError):
        importer._atomic_copy(src, dest)
    assert sorted(p.name for p in dest.parent.iterdir()) == ["dest.md"]


def test_a_dangling_symlink_at_the_destination_also_stops_the_fallback(importer,
                                                                       tmp_path,
                                                                       no_hard_links):
    """`lexists`, matching the link path: the NAME is what a copy collides with."""
    src = tmp_path / "src.md"
    src.write_text("NEW CONTENT", encoding="utf-8")
    dest = tmp_path / "out" / "dest.md"
    dest.parent.mkdir()
    dest.symlink_to(dest.parent / "never-created.md")
    with pytest.raises(FileExistsError):
        importer._atomic_copy(src, dest)
    assert dest.is_symlink()


def test_the_fallback_still_copies_to_a_free_name(importer, tmp_path, no_hard_links,
                                                  capsys):
    src = tmp_path / "src.md"
    src.write_text("NEW CONTENT", encoding="utf-8")
    dest = tmp_path / "out" / "fresh.md"
    importer._atomic_copy(src, dest)
    assert dest.read_text(encoding="utf-8") == "NEW CONTENT"


def test_the_fallback_says_the_guarantee_is_degraded(importer, tmp_path,
                                                     no_hard_links, capsys):
    """The docstring claimed it "says so rather than pretending"; it printed
    nothing at all."""
    src = tmp_path / "src.md"
    src.write_text("NEW CONTENT", encoding="utf-8")
    importer._atomic_copy(src, tmp_path / "out" / "fresh.md")
    out = _plain(capsys.readouterr().out)
    assert "no hard-link support" in out
    assert "degraded" in out


def test_the_hard_link_path_prints_no_such_warning(importer, tmp_path, capsys):
    """Negative case: the ordinary filesystem must stay quiet."""
    src = tmp_path / "src.md"
    src.write_text("NEW CONTENT", encoding="utf-8")
    importer._atomic_copy(src, tmp_path / "out" / "fresh.md")
    assert "degraded" not in _plain(capsys.readouterr().out)


# ============================================================
# One unreadable source file is a skip, not the end of the run
# ============================================================

@pytest.fixture
def old_root_with_one_unreadable_file(tmp_path):
    """Two subtrees; `a.md` is unreadable and sorts before `z-later.md`."""
    old = tmp_path / "old-workspace"
    (old / "knowledge").mkdir(parents=True)
    (old / "threads").mkdir(parents=True)
    (old / "knowledge" / "a.md").write_text("blofeld\n", encoding="utf-8")
    (old / "knowledge" / "z-later.md").write_text("moneypenny\n", encoding="utf-8")
    (old / "threads" / "b.md").write_text("q-branch\n", encoding="utf-8")
    (old / "knowledge" / "a.md").chmod(0o000)
    if os.access(old / "knowledge" / "a.md", os.R_OK):  # pragma: no cover - root
        pytest.skip("this user can read a 0o000 file; the defect is unreachable here")
    yield old
    (old / "knowledge" / "a.md").chmod(0o644)


def test_an_unreadable_source_file_does_not_end_the_run(
        importer, old_root_with_one_unreadable_file):
    rc = _run(importer, ["--from", str(old_root_with_one_unreadable_file)])
    assert rc == 1, "a recovery run that dropped a file must not report success"


def test_the_files_after_the_unreadable_one_are_still_imported(
        importer, old_root_with_one_unreadable_file):
    _run(importer, ["--from", str(old_root_with_one_unreadable_file)])
    assert (_knowledge_dest() / "z-later.md").read_text(encoding="utf-8") \
        == "moneypenny\n"


def test_the_later_subtree_is_still_imported(
        importer, old_root_with_one_unreadable_file):
    """The abort took every subtree after the bad file with it."""
    from scripts.utils.workspace import get_threads_dir
    _run(importer, ["--from", str(old_root_with_one_unreadable_file)])
    assert (get_threads_dir() / "b.md").read_text(encoding="utf-8") == "q-branch\n"


def test_the_unreadable_file_is_named_and_counted_apart_from_the_skips(
        importer, old_root_with_one_unreadable_file, capsys):
    _run(importer, ["--from", str(old_root_with_one_unreadable_file)])
    out = _plain(capsys.readouterr().out)
    assert "unreadable a.md" in out
    assert "1 unreadable" in out
    # It is NOT the collision skip: those two mean different things and the
    # operator acts differently on each.
    assert "skipped 0 (already exist)" in out


def test_the_run_prints_its_totals_instead_of_a_traceback(
        importer, old_root_with_one_unreadable_file, capsys):
    _run(importer, ["--from", str(old_root_with_one_unreadable_file)])
    out = _plain(capsys.readouterr().out)
    assert "Total:" in out
    assert "imported 2" in out


def test_a_whole_run_failure_still_propagates(importer, tmp_path, monkeypatch):
    """The containment is per-FILE and deliberately narrow. A full disk is not a
    skip and must not read as one - `tests/test_an_import_that_died_on_the_skip_
    it_promised.py` pins that, and this asserts the new guard did not widen it."""
    old = tmp_path / "old-workspace"
    (old / "knowledge").mkdir(parents=True)
    (old / "knowledge" / "a.md").write_text("readable\n", encoding="utf-8")

    def full_disk(src_file, dest_file):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(importer, "_atomic_copy", full_disk)
    with pytest.raises(OSError) as exc:
        _run(importer, ["--from", str(old), "--only", "knowledge"])
    assert exc.value.errno == errno.ENOSPC


def test_a_clean_run_still_exits_zero(importer, tmp_path):
    old = tmp_path / "old-workspace"
    (old / "knowledge").mkdir(parents=True)
    (old / "knowledge" / "a.md").write_text("readable\n", encoding="utf-8")
    assert _run(importer, ["--from", str(old), "--only", "knowledge"]) == 0


# ============================================================
# A source symlink is not followed
# ============================================================

@pytest.fixture
def old_root_with_a_link_out(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("UNIVERSAL EXPORTS PAYROLL\n", encoding="utf-8")
    old = tmp_path / "old-workspace"
    (old / "knowledge").mkdir(parents=True)
    (old / "knowledge" / "real.md").write_text("inside the subtree\n", encoding="utf-8")
    (old / "knowledge" / "link.md").symlink_to(outside / "secret.txt")
    return old


def test_a_source_symlinks_target_content_is_not_imported(
        importer, old_root_with_a_link_out):
    _run(importer, ["--from", str(old_root_with_a_link_out), "--only", "knowledge"])
    dest = _knowledge_dest()
    assert not (dest / "link.md").exists()
    assert "UNIVERSAL EXPORTS PAYROLL" not in "".join(
        p.read_text(encoding="utf-8") for p in dest.rglob("*") if p.is_file())


def test_the_real_file_beside_the_symlink_is_still_imported(
        importer, old_root_with_a_link_out):
    _run(importer, ["--from", str(old_root_with_a_link_out), "--only", "knowledge"])
    assert (_knowledge_dest() / "real.md").read_text(encoding="utf-8") \
        == "inside the subtree\n"


def test_the_skipped_symlink_is_named_and_counted(importer, old_root_with_a_link_out,
                                                  capsys):
    _run(importer, ["--from", str(old_root_with_a_link_out), "--only", "knowledge"])
    out = _plain(capsys.readouterr().out)
    assert "symlink link.md (not followed)" in out
    assert "1 symlink(s) not followed" in out


def test_a_policy_skip_does_not_fail_the_run(importer, old_root_with_a_link_out):
    """Unlike an unreadable file, a symlink is a decision, not a hole."""
    assert _run(importer, ["--from", str(old_root_with_a_link_out),
                           "--only", "knowledge"]) == 0


def test_the_source_symlink_itself_is_left_alone(importer, old_root_with_a_link_out):
    """Non-destructive means the old workspace comes out exactly as it went in."""
    link = old_root_with_a_link_out / "knowledge" / "link.md"
    _run(importer, ["--from", str(old_root_with_a_link_out), "--only", "knowledge"])
    assert link.is_symlink()
    assert link.read_text(encoding="utf-8") == "UNIVERSAL EXPORTS PAYROLL\n"
