#!/usr/bin/env python3
r"""The content gate got a prefilter, and a prefilter can hide a leak.

`Denylist.scan_text` ran a 760-alternative regex once per LINE. MEASURED
2026-08-31 on the operator's tree, profiling `content-guard.py --all`:

    build_denylist:      0.06s   (760 tokens)
    repo_carried_paths:  0.03s   (2150 paths)
    engine_text_files:   1.16s   (2127 files)
    scan_text:          66s      (extrapolated from 200 files)

66 of 67 seconds in one loop, and three separate test files each pay it in
full. Under a machine loaded by five parallel agents the real figure was 2m42s,
and all three tests that shell out to `--all` with `timeout=300` blew it at
once:

    FAILED tests/test_a_suite_that_only_passed_on_one_clock.py::...[Pacific/Kiritimati]
    FAILED tests/test_a_gate_that_shipped_what_it_never_read.py::test_the_whole_engine_surface_passes
    FAILED tests/test_a_colleagues_given_name_is_engine_data.py::test_the_whole_engine_surface_passes_the_default_gate

Three failures, one cause. A test whose verdict depends on how busy the machine
is measures the machine.

THREE PREFILTERS WERE TRIED. The first two are recorded because each looked
obviously right and each was refuted by the same 400-file benchmark, and a
reader who does not know that will reach for one of them again:

    whole-text regex `search()`    13.84s vs 10.91s  ->  27% SLOWER.
        One pass over the text covers the same character positions as all of
        its lines, so nothing is saved, and testing the second pattern adds a
        whole extra pass.

    "any word of any token present"  0.12s, 76x faster, and USELESS: it said
        "maybe" for 400/400 files. A single common word split out of a
        multi-word name occurs in essentially every file, so nothing was ever
        skipped.

    "every word of a multi-word token, or a single-word token verbatim"
        1.78s, skipped 360/400 files, projected 12.79s -> 3.06s. This is the
        one in the code.

The lesson is the middle row: `any` and `all` differ by one word and by the
entire value of the optimisation.

WHY IT IS SOUND. A `_pattern` match needs the token verbatim, so a single-word
token must occur as a substring and a multi-word token must have every one of
its words present. A `_loose_pattern` match widens the internal spaces to `\s+`,
so it still needs every word. The test is therefore a NECESSARY condition: a
file that passes still goes through the full scan. The only way it can be wrong
is by claiming a hit is impossible when it is not, which would make the gate
report CLEAN over a file carrying a real name, and clean is the answer nobody
re-checks. That is what this file exists to prevent.

`test_the_prefilter_never_refuses_a_text_the_scanner_would_hit` is the one
direction that matters; the differential over CASES is the broader net; and
`test_a_multi_word_token_needs_every_word_not_any_word` pins the `all`, so a
later "simplification" to `any` fails here rather than silently costing the
whole speed-up.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.content_denylist import Denylist  # noqa: E402


# Invented entities. The engine repo is public and ships no real data, so the
# fixtures here are made up rather than drawn from the operator's overlay.
TOKENS = {
    "jbondsmith": "person",
    "james bond": "person",
    "moonraker": "codename",
    "q.branch@example.invalid": "email",
    "universal exports ltd": "company",
}


@pytest.fixture
def deny():
    """A Denylist over invented tokens, compiled by the REAL `_compile()`.

    Hand-rolling the pattern build here is the drift this whole audit is about:
    an earlier draft of this fixture duplicated it, and when `_compile` grew the
    prefilter needles the copy did not, so 19 tests failed against a change that
    was correct. A fixture that reimplements the thing under test measures the
    reimplementation.
    """
    dl = Denylist(tokens=dict(TOKENS))
    dl._compile()
    return dl


def _unfiltered(dl, text):
    """What `scan_text` returned BEFORE the prefilter, recomputed here.

    The oracle is a re-implementation rather than a saved expectation, so it
    tracks the real scanner's semantics (suppression, dedupe, the wrapped pass)
    instead of freezing one day's output. It is deliberately the slow shape the
    prefilter exists to skip.
    """
    lines = text.splitlines()
    suppressed = {n for n, line in enumerate(lines, 1)
                  if "content-guard: ok" in line}
    hits, seen = [], set()
    for n, line in enumerate(lines, 1):
        if n in suppressed:
            continue
        for m in dl._pattern.finditer(line):
            hits.append((n, m.group(0), dl.tokens.get(m.group(0).lower(), "entity")))
            seen.add((n, m.group(0).lower()))
    if dl._loose_pattern is not None:
        hits.extend(dl._scan_wrapped(lines, suppressed, seen))
    return hits


# Each case is one way a name can sit in a file. The point of the list is that
# the prefilter must not change ANY of them, so it covers both answers.
CASES = [
    ("a clean file with no entity at all",
     "def add(a, b):\n    return a + b\n", False),
    ("an empty file", "", False),
    ("whitespace only", "\n\n   \n\t\n", False),
    ("a near miss that shares a prefix",
     "jbondsmithers is a different word\nmoonrakerish too\n", False),
    ("a token glued inside a longer identifier",
     "x = get_jbondsmith_id()\nMOONRAKERX = 1\n", False),
    ("a plain single-word hit", "owner: jbondsmith\n", True),
    ("a hit on the very first character", "jbondsmith owns this\n", True),
    ("a hit at end of file with no trailing newline", "cc jbondsmith", True),
    ("a multi-word hit on one line", "signed, James Bond, director\n", True),
    ("an email token", "reply to q.branch@example.invalid please\n", True),
    ("a hit adjacent to punctuation", '"jbondsmith", -jbondsmith-, .jbondsmith.\n', True),
    ("mixed case", "JBondSmith and MOONRAKER\n", True),
    ("a suppressed line", "jbondsmith  # content-guard: ok invented\n", False),
    ("suppressed on one line, live on the next",
     "jbondsmith  # content-guard: ok invented\nmoonraker\n", True),
    ("two hits on one line", "jbondsmith met moonraker\n", True),
    ("a hit far down a long clean file", "clean line\n" * 400 + "moonraker\n", True),
]


@pytest.mark.parametrize("label,text,expect_hit",
                         CASES, ids=[c[0] for c in CASES])
def test_the_prefilter_changes_no_answer(deny, label, text, expect_hit):
    """Differential: prefiltered scan == unfiltered scan, hit or no hit."""
    got = deny.scan_text(text)
    want = _unfiltered(deny, text)
    assert got == want, (
        f"the prefilter changed the answer for {label!r}. A prefilter that "
        f"drops a hit makes the gate report CLEAN over a real name.\n"
        f"  prefiltered: {got}\n  unfiltered:  {want}")
    assert bool(got) is expect_hit, (
        f"{label!r} was expected to {'hit' if expect_hit else 'stay clean'} "
        f"and did not; the fixture, not the scanner, is wrong")


def test_the_case_list_covers_both_answers():
    """Green over a one-sided fixture otherwise.

    Without this, deleting every hitting case would leave the parametrization
    proving only that a clean file stays clean, which a prefilter that returns
    [] unconditionally also satisfies.
    """
    assert sum(1 for *_, hit in CASES if hit) >= 8
    assert sum(1 for *_, hit in CASES if not hit) >= 5


def test_a_name_split_across_a_line_break_survives_the_prefilter(deny):
    r"""The case a careless prefilter fails.

    Prose here is hard-wrapped, so "James Bond" routinely arrives with a
    newline between the words. `_pattern` matches a literal space and cannot
    see it; `_loose_pattern` widens the space to `\s+` and can. A prefilter
    that consulted only `_pattern` would return early on exactly this text and
    hide the wrapped name, which is the class of leak the loose pattern was
    added for.
    """
    text = "a long sentence ending in James\nBond and continuing after it\n"
    assert deny._pattern.search(text) is None, (
        "fixture no longer exercises the loose-only path; the strict pattern "
        "now matches, so this test would pass without the loose prefilter")
    hits = deny.scan_text(text)
    assert hits, "the wrapped name was hidden by the prefilter"
    assert hits == _unfiltered(deny, text)


def test_a_name_split_by_other_whitespace_also_survives(deny):
    """A double space and a non-breaking space hid a name once before."""
    # The non-breaking space is an ESCAPE, never a literal. This workspace
    # forbids invisible characters in authored text, and the PostToolUse
    # sanitiser caught this file carrying the very thing it tests for.
    for gap in ("  ", "\u00a0", "\n\t", " \n "):
        text = f"regards, James{gap}Bond\n"
        assert deny.scan_text(text) == _unfiltered(deny, text), repr(gap)
        assert deny.scan_text(text), f"hidden by the prefilter with gap {gap!r}"


def test_neither_pattern_is_anchored(deny):
    """The soundness argument, pinned as a fact rather than a comment.

    The early return is only safe because a per-line match implies a
    whole-text match, and that holds because the patterns carry no `^`/`$`
    anchor and no MULTILINE flag. Add either and the implication breaks
    silently, so it breaks here loudly instead.
    """
    for name, pattern in (("_pattern", deny._pattern),
                          ("_loose_pattern", deny._loose_pattern)):
        assert pattern is not None, name
        assert not (pattern.flags & re.MULTILINE), (
            f"{name} is MULTILINE, so `^`/`$` now bind per line and a per-line "
            f"match no longer implies a whole-text match. The prefilter in "
            f"scan_text is unsound as written; remove it or re-derive it.")
        body = pattern.pattern
        assert "^" not in body.replace("[^", ""), (
            f"{name} carries a `^` anchor; see the MULTILINE note above")
        assert "$" not in body, f"{name} carries a `$` anchor"


def test_an_empty_denylist_still_returns_nothing():
    """The `_pattern is None` path is above the prefilter and must stay reachable."""
    dl = Denylist.__new__(Denylist)
    dl.tokens = {}
    dl.degraded = False
    dl._pattern = None
    dl._loose_pattern = None
    assert dl.scan_text("jbondsmith everywhere\n") == []


def test_the_prefilter_actually_skips_work(deny):
    """Otherwise this whole file guards an optimisation that is not there.

    Counts calls to the per-line `finditer`. On a clean file the prefilter must
    reach it zero times; on a file with a hit it must reach it once per line.
    Without this, deleting the early return leaves every test above green.

    A PROXY, not `monkeypatch.setattr`: `re.Pattern.finditer` is read-only, so
    the obvious spelling raises `AttributeError` rather than counting anything.
    """
    class CountingPattern:
        def __init__(self, real):
            self._real = real
            self.finditer_calls = 0

        def finditer(self, *a, **k):
            self.finditer_calls += 1
            return self._real.finditer(*a, **k)

        def search(self, *a, **k):
            return self._real.search(*a, **k)

    proxy = CountingPattern(deny._pattern)
    deny._pattern = proxy

    deny.scan_text("clean line\n" * 50)
    assert proxy.finditer_calls == 0, (
        f"the prefilter did not skip a clean 50-line file; per-line finditer "
        f"ran {proxy.finditer_calls} times over a file with nothing in it. The "
        f"early return in scan_text is gone.")

    proxy.finditer_calls = 0
    hits = deny.scan_text("clean line\n" * 49 + "moonraker\n")
    assert proxy.finditer_calls == 50, (
        f"a file WITH a hit must still be scanned line by line, got "
        f"{proxy.finditer_calls} calls")
    assert hits, "and it must still report the hit"


def test_the_needles_are_derived_from_the_tokens(deny):
    """A hand-kept needle list is a second copy that stops being updated.

    `_compile` builds the patterns and the needles from the same `self.tokens`,
    so a new token class cannot reach the scanner and miss the prefilter. This
    checks they describe the SAME set rather than merely both being non-empty.
    """
    singles = {t for t in TOKENS if " " not in t}
    multi = {t for t in TOKENS if " " in t}
    assert set(deny._needle_singles) == singles
    assert {" ".join(w) for w in deny._needle_multi} == multi
    assert singles and multi, "the fixture must exercise both shapes"


def test_a_multi_word_token_needs_every_word_not_any_word(deny):
    """The difference between 90% selectivity and 0%.

    MEASURED on 400 real files: requiring ANY word of a multi-word token said
    "maybe" for 400/400 files and skipped nothing, because a single common word
    split out of a name occurs everywhere. Requiring ALL words skipped 360/400.
    A future "simplification" to `any` costs the whole optimisation while
    staying correct, so it fails here instead of going unnoticed.
    """
    # "james" alone, with no "bond" anywhere, must not arm the multi-word needle.
    assert deny._could_hold_a_token("james went to the shops") is False
    assert deny._could_hold_a_token("bond markets rallied") is False
    assert deny._could_hold_a_token("james bond") is True
    # The words may be far apart; the needle is containment, not adjacency.
    assert deny._could_hold_a_token("james\n\nlater on, bond") is True


def test_the_prefilter_never_refuses_a_text_the_scanner_would_hit(deny):
    """The one direction that matters, stated as its own test.

    A prefilter can be over-permissive with no consequence beyond speed. Being
    over-RESTRICTIVE makes the gate report clean over a real name. Every case in
    CASES that produces a hit must therefore pass the prefilter.
    """
    for label, text, expect_hit in CASES:
        if not expect_hit:
            continue
        assert deny._could_hold_a_token(text.lower()) is True, (
            f"the prefilter would have skipped {label!r}, which contains a real "
            f"hit. This is the failure mode that makes a leak invisible.")


def test_an_empty_token_set_has_empty_needles():
    """The `_pattern is None` branch must set the needles too.

    It did not at first, and `scan_text` then raised `AttributeError` on a
    public clone (where the overlay is absent and the denylist is empty), which
    is the one environment the gate is supposed to no-op in quietly.
    """
    dl = Denylist(tokens={})
    dl._compile()
    assert dl._needle_singles == ()
    assert dl._needle_multi == ()
    assert dl.scan_text("jbondsmith everywhere\n") == []
