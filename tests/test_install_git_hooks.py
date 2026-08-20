"""install-git-hooks.py installs and verifies the pre-push gate."""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("install_git_hooks", ROOT / "scripts" / "install-git-hooks.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_install_writes_pre_push(tmp_path):
    # a throwaway git repo
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    src = ROOT / ".githooks" / "pre-push"
    mod.install_pre_push(tmp_path, src)
    hook = tmp_path / ".git" / "hooks" / "pre-push"
    assert hook.is_file()
    assert hook.stat().st_mode & 0o111  # executable
    assert "run-tests.py" in hook.read_text(encoding="utf-8")


def test_check_detects_missing(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert mod.check_pre_push(tmp_path) is False
    mod.install_pre_push(tmp_path, ROOT / ".githooks" / "pre-push")
    assert mod.check_pre_push(tmp_path) is True


def test_engine_hook_still_delegates_to_git_lfs():
    """The engine tracks LFS objects, and this hook occupies git-lfs's slot.

    The mirror of tests/test_data_repo_test_gate.py::
    test_shipped_data_hook_still_delegates_to_git_lfs. The data overlay got that
    guard on 2026-08-20 while the engine hook, which had dropped the same
    delegation on 2026-06-29, got none - so a newly added .png or .pdf would push
    as a pointer with no object behind it and only fail on someone else's clone.
    """
    assert "git lfs pre-push" in (ROOT / ".githooks" / "pre-push").read_text(
        encoding="utf-8")


def test_engine_hook_still_runs_the_suite_before_handing_off():
    """The hand-off must come AFTER the gate, never instead of it.

    `exec`ing git-lfs on line one would satisfy the test above and skip every
    test in the repository, so pin the order rather than the presence.

    Comment lines are stripped first. Both strings are discussed in the header,
    and an assertion that reads prose is an assertion about prose.
    """
    lines = [
        line for line in
        (ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    body = "\n".join(lines)
    assert body.index("run-tests.py") < body.index("git lfs pre-push")
