"""Regression: create-data-repo.py must make its first commit even on a machine with
no global/system git identity. git_init_commit seeds a local committer identity from
the workspace git config (with a safe fallback) before committing.

Before the fix, provisioning aborted after scaffolding with
'fatal: empty ident name ... not allowed' (exit 128) when no git identity existed.
"""
import importlib.util
import os
import subprocess
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent


def _load_cdr():
    spec = importlib.util.spec_from_file_location(
        "create_data_repo", ENGINE / "scripts" / "create-data-repo.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_git_init_commit_without_global_identity(tmp_path, monkeypatch):
    # Simulate a machine with no global and no system git identity.
    empty_global = tmp_path / "empty-gitconfig"
    empty_global.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_global))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)

    target = tmp_path / "repo"
    target.mkdir()
    (target / "README.md").write_text("scaffold\n", encoding="utf-8")

    cdr = _load_cdr()
    rc = cdr.git_init_commit(target, dry_run=False)
    assert rc == 0, "git_init_commit must succeed with no global git identity"

    # A commit exists, with a non-empty author identity.
    out = subprocess.run(
        ["git", "-C", str(target), "log", "-1", "--pretty=%an|%H"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    author, _, sha = out.partition("|")
    assert sha.strip(), "a first commit must exist"
    assert author.strip(), "commit author identity must be non-empty"


def test_the_hardcoded_fallback_identity_is_reachable(tmp_path, monkeypatch):
    """The test above passes without ever running the fallback.

    `git_init_commit` seeds the identity from `get_workspace_root()`, and that
    is the engine clone, whose LOCAL `.git/config` names a committer. Emptying
    `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` does not touch a repository-local
    config, so `git config user.name` there still answers. MEASURED 2026-09-01
    with both env vars pointed at an empty file: `Builder`. So the `or "HEADING
    OS"` arm has never executed under test, and changing it to `or ""`, the
    exact regression this file exists to prevent, since an empty ident is what
    git refuses, left the whole repository green.

    Pointing the source root at a directory that is not a git repository is what
    actually produces the no-identity-anywhere machine the module docstring
    describes.
    """
    empty_global = tmp_path / "empty-gitconfig"
    empty_global.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_global))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)

    cdr = _load_cdr()

    # Not a repo, so there is no local config either: every layer is now silent.
    bare = tmp_path / "not-a-repo"
    bare.mkdir()
    monkeypatch.setattr(cdr, "get_workspace_root", lambda: bare)
    probe = subprocess.run(["git", "config", "user.name"], cwd=str(bare),
                           capture_output=True, text=True)
    assert not probe.stdout.strip(), (
        "the fixture did not actually silence git identity; this test would "
        "then prove nothing, exactly like the one above it")

    target = tmp_path / "repo"
    target.mkdir()
    (target / "README.md").write_text("scaffold\n", encoding="utf-8")
    assert cdr.git_init_commit(target, dry_run=False) == 0

    author, email = subprocess.run(
        ["git", "-C", str(target), "log", "-1", "--pretty=%an%n%ae"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert author == "HEADING OS"
    assert email == "noreply@users.noreply.github.com"
