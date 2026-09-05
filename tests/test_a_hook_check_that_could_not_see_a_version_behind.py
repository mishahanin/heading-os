#!/usr/bin/env python3
"""`--check` said the push gate was present while it was a version behind.

MEASURED 2026-09-05 in HELM, on the real clone, before the fix:

    $ .venv/bin/python scripts/install-git-hooks.py --check
    engine pre-push hook present
    exit=0

    $ diff .githooks/pre-push .git/hooks/pre-push
    55c32
    < replay_refs | "$PY" "$ROOT/scripts/run-tests.py" --pre-push
    ---
    > "$PY" "$ROOT/scripts/run-tests.py"

The installed hook was the shape from before 2026-09-04: no `--pre-push`, no
stdin replay. So `prepush_gate.decide()` was never called on a real push, every
push from this clone ran all 24,805 tests, and the narrowing that shipped the
day before had not once run. Nothing said so, because `check_pre_push` asked
only whether the file contained the string `run-tests.py` — true of every
version of the hook, including the one it was meant to replace.

The message was the sharper half. It read `engine pre-push hook MISSING/stale`
on the failing branch, so the word `stale` was already promised by a method that
could not establish it: `.claude/rules/scope-claims.md`, a sentence asserting
more than its method establishes.

`install_pre_push` is a plain `shutil.copyfile` with nothing stamped in, so a
byte comparison against `.githooks/pre-push` is exact. The one case that is NOT
a comparison is the versioned source being unreadable, and that must degrade to
a stated reduction in coverage rather than to a clean pass.

Run: .venv/bin/python -m pytest \\
     tests/test_a_hook_check_that_could_not_see_a_version_behind.py -q
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# One literal, for the reason `tests/test_install_git_hooks.py` records at its
# own import: day mode finds a test's subject by reading its string constants,
# and a two-part join is not a constant it can recognise.
VERSIONED = ROOT / ".githooks/pre-push"
INSTALLER = ROOT / "scripts/install-git-hooks.py"


@pytest.fixture(scope="module")
def igh():
    spec = importlib.util.spec_from_file_location(
        "install_git_hooks_under_test", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True,
                   capture_output=True)
    return tmp_path


# ============================================================
# The floor: the two versions must actually differ in substance
# ============================================================

def test_the_versioned_hook_carries_the_flag_the_stale_one_lacked():
    """Without this the whole file could pass over an empty distinction.

    If `--pre-push` ever leaves the versioned hook, the mutation below stops
    being a mutation and every case here goes green while testing nothing.
    """
    body = VERSIONED.read_text(encoding="utf-8")
    assert "run-tests.py" in body
    assert "--pre-push" in body, (
        "the versioned hook no longer passes --pre-push, so this file's stale "
        "case is no longer the defect it was written for")


# ============================================================
# THE GUARD: a hook one version behind is not "present"
# ============================================================

def test_a_hook_a_version_behind_is_reported_stale(igh, repo):
    """The exact HELM state of 2026-09-05: armed, current-looking, obsolete."""
    igh.install_pre_push(repo, VERSIONED)
    hook = igh._hooks_dir(repo) / "pre-push"
    hook.write_text(
        hook.read_text(encoding="utf-8").replace(
            'run-tests.py" --pre-push', 'run-tests.py"'),
        encoding="utf-8")

    ok, why = igh.pre_push_verdict(repo, VERSIONED)

    assert ok is False, (
        f"a hook missing --pre-push passed the check; verdict was {why!r}")
    assert "STALE" in why, why
    assert "install-git-hooks.py" in why, (
        f"the verdict does not say how to fix it: {why!r}")


def test_the_current_hook_passes_and_says_what_was_compared(igh, repo):
    """The other direction. A check that refuses everything is not a check."""
    igh.install_pre_push(repo, VERSIONED)

    ok, why = igh.pre_push_verdict(repo, VERSIONED)

    assert ok is True, why
    assert "versioned source" in why, (
        f"the passing verdict does not name what it compared: {why!r}")


def test_an_absent_hook_is_not_a_stale_one(igh, repo):
    ok, why = igh.pre_push_verdict(repo, VERSIONED)
    assert ok is False
    assert "no pre-push hook" in why, why


def test_a_hook_that_runs_no_gate_at_all_fails_before_the_comparison(igh, repo):
    """The stock git-lfs hook. Named as its own state, not as staleness."""
    hooks = igh._hooks_dir(repo)
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-push").write_text("#!/bin/sh\nexec git lfs pre-push \"$@\"\n",
                                    encoding="utf-8")

    ok, why = igh.pre_push_verdict(repo, VERSIONED)

    assert ok is False
    assert "does not run the test gate" in why, why


# ============================================================
# The degradation: no source to compare against
# ============================================================

def test_without_a_source_the_verdict_says_it_did_not_compare(igh, repo):
    """A clone with no `.githooks/` still gets an answer, and an honest one.

    Passing here is correct: the gate IS armed. Claiming it matches would be the
    original defect with a new coat.
    """
    igh.install_pre_push(repo, VERSIONED)

    ok, why = igh.pre_push_verdict(repo, None)

    assert ok is True, why
    assert "not compared" in why, (
        f"a check that could not compare reported as if it had: {why!r}")


def test_an_unreadable_source_degrades_rather_than_failing_the_gate(igh, repo,
                                                                    tmp_path):
    igh.install_pre_push(repo, VERSIONED)

    ok, why = igh.pre_push_verdict(repo, tmp_path / "no-such-hook-source")

    assert ok is True, why
    assert "not compared" in why, why


# ============================================================
# The real entry point, and its exit code
# ============================================================

def test_check_exits_non_zero_over_a_stale_hook(igh, repo, monkeypatch,
                                                capsys):
    """Driven through `main()`, because the exit code is what a human reads.

    A test asserting only the predicate passes while `--check` still exits 0,
    which is the state that let this survive: the predicate was never the thing
    the operator saw.
    """
    igh.install_pre_push(repo, VERSIONED)
    hook = igh._hooks_dir(repo) / "pre-push"
    hook.write_text(
        hook.read_text(encoding="utf-8").replace(
            'run-tests.py" --pre-push', 'run-tests.py"'),
        encoding="utf-8")

    monkeypatch.setattr(igh, "get_workspace_root", lambda: repo)
    monkeypatch.setattr(sys, "argv", ["install-git-hooks.py", "--check"])
    # The versioned source has to live where main() looks for it.
    (repo / ".githooks").mkdir(exist_ok=True)
    (repo / ".githooks" / "pre-push").write_text(
        VERSIONED.read_text(encoding="utf-8"), encoding="utf-8")

    code = igh.main()

    assert code == 1, (
        f"--check exited {code} over a stale hook:\n{capsys.readouterr().out}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
