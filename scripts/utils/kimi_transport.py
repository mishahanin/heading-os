"""Research-only Kimi transport (ollama OpenAI-compat).

Separate from scripts/kimi-consult.py (the council wrapper) by design: this
transport carries NO 31C/council prompt coupling, so deep-research never leaks
business context into the third-party cloud. Reproduces the empty/length
truncation retry that kimi-k2.6 (a thinking model) requires.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.api import load_api_key  # noqa: E402

DEFAULT_MODEL = "kimi-k2.6:cloud"
OLLAMA_BASE_URL = "http://localhost:11434/v1"
RETRY_CEILING = 16384


def _make_client(api_key, timeout=120.0):
    """Build the OpenAI SDK client pointed at ollama. Isolated for test patching."""
    from openai import OpenAI
    return OpenAI(api_key=api_key, base_url=OLLAMA_BASE_URL, timeout=timeout)


def reason(prompt, model=DEFAULT_MODEL, temperature=0.3, max_tokens=8192, timeout=120.0):
    """Send prompt to Kimi, return the visible answer text.

    Raises RuntimeError on missing key, API failure, or genuine empty/truncated
    output. Low default temperature (0.3): research reasoning wants determinism.
    timeout (seconds) is forwarded to the client; raise it for large reasoning
    prompts where cloud latency makes the 120s default too tight.
    """
    api_key = load_api_key("OLLAMA_API_KEY", required=False)
    if not api_key:
        raise RuntimeError("OLLAMA_API_KEY is missing from .env. Add it before running deep-research-advance.")

    client = _make_client(api_key, timeout=timeout)

    def _call(tok_budget):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=tok_budget,
            )
        except Exception as e:  # transport/API errors normalised for the caller
            raise RuntimeError(f"Kimi API call failed: {e}") from e
        if not resp.choices:
            raise RuntimeError("Kimi returned no choices.")
        ch = resp.choices[0]
        return (ch.message.content or ""), ch.finish_reason

    content, finish_reason = _call(max_tokens)
    if content.strip():
        return content

    if finish_reason == "length":
        ceiling = max(max_tokens * 2, RETRY_CEILING)
        content, _fr = _call(ceiling)
        if content.strip():
            return content
        raise RuntimeError(
            f"Kimi exhausted its token budget ({ceiling}) in the reasoning phase "
            "without a visible answer (finish_reason=length)."
        )
    if finish_reason == "content_filter":
        raise RuntimeError("Kimi returned empty: blocked by safety filters (content_filter).")
    raise RuntimeError(f"Kimi returned an empty answer (finish_reason={finish_reason}).")
