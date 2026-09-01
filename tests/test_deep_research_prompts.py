"""Research prompts carry NO 31C/business context and request the right structure."""
from __future__ import annotations

from scripts.utils import deep_research_prompts as drp


def test_decompose_prompt_is_neutral_and_asks_for_n():
    p = drp.build_decompose_prompt("What is the state of EU AI regulation?", 4)
    assert "31C" not in p
    assert "ODUN" not in p
    assert "Tribe" not in p
    assert "4" in p
    assert "JSON" in p


def test_reason_prompt_includes_corpus_and_schema():
    corpus = [{"angle": "a", "content": "finding text", "source_ids": [1]}]
    p = drp.build_reason_prompt("the question", corpus)
    assert "31C" not in p
    assert "finding text" in p
    assert "status" in p and "confidence" in p and "source_ids" in p
    assert "supported" in p


# --- citation integrity -----------------------------------------------------
# Measured defect, 2026-08-22: every angle's Perplexity content carries inline
# markers numbered from [1] LOCALLY, while source_ids are global. The model
# echoed the local numbers, so a claim about a FastFlowLM endpoint cited an AMD
# release article. Anyone reading intermediate.json got the wrong URL.


def test_inline_markers_are_remapped_to_global_ids():
    """An angle's local [1]/[2] must become its own global ids, not stay 1/2."""
    corpus = [
        {"angle": "first", "content": "alpha [1] and beta [2]", "source_ids": [1, 2]},
        {"angle": "second", "content": "gamma [1] and delta [2]", "source_ids": [41, 42]},
    ]
    p = drp.build_reason_prompt("q", corpus)
    assert "gamma [41] and delta [42]" in p
    assert "gamma [1]" not in p
    # The first angle already starts at 1, so it must be left intact.
    assert "alpha [1] and beta [2]" in p


def test_multi_citation_markers_are_remapped():
    """Perplexity emits [1][3] runs and [1, 3] pairs; both must remap."""
    corpus = [
        {"angle": "a", "content": "claim [1][3] and other [2, 3]", "source_ids": [10, 11, 12]},
    ]
    p = drp.build_reason_prompt("q", corpus)
    assert "claim [10][12]" in p
    assert "other [11, 12]" in p


def test_out_of_range_marker_is_left_alone():
    """A marker past the angle's source count is not a citation. Do not corrupt it."""
    corpus = [{"angle": "a", "content": "footnote [9] here", "source_ids": [5, 6]}]
    p = drp.build_reason_prompt("q", corpus)
    assert "footnote [9] here" in p


# The range test above uses [9] against two sources, which is four clear of the
# boundary. A case four clear of a bound cannot tell a correct bound from a
# wrong one: MEASURED 2026-09-01, widening the upper limit to
# `len(source_ids) + 1` and lowering the floor to `0` BOTH left all 65 tests
# across the three files that exercise this module green.
#
# Neither survivor is cosmetic. `+1` sends `source_ids[len]` into an IndexError
# that nothing on the path catches, so one stray marker takes down the whole
# reason prompt. `0 <=` is worse because it is silent: `source_ids[0 - 1]` is
# the angle's LAST source, so a literal `[0]` in a code snippet or an array
# index inside a Perplexity answer is renumbered into a real global id and the
# model attributes a claim to a source that never carried it. That is the exact
# failure the remap was written to end, arriving through the branch that was
# supposed to refuse.


def test_the_last_valid_marker_is_still_remapped():
    """One case ON the upper bound, from the inside."""
    assert drp._remap_inline_citations("tail [2]", [41, 42]) == "tail [42]"


def test_the_first_marker_past_the_upper_bound_is_left_alone():
    """One case ON the upper bound, from the outside. `[3]` with two sources is
    the marker an off-by-one would index straight off the end of the list."""
    assert drp._remap_inline_citations("tail [3]", [41, 42]) == "tail [3]"


def test_a_zero_marker_is_not_a_citation():
    """One case ON the lower bound. Python's negative indexing means a floor of
    0 does not raise; it quietly returns the angle's last source."""
    assert drp._remap_inline_citations("index [0] here", [41, 42]) == "index [0] here"
    # And through the public builder, so this cannot pass against a
    # `build_reason_prompt` that stopped calling the remap.
    corpus = [{"angle": "a", "content": "index [0] here", "source_ids": [41, 42]}]
    assert "index [0] here" in drp.build_reason_prompt("q", corpus)


def test_a_group_is_left_whole_when_any_member_is_out_of_range():
    """`[1, 3]` against two sources: partially remapping it would produce a
    marker mixing a global id with a local one, which reads as neither."""
    assert drp._remap_inline_citations("mixed [1, 3]", [41, 42]) == "mixed [1, 3]"
    assert drp._remap_inline_citations("mixed [1][3]", [41, 42]) == "mixed [41][3]"


def test_urls_are_shown_beside_each_global_id():
    """The model cannot anchor an id it never saw a URL for."""
    corpus = [{"angle": "a", "content": "text [1]", "source_ids": [7]}]
    sources = [{"id": 7, "url": "https://example.org/page", "angle": "a"}]
    p = drp.build_reason_prompt("q", corpus, sources)
    assert "https://example.org/page" in p
    assert "[7]" in p


def test_sources_argument_is_optional():
    """Callers that predate the URL table must keep working."""
    corpus = [{"angle": "a", "content": "text [1]", "source_ids": [7]}]
    assert "text [7]" in drp.build_reason_prompt("q", corpus)
