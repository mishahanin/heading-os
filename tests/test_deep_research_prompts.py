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
