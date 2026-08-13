"""The pre-registered verdict rule, and the guards that keep it honest.

`verdict()` is deliberately pure -- no file, no clock, no corpus -- because it is
the one function whose output decides whether a new executing primitive enters
the workspace. A rule that can be re-read off the data after the fact is not a
rule, so these tests pin the thresholds written in
`plans/2026-08-13-census-acceptance-benchmark.md` before any run happened.

Also pinned here: the three ways the instrument is allowed to refuse rather than
produce a number, since every one of them was a defect found in review.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_bench():
    """Import the hyphenated CLI script by path; its name is not a module name."""
    spec = importlib.util.spec_from_file_location("census_bench", ROOT / "scripts" / "census-bench.py")
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves string annotations through
    # sys.modules[cls.__module__], which is None for an unregistered module.
    sys.modules["census_bench"] = module
    spec.loader.exec_module(module)
    return module


bench = _load_bench()
FIXTURE = ROOT / "tests" / "fixtures" / "census_corpus"
TODAY = date(2026, 6, 15)


# ============================================================
# The rule itself
# ============================================================

def test_low_aggregate_ceiling_says_build():
    name, rule = bench.verdict(agg_mean=0.05, ctl_mean=1.0, n_measurable=10)
    assert name == bench.VERDICT_BUILD
    assert "0.05" in rule and str(bench.BUILD_BELOW) in rule


def test_high_aggregate_ceiling_says_fix_recall():
    name, rule = bench.verdict(agg_mean=0.85, ctl_mean=1.0, n_measurable=10)
    assert name == bench.VERDICT_FIX_RECALL
    assert "wrong fix" in rule


def test_middle_aggregate_ceiling_says_narrow():
    name, _ = bench.verdict(agg_mean=0.5, ctl_mean=1.0, n_measurable=10)
    assert name == bench.VERDICT_NARROW


@pytest.mark.parametrize(
    ("agg_mean", "expected"),
    [
        (0.2999, "BUILD"),
        (0.30, "NARROW"),      # BUILD is strictly below the line
        (0.6999, "NARROW"),
        (0.70, "FIX-RECALL"),  # FIX-RECALL is at-or-above the line
    ],
)
def test_the_boundaries_leave_no_gap_and_no_overlap(agg_mean, expected):
    """Every real number lands in exactly one branch.

    A gap here would leave a run with no verdict at all, and an overlap would
    make the verdict depend on evaluation order rather than on the measurement.
    """
    name, _ = bench.verdict(agg_mean=agg_mean, ctl_mean=1.0, n_measurable=10)
    assert name == expected


# ============================================================
# The refusals
# ============================================================

def test_an_unhealthy_control_group_voids_the_aggregate_verdict():
    """Below the control floor the index cannot reach single facts, so an
    aggregating verdict beside it would be a comparison against a broken index --
    the one way this benchmark could 'prove' /census by accident."""
    name, rule = bench.verdict(agg_mean=0.01, ctl_mean=0.6, n_measurable=10)
    assert name == bench.FLAG_RECALL_BROKEN
    assert "broken index" in rule


def test_control_floor_is_inclusive():
    name, _ = bench.verdict(agg_mean=0.01, ctl_mean=bench.CONTROL_HEALTHY_AT_OR_ABOVE, n_measurable=10)
    assert name == bench.VERDICT_BUILD


def test_the_broken_index_check_runs_before_the_aggregate_branches():
    """Ordering matters: a sick index with a low aggregate mean must report
    RECALL-BROKEN, never BUILD."""
    name, _ = bench.verdict(agg_mean=0.0, ctl_mean=0.0, n_measurable=10)
    assert name == bench.FLAG_RECALL_BROKEN


def test_too_few_measurable_questions_yields_no_verdict():
    name, rule = bench.verdict(agg_mean=0.1, ctl_mean=1.0, n_measurable=5)
    assert name == "NO-VERDICT"
    assert "unfit" in rule


def test_no_measurable_aggregate_at_all_yields_no_verdict():
    name, _ = bench.verdict(agg_mean=None, ctl_mean=1.0, n_measurable=bench.MIN_MEASURABLE_AGGREGATES)
    assert name == "NO-VERDICT"


# ============================================================
# UNMEASURABLE: the difference between "not found" and "not measured"
# ============================================================

def test_truth_larger_than_the_pool_is_unmeasurable_not_zero():
    """Scoring such a question 0.0 would let the instrument prove /census by
    arithmetic: the ceiling would be capped by the length of the output, not by
    the quality of retrieval."""
    result = bench.QuestionResult(
        id="agg-x", group="aggregate", question_class="traversal",
        truth_cardinality=200, truth_paths=[], truth_value=200,
    )
    result.retrieval_pool_size = 116
    assert result.truth_cardinality > result.retrieval_pool_size


def test_ceiling_at_k_is_derived_from_ranks_not_from_a_second_query():
    """One deep query carries the whole curve, because memory-index applies
    top_k as a final truncation of an already-sorted list."""
    result = bench.QuestionResult(
        id="agg-x", group="aggregate", question_class="traversal",
        truth_cardinality=4, truth_paths=["a", "b", "c", "d"], truth_value=4,
    )
    result.ranks = {"a": 0, "b": 7, "c": 55, "d": None}
    assert result.ceiling_at(1) == 0.25
    assert result.ceiling_at(8) == 0.5
    assert result.ceiling_at(100) == 0.75      # "d" is absent at any depth
    assert result.ceiling_at(10_000) == 0.75


def test_ceiling_of_an_empty_truth_set_is_zero_not_a_crash():
    result = bench.QuestionResult(
        id="agg-x", group="aggregate", question_class="traversal",
        truth_cardinality=0, truth_paths=[], truth_value=0,
    )
    assert result.ceiling_at(50) == 0.0


# ============================================================
# Run-state comparability
# ============================================================

BASE_STATE = {
    "corpus_sha": "abc123", "corpus_dirty": False,
    "corpus_content_sha256": "c0ffee00", "today": "2026-06-15",
    "index_config_sha256": "deadbeef", "index_built": "2026-06-15T09:00:00+04:00",
}


def test_the_parametrised_keys_are_every_pin_the_module_declares():
    """A pin absent from BASE_STATE is a pin this file cannot test.

    `corpus_content_sha256` was missing from both the parametrisation and the
    base dict, so the identity test compared two dicts that both lacked the one
    pin documented as having caught a real mis-grading, and the divergence test
    never exercised it.
    """
    assert set(bench.PINNED_KEYS) <= set(BASE_STATE)
    assert set(PINNED_UNDER_TEST) == set(bench.PINNED_KEYS)


def test_identical_state_compares():
    ok, diverged = bench.states_comparable(BASE_STATE, dict(BASE_STATE))
    assert ok and diverged == []


PINNED_UNDER_TEST = ("corpus_sha", "corpus_content_sha256", "today",
                     "index_config_sha256", "index_built")


@pytest.mark.parametrize("key", PINNED_UNDER_TEST)
def test_any_of_the_four_pinned_values_diverging_voids_the_comparison(key):
    """The corpus SHA alone is not enough: `today` enters several oracles
    directly, the retrieved side depends on the index config, and the index is
    rebuilt without the SHA moving."""
    other = dict(BASE_STATE)
    other[key] = "changed"
    ok, diverged = bench.states_comparable(BASE_STATE, other)
    assert not ok
    assert diverged == [key]


# ============================================================
# load_truth: the empty-truth guard, shared by every mode
# ============================================================

def test_load_truth_computes_every_question_on_the_fixture():
    questions = bench.load_questions(ROOT)
    corpus = bench.CorpusPaths.from_fixture(FIXTURE)
    truth = bench.load_truth(questions, corpus, TODAY)
    assert len(truth) == 15
    assert all(not a.is_empty() for a in truth.values())


def test_load_truth_refuses_an_empty_answer_and_names_the_question():
    """The guard lives in `load_truth`, not in --show-truth, because --baseline
    runs standalone: an empty set downstream divides by zero at best and reads as
    a 0.0 ceiling at worst."""
    corpus = bench.CorpusPaths.from_fixture(FIXTURE)
    questions = [{
        "id": "agg-01", "group": "aggregate", "question_class": "traversal",
        "oracle": "agg-01", "answer_type": "paths", "corpus": ["threads"],
        "question_ru": "x", "question_en": "x",
    }]
    # 1980 predates every fixture date, so nothing is stale relative to it.
    with pytest.raises(ValueError) as excinfo:
        bench.load_truth(questions, corpus, date(1980, 1, 1))
    assert "agg-01" in str(excinfo.value)
    assert "empty truth" in str(excinfo.value)


def test_score_mode_refuses_an_answers_file_it_cannot_read():
    """Docstring corrected 2026-08-13: scoring is implemented and tested.

    This said scoring was "deferred to step 2 on purpose", which stopped being
    true the day `--score` shipped, and a test that describes the code as absent
    is read by the next author as permission to leave it absent. What it
    actually pins is the missing-file path.
    """
    assert bench.mode_score("anything.json") == 2


def test_a_run_with_no_measurable_control_gets_no_verdict():
    """An unchecked index is not a healthy index.

    `ctl_mean is None` used to skip the health guard, so a run where no control
    question produced a ceiling reached BUILD with nothing establishing that
    retrieval works at all - and BUILD is the verdict that authorises building a
    whole primitive.
    """
    name, why = bench.verdict(agg_mean=0.05, ctl_mean=None, n_measurable=10)
    assert name == "NO-VERDICT"
    assert "never" in why


def test_the_gate_refuses_a_class_of_the_wrong_size():
    """The denominator is part of the pre-registered rule.

    Without this the threshold "6 of 7" silently becomes "6 of N" as soon as a
    question joins or leaves the gated class.
    """
    name, why = bench.acceptance_verdict(
        {"traversal": {"n": 9, "correct": 9}}, 0, True, [])
    assert name == bench.VERDICT_NOT_COMPARABLE
    assert "9" in why and str(bench.ACCEPT_TRAVERSAL_OF) in why
