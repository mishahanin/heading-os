"""`stratified_sample` counted a finding it was structurally unable to draw.

`scripts/scrutinize-replay.py` files every sample by severity with
`by_sev.setdefault(s.severity, [])`, so an unrecognised severity gets a bucket.
Both loops that read those buckets then iterated a hardcoded five-name tuple, so
that bucket was never visited.

The caller prints "Found N reports, M findings in range" from the whole list, so
such a finding was counted and then silently omitted from the sheet the CEO
hand-fills. Measured 2026-08-30:

  * corpus `[HIGH, UNKNOWN, CRITICAL]`, `--sample 10`: only `HIGH` came back;
  * corpus `[UNKNOWN]`, `--sample 5`: **nothing** came back, while the line
    above it reported one finding.

The second case is the sharp one. The kappa statistic this sheet exists to
compute was computed over an empty sheet, after a run that said it had found
something. The tier list is derived from the data now.

Run: python3 -m pytest tests/test_a_sampler_that_counted_findings_it_could_never_draw.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load():
    spec = importlib.util.spec_from_file_location(
        "scrutinize_replay_sampler", ROOT / "scripts" / "scrutinize-replay.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["scrutinize_replay_sampler"] = mod
    spec.loader.exec_module(mod)
    return mod


RP = _load()

KNOWN = ("BLOCKER", "HIGH", "MEDIUM", "LOW", "NIT")


def _sample(index: int, severity: str):
    return RP.FindingSample(
        scrutiny_id="2026-08-30_scrutiny_moneypenny",
        finding_id=f"X{index}",
        severity=severity,
        confidence=90,
        statement="the brief named a module the code never imports",
        location="scripts/example.py:12",
        evidence="import list",
        was_flagged_fp=False,
    )


def test_a_lone_off_tier_finding_is_drawn_rather_than_dropped():
    """THE case. A corpus of one returning zero is the sharpest shape of it."""
    corpus = [_sample(1, "UNKNOWN")]
    assert corpus, "empty corpus proves nothing"

    picked = RP.stratified_sample(corpus, 5)

    assert [s.severity for s in picked] == ["UNKNOWN"]


def test_an_off_tier_finding_is_not_skipped_beside_known_ones():
    corpus = [_sample(1, "HIGH"), _sample(2, "UNKNOWN"), _sample(3, "CRITICAL")]

    picked = RP.stratified_sample(corpus, 10)

    assert sorted(s.severity for s in picked) == ["CRITICAL", "HIGH", "UNKNOWN"]


def test_nothing_the_caller_counted_is_unreachable():
    """States the invariant the count line depends on: with room for everything,
    the sampler returns everything, whatever the severities are."""
    corpus = [_sample(i, sev) for i, sev in
              enumerate(["BLOCKER", "UNKNOWN", "NIT", "critical", "", "MEDIUM"])]

    picked = RP.stratified_sample(corpus, len(corpus))

    assert len(picked) == len(corpus)
    assert {id(s) for s in picked} == {id(s) for s in corpus}


def test_the_ordinary_five_tier_sampling_is_unchanged():
    """The negative control. A sampler that simply returned everything would
    pass the three tests above and destroy the balancing this function is for."""
    corpus = ([_sample(i, "MEDIUM") for i in range(50)]
              + [_sample(90, "BLOCKER"), _sample(91, "HIGH")])

    picked = RP.stratified_sample(corpus, 5)

    assert len(picked) == 5, "the cap stopped being a cap"
    severities = {s.severity for s in picked}
    assert "BLOCKER" in severities, "the thin tier lost its guaranteed slot"
    assert severities <= set(KNOWN)


def test_the_cap_holds_when_the_tiers_outnumber_it():
    """The `picked[:n]` slice, with a case ON the line rather than beside it.

    Every other case here lands on exactly `n` before the slice runs, so the cap
    was asserted (`len(picked) == 5`, "the cap stopped being a cap") by tests
    that never reached it: deleting the slice left the whole file green. The
    first loop takes `quota` per tier, so once the corpus carries more distinct
    severities than `n`, that loop alone overshoots and only the slice brings it
    back. Seven severities, one finding each, `--sample 5` returned 7 without it.
    """
    severities = ["BLOCKER", "HIGH", "MEDIUM", "LOW", "NIT", "UNKNOWN", "CRITICAL"]
    corpus = [_sample(i, sev) for i, sev in enumerate(severities)]
    assert len(corpus) > 5, "the corpus must exceed the cap or this proves nothing"

    picked = RP.stratified_sample(corpus, 5)

    assert len(picked) == 5, (
        f"asked for 5, got {len(picked)}: {[s.severity for s in picked]}")


def test_a_thin_tier_keeps_its_slot_below_a_sample_of_five():
    """`max(1, n // 5)`, with a case ON the `max`.

    `n // 5` alone is 0 for every `--sample` under 5, which empties the
    per-tier loop and leaves the whole draw to the leftover pass. That pass
    walks the tiers in order and takes the head of the list, so a fat MEDIUM
    tier consumes the entire sample and the one NIT finding, which the
    stratification exists to surface, is never drawn. Dropping the `max` left
    the rest of this file green.
    """
    corpus = [_sample(i, "MEDIUM") for i in range(50)] + [_sample(99, "NIT")]

    picked = RP.stratified_sample(corpus, 3)

    assert len(picked) == 3
    assert "NIT" in {s.severity for s in picked}, (
        "the thin tier lost its guaranteed slot below n=5: "
        f"{[s.severity for s in picked]}")


def test_the_sampler_is_still_deterministic():
    """The sheet is a benchmark; two runs over one corpus must agree."""
    corpus = [_sample(i, sev) for i, sev in
              enumerate(["BLOCKER", "HIGH", "MEDIUM", "LOW", "NIT", "UNKNOWN"] * 3)]

    first = [s.finding_id for s in RP.stratified_sample(corpus, 7)]
    second = [s.finding_id for s in RP.stratified_sample(corpus, 7)]

    assert first == second
    assert len(first) == 7
