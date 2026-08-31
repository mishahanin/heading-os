"""The benchmark delta must be primary minus baseline, whatever the names sort to.

Found by the 2026-08-23 audit. `aggregate_results` took the two configurations
in `sorted()` discovery order and computed `configs[0] - configs[1]`. That is
right for the new-skill layout and inverted for the one
`.claude/skills/skill-creator/references/running-evals.md` prescribes when
IMPROVING a skill: the baseline saves to `old_skill/`, and "old_skill" sorts
before "with_skill".

A skill that genuinely improved therefore reported a NEGATIVE pass-rate delta,
against the single keep-or-discard decision the whole benchmark exists to
support. `eval-viewer/viewer.html` already carried the correct rule:

    const isBaseline = config === "without_skill" || config === "old_skill";
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGG = ROOT / ".claude" / "skills" / "skill-creator" / "scripts" / "aggregate_benchmark.py"

# Loaded by path: the repo root owns a `scripts` package of its own.
_spec = importlib.util.spec_from_file_location("_agg_under_test", AGG)
agg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agg)


def _runs(pass_rate: float) -> list[dict]:
    return [{"pass_rate": pass_rate, "time_seconds": 10.0, "tokens": 1000}]


def _delta(results: dict) -> float:
    return float(agg.aggregate_results(results)["delta"]["pass_rate"])


def test_the_improve_layout_reports_a_positive_delta_for_an_improvement():
    """`old_skill` sorts first. Before the fix this returned -0.50."""
    results = {
        "old_skill": _runs(0.40),
        "with_skill": _runs(0.90),
    }
    assert _delta(results) == 0.50


def test_the_improve_layout_reports_a_negative_delta_for_a_regression():
    """The other direction: a real regression must not read as a win."""
    results = {
        "old_skill": _runs(0.90),
        "with_skill": _runs(0.40),
    }
    assert _delta(results) == -0.50


def test_insertion_order_does_not_change_the_sign():
    forward = _delta({"old_skill": _runs(0.40), "with_skill": _runs(0.90)})
    reverse = _delta({"with_skill": _runs(0.90), "old_skill": _runs(0.40)})
    assert forward == reverse == 0.50


def test_the_new_skill_layout_still_works():
    """The case that was already right must stay right."""
    assert _delta({"with_skill": _runs(0.80), "without_skill": _runs(0.20)}) == 0.60
    assert _delta({"new_skill": _runs(0.80), "old_skill": _runs(0.20)}) == 0.60


def test_unnamed_configs_fall_back_to_discovery_order():
    """An unrecognised layout keeps its old behaviour instead of reporting 0."""
    assert agg.split_configs(["cfg_a", "cfg_b"]) == ("cfg_a", "cfg_b")
    assert _delta({"cfg_a": _runs(0.70), "cfg_b": _runs(0.20)}) == 0.50


def test_one_known_name_pairs_with_whatever_else_is_there():
    assert agg.split_configs(["mystery", "old_skill"]) == ("mystery", "old_skill")
    assert agg.split_configs(["with_skill", "mystery"]) == ("with_skill", "mystery")


def test_a_single_config_reports_no_delta_at_all():
    """CHANGED 2026-08-31, and the change is the point.

    This test used to read::

        assert _delta({"with_skill": _runs(0.65)}) == 0.65

    which is the F10 defect written down as an expectation. With one
    configuration there IS no baseline, and 0.65 is `0.65 - 0`: it asserts that
    the primary beat a baseline scoring zero, when no baseline ran at all. The
    old assertion carried no docstring and no rationale - it characterised the
    arithmetic rather than defending an invariant.

    A delta over a missing operand is not a small delta. It is not a delta.
    Full reasoning: `tests/test_skill_creator_eval_scratch_and_absence.py`.
    """
    delta = agg.aggregate_results({"with_skill": _runs(0.65)})["delta"]
    assert delta["pass_rate"] == agg.NOT_MEASURED
    assert delta["unmeasured"] == ["baseline"]


def test_the_markdown_table_puts_the_primary_in_the_first_column():
    """Otherwise the table reads baseline-first beside a primary-first delta."""
    benchmark = {
        "metadata": {
            "skill_name": "demo",
            "executor_model": "test",
            "timestamp": "2026-08-23T00:00:00",
            "evals_run": [0],
            "runs_per_configuration": 1,
        },
        "run_summary": agg.aggregate_results(
            {"old_skill": _runs(0.40), "with_skill": _runs(0.90)}
        ),
    }
    md = agg.generate_markdown(benchmark)
    header = next(line for line in md.splitlines() if line.startswith("| Metric"))
    assert header.index("With Skill") < header.index("Old Skill"), header
    assert "+0.50" in md
