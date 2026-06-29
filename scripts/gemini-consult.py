#!/usr/bin/env python3
"""
gemini-consult.py - Council second-opinion API wrapper.

Calls Gemini with a structured prompt and returns the response on stdout.
Pure API wrapper - no disk writes, no orchestration logic. The /council
skill handles inputs, output formatting, and transcript persistence.

SDK: google-genai==2.2.0 (pinned in requirements.txt). The legacy
google-generativeai SDK is NOT used. Update DEFAULT_MODEL and the pinned
version in requirements.txt when Google releases a new Pro tier or SDK release.

Usage:
  python scripts/gemini-consult.py --mode independent --question "..." [--context "..."]
  python scripts/gemini-consult.py --mode critique    --draft "..."    [--context "..."]
  python scripts/gemini-consult.py --mode independent --question "..." --model gemini-2.5-flash

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

DEFAULT_MODEL = "gemini-3.5-flash"  # GA 2026-05-19, flagship intelligence at Flash speed, Dynamic Thinking on by default
DEFAULT_TEMPERATURE = 0.7              # Independent mode: room to reason creatively
DEFAULT_CRITIQUE_TEMPERATURE = 0.4     # Critique mode: more deterministic, less paraphrasing
DEFAULT_MAX_TOKENS = 8192


# ============================================================
# Gemini API call
# ============================================================

def consult_gemini(
    prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """
    Send the prompt to Gemini and return the response text.

    Uses the google-genai SDK (NOT the legacy google-generativeai). Pattern:
        client = genai.Client(api_key=...)
        client.models.generate_content(model=..., contents=..., config=...)

    We configure max_output_tokens and temperature explicitly. Raises
    RuntimeError on missing-key, auth, rate-limit/quota, timeout, invalid-arg,
    and other API errors. Error classification uses structured APIError.code/status
    attributes when available, with message-pattern fallback for non-APIError
    exceptions (e.g., network errors).
    """
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError as e:
        raise RuntimeError(
            "google-genai SDK is not installed. "
            "Run: pip install google-genai==2.2.0 (see requirements.txt)"
        ) from e

    # required=False so the missing-key path raises RuntimeError here instead of
    # sys.exit() inside load_api_key. main() maps the RuntimeError to exit code 2.
    api_key = load_api_key("GEMINI_API_KEY", required=False)
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing from .env. "
            "Add it before invoking the council."
        )

    client = genai.Client(api_key=api_key)

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
    except Exception as e:
        # Hybrid classification: structured google-genai APIError attributes (.code, .status)
        # when available, falling back to message-pattern for non-APIError exceptions.
        # The message-pattern fallback catches network-level errors and SDK-version
        # variations where the structured attributes might not be populated.
        code = getattr(e, "code", None)
        status = getattr(e, "status", "") or ""
        msg = str(e)

        if code == 429 or "RESOURCE_EXHAUSTED" in status or "quota" in msg.lower():
            raise RuntimeError(
                f"Gemini API rate-limited or quota exceeded: {e}. "
                "Retry in 60 seconds, rerun with --model gemini-2.5-flash, "
                "or enable billing on the Google AI Studio project."
            ) from e
        if code in (401, 403) or any(s in status for s in ("PERMISSION_DENIED", "UNAUTHENTICATED")):
            raise RuntimeError(
                f"Gemini API auth failed: {e}. "
                "Check GEMINI_API_KEY in .env (rotate if it was leaked)."
            ) from e
        if code == 504 or "DEADLINE_EXCEEDED" in status or "timeout" in msg.lower():
            raise RuntimeError(
                f"Gemini API timeout: {e}. "
                "Retry, or reduce --max-tokens / use --model gemini-2.5-flash."
            ) from e
        if code == 400 or "INVALID_ARGUMENT" in status:
            raise RuntimeError(
                f"Gemini API rejected the request: {e}. "
                "Check --model spelling and prompt content."
            ) from e
        if isinstance(code, int) and 500 <= code < 600:
            raise RuntimeError(
                f"Gemini API server error ({code}): {e}. "
                "This is transient (e.g., 500 INTERNAL or 503 UNAVAILABLE). "
                "Retry in 30 seconds."
            ) from e
        raise RuntimeError(f"Gemini API call failed: {e}") from e

    if not response.text:
        raise RuntimeError(
            "Gemini returned an empty response. "
            "This often means the prompt was blocked by safety filters."
        )

    return response.text


# ============================================================
# CLI entry point
# ============================================================

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments and validate mode-specific requirements."""
    p = argparse.ArgumentParser(
        prog="gemini-consult.py",
        description="Council: consult Gemini for an independent second opinion.",
    )
    p.add_argument(
        "--mode",
        choices=["independent", "critique"],
        required=True,
        help="independent: Gemini reasons fresh from the question. "
             "critique: Gemini stress-tests a provided draft.",
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
        help=f"Gemini model. Default: {DEFAULT_MODEL}",
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
    """Build the prompt for the requested mode, call Gemini, print to stdout."""
    try:
        args = parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 1

    if args.mode == "independent":
        prompt = build_independent_prompt(args.question, args.context)
    else:
        prompt = build_critique_prompt(args.draft, args.context)

    try:
        response = consult_gemini(
            prompt,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
    except RuntimeError as e:
        msg = str(e)
        if "GEMINI_API_KEY" in msg:
            print(f"{RED}Error:{RESET} {msg}", file=sys.stderr)
            return 2
        print(f"{RED}Error:{RESET} {msg}", file=sys.stderr)
        return 3

    # Print response to stdout for the skill to capture
    print(response)
    return 0


if __name__ == "__main__":
    sys.exit(main())
