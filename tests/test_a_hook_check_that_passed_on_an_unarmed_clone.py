#!/usr/bin/env python3
"""The diagnostic for an unarmed clone certified an unarmed clone.

`.claude/rules/security.md` and `docs/DEPLOYMENT.md` both name
`python scripts/install-hooks.py --check` as the way to confirm that the commit
gates are armed, and `docs/DEPLOYMENT.md` offers it specifically for the symptom
"the commit secret gate never fires". Until 2026-09-02 the framework branch of
that command exited 0 on the sole evidence that `.pre-commit-config.yaml` existed
on disk. That file is committed, so it exists in every clone, including one where
`pre-commit install` was never run.

MEASURED 2026-09-02 before the fix, in a scratch repository holding the config and
no `.git/hooks/pre-commit`:

    $ WORKSPACE_ROOT=/tmp/hookcheck-... .venv/bin/python scripts/install-hooks.py --check
    Git hooks status:
      managed by the pre-commit framework (.pre-commit-config.yaml present)
      This installer is superseded; secret scanning runs as the secret-scanner-31c local hook.
    exit=0

A green line and exit 0 over a clone with no commit gate at all. This file pins
the four ways a clone can be unarmed while that config sits there, the two ways it
can be genuinely armed, and the arming path itself, so the refusal cannot quietly
become unconditional and the pass cannot quietly become a rubber stamp.

Every repository here is created under `tmp_path`. Nothing reads or writes the
real clone, and no test asserts anything about the host's own hook state.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ih = _load("install-hooks", "install_hooks_unarmed_clone")
igh = _load("install-git-hooks", "install_git_hooks_unarmed_clone")

# What the pre-commit framework stamps into what it generates. Read from the
# sibling installer rather than retyped, so a test cannot pass against a marker
# the verifier no longer looks for.
FRAMEWORK_HOOK = f"#!/usr/bin/env bash\n# {igh.PRE_COMMIT_FRAMEWORK_MARKER}: https://pre-commit.com\nexit 0\n"


# ============================================================
# Scratch repositories
# ============================================================
def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A real git repository carrying `.pre-commit-config.yaml` and no hook.

    A REAL repository, not a directory with a `.git/hooks/` in it, because the
    thing under test asks git where hooks live. A fake would answer through the
    fallback branch and never exercise what runs on an operator's machine.
    """
    repo = tmp_path / "clone"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    return repo


def _arm(repo: Path, hooks_dir: Path | None = None) -> Path:
    """Write an executable framework-generated pre-commit hook. Returns its path."""
    target = (hooks_dir or repo / ".git" / "hooks") / "pre-commit"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(FRAMEWORK_HOOK, encoding="utf-8")
    target.chmod(0o755)
    return target


def _check(repo: Path, monkeypatch) -> int:
    """Run the `--check` path in-process against `repo`; return its exit code."""
    monkeypatch.setattr(ih, "get_workspace_root", lambda: repo)
    monkeypatch.setattr(sys, "argv", ["install-hooks.py", "--check"])
    with pytest.raises(SystemExit) as exit_info:
        ih.main()
    return exit_info.value.code


# ============================================================
# The defect: config present, nothing installed
# ============================================================
def test_an_unarmed_clone_is_refused_and_the_missing_file_is_named(clone, monkeypatch, capsys):
    """The whole finding. Before the fix this returned 0 with a green line.

    The assertion on the exit code alone would pass against a check that refuses
    everything, so the message is asserted too: it has to name the path that is
    absent and the command that creates it, or the operator learns only that
    something is wrong.
    """
    assert _check(clone, monkeypatch) == 1

    out = capsys.readouterr().out
    assert "NOT ARMED" in out
    assert str(clone / ".git" / "hooks" / "pre-commit") in out
    assert "pre-commit install" in out


def test_the_end_to_end_command_the_two_documents_name_exits_non_zero(clone):
    """Same case through the real command line, because that is what is documented.

    `.claude/rules/security.md` and `docs/DEPLOYMENT.md` tell the operator to run
    a shell command and read its verdict. An in-process `main()` call proves the
    logic; only a subprocess proves the exit code a shell or a CI step sees.
    """
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "install-hooks.py"), "--check"],
        env={**os.environ, "WORKSPACE_ROOT": str(clone)},
        capture_output=True, text=True, check=False, timeout=120)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "NOT ARMED" in proc.stdout


# ============================================================
# The control: the refusal is not unconditional
# ============================================================
def test_an_armed_clone_passes(clone, monkeypatch, capsys):
    """Without this, a check hard-coded to `return 1` would pass every test above."""
    _arm(clone)
    assert _check(clone, monkeypatch) == 0
    assert "ARMED" in capsys.readouterr().out


def test_the_passing_line_states_what_it_did_not_check(clone, monkeypatch, capsys):
    """`.claude/rules/scope-claims.md`: a tool says only what its method established.

    Reading the hook file cannot tell you whether that hook still matches the
    config it was generated from, nor whether its hooks pass. A green line that
    leaves both unsaid is read as full coverage of the commit gate.
    """
    _arm(clone)
    _check(clone, monkeypatch)
    out = capsys.readouterr().out
    assert "NOT checked" in out
    assert ".pre-commit-config.yaml" in out


# ============================================================
# The three other ways a clone is unarmed while the config sits there
# ============================================================
def test_a_hook_the_framework_did_not_generate_is_not_the_gate(clone, monkeypatch, capsys):
    """A leftover hook from git-lfs or a previous tool is a file, not a commit gate."""
    hook = clone / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hook.chmod(0o755)

    assert _check(clone, monkeypatch) == 1
    assert igh.PRE_COMMIT_FRAMEWORK_MARKER in capsys.readouterr().out


def test_a_hook_that_is_not_executable_is_not_armed(clone, monkeypatch, capsys):
    """MEASURED 2026-09-02 on git 2.43.0, with a `chmod -x` pre-commit hook:

        $ git -C <repo> hook run pre-commit
        hint: The '.git/hooks/pre-commit' hook was ignored because it's not set
              as executable.
        error: cannot find a hook named pre-commit

    So the file being present says nothing on its own. This is the case the task
    asked to be judged rather than assumed: it IS detectable, `os.access(...,
    os.X_OK)` answers it, and a listing of `.git/hooks/` does not.
    """
    hook = _arm(clone)
    hook.chmod(0o644)

    assert _check(clone, monkeypatch) == 1
    assert "not executable" in capsys.readouterr().out


def test_a_hooks_path_redirect_to_an_empty_directory_is_detected(clone, monkeypatch, capsys):
    """`core.hooksPath` is one of the two gates `.claude/rules/security.md` bans
    setting by hand, because a literal value here once bypassed every hook in
    this workspace.

    The check does not refuse on the setting itself. It follows the redirect and
    asks the same three questions there, so this fails because the redirect
    target holds no hook, which is the honest reason. The setting is REPORTED
    because it changes the remedy: MEASURED 2026-09-02, `pre-commit install`
    exits 1 with "Cowardly refusing to install hooks with `core.hooksPath` set",
    so an operator told only to run that command would be sent in a circle.
    """
    elsewhere = clone / "elsewhere"
    elsewhere.mkdir()
    _git(clone, "config", "core.hooksPath", str(elsewhere))

    assert _check(clone, monkeypatch) == 1
    out = capsys.readouterr().out
    assert "core.hooksPath" in out
    assert str(elsewhere) in out


def test_a_hooks_path_redirect_that_really_is_armed_still_passes(clone, monkeypatch, capsys):
    """The negative control for the test above, and the reason it is honest.

    A redirect is not itself a broken gate. When the directory git points at
    holds a real framework hook, the clone IS armed and saying otherwise would be
    a false alarm on a supported git configuration. Without this test the
    redirect check could quietly become "refuse whenever core.hooksPath is set",
    which passes the previous test while measuring nothing.
    """
    elsewhere = clone / "elsewhere"
    elsewhere.mkdir()
    _git(clone, "config", "core.hooksPath", str(elsewhere))
    _arm(clone, hooks_dir=elsewhere)

    assert _check(clone, monkeypatch) == 0
    assert "core.hooksPath" in capsys.readouterr().out


# ============================================================
# The verifier is reused, not copied
# ============================================================
def test_the_check_reads_the_hook_through_the_sibling_verifier(clone, monkeypatch):
    """`scripts/install-git-hooks.py` owns the marker test and the hooks-dir
    lookup. A second copy in `install-hooks.py` is the copy that stops being
    fixed: `_hooks_dir` there is itself the fix for a hand-spelled path that was
    wrong in a linked worktree, and a hand-spelled path here would have missed
    it.

    Proved two ways, because either alone is weak. The marker literal must not
    be retyped in this file's source, and stubbing the sibling module's verdict
    must change this command's verdict.
    """
    source = (ROOT / "scripts" / "install-hooks.py").read_text(encoding="utf-8")
    assert igh.PRE_COMMIT_FRAMEWORK_MARKER not in source

    _arm(clone)
    calls = []

    class _Stub:
        PRE_COMMIT_FRAMEWORK_MARKER = igh.PRE_COMMIT_FRAMEWORK_MARKER

        @staticmethod
        def _hooks_dir(repo):
            return igh._hooks_dir(repo)

        @staticmethod
        def check_pre_commit(repo):
            calls.append(repo)
            return False

    monkeypatch.setattr(ih, "git_hooks_module", lambda: _Stub)
    assert _check(clone, monkeypatch) == 1
    assert calls, "framework_gate_state never asked the sibling verifier"


# ============================================================
# The arming path still arms
# ============================================================
def test_the_arming_path_still_writes_a_working_hook(tmp_path, monkeypatch, capsys):
    """A check that refuses is worthless if the fix broke the installer beside it.

    No `.pre-commit-config.yaml` here, which is the only state in which this
    legacy installer still installs anything. The hook must land, be executable,
    carry the scanner marker, and have the scanner reachable rather than sitting
    below an unconditional exit.
    """
    repo = tmp_path / "legacy"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)

    monkeypatch.setattr(ih, "get_workspace_root", lambda: repo)
    monkeypatch.setattr(sys, "argv", ["install-hooks.py"])
    ih.main()

    hook = repo / ".git" / "hooks" / "pre-commit"
    assert hook.is_file()
    assert os.access(hook, os.X_OK)
    body = hook.read_text(encoding="utf-8")
    assert ih.HOOK_MARKER in body
    assert ih.scanner_reachability(body)[0] is True
    assert "Done." in capsys.readouterr().out


def test_the_installer_still_refuses_beside_the_framework_config(clone, monkeypatch, capsys):
    """The May 2026 incident: two mechanisms fighting silently bypassed every hook.

    The refusal is what keeps this installer from clobbering the framework's own
    generated hook, and it sits in the same `main()` branch the check does.
    """
    monkeypatch.setattr(ih, "get_workspace_root", lambda: clone)
    monkeypatch.setattr(sys, "argv", ["install-hooks.py"])
    with pytest.raises(SystemExit) as exit_info:
        ih.main()
    assert exit_info.value.code == 1
    assert "Refusing to install" in capsys.readouterr().out


# ============================================================
# The hooks directory is asked of git, not spelled by hand
# ============================================================
def test_a_linked_worktree_is_not_reported_as_not_a_git_repository(clone, tmp_path, monkeypatch, capsys):
    """In a linked worktree `.git` is a FILE holding `gitdir: ...`, so the old
    hand-spelled `<repo>/.git/hooks` named a path underneath a file.

    `install-git-hooks._hooks_dir` was written for exactly this after it was
    measured on a worktree of this repository; `install-hooks.py` still spelled
    the path by hand and exited 1 with "Is this a git repository?" in a
    perfectly good worktree. The shared hook of a worktree is the main clone's,
    so arming the clone arms the worktree, and the check must say so.
    """
    # Committed BEFORE the hook is armed, so nothing this test writes ever runs
    # as a hook. `git worktree add` needs a HEAD, and the worktree needs the
    # config in its own checkout for the framework branch to be the one taken.
    (clone / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(clone, "config", "user.email", "builder@example.invalid")
    _git(clone, "config", "user.name", "Builder")
    _git(clone, "add", "seed.txt", ".pre-commit-config.yaml")
    _git(clone, "commit", "-q", "-m", "seed")
    _arm(clone)

    linked = tmp_path / "linked"
    _git(clone, "worktree", "add", "-q", str(linked))
    assert (linked / ".git").is_file(), "expected a worktree gitdir FILE, not a directory"

    assert _check(linked, monkeypatch) == 0
    assert "Is this a git repository" not in capsys.readouterr().out
