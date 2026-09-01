#!/usr/bin/env python3
"""Two readers whose existence check stood in for a read that could still fail.

`scripts/sc-trace.py` checks `anchor.is_file()` and then reads the anchor with no
guard. `is_file()` answers whether the NAME is there, never whether the bytes can
be had, so an artifact with its permission bits off raised PermissionError out of
`main`: a traceback and interpreter exit 1, where the file's own exit-code table
promises 2 for "a missing artifact / contract path". The `--contract` branch one
line below already had exactly this guard, and its comment claimed parity the
anchor never got.

`scripts/implement-trajectory-log.py`'s `_git_changed_files` ran `git` with
`text=True` and no `encoding`, so git's raw path bytes were decoded strictly. A
filename holding a byte that is not valid UTF-8 raised UnicodeDecodeError INSIDE
the try, where neither OSError nor SubprocessError catches it, and it travelled
out through `verify_trajectory` and `cmd_verify` as a traceback. An ADVISORY
reconciliation therefore killed the whole verifier - no defects, no scope line,
no verdict - against a docstring promising "an empty set on any git failure so
the run-level reconciliation degrades gracefully".
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# sc-trace: an unreadable anchor is exit 2, not a traceback
# ============================================================

@pytest.fixture
def unreadable_anchor(tmp_path):
    """A gate artifact that exists and cannot be opened."""
    anchor = tmp_path / "gate-artifact.md"
    anchor.write_text(
        "## Success Criteria\n\n- **SC-1**: the trace is read\n", encoding="utf-8")
    anchor.chmod(0o000)
    if os.access(anchor, os.R_OK):  # pragma: no cover - root, or an ACL-less FS
        pytest.skip("this user can read a 0o000 file; the defect is unreachable here")
    yield anchor
    anchor.chmod(0o644)


def test_the_anchor_still_passes_the_existence_check_it_was_guarded_by(unreadable_anchor):
    """The premise: `is_file()` says yes about a file that cannot be read."""
    assert unreadable_anchor.is_file() is True
    with pytest.raises(PermissionError):
        unreadable_anchor.read_text(encoding="utf-8")


def test_an_unreadable_anchor_exits_two_instead_of_tracebacking(unreadable_anchor,
                                                                tmp_path):
    sc = _load("sc-trace", "sc_trace_cli")
    contract = tmp_path / "test_contract.py"
    contract.write_text(
        'def test_one():\n    """SC-1. Decided here."""\n    assert True\n',
        encoding="utf-8")
    rc = sc.main(["--anchor", str(unreadable_anchor), "--contract", str(contract)])
    assert rc == 2


def test_the_refusal_names_the_artifact_and_the_reason(unreadable_anchor, tmp_path,
                                                       capsys):
    sc = _load("sc-trace", "sc_trace_cli")
    contract = tmp_path / "test_contract.py"
    contract.write_text(
        'def test_one():\n    """SC-1. Decided here."""\n    assert True\n',
        encoding="utf-8")
    sc.main(["--anchor", str(unreadable_anchor), "--contract", str(contract)])
    err = _plain(capsys.readouterr().err)
    assert "cannot read the artifact" in err
    assert str(unreadable_anchor) in err
    assert "Permission denied" in err


def test_a_readable_anchor_is_untouched_by_the_guard(tmp_path, capsys):
    """The guard must not become a new way to refuse a good artifact."""
    sc = _load("sc-trace", "sc_trace_cli")
    anchor = tmp_path / "gate-artifact.md"
    anchor.write_text(
        "## Success Criteria\n\n- **SC-1**: the trace is read\n", encoding="utf-8")
    contract = tmp_path / "test_contract.py"
    contract.write_text(
        'def test_one():\n    """SC-1. Decided here."""\n    assert True\n',
        encoding="utf-8")
    rc = sc.main(["--anchor", str(anchor), "--contract", str(contract)])
    out = _plain(capsys.readouterr().out)
    assert rc == 0, out
    assert "SC-1" in out


# ============================================================
# trajectory verify: a filename git cannot spell in UTF-8
# ============================================================

_UNDECODABLE = b"bad\xffname"


@pytest.fixture
def repo_with_an_undecodable_filename(tmp_path):
    """A real git repo holding a filename that is not valid UTF-8."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")

    def git(*args):
        return subprocess.run(["git", *args], cwd=str(repo), env=env,
                              capture_output=True, check=True)

    git("init", "-q", ".")
    git("config", "user.email", "q@example.invalid")
    git("config", "user.name", "Q Branch")
    (repo / "tracked.txt").write_text("moneypenny\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "seed")
    try:
        (repo / os.fsdecode(_UNDECODABLE)).write_bytes(b"skyfall\n")
    except (OSError, UnicodeError):  # pragma: no cover - a filesystem that refuses
        pytest.skip("this filesystem will not hold a non-UTF-8 filename")
    return repo


def test_git_really_emits_the_byte_that_broke_the_strict_decode(
        repo_with_an_undecodable_filename):
    """The premise, pinned: without an errors= policy the decode raises."""
    with pytest.raises(UnicodeDecodeError):
        subprocess.run(["git", "ls-files", "-z", "--others", "--exclude-standard"],
                       cwd=str(repo_with_an_undecodable_filename),
                       capture_output=True, text=True, encoding="utf-8", timeout=10)


def test_the_change_set_survives_a_filename_it_cannot_decode(
        repo_with_an_undecodable_filename, monkeypatch):
    itl = _load("implement-trajectory-log", "implement_trajectory_log")
    monkeypatch.setattr(itl, "WORKSPACE_ROOT", repo_with_an_undecodable_filename)
    changed = itl._git_changed_files("HEAD")
    assert isinstance(changed, set)
    assert len(changed) == 1, changed
    # The undecodable name survives in a printable form rather than taking the
    # whole reconciliation down with it.
    only = next(iter(changed))
    assert only.startswith("bad") and only.endswith("name")


def test_the_surviving_entries_are_printable(repo_with_an_undecodable_filename,
                                             monkeypatch, capsys):
    """`surrogateescape` would have moved the crash to the print, not removed it."""
    itl = _load("implement-trajectory-log", "implement_trajectory_log")
    monkeypatch.setattr(itl, "WORKSPACE_ROOT", repo_with_an_undecodable_filename)
    for entry in itl._git_changed_files("HEAD"):
        print(f"(advisory) {entry} was modified in this run")
    assert "was modified in this run" in capsys.readouterr().out


@pytest.fixture
def repo_whose_TRACKED_file_is_undecodable(tmp_path):
    """The other leg. `_git_changed_files` runs TWO git commands and decodes
    each: `diff --name-only` for tracked changes and `ls-files --others` for
    untracked ones. The fixture above only ever creates an UNTRACKED file, so
    the tracked leg was never driven.

    MEASURED 2026-09-01: changing ONLY the diff leg's decode to a strict
    `decode("utf-8")` left this file green at 9 passed, while a tracked file
    named b'bad\\xffname' then raised UnicodeDecodeError out of `cmd_verify` -
    the exact traceback this file exists to prevent, through the door nobody
    opened. A fix present in two legs and tested in one is the shape this
    repository keeps producing.
    """
    repo = tmp_path / "tracked-repo"
    repo.mkdir()
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")

    def git(*args):
        return subprocess.run(["git", *args], cwd=str(repo), env=env,
                              capture_output=True, check=True)

    git("init", "-q", ".")
    git("config", "user.email", "q@example.invalid")
    git("config", "user.name", "Q Branch")
    try:
        (repo / os.fsdecode(_UNDECODABLE)).write_bytes(b"skyfall\n")
    except (OSError, UnicodeError):  # pragma: no cover - a filesystem that refuses
        pytest.skip("this filesystem will not hold a non-UTF-8 filename")
    (repo / "tracked.txt").write_text("moneypenny\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "seed")
    # Both files now TRACKED, and both modified: they reach the diff leg, and
    # nothing reaches the untracked leg at all.
    (repo / os.fsdecode(_UNDECODABLE)).write_bytes(b"spectre\n")
    (repo / "tracked.txt").write_text("q branch\n", encoding="utf-8")
    return repo


def test_the_tracked_leg_survives_a_filename_it_cannot_decode(
        repo_whose_TRACKED_file_is_undecodable, monkeypatch):
    itl = _load("implement-trajectory-log", "implement_trajectory_log")
    monkeypatch.setattr(itl, "WORKSPACE_ROOT", repo_whose_TRACKED_file_is_undecodable)

    changed = itl._git_changed_files("HEAD")

    assert "tracked.txt" in changed, (
        "the readable neighbour vanished with the undecodable one")
    assert len(changed) == 2, changed
    mangled = next(p for p in changed if p != "tracked.txt")
    assert mangled.startswith("bad") and mangled.endswith("name")


def test_the_tracked_leg_is_driven_and_the_untracked_one_is_not(
        repo_whose_TRACKED_file_is_undecodable):
    """Establish the fixture's premise rather than assume it.

    Without this, a fixture that accidentally left the file untracked would
    re-test the leg already covered above and report a coverage it never had.
    """
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    repo = repo_whose_TRACKED_file_is_undecodable
    others = subprocess.run(["git", "ls-files", "-z", "--others",
                             "--exclude-standard"],
                            cwd=str(repo), env=env, capture_output=True)
    diff = subprocess.run(["git", "diff", "--name-only", "-z", "HEAD"],
                          cwd=str(repo), env=env, capture_output=True)

    assert others.stdout == b"", f"the fixture left untracked files: {others.stdout!r}"
    assert _UNDECODABLE in diff.stdout, (
        f"the undecodable name is not in the tracked diff: {diff.stdout!r}")


def test_a_decodable_repo_still_reports_both_halves_of_the_change_set(tmp_path,
                                                                     monkeypatch):
    """Regression guard: the errors= policy must not narrow the ordinary case."""
    itl = _load("implement-trajectory-log", "implement_trajectory_log")
    repo = tmp_path / "plain"
    repo.mkdir()
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")

    def git(*args):
        subprocess.run(["git", *args], cwd=str(repo), env=env,
                       capture_output=True, check=True)

    git("init", "-q", ".")
    git("config", "user.email", "q@example.invalid")
    git("config", "user.name", "Q Branch")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "seed")
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    (repo / "fresh.txt").write_text("three\n", encoding="utf-8")

    monkeypatch.setattr(itl, "WORKSPACE_ROOT", repo)
    assert itl._git_changed_files("HEAD") == {"tracked.txt", "fresh.txt"}


def test_a_git_failure_still_degrades_to_an_empty_set(tmp_path, monkeypatch):
    """The documented graceful degrade, unchanged: not a git repo, no defect."""
    itl = _load("implement-trajectory-log", "implement_trajectory_log")
    monkeypatch.setattr(itl, "WORKSPACE_ROOT", tmp_path)
    assert itl._git_changed_files("deadbeef") == set()
