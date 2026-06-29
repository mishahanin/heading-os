#!/usr/bin/env python3
"""Advisory pre-approval critique for a queued outbound draft (R5b).

A single bounded model call that reads a drafted send (subject + body, plus the
recipient address) and returns a structured, advisory second opinion scoped to
the lethal-trifecta-relevant risks of *sending*: private-data exposure to an
external recipient, recipient/subject mismatch, unverified or fabricated factual
claims, and voice/tone. It is ADVISORY ONLY - it returns a dict the caller may
stamp onto a card via ``action_queue.annotate_card`` (which cannot change
status). It never sends, approves, or dismisses anything.

This is NOT ``/evaluate`` (that grades workspace artifacts against workspace
standards; it has no notion of an outbound email, a recipient, tone, or data
leak). This is a thin, purpose-built critic for a send.

Design (plan 2026-06-04, R5b Decisions 2/4/8):

- ``critique_draft`` NEVER raises. On a missing SDK, missing API key, timeout,
  empty body, or any exception it returns ``None`` (graceful skip) - the caller
  then leaves the card uncritiqued, to be retried on a later tick. This mirrors
  the resilience contract of ``daemon_heartbeat.beat`` / ``dead_letter.record``.
- One bounded call on a cheap model (Haiku-class default), low ``max_tokens``.
- Mirrors the model-call shape of ``scripts/run-skill-eval.call_skill``
  (lazy ``anthropic`` import, ``@observe`` wrapper, key via ``load_env``).

Smoke test (no API call unless a key is present): ``python scripts/utils/draft_critique.py``
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from scripts.utils import trace
from scripts.utils.observability import observe

# Cheap-model default and alias map (mirrors scripts/run-skill-eval.py).
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MODEL_ALIAS = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-7",
}

_RISK_LEVELS = ("low", "medium", "high")

_SYSTEM_PROMPT = (
    "You are a skeptical reviewer giving a SECOND OPINION on an outbound email "
    "draft before a human decides whether to send it. You do not send anything; "
    "you only flag risk. Focus strictly on the risks of SENDING this message:\n"
    "1. Private-data exposure: does the body leak internal, confidential, or "
    "personal data to what looks like an external recipient?\n"
    "2. Recipient/subject mismatch: does the body's content, salutation, or ask "
    "fit the recipient address and the subject line?\n"
    "3. Unverified or fabricated claims: specific figures, names, dates, or "
    "commitments that read as invented or unconfirmed.\n"
    "4. Voice/tone: anything off for senior, direct, professional executive "
    "correspondence (over-familiar, hedged, sloppy, or promotional).\n\n"
    "Respond with ONLY a JSON object, no prose, no markdown fences:\n"
    '{"risk": "low|medium|high", "flags": ["short flag", ...], '
    '"summary": "one sentence, <=300 chars"}\n'
    "risk is the overall send risk. flags is a short list (may be empty) naming "
    "concrete issues. If the draft looks safe to send, return risk \"low\" with "
    "an empty flags list."
)


def _resolve_model(model: str | None) -> str:
    if not model:
        return _DEFAULT_MODEL
    return _MODEL_ALIAS.get(model, model)


def _extract_text(response) -> str:
    out = ""
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            out += block.text
    return out


def _parse(text: str) -> dict | None:
    """Parse the model's JSON answer. Tolerates surrounding markdown fences.
    Returns None on any malformed output (caller treats as graceful skip)."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        # strip ```json ... ``` fences
        s = s.split("```", 2)
        s = s[1] if len(s) > 1 else text
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    s = s.strip().strip("`").strip()
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


@observe()
def critique_draft(subject, body, recipient=None, *, model=None) -> dict | None:
    """Return an advisory critique of an outbound draft, or None (graceful skip).

    ``recipient`` is the destination address string; callers feed it from the
    Action Queue card's ``to`` field. Result shape:
    ``{"risk": "low|medium|high", "flags": [...], "summary": str,
       "model": str, "at": iso8601, "trace_id": str}``.
    NEVER raises - any failure returns None.
    """
    try:
        body_text = (body or "").strip()
        if not body_text:
            return None

        import os
        from scripts.utils.workspace import load_env
        load_env()
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return None

        import anthropic  # lazy import - graceful skip if SDK absent
        client = anthropic.Anthropic(api_key=api_key)
        resolved = _resolve_model(model)

        user = (
            f"Recipient: {recipient or '(unknown)'}\n"
            f"Subject: {subject or '(none)'}\n\n"
            f"Body:\n{body_text}"
        )
        response = client.messages.create(
            model=resolved,
            max_tokens=600,
            system=[{
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user}],
            timeout=30,
        )
        parsed = _parse(_extract_text(response))
        if parsed is None:
            return None

        risk = parsed.get("risk")
        if risk not in _RISK_LEVELS:
            risk = "medium"  # unknown/garbled risk -> conservative
        flags = parsed.get("flags") or []
        if not isinstance(flags, list):
            flags = [str(flags)]
        flags = [str(f) for f in flags][:10]
        summary = str(parsed.get("summary") or "")[:300]

        return {
            "risk": risk,
            "flags": flags,
            "summary": summary,
            "model": resolved,
            "at": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace.get() or "-",
        }
    except Exception:
        # Advisory only: a critique failure must never propagate. Skip silently;
        # the card stays uncritiqued and is retried on the next sweep tick.
        return None


if __name__ == "__main__":
    # Smoke test. Makes a real call only if ANTHROPIC_API_KEY is set; otherwise
    # exercises the graceful-skip path (returns None, no raise).
    res = critique_draft(
        subject="Re: Q2 pricing",
        body="Hi Jane, as discussed our list price is 347,850 AED for the sovereign tier. Best, Misha",
        recipient="jane@acme.com",
    )
    print(json.dumps(res, indent=2) if res else "None (graceful skip - no SDK/key, or empty body)")
