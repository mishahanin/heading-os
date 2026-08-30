#!/usr/bin/env python3
"""The ops-radar signal shape, held to by every producer instead of by prose.

`scripts/utils/ops_signals.py` opens by declaring that each of its functions
returns `{key, value, threshold, due, severity, tier, summary}`, and every
consumer in `scripts/ops-radar.py` reads that shape with no default:
`autoheal_signals` does `sig["due"]`, `select_candidates` does `s["tier"]` and
`s["due"]`, `ack_suppressed` does `sig["key"]` and `sig["severity"]`. A dict
short by one key does not degrade there, it raises, and it raises inside a
detector whose whole job is to be the thing that still works when other things
have stopped.

A docstring cannot be conformed to mechanically, and on 2026-08-30 a test stub
did not: it built `{"key": ..., "severity": ...}` by hand and `sig["due"]`
raised KeyError. The shape is now `ops_signals.SIGNAL_KEYS`, and this file is
what makes that constant load-bearing rather than decorative.

Two things are asserted, and the second is the one that keeps the first honest:

  1. Every branch of every classifier returns EXACTLY `SIGNAL_KEYS`.
  2. The list of classifiers exercised here IS the list the module defines. A
     shape guard whose corpus is a hand-written list stops covering the
     eleventh producer the moment someone adds it, and passes while doing so.

The consumers are also pinned to their loud failure. Replacing `sig["due"]`
with `sig.get("due")` would have made the 2026-08-30 KeyError disappear without
making the malformed dict go away: the radar would have read a signal it could
not understand as "not due" and printed "all clear". That is the quieter
failure, not the fixed one.

Run: .venv/bin/python -m pytest tests/test_a_signal_shape_that_only_a_docstring_promised.py
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import ops_signals as ops  # noqa: E402


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


opr = _load("scripts/ops-radar.py", "ops_radar_signal_shape")


# Every classifier, with arguments chosen to walk its distinct branches: the
# quiet case, the escalated case, and each "I could not measure this" sentinel
# the function documents. The shape must not depend on which branch answered.
CASES: dict[str, list[tuple]] = {
    "classify_backup": [
        ((0, 0.0, 0), {}),
        ((3, 30.0, 0), {}),
        ((3, 200.0, 1), {}),
        ((3, None, 0), {}),          # dirty paths, none stat-able
        ((0, 0.0, 0), {"unreadable": 2}),
    ],
    "classify_weekly_review": [((None,), {}), ((3,), {}), ((9,), {}), ((30,), {})],
    "classify_cold_sweep": [((0,), {}), ((7,), {}), ((20,), {})],
    "classify_publish": [((0,), {}), ((5,), {})],
    "classify_odin": [
        (({},), {}),
        (({"nudge": True, "unharvested_total": 4, "reflect_clusters": 1},), {}),
        (({"nudge": True, "stale_clusters": 2},), {}),
    ],
    "classify_queue": [((0, 0), {}), ((2, 0), {}), ((0, 1), {})],
    "classify_ollama": [
        ((True, None), {}),
        ((False, None), {}),
        ((True, False), {"model": "bge-m3"}),
    ],
    "classify_ollama_accel": [
        ((False, False), {}),
        ((True, True), {"model_present": True}),
        ((True, False), {}),
        ((True, True), {"model_present": False}),
        ((True, False), {"pin_unresolvable": True}),
    ],
    "classify_index": [
        ((None, False), {}),         # never built
        ((0, False), {}),
        ((3, False), {}),
        ((9, True), {}),
    ],
    "classify_router_accuracy": [
        ((None, None), {}),          # producer dead
        (({"overall_rate": 0.9, "per_skill": {"a": 0.9}}, None), {}),
        (({"overall_rate": 0.4, "per_skill": {"a": 0.2}},
          {"overall_rate": 0.9, "per_skill": {"a": 0.9}}), {}),
        (({"overall_rate": 0.9, "per_skill": {"a": 0.9}},
          {"overall_rate": 0.9, "per_skill": {"a": 0.9}}), {}),
    ],
}


def _defined_classifiers() -> set[str]:
    """Every `classify_*` this module defines, asked of the module itself."""
    return {
        name for name, obj in vars(ops).items()
        if name.startswith("classify_")
        and inspect.isfunction(obj)
        and obj.__module__ == ops.__name__
    }


def test_the_corpus_is_every_classifier_the_module_defines():
    """The anti-decay case. A hand-written list of ten silently covers ten
    forever, so an eleventh producer would ship unchecked while this file kept
    reporting green over the corpus it happened to know about."""
    assert _defined_classifiers() == set(CASES), (
        "a classifier was added or renamed in ops_signals.py without a shape "
        "case here; add its branches to CASES"
    )


def test_the_corpus_is_not_empty_and_reaches_every_producer():
    """A guard over an empty corpus passes everything it never ran."""
    assert len(CASES) >= 10
    assert all(cases for cases in CASES.values())


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_branch_of_every_producer_returns_exactly_the_declared_shape(name):
    fn = getattr(ops, name)
    for args, kwargs in CASES[name]:
        out = fn(*args, **kwargs)
        assert isinstance(out, dict), f"{name}{args} did not return a dict"
        assert set(out) == set(ops.SIGNAL_KEYS), (
            f"{name}{args} returned {sorted(set(out) ^ set(ops.SIGNAL_KEYS))} "
            f"off the declared shape"
        )


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_three_fields_the_radar_subscripts_are_usable(name):
    """`due`, `tier` and `severity` are read without a default by
    `select_candidates` and `ack_suppressed`, and a present-but-nonsense value
    is the same outage as an absent one: a `tier` outside A/B drops the signal
    out of the candidate list in silence, and an unranked `severity` sorts as
    the weakest thing on the wire."""
    fn = getattr(ops, name)
    for args, kwargs in CASES[name]:
        out = fn(*args, **kwargs)
        assert isinstance(out["due"], bool), f"{name}{args} due is not a bool"
        assert out["tier"] in ("A", "B"), f"{name}{args} tier={out['tier']!r}"
        assert out["severity"] in ops.SEVERITY_ORDER, (
            f"{name}{args} severity={out['severity']!r} ranks 0 like an unknown"
        )
        assert isinstance(out["key"], str) and out["key"]
        assert isinstance(out["summary"], str) and out["summary"]


def test_the_declared_shape_is_what_the_module_docstring_lists():
    """The docstring is still the first thing a reader meets, so the constant
    and the prose must not drift apart."""
    listed = ops.__doc__.split("{", 1)[1].split("}", 1)[0]
    assert {part.strip() for part in listed.split(",")} == set(ops.SIGNAL_KEYS)


# ============================================================
# The consumers stay loud
# ============================================================

def _short(key: str) -> dict:
    """The exact dict the 2026-08-30 stub produced."""
    return {"key": key, "severity": "warn"}


def test_autoheal_signals_refuses_a_short_signal_rather_than_reading_it_as_quiet():
    """The regression, run at the seam rather than through a stub's luck. Turn
    `sig["due"]` into `sig.get("due")` and this passes while the radar reports
    all clear over a signal it could not read."""
    with pytest.raises(KeyError):
        opr.autoheal_signals([_short("ollama")], {})
    with pytest.raises(KeyError):
        opr.autoheal_signals([_short("memory_index")], {})


def test_select_candidates_refuses_a_short_signal_too():
    with pytest.raises(KeyError):
        opr.select_candidates([_short("backup")], {})


def test_a_well_shaped_tier_a_signal_passes_through_the_same_seam():
    """The other direction. A refusal test alone is satisfied by a function that
    refuses everything, so the accepting case is asserted on the same line."""
    good = ops.classify_ollama(False, None)
    assert good["key"] == "ollama" and good["due"] is True
    assert opr.autoheal_signals([good], {}) == []          # 0 failures, silent
    escalated = opr.autoheal_signals(
        [good], {"ollama": {"failures": ops.AUTOHEAL_ESCALATE}})
    assert [s["key"] for s in escalated] == ["ollama_autoheal"]
    assert set(escalated[0]) == set(ops.SIGNAL_KEYS), (
        "the synthetic auto-heal signal is fed back to the same consumers, so "
        "it owes the same shape"
    )


def test_every_key_the_radar_will_accept_for_an_ack_is_a_key_something_produces():
    """`KNOWN_KEYS` is the ack allow-list. A key in it that no producer emits
    banded to "ok" and silenced nothing, which is the defect `cmd_ack`'s own
    comment records for the synthetic auto-heal names."""
    produced = {fn(*args, **kwargs)["key"]
                for name, cases in CASES.items()
                for args, kwargs in cases
                for fn in (getattr(ops, name),)}
    produced |= {f"{t}_autoheal" for t in opr.TIER_A_TARGETS}
    assert produced >= opr.KNOWN_KEYS, (
        f"ack accepts {sorted(opr.KNOWN_KEYS - produced)}, which nothing emits"
    )
