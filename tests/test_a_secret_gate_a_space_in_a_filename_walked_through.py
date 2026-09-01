"""Two ways a space in a filename walked past the standalone pre-commit hook.

The second, added 2026-09-01, is the one that actually carried a secret: the
hook piped the staged list to `secret-scanner.py --stdin`, which reads one path
per LINE and STRIPS each line, so a name padded with a space arrived as a
different name that opens nothing and was skipped in silence. MEASURED that day
in a scratch repository, `harmless.txt` plus `" leading-space.env"` plus
`"trailing-space.env "` with a `ghp_`-shaped token in each padded file:
"No secrets detected.", exit 0. The same token in `control.env` was refused. The
unbypassable push wall already handed its list over NUL-delimited; this
bypassable commit-time layer had fallen behind it. The fix is the same NUL
handoff, `git diff --cached -z` into `--stdin0`, so the shell stops being the
transport for a filename. Those cases are at the bottom of this file.

The first, which is the rest of it:

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
import string
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
    """A path git C-quotes is renamed, not committed.

    Without `-z`, git wraps such a path in double quotes with escapes. Measured
    before the guard, back when that same listing was also piped to the
    scanner: a staged `two\\nlines.env` produced "No secrets detected." and exit
    0 over a file that was never opened. A clean verdict for an unread file is
    the exact failure this hook exists to prevent.

    Since 2026-09-01 the scanner is fed NUL-delimited, so an escaped path can
    no longer reach it as a literal and this refusal is the second line rather
    than the only one. It is asserted here because the hook still refuses: the
    operator's posture is that such a name gets renamed, and a refusal can only
    over-block.
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


# ---------------------------------------------------------------------------
# A space in the NAME, which git does not escape, walked a secret through
# ---------------------------------------------------------------------------
#
# The guards above all end at the point where the path list is handed to the
# scanner. Until 2026-09-01 that handoff was `echo "$STAGED" | secret-scanner.py
# --stdin`, and `--stdin` reads one path per LINE and STRIPS each line. A
# leading or trailing space is legal in a POSIX filename and git prints it
# verbatim WITHOUT C-quoting, so the escape guard never sees it either. Stripped
# it names nothing, and `scan_files` skips a path that is not a file in silence.
#
# MEASURED 2026-09-01 in a scratch repository running the generated hook, with
# `harmless.txt`, `" leading-space.env"` and `"trailing-space.env "` staged
# together and the two padded files each holding the token below: the hook
# printed "No secrets detected." and exited 0. The identical token in
# `control.env` was refused. The unbypassable push wall
# (`scripts/push-all.py`) had already moved to `--stdin0`; this bypassable
# commit-time layer had fallen behind it.
#
# Synthesised at import, never written out as one literal, so this tracked file
# does not itself carry a real-shaped credential. Same construction as
# `tests/test_two_secret_walls_that_split_a_filename_in_half.py`.
TOKEN = "ghp" + "_" + (string.ascii_lowercase + string.digits * 2)[:36]

# TWO padded names, not one, and a harmless file in FRONT of them. With a single
# path a newline join and a NUL join produce identical bytes, so a one-file case
# would stay green against the very defect it claims to measure.
PADDED = (" leading-space.env", "trailing-space.env ")


def _commit(repo: Path) -> subprocess.CompletedProcess:
    """Drive the real `git commit`, so git runs the hook the way git runs it."""
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "attempt"],
        capture_output=True, text=True)


def test_a_space_padded_name_cannot_carry_a_secret_past_the_commit_hook(repo):
    (repo / "harmless.txt").write_text("nothing here\n", encoding="utf-8")
    _git(repo, "add", "--", "harmless.txt")
    for name in PADDED:
        (repo / name).write_text(f"TOKEN={TOKEN}\n", encoding="utf-8")
        _git(repo, "add", "--", name)

    result = _commit(repo)
    out = result.stdout + result.stderr

    assert result.returncode != 0, f"the commit went through: {out}"
    assert "COMMIT BLOCKED: Secrets detected" in out, out
    for name in PADDED:
        assert name in out, f"the refusal never named {name!r}: {out}"
    assert _git(repo, "rev-list", "--count", "--all").stdout.strip() == "0", \
        "a commit object was created despite the refusal"


def test_the_padded_names_are_the_whole_of_the_difference(repo):
    """The straw-man check: is it the PADDING that used to leak, or the token?

    The same token under an ordinary name was always refused. If this passed
    only because the token is refused everywhere, the test above would measure
    nothing about the padding. Both directions are asserted, so the two cases
    are known to differ in exactly one property.
    """
    (repo / "control.env").write_text(f"TOKEN={TOKEN}\n", encoding="utf-8")
    _git(repo, "add", "--", "control.env")

    result = _commit(repo)
    out = result.stdout + result.stderr

    assert result.returncode != 0
    assert "control.env" in out


def test_a_space_padded_name_without_a_secret_still_commits(repo):
    """The over-refusal anchor, and it is load-bearing.

    A hook that refused every padded name, or simply refused everything, would
    pass the case above while making the gate unusable. The clean tree here
    holds the same two padded names and the same harmless file; only the token
    is gone.
    """
    (repo / "harmless.txt").write_text("nothing here\n", encoding="utf-8")
    _git(repo, "add", "--", "harmless.txt")
    for name in PADDED:
        (repo / name).write_text("no credential in this one\n", encoding="utf-8")
        _git(repo, "add", "--", name)

    result = _commit(repo)
    out = result.stdout + result.stderr

    assert result.returncode == 0, out
    assert "BLOCKED" not in out
    assert _git(repo, "rev-list", "--count", "--all").stdout.strip() == "1"


def test_the_generated_hook_hands_the_scanner_a_nul_list(repo):
    """The mechanism, pinned so a revert to the line-oriented pipe is caught.

    The behavioural tests above are the real measurement; this one names WHY,
    so a future edit that reintroduces `--stdin` fails with the reason rather
    than with a puzzling secret that walked through.
    """
    hook = (repo / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")

    assert "--stdin0" in hook
    assert "secret-scanner.py --stdin\n" not in hook, \
        "the line-oriented, space-stripping handoff is back"
    assert 'echo "$STAGED" | python3' not in hook, \
        "the shell is the transport for a filename again"
