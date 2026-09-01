"""Unit tests for scripts/utils/salience.py -- the shared type-weight +
access-count reinforcement formula used by memory-index.py's ranking
combiner (Gap #2) and dream-shadow.py's merge worklist (Gap #1).

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
    assert salience.reinforcement_bonus(10_000) == pytest.approx(salience.REINFORCE_CAP)


def test_reinforcement_bonus_calibrated_to_the_previous_curve_at_ten():
    """The change must be invisible on the range the old curve covered.

    Old: 1.0 + 0.03 * count, capped 1.3 — so exactly 1.30 at count 10.
    New: log-scaled, exact at zero and 1.2997369 at ten (the old 1.30 to four
    decimal places, which is as close as any K lands), and it keeps separating
    above 10 where the old curve was flat.
    """
    assert salience.reinforcement_bonus(0) == pytest.approx(1.0)
    assert salience.reinforcement_bonus(10) == pytest.approx(1.2997369, abs=1e-6)
    assert salience.reinforcement_bonus(50) > salience.reinforcement_bonus(10)
    assert salience.reinforcement_bonus(50) < salience.REINFORCE_CAP


def test_reinforcement_bonus_never_decreases():
    values = [salience.reinforcement_bonus(n) for n in range(400)]
    assert values == sorted(values)


def test_composite_salience_multiplies_weight_and_bonus():
    """`feedback` weighs exactly 1.0, so this case alone cannot see the operator.

    Multiplying by 1.0 and adding 1.0-minus-one give the same number, and the
    other composite case below has a bonus of exactly 1.0, which is degenerate
    the same way. MEASURED 2026-09-01: rewriting `composite_salience` as
    `type_weight(...) + reinforcement_bonus(...) - 1.0` left both green. The
    `reference` case is the one that discriminates: weight 0.5, bonus 1.1733,
    product 0.5866 against a sum of 0.6733.
    """
    expected = salience.type_weight("feedback") * salience.reinforcement_bonus(3)
    assert salience.composite_salience("feedback", 3) == pytest.approx(expected)


def test_composite_salience_is_a_product_and_not_a_sum():
    weight = salience.type_weight("reference")
    bonus = salience.reinforcement_bonus(3)
    assert weight != pytest.approx(1.0) and bonus != pytest.approx(1.0), \
        "fixture no longer discriminates a product from a shifted sum"

    got = salience.composite_salience("reference", 3)

    assert got == pytest.approx(weight * bonus)
    assert got != pytest.approx(weight + bonus - 1.0)


def test_composite_salience_zero_access_equals_type_weight():
    assert salience.composite_salience("project", 0) == pytest.approx(0.8)
