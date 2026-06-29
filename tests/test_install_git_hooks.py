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
