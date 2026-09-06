#!/usr/bin/env python3
"""The confidence cut was a constant, and its input doubled underneath it.

MEASURED 2026-09-06: the corpus went from 2,175 indexed files to 5,180 in thirty
days, and `threshold: 0.55` had not moved since it was calibrated on the smaller
one. Operator instruction the same day: recalibration must be regular and
automatic rather than an event somebody remembers.

Three things have to hold for that to be safe, and this file asserts each one.

**Where the calibrated value lives.** In the DATA overlay, never in the engine.
A weekly job writing into a TRACKED config dirties the working tree, trips the
push gates, and puts a machine-derived number into a public repository. A
GITIGNORED engine file would be worse still: invisible in every worktree, which
is precisely the defect fixed the same day for the embedder pin. The threshold
is a function of the corpus and the corpus is the overlay.

**What the job may change by itself.** Only a STRICT PARETO improvement inside a
bounded envelope. A rule that merely maximises the benefit column always says
"lower the cut", which is how a threshold ends up admitting confident answers to
questions the corpus cannot answer. Outside the envelope the job changes
nothing and leaves a named proposal.

**That the seam fails quiet.** An absent, unreadable, unparseable or out-of-range
calibration returns None and recall falls back to the value the repository ships.
A corrupt file must never be able to make everything confident, or nothing.

Run: .venv/bin/python -m pytest \\
     tests/test_a_threshold_calibrated_once_against_a_corpus_that_doubled.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

INDEX = ROOT / "scripts" / "memory-index.py"
CALIB = ROOT / "scripts" / "recall-calibration.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def index():
    return _load(INDEX, "memory_index_calibration")


@pytest.fixture(scope="module")
def calib():
    return _load(CALIB, "recall_calibration_under_test")


def _write_calibration(tmp_path: Path, payload) -> Path:
    """A fake datastore holding one calibration record."""
    d = tmp_path / "datastore" / "operations" / "memory-calibration"
    d.mkdir(parents=True)
    target = d / "current.json"
    target.write_text(payload if isinstance(payload, str)
                      else json.dumps(payload), encoding="utf-8")
    return tmp_path / "datastore"


# ============================================================
# The seam: where the value comes from, and how it fails
# ============================================================

def test_a_recorded_calibration_is_read(index, tmp_path, monkeypatch):
    store = _write_calibration(tmp_path, {"threshold": 0.47})
    monkeypatch.setattr(index, "get_datastore_dir", lambda: store)
    assert index.calibrated_threshold() == 0.47


def test_no_calibration_means_the_shipped_value(index, tmp_path, monkeypatch):
    monkeypatch.setattr(index, "get_datastore_dir", lambda: tmp_path / "nothing")
    assert index.calibrated_threshold() is None


@pytest.mark.parametrize("payload", [
    "{not json at all",
    {"threshold": "not a number"},
    {},
    {"threshold": None},
])
def test_an_unusable_calibration_is_ignored(index, tmp_path, monkeypatch, payload):
    """Quiet fallback, never a crash and never a guess."""
    store = _write_calibration(tmp_path, payload)
    monkeypatch.setattr(index, "get_datastore_dir", lambda: store)
    assert index.calibrated_threshold() is None


@pytest.mark.parametrize("value", [0.0, 0.19, 0.91, 1.0, 42.0, -0.5])
def test_a_calibration_outside_the_sane_range_is_refused(index, tmp_path,
                                                          monkeypatch, value):
    """A cut at either end turns recall into all-confident or all-gap."""
    store = _write_calibration(tmp_path, {"threshold": value})
    monkeypatch.setattr(index, "get_datastore_dir", lambda: store)
    assert index.calibrated_threshold() is None, (
        f"{value} was accepted as a confidence threshold")


# ============================================================
# Resolution order
# ============================================================

def _cfg(calibrated=None):
    return {
        "threshold": 0.55,
        "calibrated_threshold": calibrated,
        "layers": [{"layer": "commit-engine", "threshold": 0.45},
                   {"layer": "odin"}],
    }


def test_a_calibration_replaces_the_shipped_global_cut(index):
    assert index.resolve_threshold(_cfg(0.50), None, None) == 0.50


def test_without_a_calibration_the_shipped_cut_stands(index):
    assert index.resolve_threshold(_cfg(None), None, None) == 0.55


def test_a_layers_own_measured_cut_still_wins(index):
    """`commit-engine` was measured at 0.45 on a different register.

    A corpus-wide calibration of the PROSE cut says nothing about it, so it must
    not be overwritten.
    """
    assert index.resolve_threshold(_cfg(0.50), None, {"commit-engine"}) == 0.45


def test_an_explicit_flag_still_beats_everything(index):
    assert index.resolve_threshold(_cfg(0.50), 0.9, {"commit-engine"}) == 0.9


# ============================================================
# The envelope: what the job may do without being asked
# ============================================================

def _row(threshold, *, correct, false_conf=0, lexical=0, errors=()):
    return {"threshold": threshold, "questions": 20,
            "confident_correct": correct, "ranked_correct": correct,
            "false_confident": false_conf, "lexical_confident": lexical,
            "errors": list(errors)}


def test_a_strict_improvement_inside_the_envelope_is_adopted(calib):
    rows = [_row(0.50, correct=15), _row(0.55, correct=11)]
    chosen, why = calib.decide(rows, 0.55)
    assert chosen == 0.50, why


def test_more_correct_answers_at_a_higher_cost_is_refused(calib):
    """The trade this measurement actually found, and a job must not take it."""
    rows = [_row(0.50, correct=15, false_conf=3), _row(0.55, correct=11, false_conf=1)]
    chosen, why = calib.decide(rows, 0.55)
    assert chosen is None, (
        f"the job adopted a cut that answers more questions AND invents more "
        f"confident answers to unanswerable ones: {why}")


def test_extra_lexical_false_confidence_is_refused_too(calib):
    rows = [_row(0.50, correct=15, lexical=2), _row(0.55, correct=11, lexical=0)]
    assert calib.decide(rows, 0.55)[0] is None


def test_a_better_cut_outside_the_envelope_is_refused(calib):
    """0.10 either way. Further than that is a redesign, not a calibration."""
    rows = [_row(0.30, correct=19), _row(0.55, correct=11)]
    chosen, why = calib.decide(rows, 0.55)
    assert chosen is None, why


def test_an_incomplete_grid_changes_nothing(calib):
    """One errored query and the whole grid is unusable."""
    rows = [_row(0.50, correct=15, errors=("q07: embed_unavailable",)),
            _row(0.55, correct=11)]
    assert calib.decide(rows, 0.55)[0] is None


def test_a_grid_that_never_measured_the_incumbent_changes_nothing(calib):
    """Without the incumbent there is nothing to improve ON."""
    rows = [_row(0.45, correct=14), _row(0.50, correct=15)]
    assert calib.decide(rows, 0.55)[0] is None


def test_no_improvement_leaves_the_shipped_cut_alone(calib):
    rows = [_row(0.50, correct=9), _row(0.55, correct=11)]
    assert calib.decide(rows, 0.55)[0] is None


# ============================================================
# Rot: a gold set whose answers moved scores like a worse corpus
# ============================================================

def test_a_missing_gold_answer_is_named_not_scored(calib, tmp_path):
    gold = {"questions": [
        {"id": "q01", "answer_path": "context/present.md", "alternates": []},
        {"id": "q02", "answer_path": "context/gone.md", "alternates": []},
    ]}
    (tmp_path / "context").mkdir()
    (tmp_path / "context" / "present.md").write_text("x", encoding="utf-8")

    missing = calib.check_answer_paths(gold, tmp_path)
    assert missing == ["q02: context/gone.md"], (
        f"a moved answer file was not reported, so the next run would read set "
        f"rot as corpus decay: {missing}")


# ============================================================
# The unit that will run this, unattended
# ============================================================

def test_the_timer_survives_a_reboot():
    """All three mechanisms, per the development standards. A user timer without
    lingering is silent after an unattended reboot, which is the one that gets
    forgotten."""
    timer = (ROOT / "scripts/templates/systemd/recall-calibration.timer").read_text(
        encoding="utf-8")
    installer = (ROOT / "scripts/install-recall-calibration-timer.sh").read_text(
        encoding="utf-8")

    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer
    assert "systemctl --user enable" in installer
    assert "enable-linger" in installer


def test_the_installer_refuses_to_run_from_a_yard():
    """The unit substitutes the workspace path into `WorkingDirectory=`, so a
    yard install points a live timer at a checkout that is deleted tomorrow."""
    installer = (ROOT / "scripts/install-recall-calibration-timer.sh").read_text(
        encoding="utf-8")
    assert "require_main_clone" in installer


def test_the_unit_runs_the_command_that_exists():
    """A renamed sibling's ExecStart is the classic copy defect."""
    service = (ROOT / "scripts/templates/systemd/recall-calibration.service").read_text(
        encoding="utf-8")
    assert "scripts/recall-calibration.py check" in service
    assert (ROOT / "scripts" / "recall-calibration.py").is_file()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
