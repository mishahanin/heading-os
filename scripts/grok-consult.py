#!/usr/bin/env python3
"""
grok-consult.py - Council second-opinion API wrapper.

Calls Grok via xAI's OpenAI-compatible API with a structured prompt and
returns the response on stdout. Pure API wrapper - no disk writes, no
orchestration logic. The /council skill handles inputs, output formatting,
and transcript persistence.

SDK: openai (pinned in requirements.txt). Uses xAI's OpenAI-compatible
endpoint at https://api.x.ai/v1 - the same SDK as for OpenAI's own API,
just pointed at xAI's base_url. Update DEFAULT_MODEL when xAI ships a
new flagship model.

Usage:
  python scripts/grok-consult.py --mode independent --question "..." [--context "..."]
  python scripts/grok-consult.py --mode critique    --draft "..."    [--context "..."]
  python scripts/grok-consult.py --mode independent --question "..." --model grok-3-mini

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

DEFAULT_MODEL = "grok-4.3"  # Launched 2026-05-04, Intelligence Index 53, 1M-token context, built-in reasoning
DEFAULT_TEMPERATURE = 0.7              # Independent mode: room to reason creatively
DEFAULT_CRITIQUE_TEMPERATURE = 0.4     # Critique mode: more deterministic, less paraphrasing
DEFAULT_MAX_TOKENS = 8192
# Hard ceiling for the single reasoning-truncation retry (see consult_grok). grok-4.3 has
# built-in reasoning; xAI keeps the chain-of-thought in a separate `reasoning_content` field,
# so in practice the visible answer is rarely starved (unlike kimi-k2.6). The retry is defensive
# parity with kimi-consult: if `content` ever comes back empty under finish_reason="length",
# retry once at this ceiling before raising an accurate truncation error.
RETRY_CEILING = 16384
XAI_BASE_URL = "https://api.x.ai/v1"


# ============================================================
# Grok API call
# ============================================================

def consult_grok(
    prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """
    Send the prompt to Grok via xAI's OpenAI-compatible API and return the response text.

    Uses the openai SDK pointed at XAI_BASE_URL. Pattern:
        client = OpenAI(api_key=..., base_url="https://api.x.ai/v1")
        client.chat.completions.create(model=..., messages=[...], ...)

    Raises RuntimeError on missing-key, auth, rate-limit/quota, timeout,
    invalid-arg, and other API errors. Error classification uses the
    OpenAI exception class hierarchy (AuthenticationError, RateLimitError,
    BadRequestError, APITimeoutError, InternalServerError) with a
    message-pattern fallback for non-classified APIError exceptions.

    Reasoning-model handling: grok-4.3 has built-in reasoning. The response is branched
    precisely on (content, finish_reason) — identical to kimi-consult — so an empty answer
    is never misattributed to a safety block:
      - content present                        -> return it (success; finish_reason="length"
                                                  here just means the answer itself was truncated).
      - empty + finish_reason="length"         -> retry ONCE at RETRY_CEILING, then raise an
                                                  accurate truncation error if still empty.
      - empty + finish_reason="content_filter" -> blocked by safety filters.
      - empty + any other finish_reason        -> genuinely empty answer.
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
    api_key = load_api_key("XAI_API_KEY", required=False)
    if not api_key:
        raise RuntimeError(
            "XAI_API_KEY is missing from .env. "
            "Add it before invoking the council."
        )

    # 60s timeout caps worst-case wall time when the council is dispatched in parallel.
    # The openai SDK default is 600s (10 min), which is unacceptable for /council UX.
    # If a slow Grok response is normal in practice, raise this — but never to default.
    client = OpenAI(api_key=api_key, base_url=XAI_BASE_URL, timeout=60.0)

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
                f"Grok API auth failed: {e}. "
                "Check XAI_API_KEY in .env (rotate if it was leaked)."
            ) from e
        except RateLimitError as e:
            raise RuntimeError(
                f"Grok API rate-limited or quota exceeded: {e}. "
                "Retry in 60 seconds, switch with --model grok-3-mini, "
                "or check billing on console.x.ai."
            ) from e
        except NotFoundError as e:
            raise RuntimeError(
                f"Grok API returned 404: {e}. "
                "Check --model spelling (e.g., grok-4, grok-3-mini)."
            ) from e
        except BadRequestError as e:
            raise RuntimeError(
                f"Grok API rejected the request: {e}. "
                "Check --model spelling and prompt content."
            ) from e
        except APITimeoutError as e:
            raise RuntimeError(
                f"Grok API timeout: {e}. "
                "Retry, or reduce --max-tokens / use --model grok-3-mini."
            ) from e
        except APIConnectionError as e:
            raise RuntimeError(
                f"Grok API connection failed: {e}. "
                "Check network connectivity or VPN. The xAI endpoint is api.x.ai."
            ) from e
        except InternalServerError as e:
            raise RuntimeError(
                f"Grok API server error: {e}. "
                "This is transient (e.g., 500 INTERNAL or 503 UNAVAILABLE). "
                "Retry in 30 seconds."
            ) from e
        except APIError as e:
            # Catch-all for less-common APIError subclasses
            raise RuntimeError(f"Grok API call failed: {e}") from e
        except Exception as e:
            # Network errors and other non-APIError exceptions. Use message-pattern fallback.
            msg = str(e).lower()
            if "timeout" in msg or "timed out" in msg:
                raise RuntimeError(
                    f"Grok API timeout: {e}. Retry or reduce --max-tokens."
                ) from e
            raise RuntimeError(f"Grok API call failed: {e}") from e

        if not response.choices:
            raise RuntimeError("Grok returned no choices in the response.")
        choice = response.choices[0]
        return (choice.message.content or ""), choice.finish_reason

    content, finish_reason = _call(max_tokens)
    if content.strip():
        return content

    # Empty content — disambiguate precisely by finish_reason (see docstring).
    if finish_reason == "length":
        # The token budget was spent on the reasoning phase before any answer.
        # Retry once at a strictly higher budget; more budget resolves this deterministically.
        ceiling = max(max_tokens * 2, RETRY_CEILING)
        if ceiling > max_tokens:
            content, finish_reason = _call(ceiling)
            if content.strip():
                return content
        raise RuntimeError(
            f"Grok exhausted its token budget ({ceiling}) in the reasoning phase without "
            "producing a visible answer (finish_reason=length). This is a thinking-model "
            "truncation, not a safety block — raise --max-tokens or simplify the prompt."
        )

    if finish_reason == "content_filter":
        raise RuntimeError(
            "Grok returned an empty response: blocked by safety filters "
            "(finish_reason=content_filter)."
        )

    raise RuntimeError(
        f"Grok returned an empty answer (finish_reason={finish_reason}). The model produced "
        "no visible content; retry or rephrase the prompt."
    )


# ============================================================
# CLI entry point
# ============================================================

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments and validate mode-specific requirements."""
    p = argparse.ArgumentParser(
        prog="grok-consult.py",
        description="Council: consult Grok for an independent second opinion.",
    )
    p.add_argument(
        "--mode",
        choices=["independent", "critique"],
        required=True,
        help="independent: Grok reasons fresh from the question. "
             "critique: Grok stress-tests a provided draft.",
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
        help=f"Grok model. Default: {DEFAULT_MODEL}",
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
    """Build the prompt for the requested mode, call Grok, print to stdout."""
    try:
        args = parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 1

    if args.mode == "independent":
        prompt = build_independent_prompt(args.question, args.context)
    else:
        prompt = build_critique_prompt(args.draft, args.context)

    try:
        response = consult_grok(
            prompt,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
    except RuntimeError as e:
        msg = str(e)
        if "XAI_API_KEY" in msg:
            print(f"{RED}Error:{RESET} {msg}", file=sys.stderr)
            return 2
        print(f"{RED}Error:{RESET} {msg}", file=sys.stderr)
        return 3

    # Print response to stdout for the skill to capture
    print(response)
    return 0


if __name__ == "__main__":
    sys.exit(main())
