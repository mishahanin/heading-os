"""Integration tests for scripts/linkedin-archive.py image handling.

Regression: an image path OUTSIDE the data repo (e.g. /mnt/c/... screenshot,
clipboard save, Downloads) must be archived by COPY + git add, not `git mv`,
which fails for out-of-repo sources. Reproduced live 2026-06-18 archiving the
15k-followers post with a /mnt/c screenshot (exit 4). The external path also
masked the untracked-.md pre-check (git's "outside repository" error does not
match the pathspec regex), turning a clean exit 7 into a confusing exit 4.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "linkedin-archive.py"
_spec = importlib.util.spec_from_file_location("linkedin_archive", SCRIPT)
la = importlib.util.module_from_spec(_spec)
sys.modules["linkedin_archive"] = la
_spec.loader.exec_module(la)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A temp data repo with the linkedin staging + archive layout, git-initialised,
    holding one tracked+committed staged post."""
    r = tmp_path / "repo"
    (r / "outputs" / "content" / "linkedin").mkdir(parents=True)
    (r / "datastore" / "content" / "linkedin-archive" / "posts").mkdir(parents=True)
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t.t")
    _git(r, "config", "user.name", "t")
    md = r / "outputs" / "content" / "linkedin" / "2026-06-18_linkedin-post_milestone.md"
    md.write_text("Body of the post.\n", encoding="utf-8")
    _git(r, "add", str(md))
    _git(r, "commit", "-qm", "stage post")
    monkeypatch.setattr(la, "get_data_root", lambda: r)
    monkeypatch.setattr(la, "get_outputs_dir", lambda: r / "outputs")
    monkeypatch.setattr(la, "get_datastore_dir", lambda: r / "datastore")
    return r


def _status(repo: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()


def test_external_image_copied_and_committed(repo, tmp_path):
    """An image OUTSIDE the repo is archived (copy + git add), not git-mv'd."""
    ext_img = tmp_path / "outside" / "shot.png"
    ext_img.parent.mkdir()
    ext_img.write_bytes(b"\x89PNG\r\n\x1a\nfake-bytes")

    rc = la.main(["--image", str(ext_img), "--execute", "--commit"])

    assert rc == 0
    dest = (repo / "datastore" / "content" / "linkedin-archive" / "posts"
            / "2026-06-18_linkedin-post_milestone")
    assert (dest / "2026-06-18_linkedin-post_milestone.md").exists()
    assert (dest / "shot.png").exists()                                  # image archived
    assert (dest / "shot.png").read_bytes() == b"\x89PNG\r\n\x1a\nfake-bytes"
    assert ext_img.exists()                                             # copy, not move
    assert _status(repo) == ""                                         # committed, clean tree


def test_untracked_md_returns_7_even_with_external_image(repo, tmp_path):
    """An untracked .md must be caught (exit 7), not masked by an external image path."""
    md2 = repo / "outputs" / "content" / "linkedin" / "2026-06-19_linkedin-post_fresh.md"
    md2.write_text("Fresh untracked post.\n", encoding="utf-8")  # never git-added
    ext_img = tmp_path / "shot.png"
    ext_img.write_bytes(b"img")

    rc = la.main(["--slug", "2026-06-19_linkedin-post_fresh",
                  "--image", str(ext_img), "--execute"])

    assert rc == 7
