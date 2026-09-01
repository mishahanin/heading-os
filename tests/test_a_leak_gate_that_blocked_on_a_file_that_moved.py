#!/usr/bin/env python3
"""The leak gate blocked a commit when a listed file vanished before the read.

`scripts/leak-guard.py::check_paths` receives a path list (from pre-commit, or
from `git ls-files` in CI) and reads each entry afterwards. A file created and
deleted inside that window raised `FileNotFoundError`, which landed in
`unreadable`, which prints BLOCKED and returns 1. A commit stopped over the
gate's own timing, on a file carrying nothing.

## The same defect gets the OPPOSITE fix here, and that is the point

`scripts/push-all.py` has the identical race and SKIPS. It is allowed to,
because that script stages with `git add -A` before committing, so a path gone
from the worktree is gone from the commit as well. MEASURED 2026-09-01: an index
entry holding content before `add -A` and no entry after it.

Nothing re-stages here. This gate runs over an index somebody else built, so a
tracked file deleted from the worktree keeps its content in the INDEX, and the
index is what the commit carries. A blind skip would let a hardcoded data path
through by deleting the file after staging it -- the silent-skip defect this
gate's `unreadable` list was added to close on 2026-09-01, reached from a
different direction.

So the branch asks the index. Still staged means still committed means BLOCK.
Absent from both worktree and index means nothing to check, reported and not
blocking. Two gates, one race, two answers, because their callers stage
differently. These tests hold both answers so the pair cannot be "made
consistent" by someone who noticed the difference and not the reason.

Run: .venv/bin/python -m pytest \\
        tests/test_a_leak_gate_that_blocked_on_a_file_that_moved.py -q
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "leak_guard_moved", ROOT / "scripts" / "leak-guard.py")
lg = importlib.util.module_from_spec(_spec)
sys.modules["leak_guard_moved"] = lg
_spec.loader.exec_module(lg)

# An engine-routed path that does not exist. `scripts/` resolves `engine`, so
# the gate does not skip it on classification before it ever tries the read.
PHANTOM = ROOT / "scripts" / "zz_ghost_probe_9931.py"


def test_a_vanished_unstaged_file_does_not_block_the_commit(capsys):
    """The reported failure. Nothing to check is not the same as unchecked."""
    assert not PHANTOM.exists(), "the fixture is only meaningful while absent"

    assert lg.check_paths([str(PHANTOM)]) == 0, (
        "a path that vanished before it could be read blocked the commit, "
        "even though it is not staged and carries nothing into it")

    out = capsys.readouterr().out
    assert "vanished" in out and PHANTOM.name in out, (
        "the dropped path was not named, so the gate narrowed its corpus in "
        "silence while still returning the code that means clean")
    assert "BLOCKED" not in out


def test_a_vanished_but_still_staged_file_blocks(monkeypatch, capsys):
    """The jaw. This is the case a blind skip would have shipped.

    Content deleted from the worktree but present in the index IS the commit.
    The gate never read it, so it must not report it clean.
    """
    monkeypatch.setattr(lg, "_staged_in_index", lambda p: True)

    assert lg.check_paths([str(PHANTOM)]) == 1, (
        "a file gone from the worktree but still STAGED was passed. Its content "
        "is in this commit and no gate has read it")

    out = capsys.readouterr().out
    assert "BLOCKED" in out
    assert "still staged" in out, (
        "the operator is not told WHY this one blocked while a vanished file "
        "does not, so the only available fix is guesswork")


def test_the_index_question_fails_closed(monkeypatch):
    """"I could not tell" must never resolve to "nothing was there".

    A gate whose whole job is refusing cannot treat an unanswerable question as
    a pass. Both failure shapes are driven: git returning nothing usable, and
    git not being callable at all.
    """
    class Boom:
        returncode = 128        # measured: git's exit outside a repository
        stdout = b""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Boom())
    assert lg._staged_in_index(PHANTOM) is True, (
        "a git call that failed was read as 'not staged', which turns an "
        "unanswerable question into a pass")

    def explode(*a, **k):
        raise OSError("git is not installed")

    monkeypatch.setattr(subprocess, "run", explode)
    assert lg._staged_in_index(PHANTOM) is True


def test_a_path_outside_the_workspace_fails_closed(monkeypatch):
    """A path the gate cannot even place is not a path it may wave through."""
    assert lg._staged_in_index(Path("/etc/hostname")) is True


def test_the_gate_still_blocks_a_real_violation(tmp_path, capsys):
    """A fix that turns a block into a pass is worse than the block it replaced.

    The violating file is present the whole time and routes `engine`, so nothing
    about the vanished-path branch should reach it.
    """
    live = ROOT / "scripts" / "zz_probe_violation_9931.py"
    live.write_text('P = "crm/contacts/x.md"\n', encoding="utf-8")
    try:
        assert lg.check_paths([str(live)]) == 1, (
            "a hardcoded data path in a present engine file was not caught")
    finally:
        live.unlink(missing_ok=True)

    assert "BLOCKED" in capsys.readouterr().out


def test_a_clean_present_file_still_passes(capsys):
    """The ordinary case. A gate that blocks everything is not a gate."""
    assert lg.check_paths([str(ROOT / "scripts" / "leak-guard.py")]) == 0
    out = capsys.readouterr().out
    assert "BLOCKED" not in out and "vanished" not in out


def test_a_tracked_file_reads_as_staged():
    """The positive anchor for `_staged_in_index`, driven against real git.

    Every other test of that helper drives a FAILURE shape, and a helper that
    returned True unconditionally would satisfy all of them while blocking every
    vanished path. This is the case that has to come back True for a real
    reason, and `scripts/leak-guard.py` is tracked by definition -- it is the
    file under test.
    """
    assert lg._staged_in_index(ROOT / "scripts" / "leak-guard.py") is True


def test_an_untracked_present_file_reads_as_not_staged(tmp_path):
    """The other side of the anchor, or True-always passes everything above.

    Written into the repository rather than `tmp_path`, because the question is
    "does git's index know this path", and a path outside the worktree short
    circuits on the `relative_to` guard instead of reaching git at all.
    """
    scratch = ROOT / "scripts" / "zz_untracked_probe_9931.py"
    scratch.write_text("# scratch\n", encoding="utf-8")
    try:
        assert lg._staged_in_index(scratch) is False, (
            "an untracked file was reported as staged, so every vanished "
            "scratch file would block a commit")
    finally:
        scratch.unlink(missing_ok=True)
