"""Neutral prompt builders for deep-research-advance.

NO 31C/business context is ever injected — these prompts go to third-party
clouds (Kimi) and must carry only the public research task. Pure functions.
"""
from __future__ import annotations

import re
from typing import List, Dict, Optional

# Perplexity numbers its inline citations from [1] within EACH answer, so the
# same marker means a different URL in every angle. Our source_ids are global.
# Handing both to the model unremapped is what made it echo local numbers.
_MARKER = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def _remap_inline_citations(content: str, source_ids: List[int]) -> str:
    """Rewrite an angle's local [n] markers to the global ids it was assigned.

    A marker outside the angle's source range is not a citation (a footnote, a
    literal array index, a version number in brackets) and is left untouched.
    """
    if not source_ids:
        return content

    def sub(match: re.Match) -> str:
        parts = [p.strip() for p in match.group(1).split(",")]
        mapped = []
        for part in parts:
            local = int(part)
            if not 1 <= local <= len(source_ids):
                return match.group(0)  # out of range: not ours to renumber
            mapped.append(str(source_ids[local - 1]))
        joiner = ", " if "," in match.group(1) else ""
        if joiner:
            return "[" + joiner.join(mapped) + "]"
        return "[" + mapped[0] + "]"

    return _MARKER.sub(sub, content)


def build_decompose_prompt(question: str, n: int) -> str:
    """Ask the model to split a research question into n focused sub-questions."""
    return (
        "You are planning a web-research task. Break the question below into "
        f"exactly {n} focused, non-overlapping sub-questions that together give "
        "thorough coverage. Each sub-question must be independently searchable.\n\n"
        f"Question: {question.strip()}\n\n"
        'Respond with ONLY a JSON array of strings, e.g. ["...", "..."]. No prose.'
    )


def build_reason_prompt(question: str, corpus: List[Dict],
                        sources: Optional[List[Dict]] = None) -> str:
    """Ask the model to synthesize and per-claim verify the gathered corpus.

    corpus items: {"angle": str, "content": str, "source_ids": [int]}.
    sources items: {"id": int, "url": str, ...} — optional, but pass it: an id
    shown beside its URL is one the model can anchor, and an id shown bare is
    one it renumbers.

    Every angle's inline markers are remapped to global ids before the model
    sees them, so [n] in the corpus and source_ids in the reply mean the same
    source. Without that remap they did not (measured 2026-08-22).
    """
    url_for = {s["id"]: s.get("url", "") for s in (sources or []) if "id" in s}

    blocks = []
    for item in corpus:
        sids = item.get("source_ids", [])
        if url_for:
            listing = "\n".join(f"  [{i}] {url_for.get(i, '(url unrecorded)')}" for i in sids)
        else:
            listing = "  " + ", ".join(f"[{i}]" for i in sids)
        content = _remap_inline_citations(item["content"], sids)
        blocks.append(f"### Angle: {item['angle']}\nSources:\n{listing}\n\n{content}")
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
        "the corpus.\n"
        "The ids are GLOBAL and continue across angles — an angle may start at "
        "[41]. Cite the id printed in the corpus, never a position within an "
        "angle, and never renumber from 1.\n\n"
        f"Respond with ONLY a JSON object matching this schema (no prose, no code fence):\n{schema}"
    )
