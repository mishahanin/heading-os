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

# A council consult wants a compact position, not an essay, so both builders ask
# for one. It became a parameter on 2026-08-23: the 2026-08-23 engine audit sent
# its per-file shards through `kimi-consult --mode independent`, and every shard
# inherited this cap while its own question said "list EVERY defect". The cap is
# right for the thing these builders were written for and wrong for enumeration,
# so the caller now chooses. Pass "" (or None) to omit the sentence entirely.
DEFAULT_LENGTH_HINT = "Aim for 200-400 words."


def _with_hint(instruction: str, length_hint: str | None) -> str:
    """Append the length sentence to an Output instruction, or leave it off."""
    hint = (length_hint or "").strip()
    return f"{instruction} {hint}" if hint else instruction


def build_independent_prompt(question: str, context: str = "",
                             length_hint: str | None = DEFAULT_LENGTH_HINT) -> str:
    """Build the independent-perspective prompt for a council advisor.

    Frames the receiving model as a second-opinion advisor that reasons from
    first principles and reaches its own conclusion without deferring to any
    prior framing. ``context``, if non-empty, is appended under a ``## Context``
    section. ``length_hint`` is the closing length instruction; pass ``""`` or
    ``None`` for an enumerating task that must not be capped. Returns the full
    prompt string.
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
        _with_hint(
            "Reason through the problem. Provide your conclusion as a clear "
            "position with the reasoning behind it. State explicitly what you "
            "would do, what risks you see, and what assumptions you are "
            "making.",
            length_hint,
        )
    )
    return "\n".join(parts)


def build_critique_prompt(draft: str, context: str = "",
                          length_hint: str | None = DEFAULT_LENGTH_HINT) -> str:
    """Build the critique prompt for a council advisor.

    Frames the receiving model as a critical reviewer whose job is to find
    flaws, missing angles, weak assumptions, and unstated risks in ``draft``.
    ``context``, if non-empty, is appended under a ``## Context`` section.
    ``length_hint`` is the closing length instruction; pass ``""`` or ``None``
    for an enumerating task that must not be capped. Returns the full prompt
    string.
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
        _with_hint(
            "Identify the strongest objections to this draft. List the "
            "assumptions that, if wrong, would change the conclusion. Name "
            "the angles or evidence that are missing. End with: would you "
            "ship this as-is, or what would you change first?",
            length_hint,
        )
    )
    return "\n".join(parts)
