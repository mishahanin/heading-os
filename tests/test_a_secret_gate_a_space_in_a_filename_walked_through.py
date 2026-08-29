"""The standalone pre-commit hook's dirty-tree guard failed open on quoting.

`scripts/install-hooks.py` writes a `SCANNER_BLOCK` whose first job is to REFUSE
a partially-staged commit. The scanner takes PATHS and reads them from the
WORKING TREE, while the staged list comes from the INDEX, so when the two differ
it scans bytes that will not be committed and never sees the bytes that will.
The guard closes that by blocking instead.

It was written `git diff --name-only -- $STAGED`, unquoted. MEASURED 2026-08-30
in a scratch repository: with `my secret.env` staged and its worktree copy then
changed without re-staging, the shell split the name into `my` and
`secret.env`, git matched neither, the guard reported NO dirty file, and the run
went on to scan the harmless worktree bytes.

WHITESPACE is the whole of the bypass. Glob characters are not a second case,
and this file said they were until it was measured: a staged `a*.env` beside
`ab.env` and `ac.env` was still reported dirty, because git's own pathspec
matching is glob-aware, so the shell's expansion only WIDENS what matches. A
wider pathspec can block a commit it need not have blocked; it cannot let one
through. The glob names below are kept as coverage that the fix did not break
odd filenames, not as cases that ever leaked.

Quoting `"$STAGED"` is NOT the fix: several staged files arrive newline-joined
in one variable and would become a single nonsense pathspec. The list is built
NUL-delimited and handed over by `xargs -0`.

Scope, stated rather than assumed: on this clone the installed
`.git/hooks/pre-commit` is the pre-commit FRAMEWORK's file, and the framework
stashes unstaged changes, so it never had this hole. This template is what
`install-hooks.py` writes for a clone without the framework. Everything below
runs the GENERATED hook in a throwaway repository under tmp_path; nothing
touches this repository's hooks.

Run: python3 -m pytest tests/test_a_secret_gate_a_space_in_a_filename_walked_through.py
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _install_hooks_module():
    spec = importlib.util.spec_from_file_location(
        "install_hooks_quoting", ROOT / "scripts" / "install-hooks.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["install_hooks_quoting"] = mod
    spec.loader.exec_module(mod)
    return mod


IH = _install_hooks_module()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


@pytest.fixture()
def repo(tmp_path):
    """A throwaway repo carrying the REAL generated hook and a real scanner."""
    root = tmp_path / "clone"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _git(root, "config", "user.email", "q@example.invalid")
    _git(root, "config", "user.name", "Q Branch")

    (root / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "secret-scanner.py",
                root / "scripts" / "secret-scanner.py")
    shutil.copytree(ROOT / "scripts" / "utils", root / "scripts" / "utils")

    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\n\n{IH.SCANNER_BLOCK}\nexit 0\n", encoding="utf-8")
    hook.chmod(0o755)
    assert IH.HOOK_MARKER in hook.read_text(encoding="utf-8"), \
        "the generated hook is not the scanner block; this fixture measures nothing"
    return root


def _run_hook(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(repo / ".git" / "hooks" / "pre-commit")],
                          cwd=repo, capture_output=True, text=True)


def _stage_then_dirty(repo: Path, name: str) -> None:
    """Stage one version of a file, then change the worktree copy. This is the
    exact split the guard exists to refuse."""
    target = repo / name
    target.write_text("the bytes that would be committed\n", encoding="utf-8")
    _git(repo, "add", "--", name)
    target.write_text("harmless bytes the scanner would read instead\n",
                      encoding="utf-8")


@pytest.mark.parametrize("name", [
    "my notes.env",          # THE case: a space split the pathspec in two
    "quarterly report.env",  # a second spaced name, so one is not a fluke
    "tab\tname.env",         # any IFS character does it, not just a space
    "star*name.env",         # coverage only: glob names never leaked (see above)
    "bracket[1].env",        # coverage only
])
def test_a_partially_staged_file_is_refused_whatever_its_name(repo, name):
    _stage_then_dirty(repo, name)

    result = _run_hook(repo)

    assert result.returncode == 1, (
        f"the guard let {name!r} through: {result.stdout}{result.stderr}")
    assert "unstaged edits" in result.stdout
    assert name in result.stdout, "the refusal did not name the offending file"


def test_an_ordinary_filename_is_still_refused(repo):
    """The case that always worked. A fix that lost it would be a regression."""
    _stage_then_dirty(repo, "ordinary.env")

    result = _run_hook(repo)

    assert result.returncode == 1
    assert "ordinary.env" in result.stdout


def test_a_fully_staged_spaced_file_is_allowed_through(repo):
    """The negative control, and it carries the whole file.

    A guard that simply refused everything would pass every test above while
    making the hook unusable.
    """
    (repo / "my notes.env").write_text("nothing secret here\n", encoding="utf-8")
    _git(repo, "add", "--", "my notes.env")

    result = _run_hook(repo)

    assert result.returncode == 0, f"{result.stdout}{result.stderr}"
    assert "unstaged edits" not in result.stdout


def test_one_dirty_file_among_several_is_named_alone(repo):
    """Why `"$STAGED"` is not the fix: several staged paths arrive newline-joined
    in one variable, and quoting the lot makes one nonsense pathspec that matches
    nothing. Each path has to reach git as its own argument."""
    for clean in ("alpha.env", "beta gamma.env", "delta.env"):
        (repo / clean).write_text("clean\n", encoding="utf-8")
        _git(repo, "add", "--", clean)
    _stage_then_dirty(repo, "epsilon zeta.env")

    result = _run_hook(repo)

    assert result.returncode == 1
    assert "epsilon zeta.env" in result.stdout
    for clean in ("alpha.env", "beta gamma.env", "delta.env"):
        assert clean not in result.stdout, f"{clean} was reported dirty and is not"


@pytest.mark.parametrize("name", [
    "two\nlines.env",      # git C-quotes a newline
    'quote".env',          # and a double quote
    "back\\slash.env",     # and a backslash
    "\u043f\u0430\u0440\u043e\u043b\u0438.env",           # and, by default, any non-ASCII byte
])
def test_an_escaped_path_is_refused_rather_than_mis_scanned(repo, name):
    """The scanner's `--stdin` contract is one RAW path per line.

    Without `-z`, git wraps such a path in double quotes with escapes, so the
    scanner is handed a literal naming no file. Measured before the guard: a
    staged `two\\nlines.env` produced "No secrets detected." and exit 0 over a
    file that was never opened. A clean verdict for an unread file is the exact
    failure this hook exists to prevent.
    """
    (repo / name).write_text("content\n", encoding="utf-8")
    _git(repo, "add", "--", name)

    result = _run_hook(repo)

    assert result.returncode == 1, (
        f"{name!r} reached the scanner as an escaped literal: "
        f"{result.stdout}{result.stderr}")
    assert "must escape" in result.stdout


def test_an_ascii_path_is_not_swept_up_by_the_escape_guard(repo):
    """The control for the guard above. Refusing every path would pass it."""
    (repo / "plain-name.env").write_text("content\n", encoding="utf-8")
    _git(repo, "add", "--", "plain-name.env")

    result = _run_hook(repo)

    assert result.returncode == 0, f"{result.stdout}{result.stderr}"
    assert "must escape" not in result.stdout


def test_an_empty_index_runs_no_scan_and_blocks_nothing(repo):
    """The boundary at the other end: nothing staged is not a refusal."""
    result = _run_hook(repo)

    assert result.returncode == 0
    assert "BLOCKED" not in result.stdout
