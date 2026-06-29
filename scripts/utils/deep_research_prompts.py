"""Neutral prompt builders for deep-research-advance.

NO 31C/business context is ever injected — these prompts go to third-party
clouds (Kimi) and must carry only the public research task. Pure functions.
"""
from __future__ import annotations

from typing import List, Dict


def build_decompose_prompt(question: str, n: int) -> str:
    """Ask the model to split a research question into n focused sub-questions."""
    return (
        "You are planning a web-research task. Break the question below into "
        f"exactly {n} focused, non-overlapping sub-questions that together give "
        "thorough coverage. Each sub-question must be independently searchable.\n\n"
        f"Question: {question.strip()}\n\n"
        'Respond with ONLY a JSON array of strings, e.g. ["...", "..."]. No prose.'
    )


def build_reason_prompt(question: str, corpus: List[Dict]) -> str:
    """Ask the model to synthesize and per-claim verify the gathered corpus.

    corpus items: {"angle": str, "content": str, "source_ids": [int]}.
    The model must cite source_ids it was given and judge support per claim.
    """
    blocks = []
    for item in corpus:
        ids = ", ".join(str(i) for i in item.get("source_ids", []))
        blocks.append(f"### Angle: {item['angle']}\n(sources: {ids})\n{item['content']}")
    corpus_text = "\n\n".join(blocks)

    schema = (
        '{\n'
        '  "summary": "2-4 sentence synthesis",\n'
        '  "claims": [\n'
        '    {"claim": "factual claim", "status": "supported|unsupported|contradicted",\n'
        '     "confidence": 0.0, "source_ids": [1]}\n'
        '  ],\n'
        '  "contradictions": ["where sources disagree"]\n'
        '}'
    )
    return (
        "You are a rigorous research analyst. Below is a corpus gathered from web "
        "search, grouped by angle, with numbered source ids.\n\n"
        f"Research question: {question.strip()}\n\n"
        f"CORPUS:\n{corpus_text}\n\n"
        "Tasks:\n"
        "1. Synthesize the findings.\n"
        "2. Extract the key factual claims. For EACH claim, judge whether the "
        "corpus supports it (status), how confident you are (confidence 0.0-1.0), "
        "and which source_ids back it.\n"
        "3. List any contradictions between sources.\n\n"
        "Only use the source_ids provided. Do not invent sources or facts beyond "
        "the corpus.\n\n"
        f"Respond with ONLY a JSON object matching this schema (no prose, no code fence):\n{schema}"
    )
