#!/usr/bin/env python3
"""
kimi-consult.py - Council second-opinion API wrapper.

Calls Kimi via ollama's OpenAI-compatible endpoint and returns the response on
stdout. Pure API wrapper - no disk writes, no orchestration logic. The /council
skill handles inputs, output formatting, and transcript persistence.

Transport: ollama OpenAI-compatible endpoint at http://localhost:11434/v1.
Auth key: OLLAMA_API_KEY (loaded from .env). For cloud-routed models served by
a locally-running ollama daemon, some installs accept any non-empty string; the
value in .env is used verbatim.

Update DEFAULT_MODEL when Moonshot ships a new flagship Kimi variant.

Usage:
  python scripts/kimi-consult.py --mode independent --question "..." [--context "..."]
  python scripts/kimi-consult.py --mode critique    --draft "..."    [--context "..."]
  python scripts/kimi-consult.py --mode independent --question "..." --model kimi-k2.6:cloud

Exit codes:
  0  success, response printed to stdout
  2  argument error or missing API key (argparse + custom validation share this code)
  3  API call failed (network, rate limit, invalid model, etc.)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

# Workspace imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.api import load_api_key  # noqa: E402
from scripts.utils.colors import RED, RESET  # noqa: E402
from scripts.utils.council_prompts import (  # noqa: E402
    THIRTY_ONE_C_BLOCK,
    build_independent_prompt,
    build_critique_prompt,
)

# ============================================================
# Configuration
# ============================================================

DEFAULT_MODEL = "kimi-k2.6:cloud"   # Moonshot Kimi via local ollama cloud routing
DEFAULT_TEMPERATURE = 0.7              # Independent mode: room to reason creatively
DEFAULT_CRITIQUE_TEMPERATURE = 0.4     # Critique mode: more deterministic, less paraphrasing
DEFAULT_MAX_TOKENS = 8192
# Hard ceiling for the single reasoning-truncation retry (see consult_kimi). kimi-k2.6
# is a thinking model: its chain-of-thought consumes the same token budget as the answer,
# so a too-small budget can be entirely spent on reasoning, leaving content empty. Empirically
# `think:false`/`reasoning_effort` are ignored by the ollama cloud proxy, so max_tokens is the
# only lever — when an answer is starved we retry once at this ceiling before erroring.
RETRY_CEILING = 16384
OLLAMA_BASE_URL = "http://localhost:11434/v1"


# ============================================================
# Kimi API call
# ============================================================

def consult_kimi(
    prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """
    Send the prompt to Kimi via ollama's OpenAI-compatible API and return the response text.

    Uses the openai SDK pointed at OLLAMA_BASE_URL. Pattern:
        client = OpenAI(api_key=..., base_url="http://localhost:11434/v1")
        client.chat.completions.create(model=..., messages=[...], ...)

    Raises RuntimeError on missing-key, auth, rate-limit/quota, timeout,
    invalid-arg, and other API errors. Error classification uses the
    OpenAI exception class hierarchy (AuthenticationError, RateLimitError,
    BadRequestError, APITimeoutError, InternalServerError) with a
    message-pattern fallback for non-classified APIError exceptions.

    Reasoning-model handling: kimi-k2.6 emits its chain-of-thought into a separate
    `reasoning` field BEFORE the visible answer, drawing on the same max_tokens budget.
    The response is therefore branched precisely on (content, finish_reason):
      - content present                  -> return it (success; a finish_reason="length"
                                            here means the answer itself was truncated, which
                                            is still usable for the council's bounded asks).
      - empty + finish_reason="length"   -> the budget was spent entirely on reasoning before
                                            any answer. Retry ONCE at RETRY_CEILING (more budget
                                            resolves this deterministically); if still empty,
                                            raise an accurate truncation error — never a safety
                                            claim.
      - empty + finish_reason="content_filter" -> blocked by safety filters.
      - empty + any other finish_reason  -> genuinely empty answer.
    """
    try:
        from openai import (
            OpenAI,
            APIError,
            APIConnectionError,
            AuthenticationError,
            BadRequestError,
            NotFoundError,
            RateLimitError,
            APITimeoutError,
            InternalServerError,
        )
    except ImportError as e:
        raise RuntimeError(
            "openai SDK is not installed. "
            "Run: pip install openai (see requirements.txt for pinned version)"
        ) from e

    # required=False so the missing-key path raises RuntimeError here instead of
    # sys.exit() inside load_api_key. main() maps the RuntimeError to exit code 2.
    api_key = load_api_key("OLLAMA_API_KEY", required=False)
    if not api_key:
        raise RuntimeError(
            "OLLAMA_API_KEY is missing from .env. "
            "Add it before invoking the council."
        )

    # 120s timeout: Kimi reasons deeply and cloud routing adds latency.
    # The openai SDK default is 600s (10 min), which is unacceptable for /council UX.
    # If slow Kimi responses are normal in practice, raise this — but never to default.
    client = OpenAI(api_key=api_key, base_url=OLLAMA_BASE_URL, timeout=120.0)

    def _call(tok_budget: int) -> tuple[str, Optional[str]]:
        """One API round-trip. Returns (content, finish_reason). Raises RuntimeError
        on transport/API errors (classified via the OpenAI exception hierarchy)."""
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=tok_budget,
            )
        except AuthenticationError as e:
            raise RuntimeError(
                f"Kimi API auth failed: {e}. "
                "Check OLLAMA_API_KEY in .env (rotate if it was leaked)."
            ) from e
        except RateLimitError as e:
            raise RuntimeError(
                f"Kimi API rate-limited or quota exceeded: {e}. "
                "Retry in 60 seconds, or check ollama list shows kimi-k2.6:cloud."
            ) from e
        except NotFoundError as e:
            raise RuntimeError(
                f"Kimi API returned 404: {e}. "
                "Check --model spelling (run `ollama list` to see available models)."
            ) from e
        except BadRequestError as e:
            raise RuntimeError(
                f"Kimi API rejected the request: {e}. "
                "Check --model spelling and prompt content."
            ) from e
        except APITimeoutError as e:
            raise RuntimeError(
                f"Kimi API timeout: {e}. "
                "Retry, or reduce --max-tokens / check ollama is running at localhost:11434."
            ) from e
        except APIConnectionError as e:
            raise RuntimeError(
                f"Kimi API connection failed: {e}. "
                "Check that ollama is running (ollama serve) and listening at localhost:11434."
            ) from e
        except InternalServerError as e:
            raise RuntimeError(
                f"Kimi API server error: {e}. "
                "This is transient (e.g., 500 INTERNAL or 503 UNAVAILABLE). "
                "Retry in 30 seconds."
            ) from e
        except APIError as e:
            # Catch-all for less-common APIError subclasses
            raise RuntimeError(f"Kimi API call failed: {e}") from e
        except Exception as e:
            # Network errors and other non-APIError exceptions. Use message-pattern fallback.
            msg = str(e).lower()
            if "timeout" in msg or "timed out" in msg:
                raise RuntimeError(
                    f"Kimi API timeout: {e}. Retry or reduce --max-tokens."
                ) from e
            raise RuntimeError(f"Kimi API call failed: {e}") from e

        if not response.choices:
            raise RuntimeError("Kimi returned no choices in the response.")
        choice = response.choices[0]
        return (choice.message.content or ""), choice.finish_reason

    content, finish_reason = _call(max_tokens)
    if content.strip():
        return content

    # Empty content — disambiguate precisely by finish_reason (see docstring).
    if finish_reason == "length":
        # The token budget was spent entirely on the reasoning phase before any answer.
        # Retry once at a strictly higher budget; more budget resolves this deterministically.
        ceiling = max(max_tokens * 2, RETRY_CEILING)
        if ceiling > max_tokens:
            content, finish_reason = _call(ceiling)
            if content.strip():
                return content
        raise RuntimeError(
            f"Kimi exhausted its token budget ({ceiling}) in the reasoning phase without "
            "producing a visible answer (finish_reason=length). This is a thinking-model "
            "truncation, not a safety block — raise --max-tokens or simplify the prompt."
        )

    if finish_reason == "content_filter":
        raise RuntimeError(
            "Kimi returned an empty response: blocked by safety filters "
            "(finish_reason=content_filter)."
        )

    raise RuntimeError(
        f"Kimi returned an empty answer (finish_reason={finish_reason}). The model produced "
        "no visible content; retry or rephrase the prompt."
    )


# ============================================================
# CLI entry point
# ============================================================

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments and validate mode-specific requirements."""
    p = argparse.ArgumentParser(
        prog="kimi-consult.py",
        description="Council: consult Kimi for an independent second opinion.",
    )
    p.add_argument(
        "--mode",
        choices=["independent", "critique"],
        required=True,
        help="independent: Kimi reasons fresh from the question. "
             "critique: Kimi stress-tests a provided draft.",
    )
    p.add_argument(
        "--question",
        default="",
        help="The question to consult on (independent mode).",
    )
    p.add_argument(
        "--draft",
        default="",
        help="The draft to critique (critique mode).",
    )
    p.add_argument(
        "--context",
        default="",
        help="Additional context for either mode.",
    )
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Kimi model. Default: {DEFAULT_MODEL}",
    )
    p.add_argument(
        "--temperature",
        type=float,
        default=None,
        help=(
            f"Sampling temperature. Default: {DEFAULT_TEMPERATURE} for independent mode, "
            f"{DEFAULT_CRITIQUE_TEMPERATURE} for critique mode (more deterministic)."
        ),
    )
    p.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help=f"Max output tokens. Default: {DEFAULT_MAX_TOKENS}",
    )
    args = p.parse_args(argv)

    if args.mode == "independent" and not args.question.strip():
        p.error("--question is required in independent mode")
    if args.mode == "critique" and not args.draft.strip():
        p.error("--draft is required in critique mode")

    # Per-mode temperature default. Critique mode benefits from more deterministic output
    # (less creative paraphrasing, more focused fault-finding).
    if args.temperature is None:
        args.temperature = (
            DEFAULT_CRITIQUE_TEMPERATURE if args.mode == "critique" else DEFAULT_TEMPERATURE
        )

    return args


def main(argv: Optional[list[str]] = None) -> int:
    """Build the prompt for the requested mode, call Kimi, print to stdout."""
    try:
        args = parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 1

    if args.mode == "independent":
        prompt = build_independent_prompt(args.question, args.context)
    else:
        prompt = build_critique_prompt(args.draft, args.context)

    try:
        response = consult_kimi(
            prompt,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
    except RuntimeError as e:
        msg = str(e)
        if "OLLAMA_API_KEY" in msg:
            print(f"{RED}Error:{RESET} {msg}", file=sys.stderr)
            return 2
        print(f"{RED}Error:{RESET} {msg}", file=sys.stderr)
        return 3

    # Print response to stdout for the skill to capture
    print(response)
    return 0


if __name__ == "__main__":
    sys.exit(main())
