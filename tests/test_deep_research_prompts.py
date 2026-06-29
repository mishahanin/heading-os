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
