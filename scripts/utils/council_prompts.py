"""Shared council prompt builders.

Single source of truth for the 31C context block and the independent/critique
prompt builders consumed by the council consult wrappers (gemini/grok/kimi).
Extracted 2026-06-18 per the TODO in grok-consult.py when the third model
(Kimi) was added. Pure functions — no I/O, unit-tested in tests/test_council_prompts.py.
"""
from __future__ import annotations

THIRTY_ONE_C_BLOCK = """\
You are advising the CEO of 31 Concept (31C). Quick context:
- The Tribe = 31C's people. NOT "team", "family", or "crew".
- ODUN.ONE = 31C's sovereign Deep Packet Intelligence (DPI+) platform.
- DPI+ = next-generation Deep Packet Intelligence; ODUN.ONE classifies encrypted traffic via metadata and AI, it does NOT decrypt.
- TrustONE = ODUN.ONE's separately-licensed DLP module for LLM traffic (client-side proxy with subscriber CA trust, NOT operator MITM).
- Five Core Principles: Proof of Value over PoC, Partnership for Life, Operate with Integrity, Deliver Under Pressure, Data Sovereignty Always.
- Sanctions-compliance constraint: 31C does not target sanctioned countries. Any suggestion that violates this is an existential risk.
- Operational vocabulary: heading, sea state, course correction, drift, state check, crunch mode, operational state.
"""


def build_independent_prompt(question: str, context: str = "") -> str:
    """Build the independent-perspective prompt for a council advisor.

    Frames the receiving model as a second-opinion advisor that reasons from
    first principles and reaches its own conclusion without deferring to any
    prior framing. ``context``, if non-empty, is appended under a ``## Context``
    section. Returns the full prompt string.
    """
    parts = [THIRTY_ONE_C_BLOCK.strip(), ""]
    parts.append("## Your role")
    parts.append(
        "You are a second-opinion advisor. The user is consulting you "
        "because they want an independent perspective. Reason from first "
        "principles. Do not defer to anyone else's framing or proposed "
        "answer. Reach your own conclusion."
    )
    parts.append("")
    parts.append("## Question")
    parts.append(question.strip())
    if context.strip():
        parts.append("")
        parts.append("## Context")
        parts.append(context.strip())
    parts.append("")
    parts.append("## Output")
    parts.append(
        "Reason through the problem. Provide your conclusion as a clear "
        "position with the reasoning behind it. State explicitly what you "
        "would do, what risks you see, and what assumptions you are "
        "making. Aim for 200-400 words."
    )
    return "\n".join(parts)


def build_critique_prompt(draft: str, context: str = "") -> str:
    """Build the critique prompt for a council advisor.

    Frames the receiving model as a critical reviewer whose job is to find
    flaws, missing angles, weak assumptions, and unstated risks in ``draft``.
    ``context``, if non-empty, is appended under a ``## Context`` section.
    Returns the full prompt string.
    """
    parts = [THIRTY_ONE_C_BLOCK.strip(), ""]
    parts.append("## Your role")
    parts.append(
        "You are a critical reviewer. The user has produced a draft "
        "(proposal, argument, message, decision). Your job is to find "
        "flaws, missing angles, weak assumptions, and unstated risks. "
        "Be direct. Be specific. Disagreement is more valuable than "
        "agreement here."
    )
    parts.append("")
    parts.append("## Draft to critique")
    parts.append(draft.strip())
    if context.strip():
        parts.append("")
        parts.append("## Context")
        parts.append(context.strip())
    parts.append("")
    parts.append("## Output")
    parts.append(
        "Identify the strongest objections to this draft. List the "
        "assumptions that, if wrong, would change the conclusion. Name "
        "the angles or evidence that are missing. End with: would you "
        "ship this as-is, or what would you change first? Aim for "
        "200-400 words."
    )
    return "\n".join(parts)
