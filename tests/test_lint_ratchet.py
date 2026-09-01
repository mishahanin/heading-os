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


# ============================================================
# The ratchet itself
# ============================================================
#
# Every case above is about the "ruff never ran" discriminator. MEASURED
# 2026-09-01, `cmd_check`'s `return 1` on a regression could be changed to
# `return 0` with all six of them green. The gate's ACTUAL job, refusing a
# merge that adds lint debt, had no witness at all.


def _ruff_findings(monkeypatch, findings):
    """Canned ruff JSON. `findings` is a list of (relpath, code) pairs."""
    payload = json.dumps([{"filename": f, "code": c} for f, c in findings])
    _ruff_answer(monkeypatch, returncode=1 if findings else 0, stdout=payload)


def test_check_refuses_a_new_finding(monkeypatch, tmp_path):
    """A `(file, rule)` bucket that is not in the baseline blocks the merge."""
    baseline = tmp_path / ".lint-baseline.json"
    baseline.write_text(json.dumps({"scripts/old.py::S310": 1}), encoding="utf-8")
    monkeypatch.setattr(lint_ratchet, "BASELINE", baseline)
    _ruff_findings(monkeypatch, [("scripts/old.py", "S310"),
                                 ("scripts/new.py", "S603")])

    assert lint_ratchet.cmd_check() == 1, (
        "a brand new lint finding did not block the merge")


def test_check_refuses_a_bucket_that_grew(monkeypatch, tmp_path):
    """The count half. A known `(file, rule)` going 1 -> 2 is new debt too."""
    baseline = tmp_path / ".lint-baseline.json"
    baseline.write_text(json.dumps({"scripts/old.py::S310": 1}), encoding="utf-8")
    monkeypatch.setattr(lint_ratchet, "BASELINE", baseline)
    _ruff_findings(monkeypatch, [("scripts/old.py", "S310"),
                                 ("scripts/old.py", "S310")])

    assert lint_ratchet.cmd_check() == 1, (
        "an existing bucket that doubled did not block the merge")


def test_check_accepts_a_tree_at_or_below_the_baseline(monkeypatch, tmp_path):
    """The anchor. A ratchet that refuses everything is a ratchet that gets
    removed, so the pass path is measured beside the two refusals."""
    baseline = tmp_path / ".lint-baseline.json"
    baseline.write_text(
        json.dumps({"scripts/old.py::S310": 2, "scripts/gone.py::B008": 1}),
        encoding="utf-8")
    monkeypatch.setattr(lint_ratchet, "BASELINE", baseline)
    _ruff_findings(monkeypatch, [("scripts/old.py", "S310")])

    assert lint_ratchet.cmd_check() == 0


def test_a_corpus_that_vanished_is_not_a_clean_tree(monkeypatch, tmp_path):
    """ruff RAN, exited 0, and inspected nothing.

    The discriminator above reads the exit status, so it cannot see this third
    answer. MEASURED 2026-09-01 against the committed 143-bucket baseline with
    ruff stubbed to exit 0 and print `[]`: `check` printed "OK - 0 findings, at
    or below baseline (245 fewer...)" and returned 0. An `exclude` widened by
    one line in pyproject.toml disarms the whole gate and reports success.
    """
    baseline = tmp_path / ".lint-baseline.json"
    baseline.write_text(json.dumps({"scripts/a.py::S310": 3,
                                    "scripts/b.py::B008": 1}), encoding="utf-8")
    monkeypatch.setattr(lint_ratchet, "BASELINE", baseline)
    _ruff_answer(monkeypatch, returncode=0, stdout="[]")

    with pytest.raises(SystemExit) as exc:
        lint_ratchet.cmd_check()
    assert "vanished" in str(exc.value)


def test_update_cannot_erase_the_baseline_over_a_vanished_corpus(monkeypatch, tmp_path):
    """The destructive half of the same door. `update` would write `{}`."""
    baseline = tmp_path / ".lint-baseline.json"
    original = {"scripts/a.py::S310": 3}
    baseline.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(lint_ratchet, "BASELINE", baseline)
    _ruff_answer(monkeypatch, returncode=0, stdout="[]")

    with pytest.raises(SystemExit):
        lint_ratchet.cmd_update()
    assert json.loads(baseline.read_text()) == original


def test_a_first_run_with_no_baseline_can_still_record_an_empty_tree(monkeypatch, tmp_path):
    """The refusal must not brick a fresh clone that genuinely has no findings,
    and must not brick the deliberate `rm .lint-baseline.json` escape it names."""
    baseline = tmp_path / ".lint-baseline.json"
    monkeypatch.setattr(lint_ratchet, "BASELINE", baseline)
    _ruff_answer(monkeypatch, returncode=0, stdout="[]")

    assert lint_ratchet.cmd_update() == 0
    assert json.loads(baseline.read_text()) == {}
    assert lint_ratchet.cmd_check() == 0


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
