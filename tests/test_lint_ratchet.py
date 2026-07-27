"""The ratchet has to tell "ruff found nothing" apart from "ruff never ran".

Both answers reach `_current()` as an empty result, and for two days they were
treated as the same answer. Under an interpreter with no ruff installed --
which is exactly what pre-commit's `language: system` resolves `python3` to on
this machine -- `check` printed "OK - 0 findings" and exited 0 while CI failed
on four real entries, and `update` would have written an EMPTY baseline,
erasing every recorded finding and leaving the ratchet permanently vacuous.

A gate that answers "clean" when it did not run is worse than no gate: it
reports the reassuring half of the truth.
"""
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("lint_ratchet", ROOT / "scripts" / "lint-ratchet.py")
lint_ratchet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint_ratchet)


def _ruff_answer(monkeypatch, *, returncode: int, stdout: str, stderr: str = ""):
    """Replace the ruff invocation with one canned answer."""
    def _fake(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], returncode, stdout, stderr)

    monkeypatch.setattr(lint_ratchet.subprocess, "run", _fake)


def test_a_ruff_that_never_ran_is_not_reported_as_a_clean_tree(monkeypatch):
    """`python -m ruff` with no ruff installed exits 1 with nothing on stdout."""
    _ruff_answer(monkeypatch, returncode=1, stdout="",
                 stderr="/usr/bin/python3: No module named ruff\n")

    with pytest.raises(SystemExit) as exc:
        lint_ratchet._current()

    assert "did not run" in str(exc.value)


def test_a_genuinely_clean_tree_is_still_accepted(monkeypatch):
    """The discriminator must not turn a real zero into a failure."""
    _ruff_answer(monkeypatch, returncode=0, stdout="[]")

    assert lint_ratchet._current() == Counter()


def test_update_cannot_erase_the_baseline_when_ruff_is_missing(monkeypatch, tmp_path):
    """The destructive half: `update` writes whatever `_current()` returns.

    An empty answer accepted as truth rewrites .lint-baseline.json to `{}`,
    which silently forgives every existing finding and disarms the ratchet for
    good. The refusal has to land before the write.
    """
    baseline = tmp_path / ".lint-baseline.json"
    baseline.write_text(json.dumps({"scripts/example.py::S310": 1}) + "\n", encoding="utf-8")
    monkeypatch.setattr(lint_ratchet, "BASELINE", baseline)
    _ruff_answer(monkeypatch, returncode=1, stdout="",
                 stderr="No module named ruff\n")

    with pytest.raises(SystemExit):
        lint_ratchet.cmd_update()

    assert json.loads(baseline.read_text()) == {"scripts/example.py::S310": 1}


def test_the_interpreter_prefers_the_repos_own_venv(monkeypatch, tmp_path):
    """pre-commit runs `python3` from PATH, not the venv the repo documents.

    Reading `sys.executable` alone is what put the gate under an interpreter
    that had never heard of ruff.
    """
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    monkeypatch.setattr(lint_ratchet, "get_workspace_root", lambda: tmp_path)

    assert lint_ratchet._interpreter() == str(venv_python)


def test_the_interpreter_falls_back_to_the_running_one(monkeypatch, tmp_path):
    monkeypatch.setattr(lint_ratchet, "get_workspace_root", lambda: tmp_path)

    assert lint_ratchet._interpreter() == sys.executable


def test_check_refuses_rather_than_passing_when_ruff_never_ran(monkeypatch):
    """The exit code the pre-commit hook and CI both consume.

    `_current()` raising is only useful if `check` propagates it instead of
    catching it into a pass. This is the assertion that maps to the two days of
    green commits over a red merge gate.
    """
    _ruff_answer(monkeypatch, returncode=1, stdout="", stderr="No module named ruff\n")

    with pytest.raises(SystemExit) as exc:
        lint_ratchet.cmd_check()

    assert exc.value.code != 0
