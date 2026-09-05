#!/usr/bin/env python3
"""The push wall named the file and not the state it was in.

MEASURED 2026-09-05 on a real `push-all.py` run the operator asked for. The
wall refused, correctly, over two UNTRACKED files under another task's output
directory, and said only:

    REFUSING TO PUSH — secret-like CONTENT in a file about to be pushed.

The claim is true in the default mode, because step 3 runs `git add -A` and the
files are in the commit a moment later. What the operator could not tell from
that sentence is whether the offending bytes were ALREADY IN A COMMIT (which
needs history surgery) or were a scratch file this run was about to sweep in
(which needs nothing but leaving it alone). Establishing that took three git
commands by hand while the push sat blocked.

`.claude/rules/scope-claims.md` obligation 2 is the one at stake: name what your
method covers. `_scan_set_composition` prints the scanned set BY GIT STATE
beneath the refusal, and says in the same line why those legs are there — in the
default mode because `git add -A` is about to run, under `--no-commit` because
the committed delta is the whole set that invocation can push.

Counts, not paths, deliberately: the scanner has already printed the paths that
matter, and a second full listing buries them.

Run: .venv/bin/python -m pytest \\
     tests/test_a_refusal_that_named_a_file_without_naming_its_state.py -q
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PUSH_ALL = ROOT / "scripts/push-all.py"


@pytest.fixture(scope="module")
def push_all():
    spec = importlib.util.spec_from_file_location("push_all_state_under_test",
                                                  PUSH_ALL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A clone with a real `origin/main` and one file in each of the four legs.

    A real bare remote, not a faked ref: `_scan_set_composition` asks git to
    resolve `origin/main`, and a scratch repo without one takes a different
    branch entirely.
    """
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True,
                   capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True,
                   capture_output=True)
    _git(work, "config", "user.email", "t@example.invalid")
    _git(work, "config", "user.name", "t")
    _git(work, "checkout", "-q", "-b", "main")

    (work / "base.txt").write_text("base\n", encoding="utf-8")
    _git(work, "add", "base.txt")
    _git(work, "commit", "-q", "-m", "base")
    _git(work, "push", "-q", "-u", "origin", "main")

    (work / "committed.txt").write_text("committed and unpushed\n",
                                        encoding="utf-8")
    _git(work, "add", "committed.txt")
    _git(work, "commit", "-q", "-m", "unpushed")

    (work / "staged.txt").write_text("staged only\n", encoding="utf-8")
    _git(work, "add", "staged.txt")
    (work / "base.txt").write_text("base edited, unstaged\n", encoding="utf-8")
    (work / "stray.txt").write_text("untracked\n", encoding="utf-8")
    return work


# ============================================================
# The floor: the fixture really holds one file per leg
# ============================================================

def test_the_fixture_puts_a_file_in_every_leg(push_all, repo):
    """Without this, every count asserted below could be a zero from an empty
    repository, and the composition line would read as correct while measuring
    nothing."""
    line = push_all._scan_set_composition(
        push_all._push_delta_legs(repo, will_commit=True), will_commit=True)

    for expected in ("1 committed-unpushed", "1 staged", "1 unstaged",
                     "1 untracked"):
        assert expected in line, (
            f"the fixture does not hold {expected!r}, so this file cannot show "
            f"that the composition line reports a real state: {line!r}")


# ============================================================
# THE GUARD, direction 1: the default mode names all four legs
# ============================================================

def test_the_default_mode_names_every_leg_and_why_they_are_there(push_all, repo):
    line = push_all._scan_set_composition(
        push_all._push_delta_legs(repo, will_commit=True), will_commit=True)

    assert "git add -A" in line, (
        f"the default mode's line does not say WHY the working-tree legs are "
        f"scanned. That reason is the whole justification for calling an "
        f"untracked file 'about to be pushed': {line!r}")
    # The MECHANISM alone is not the explanation, and this second assertion is
    # here because a mutation proved it: rewriting the clause to "so they are
    # included" left `git add -A` in the string and the case green, restoring a
    # line that names a command without saying what it does to the file.
    assert "in the commit" in line, (
        f"the line names `git add -A` without saying that it puts these files "
        f"IN THE COMMIT, which is the step that makes the refusal's sentence "
        f"true of an untracked file: {line!r}")
    assert "--no-commit" not in line, (
        f"the default-mode line mentions --no-commit, which is not the mode "
        f"that ran: {line!r}")


# ============================================================
# THE GUARD, direction 2: --no-commit names only what it can push
# ============================================================

def test_no_commit_names_only_the_committed_delta(push_all, repo):
    line = push_all._scan_set_composition(
        push_all._push_delta_legs(repo, will_commit=False), will_commit=False)

    assert "1 committed-unpushed" in line, (
        f"--no-commit drops the committed delta from its own description, and "
        f"that IS the set it pushes: {line!r}")
    for absent in ("staged", "unstaged", "untracked"):
        assert absent not in line, (
            f"--no-commit claims a {absent} leg it does not scan; the sentence "
            f"is then wrong in the same direction the wall was: {line!r}")
    assert "stages nothing" in line, (
        f"--no-commit's line does not say why the other legs are absent, so a "
        f"reader cannot tell a narrowing from a bug: {line!r}")


# ============================================================
# The refusal must actually carry it
# ============================================================

def test_the_refusal_prints_the_composition(push_all, capsys):
    """The call site, not the helper. A composition nobody prints is a string.

    Drives the real `_refuse_on_scanner` with a scanner result that failed, the
    way `content_scan` does, and asserts the line reaches stdout before the
    exit.
    """
    proc = subprocess.CompletedProcess(["scanner"], 1, "finding\n", "")

    with pytest.raises(SystemExit) as exc:
        push_all._refuse_on_scanner(proc, "a file about to be pushed",
                                    "  scanned set: 1 committed-unpushed — why.")
    assert exc.value.code == 2

    out = capsys.readouterr().out
    assert "REFUSING TO PUSH" in out
    assert "scanned set: 1 committed-unpushed" in out, (
        f"the refusal did not carry the composition line, so the operator sees "
        f"the file and not the state it is in: {out!r}")


def test_an_empty_composition_prints_nothing_extra(push_all, capsys):
    """The other direction: a caller with nothing to add must not print a
    stray blank line into the refusal."""
    proc = subprocess.CompletedProcess(["scanner"], 1, "finding\n", "")

    with pytest.raises(SystemExit):
        push_all._refuse_on_scanner(proc, "a file about to be pushed")

    out = capsys.readouterr().out
    assert "scanned set" not in out
    # The colour reset trails the period, so strip escapes before asking what
    # the last visible character is. Asserting on the raw string here is how
    # this case first went red against a correct implementation.
    import re
    visible = re.sub(r"\x1b\[[0-9;]*m", "", out).rstrip()
    assert visible.endswith("about to be pushed."), (
        f"the refusal ends with something other than its own sentence, so an "
        f"empty composition is printing anyway: {out!r}")


def test_the_composition_runs_no_subprocess(push_all, repo, monkeypatch):
    """The regression a SIBLING test caught, pinned here where it belongs.

    The first version of this feature re-ran the git commands from a second copy
    of the argv. `tests/test_two_walls_that_looked_at_the_wrong_moment.py`
    replaces `subprocess.run` wholesale with a double that asserts BYTES mode
    (the scanner handoff is bytes), so those text-mode git calls tripped an
    assertion written about a completely different call, and the pre-push gate
    refused the push. MEASURED 2026-09-05: `1 failed, 5890 passed`.

    The deeper defect was the duplication itself: two copies of
    `--diff-filter=ACMT`, so widening the filter in one would leave the other
    describing the old scan. Formatting the legs already collected fixes both.
    """
    legs = push_all._push_delta_legs(repo, will_commit=True)

    def _no(*_a, **_k):
        raise AssertionError(
            "_scan_set_composition ran a subprocess; it must describe the legs "
            "that were already collected, not walk the repository a second time")

    monkeypatch.setattr(push_all.subprocess, "run", _no)
    line = push_all._scan_set_composition(legs, will_commit=True)
    assert "scanned set:" in line


def test_the_union_of_the_legs_is_the_scan_set(push_all, repo):
    """One source, asserted. If the legs and the scanned set ever diverge, the
    composition describes something other than what was read."""
    legs = push_all._push_delta_legs(repo, will_commit=True)
    union = set().union(*legs.values(), set())

    assert union == push_all._push_delta_files(repo, will_commit=True)
    assert len(union) >= 4, (
        f"the fixture yields {len(union)} file(s); measured 2026-09-05 it holds "
        f"four, one per leg, and fewer means this case is comparing two empty "
        f"sets: {sorted(union)}")


def test_content_scan_hands_the_composition_to_the_refusal(push_all):
    """AST, at the call site.

    A helper that is correct and uncalled is the shape obligation 6 of
    `.claude/rules/development-standards.md` names: the test exercises a
    function the entry point has stopped calling.
    """
    import ast
    tree = ast.parse(PUSH_ALL.read_text(encoding="utf-8"))
    scan = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "content_scan")

    calls = [n for n in ast.walk(scan)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_refuse_on_scanner"]
    assert calls, "content_scan no longer refuses on the scanner"
    assert len(calls) == 1, (
        f"content_scan holds {len(calls)} refusal call(s); this case checked "
        f"one and cannot speak for the others")

    passes_composition = any(
        isinstance(a, ast.Call) and isinstance(a.func, ast.Name)
        and a.func.id == "_scan_set_composition"
        for a in calls[0].args
    ) or any(kw.arg == "composition" for kw in calls[0].keywords)
    assert passes_composition, (
        "content_scan refuses without handing over the scan-set composition, "
        "so the refusal names a file and not the state it is in")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
