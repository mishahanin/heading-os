"""push-all.py enforces the single authoritative test gate AND the unbypassable
engine/data leak wall.

The regression suite is run by the engine's versioned pre-push hook (one gate,
on every push to engine). push-all no longer runs it a second time itself; it
refuses to push when that hook is not armed, so the gate can never be silently
skipped on an un-provisioned clone. These tests cover that enforcement predicate
plus engine_clean_scan() -- the pure-code routing wall that no `--no-verify` can
get past.
"""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from scripts.utils import venv as _venv

ROOT = Path(__file__).resolve().parent.parent

# push-all.py calls ensure_venv() at MODULE scope, and ensure_venv os.execv's the
# whole process when the running interpreter is not .venv/bin/python. Under
# pytest that replaces the collecting process while file descriptor 1 is
# pytest's capture file, so the relaunched run's entire output lands in a temp
# file nobody reads and `pytest tests/` prints ZERO bytes. Measured on this
# repository, 2026-07-26, exit 0 on a passing set and exit 1 on a failing one.
# Setting ensure_venv's own sentinel tells it the relaunch already happened.
os.environ.setdefault(_venv._SENTINEL, "1")

_spec = importlib.util.spec_from_file_location("push_all", ROOT / "scripts" / "push-all.py")
push_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_all)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo(tmp_path) -> Path:
    repo = tmp_path / "engine"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def _write(repo, rel, body="x"):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_engine_clean_scan_passes_on_clean_tree(tmp_path):
    repo = _init_repo(tmp_path)
    _write(repo, "scripts/foo.py", "print(1)\n")
    _git(repo, "add", "-A")
    # No exit -> returns None cleanly.
    assert push_all.engine_clean_scan(repo) is None


def test_engine_clean_scan_refuses_on_data_artifact(tmp_path, capsys):
    repo = _init_repo(tmp_path)
    _write(repo, "crm/contacts/john.md", "name: John\n")  # routes private
    _git(repo, "add", "-A")
    with pytest.raises(SystemExit) as exc:
        push_all.engine_clean_scan(repo)
    assert exc.value.code == 2
    assert "crm/contacts/john.md" in capsys.readouterr().out


def test_engine_clean_scan_refuses_on_untracked_data(tmp_path, capsys):
    # A private file not yet staged is still caught -- `git add -A` would sweep it in.
    repo = _init_repo(tmp_path)
    _write(repo, "outputs/operations/leak.md", "plan\n")
    with pytest.raises(SystemExit) as exc:
        push_all.engine_clean_scan(repo)
    assert exc.value.code == 2
    assert "outputs/operations/leak.md" in capsys.readouterr().out


def _make_hook(tmp_path, body: str):
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-push").write_text(body, encoding="utf-8")
    return tmp_path


def test_gate_armed_true_when_hook_runs_tests(tmp_path):
    repo = _make_hook(tmp_path, "#!/usr/bin/env bash\nexec python scripts/run-tests.py\n")
    assert push_all._pre_push_gate_armed(repo) is True


def test_gate_not_armed_when_hook_missing(tmp_path):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    assert push_all._pre_push_gate_armed(tmp_path) is False


def test_gate_not_armed_when_hook_does_not_run_tests(tmp_path):
    repo = _make_hook(tmp_path, "#!/usr/bin/env bash\necho noop\n")
    assert push_all._pre_push_gate_armed(repo) is False


def test_this_module_does_not_relaunch_the_interpreter():
    """The one-line guard above is load-bearing, so it gets a test.

    Without it, loading push-all.py re-execs the whole pytest process, which
    inherits pytest's capture as file descriptor 1 and prints nothing at all for
    the rest of the run. Measured on this repository, 2026-07-26.
    """
    assert os.environ.get(_venv._SENTINEL) == "1"
