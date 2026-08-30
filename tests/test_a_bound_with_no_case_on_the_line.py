#!/usr/bin/env python3
"""The session-threshold bounds, tested ON the line and on both sides of it.

`_session_hard` in `scripts/utils/checkpoint_paths.py` decides whether a
session's own `session_hard_threshold` is usable or whether the reader falls
back to the workspace environment pair. It refuses on

    value < HARD_THRESHOLD_MIN or value > HARD_THRESHOLD_MAX

so both bounds are INCLUSIVE, and the CLI half tells the operator exactly that:
"outside 15-90". What the guard protects is two different failures at the two
ends. Under the minimum the derived soft reminder (`hard - SOFT_OFFSET`) lands
below the always-loaded context floor and the offer cascades, which is the
confirmed 2026-08-19 incident. Over the maximum there is no window left to write
the handoff that has to precede the compaction. Between them the operator's
number is honoured; outside them the environment answers instead.

The CLI half of this bound was closed by
`tests/test_session_compaction_threshold.py::test_a_value_exactly_on_a_bound_is_accepted`,
which drives `scripts/checkpoint-paths.py` as a subprocess. The LIBRARY half had
no case standing on either bound: the refused set was 5, 95 and 0, none of which
touches an end. Either comparison could gain an `=` and a hand-edited state file
carrying exactly 15 or exactly 90 would silently fall back to the environment
default, with the status line, the offer level and the driven compaction all
running on a number the operator did not choose and no message saying so.

The bound values are read from the module's own constants rather than typed, so
this file tracks a change to the constants instead of freezing today's pair.
`test_session_compaction_threshold.py::test_the_bounds_are_named_constants` is
where the literals 15 and 90 are pinned; that is deliberately not repeated here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import checkpoint_paths as CP  # noqa: E402

MIN = CP.HARD_THRESHOLD_MIN
MAX = CP.HARD_THRESHOLD_MAX

# The fallback pair this file asserts against. Pinned here rather than read from
# the machine, so a settings change cannot turn a real assertion into one that
# passes because the two numbers happened to agree.
ENV_SOFT = 40
ENV_HARD = 45


@pytest.fixture(autouse=True)
def _pinned_environment(monkeypatch):
    monkeypatch.setenv("CLAUDE_HANDOFF_SOFT_THRESHOLD", str(ENV_SOFT))
    monkeypatch.setenv("CLAUDE_HANDOFF_HARD_THRESHOLD", str(ENV_HARD))


def test_the_fixture_cannot_make_a_bound_case_pass_by_coincidence():
    """A guard on this file, not on the module.

    Every accepted case below asserts the bound value came back, and every
    refused case asserts the environment pair came back instead. If a bound ever
    equalled `ENV_HARD` the two assertions would stop distinguishing anything.
    """
    assert MIN < MAX, "the bounds are inverted; every case below is meaningless"
    assert ENV_SOFT < ENV_HARD, "an inverted pair triggers config()'s 25/30 reset"
    assert ENV_HARD not in (MIN, MAX)
    assert MIN - 1 not in (ENV_HARD, MAX)
    assert MAX + 1 != ENV_HARD


# --------------------------------------------------------------- the function


@pytest.mark.parametrize("on_the_line", [MIN, MAX])
def test_a_value_exactly_on_a_bound_is_the_value_the_function_returns(on_the_line):
    """ON the line, from the inside. Flipping `<` to `<=` kills the MIN case;
    flipping `>` to `>=` kills the MAX case."""
    assert CP._session_hard({"session_hard_threshold": on_the_line}) == on_the_line


@pytest.mark.parametrize("outside", [MIN - 1, MAX + 1])
def test_the_first_value_past_a_bound_is_refused(outside):
    """One step outside, from the other side. Deleting either comparison, or
    widening the accepted range in the other direction, kills these."""
    assert CP._session_hard({"session_hard_threshold": outside}) is None


@pytest.mark.parametrize("on_the_line", [MIN, MAX])
def test_a_string_exactly_on_a_bound_is_parsed_and_accepted(on_the_line):
    """The state file is JSON written by several producers, and `int(raw)` is
    what makes a stringified number usable. A bound reached through the parse
    must land the same way a bound reached as an int does."""
    assert CP._session_hard({"session_hard_threshold": str(on_the_line)}) == on_the_line


# ------------------------------------------------------------- the public seam


@pytest.mark.parametrize("on_the_line", [MIN, MAX])
def test_config_honours_a_threshold_standing_exactly_on_a_bound(on_the_line):
    """What the rest of the workspace actually reads. `config()` is the only
    caller of `_session_hard`, so a bound that fails here fails the status line,
    the offer level and the driven compaction together."""
    cfg = CP.config({"session_hard_threshold": on_the_line})
    assert cfg["hard"] == on_the_line
    assert cfg["soft"] == on_the_line - CP.SOFT_OFFSET


@pytest.mark.parametrize("outside", [MIN - 1, MAX + 1])
def test_config_falls_back_to_the_environment_one_step_past_a_bound(outside):
    cfg = CP.config({"session_hard_threshold": outside})
    assert (cfg["soft"], cfg["hard"]) == (ENV_SOFT, ENV_HARD)
