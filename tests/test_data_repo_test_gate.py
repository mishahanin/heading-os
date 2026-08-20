"""The data overlay's own tests must gate its push, the way the engine's do.

Measured on 2026-08-20: `.heading-os-data/tests/` sat in no gate at all. The
engine pre-push hook runs the ENGINE suite, and the data overlay's push went out
with `test_gate` unset, so twenty-four admin tests -- the cover on exec
provisioning -- ran only when somebody remembered to type the command.

Two things make this awkward, and both are pinned below. The data repo's
`pre-push` slot is already occupied by git-lfs, and that repo really does track
LFS objects, so a gate that overwrites the hook breaks pushes instead of guarding
them. And an executive's data overlay carries no `tests/` directory at all, so the
gate has to pass on absence rather than fail closed.
"""
import importlib.util
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("push_all", ROOT / "scripts" / "push-all.py")
push_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_all)

_ih_spec = importlib.util.spec_from_file_location(
    "install_git_hooks", ROOT / "scripts" / "install-git-hooks.py")
install_git_hooks = importlib.util.module_from_spec(_ih_spec)
_ih_spec.loader.exec_module(install_git_hooks)

SHIPPED_HOOK = ROOT / ".githooks" / "pre-push-data"


def _init_repo(tmp_path, name="data") -> Path:
    repo = tmp_path / name
    (repo / ".git" / "hooks").mkdir(parents=True)
    return repo


# --- the shipped hook -------------------------------------------------------

def test_shipped_data_hook_exists():
    assert SHIPPED_HOOK.is_file()


def test_shipped_data_hook_still_delegates_to_git_lfs():
    """The data repo tracks LFS objects. A gate that drops this breaks pushes."""
    assert "git lfs pre-push" in SHIPPED_HOOK.read_text(encoding="utf-8")


def test_shipped_data_hook_carries_the_gate_marker():
    assert push_all.DATA_GATE_MARKER in SHIPPED_HOOK.read_text(encoding="utf-8")


def test_shipped_data_hook_passes_when_the_repo_has_no_tests(tmp_path):
    """An exec's data overlay has no tests/. The gate must pass, not fail closed."""
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)

    result = subprocess.run(["bash", str(SHIPPED_HOOK)], cwd=str(repo),
                            capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


# --- the installer ----------------------------------------------------------

def test_check_data_hook_is_false_before_install(tmp_path):
    assert install_git_hooks.check_pre_push_data(_init_repo(tmp_path)) is False


def test_install_data_hook_makes_the_check_pass(tmp_path):
    repo = _init_repo(tmp_path)

    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK)

    assert install_git_hooks.check_pre_push_data(repo) is True


def test_installed_data_hook_is_executable(tmp_path):
    repo = _init_repo(tmp_path)

    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK)

    mode = (repo / ".git" / "hooks" / "pre-push").stat().st_mode
    assert mode & stat.S_IXUSR


def test_installing_the_data_hook_does_not_lose_lfs(tmp_path):
    """Installing over the stock git-lfs hook must keep LFS delegation."""
    repo = _init_repo(tmp_path)
    (repo / ".git" / "hooks" / "pre-push").write_text(
        '#!/bin/sh\ngit lfs pre-push "$@"\n', encoding="utf-8")

    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK)

    assert "git lfs pre-push" in (repo / ".git" / "hooks" / "pre-push").read_text(encoding="utf-8")


# --- push-all's refusal predicate ------------------------------------------

def test_gate_predicate_rejects_the_stock_lfs_hook(tmp_path):
    """The stock LFS hook is not a test gate, and must not read as one."""
    repo = _init_repo(tmp_path)
    (repo / ".git" / "hooks" / "pre-push").write_text(
        '#!/bin/sh\ngit lfs pre-push "$@"\n', encoding="utf-8")

    assert push_all._pre_push_gate_armed(
        repo, marker=push_all.DATA_GATE_MARKER) is False


def test_gate_predicate_accepts_the_installed_data_hook(tmp_path):
    repo = _init_repo(tmp_path)
    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK)

    assert push_all._pre_push_gate_armed(
        repo, marker=push_all.DATA_GATE_MARKER) is True


def test_engine_marker_remains_the_default(tmp_path):
    """The engine gate keeps working unchanged when no marker is passed."""
    repo = _init_repo(tmp_path)
    (repo / ".git" / "hooks" / "pre-push").write_text(
        "#!/bin/sh\nexec python scripts/run-tests.py\n", encoding="utf-8")

    assert push_all._pre_push_gate_armed(repo) is True


# --- push_repo refuses an ungated data overlay -----------------------------

def _bare_repo_with_remote(tmp_path):
    """A real repo on a real (local) remote, so push_repo gets past git plumbing."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    repo = tmp_path / "data"
    repo.mkdir()
    for cmd in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"],
                ["config", "user.name", "t"], ["remote", "add", "origin", str(remote)]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True)
    (repo / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    return repo


def test_push_repo_refuses_a_data_overlay_with_no_gate(tmp_path):
    repo = _bare_repo_with_remote(tmp_path)
    (repo / ".git" / "hooks").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "hooks" / "pre-push").write_text(
        '#!/bin/sh\ngit lfs pre-push "$@"\n', encoding="utf-8")

    with pytest.raises(push_all.RepoNotPushable) as exc:
        push_all.push_repo("DATA", repo, "m", False, True, {},
                           test_gate=True, gate_marker=push_all.DATA_GATE_MARKER)

    assert "install-git-hooks.py" in str(exc.value)


def test_push_repo_allows_a_data_overlay_once_gated(tmp_path):
    repo = _bare_repo_with_remote(tmp_path)
    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK)

    push_all.push_repo("DATA", repo, "m", False, True, {},
                       test_gate=True, gate_marker=push_all.DATA_GATE_MARKER)


# --- which repo the installer should gate ----------------------------------

def _make_git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    return path


def test_installer_gates_a_separate_data_repo(tmp_path):
    engine = _make_git_repo(tmp_path / ".heading-os")
    data = _make_git_repo(tmp_path / ".heading-os-data")

    assert install_git_hooks.data_repo_to_gate(data, engine) == data


def test_installer_skips_when_data_root_is_the_engine(tmp_path):
    """Pre-cutover single-repo mode: one repo, already gated as the engine."""
    engine = _make_git_repo(tmp_path / ".heading-os")

    assert install_git_hooks.data_repo_to_gate(engine, engine) is None


def test_installer_skips_a_data_root_that_is_not_a_git_repo(tmp_path):
    """Demo mode resolves the data root to the bundled examples/ tree."""
    engine = _make_git_repo(tmp_path / ".heading-os")
    examples = tmp_path / ".heading-os" / "examples"
    examples.mkdir(parents=True)

    assert install_git_hooks.data_repo_to_gate(examples, engine) is None


def test_installer_skips_a_missing_data_root(tmp_path):
    engine = _make_git_repo(tmp_path / ".heading-os")

    assert install_git_hooks.data_repo_to_gate(tmp_path / "nope", engine) is None
