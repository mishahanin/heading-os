#!/usr/bin/env python3
"""The overlay census kept its own `git ls-files`, and it had the fourth defect.

`scripts/utils/repo_files.git_index_paths` exists because two gates had each
grown their own copy of this reader within an hour on 2026-09-02, and both
carried the same defects. `scripts/overlay-writer-census.py` had a third copy.
It had the `-z`, it skipped text mode, it decoded with surrogateescape -- the
three the shared reader's docstring names -- and it missed the fourth, which is
the one the shared reader RAISES for: an index that came back with nothing.

MEASURED 2026-09-08, before the fix, against a repository git tracks nothing in::

    tracked_paths on an index-less repo -> frozenset()

An empty tracked set is not a neutral answer for this census. Every candidate
writer is then reported `"tracked": false`, `--untracked-only` exits 1, and the
report the rule under construction reads says the operator's own committed
provisioning tools would be REFUSED by a tracked-file rule. That is the exact
misreading the census docstring says it exists to prevent, arriving through the
reader instead of through the repo list.

Both directions are here: the refusal, and a real two-repo listing that must
still come back whole, including a path whose bytes are not valid UTF-8.

Run: .venv/bin/python -m pytest tests/test_a_second_index_reader_that_read_nothing_as_nothing_to_track.py -q
"""

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.repo_files import IndexUnreadable  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "overlay_writer_census", ROOT / "scripts" / "overlay-writer-census.py")
census = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(census)


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _commitless_add(repo: Path, name: str | bytes) -> None:
    """Stage a file. The index, not a commit, is what this reader asks about."""
    raw = name if isinstance(name, bytes) else name.encode()
    target = repo / Path(raw.decode("utf-8", "surrogateescape"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)


# ============================================================
# 1 - an index that answered with nothing is not "nothing to track"
# ============================================================

def test_an_empty_index_refuses_instead_of_reporting_no_tracked_files(
        tmp_path, monkeypatch):
    empty = _repo(tmp_path / "empty")
    monkeypatch.setattr(census, "_repos", lambda: [empty])

    with pytest.raises(IndexUnreadable):
        census.tracked_paths()


def test_a_repo_git_cannot_be_asked_about_refuses_too(tmp_path, monkeypatch):
    """Not a repository at all: `git ls-files` exits non-zero."""
    not_a_repo = tmp_path / "bare-dir"
    not_a_repo.mkdir()
    monkeypatch.setattr(census, "_repos", lambda: [not_a_repo])

    with pytest.raises(IndexUnreadable):
        census.tracked_paths()


def test_one_readable_repo_does_not_excuse_an_unreadable_second(
        tmp_path, monkeypatch):
    """The engine answering is not permission to guess about the overlay.

    A reader that skipped the failing repo would report the overlay's committed
    provisioning tools as untracked while looking entirely healthy.
    """
    good = _repo(tmp_path / "good")
    _commitless_add(good, "a.py")
    monkeypatch.setattr(census, "_repos", lambda: [good, tmp_path / "absent"])

    with pytest.raises(IndexUnreadable):
        census.tracked_paths()


# ============================================================
# 2 - the other direction: a real listing still comes back whole
# ============================================================

def test_both_repos_are_listed_as_absolute_paths(tmp_path, monkeypatch):
    engine = _repo(tmp_path / "engine")
    overlay = _repo(tmp_path / "overlay")
    _commitless_add(engine, "scripts/tool.py")
    _commitless_add(overlay, "admin/provision.py")
    monkeypatch.setattr(census, "_repos", lambda: [engine, overlay])

    tracked = census.tracked_paths()

    assert tracked == {str(engine / "scripts" / "tool.py"),
                       str(overlay / "admin" / "provision.py")}, tracked


@pytest.mark.parametrize("raw,label", [
    (b"caf\xc3\xa9.py", "a UTF-8 name git C-quotes without -z"),
    (b"weird\xff.py", "a name whose bytes are not valid UTF-8 at all"),
    (b"two\nlines.py", "a newline in the filename"),
    (b"has\rcarriage.py", "a carriage return, which text mode would rewrite"),
])
def test_a_path_git_tracks_survives_the_trip_byte_for_byte(
        tmp_path, monkeypatch, raw, label):
    """The three properties `git_index_paths` was extracted to hold.

    A census that lost one of these would report a file it never looked at,
    or silently drop it from the corpus while claiming a clean sweep.
    """
    repo = _repo(tmp_path / "bytes")
    _commitless_add(repo, raw)
    monkeypatch.setattr(census, "_repos", lambda: [repo])

    expected = str(repo / raw.decode("utf-8", "surrogateescape"))
    assert census.tracked_paths() == {expected}, label


# ============================================================
# 3 - it is the shared reader, asked of the AST
# ============================================================

def test_the_census_no_longer_carries_its_own_git_invocation():
    """A substring scan would go red on the comment explaining the removal."""
    tree = ast.parse((ROOT / "scripts" / "overlay-writer-census.py")
                     .read_text(encoding="utf-8"))

    imported = {alias.name.split(".")[0]
                for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    assert "subprocess" not in imported, (
        "the census still imports subprocess; the index reader is shared now")

    git_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for arg in node.args[:1]
        if isinstance(arg, ast.List) and arg.elts
        and isinstance(arg.elts[0], ast.Constant) and arg.elts[0].value == "git"
    ]
    assert git_calls == [], (
        f"a second git invocation is back at line(s) "
        f"{[n.lineno for n in git_calls]}")

    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "git_index_paths" in names, (
        "the shared reader is named nowhere in the module's code")


# ============================================================
# 4 - the shipped command, over the real tree
# ============================================================

def test_the_real_census_still_runs_and_sweeps_a_real_corpus():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "overlay-writer-census.py"),
         "--json"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=600)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)

    # Floors outside every loop. MEASURED 2026-09-08 on this checkout: the
    # sweep reads well over a thousand files and derives double-digit
    # resolvers; a walk that collapsed would satisfy any per-candidate check.
    assert report["swept"] >= census.MIN_FILES_SWEPT, report
    assert report["resolvers"] > 0, report
    assert report["candidates"] > 0, report
