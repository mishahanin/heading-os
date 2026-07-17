"""Unit tests for scripts/utils/salience.py -- the shared type-weight +
access-count reinforcement formula used by memory-index.py's ranking
combiner (Gap #2) and dream-shadow.py's prune/merge worklist (Gap #1).

Run: python3 -m pytest tests/test_salience.py
"""
import pytest

from scripts.utils import salience


def test_type_weight_known_types():
    assert salience.type_weight("feedback") == pytest.approx(1.0)
    assert salience.type_weight("project") == pytest.approx(0.8)
    assert salience.type_weight("user") == pytest.approx(0.7)
    assert salience.type_weight("reference") == pytest.approx(0.5)


def test_type_weight_case_insensitive():
    assert salience.type_weight("Feedback") == pytest.approx(1.0)
    assert salience.type_weight("FEEDBACK") == pytest.approx(1.0)


def test_type_weight_default_for_unknown_type():
    assert salience.type_weight("unknown-type") == pytest.approx(0.6)
    assert salience.type_weight("") == pytest.approx(0.6)
    assert salience.type_weight(None) == pytest.approx(0.6)


def test_reinforcement_bonus_floor_at_zero_access():
    assert salience.reinforcement_bonus(0) == pytest.approx(1.0)
    assert salience.reinforcement_bonus(-5) == pytest.approx(1.0)


def test_reinforcement_bonus_increases_with_access_count():
    low = salience.reinforcement_bonus(1)
    high = salience.reinforcement_bonus(5)
    assert high > low > 1.0


def test_reinforcement_bonus_caps_at_high_access_count():
    assert salience.reinforcement_bonus(1000) == pytest.approx(salience.REINFORCE_CAP)
    # Comfortably past the cap threshold, still capped, not runaway.
    assert salience.reinforcement_bonus(100) == pytest.approx(salience.REINFORCE_CAP)


def test_composite_salience_multiplies_weight_and_bonus():
    expected = salience.type_weight("feedback") * salience.reinforcement_bonus(3)
    assert salience.composite_salience("feedback", 3) == pytest.approx(expected)


def test_composite_salience_zero_access_equals_type_weight():
    assert salience.composite_salience("project", 0) == pytest.approx(0.8)
