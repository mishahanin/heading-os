"""A per-contact `cadence:` override must move the health colour, not just the label.

Found by the 2026-08-23 engine audit and reproduced exactly.

`get_thresholds` has two branches and they disagreed. For a KNOWN relationship
type the override replaced only `entry["cadence"]` -- the number the radar
prints -- while `yellow` and `red` kept the type defaults. `calculate_health`
thresholds on `red` and `yellow` and never reads `cadence`, so the override
changed the display and nothing else. For an UNKNOWN type the same override
scaled all three (`yellow = int(c * 0.7)`, `red = c`), which is the intended
semantics written out in code one branch below.

Measured before the fix, with `partner` defaults 14/10/14:

    partner + cadence 60, last touch 20 days ago  -> {'cadence': 60, 'yellow': 10, 'red': 14}  -> RED
    unknown + cadence 60, last touch 20 days ago  -> {'cadence': 60, 'yellow': 42, 'red': 60}  -> GREEN

Same contact, same override, opposite colour, decided by whether the type
happens to be in the table. The failure direction is the damaging one: every
deliberately-slowed relationship reads RED on the company radar, so the radar
fills with reds nobody should act on and the real ones stop standing out.

The guard states the property both branches must share, rather than testing one
branch against the other's arithmetic -- a test written that way would pass if
both branches drifted together.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _agg():
    path = ROOT / "scripts" / "aggregate-crm.py"
    spec = importlib.util.spec_from_file_location("aggregate_crm_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


AGG = _agg()

# Types that carry a cadence at all; the no-cadence ones never reach this code.
KNOWN_TYPES = sorted(set(AGG.DEFAULT_CADENCE) - AGG.NO_CADENCE_TYPES)


def _days_ago(n: int) -> str:
    return (AGG.TODAY - timedelta(days=n)).isoformat()


def test_the_fixture_is_not_vacuous():
    assert len(KNOWN_TYPES) >= 5
    assert "partner" in KNOWN_TYPES


@pytest.mark.parametrize("rel_type", KNOWN_TYPES)
def test_an_override_scales_every_threshold_for_a_known_type(rel_type):
    th = AGG.get_thresholds({"type": rel_type, "cadence": "60"}, AGG.DEFAULT_CADENCE)
    assert th["cadence"] == 60
    assert th["red"] == 60, (
        f"{rel_type}: the override moved the printed cadence to 60 but left "
        f"red at {th['red']}, so the colour still uses the type default"
    )
    assert th["yellow"] == int(60 * 0.7)


def test_the_two_branches_agree():
    """The unknown-type branch already did the right thing. Both must now
    produce the same thresholds for the same override."""
    known = AGG.get_thresholds({"type": "partner", "cadence": "45"}, AGG.DEFAULT_CADENCE)
    unknown = AGG.get_thresholds({"type": "no-such-type", "cadence": "45"},
                                 AGG.DEFAULT_CADENCE)
    assert (known["cadence"], known["yellow"], known["red"]) == \
           (unknown["cadence"], unknown["yellow"], unknown["red"])


def test_the_measured_case_is_green_not_red():
    """The exact reproduction from the audit."""
    th = AGG.get_thresholds({"type": "partner", "cadence": "60"}, AGG.DEFAULT_CADENCE)
    state, days = AGG.calculate_health(_days_ago(20), th)
    assert days == 20
    assert state == "green", (
        "a partner on a 60-day cadence, touched 20 days ago, still reads "
        f"{state!r}. That is the false red that floods the radar."
    )


def test_the_override_still_turns_red_past_its_own_window():
    """A wider cadence must not mean never red."""
    th = AGG.get_thresholds({"type": "partner", "cadence": "60"}, AGG.DEFAULT_CADENCE)
    assert AGG.calculate_health(_days_ago(61), th)[0] == "red"
    assert AGG.calculate_health(_days_ago(45), th)[0] == "yellow"


def test_no_override_leaves_the_type_defaults_untouched():
    """The fix must not rewrite thresholds for the ordinary case."""
    for rel_type in KNOWN_TYPES:
        th = AGG.get_thresholds({"type": rel_type}, AGG.DEFAULT_CADENCE)
        assert th == AGG.DEFAULT_CADENCE[rel_type], rel_type


def test_a_non_numeric_override_is_ignored_not_fatal():
    th = AGG.get_thresholds({"type": "partner", "cadence": "soon"}, AGG.DEFAULT_CADENCE)
    assert th == AGG.DEFAULT_CADENCE["partner"]


def test_an_unknown_type_with_no_override_falls_back():
    th = AGG.get_thresholds({"type": "no-such-type"}, AGG.DEFAULT_CADENCE)
    assert th == {"cadence": 14, "yellow": 10, "red": 14}
