#!/usr/bin/env python3
"""
gemini-consult.py - Council second-opinion API wrapper.

Calls Gemini through the local CLIProxyAPI proxy seam (scripts/utils/proxy_transport)
and returns the response on stdout. Pure API wrapper - no disk writes, no
orchestration logic. The /council skill handles inputs, output formatting,
and transcript persistence.

Transport, auth, retry, and error classification all live in proxy_transport.call_model;
this module is a thin delegate that owns only the Gemini-specific prompt/CLI surface.
The direct vendor SDK is no longer used - the proxy fronts the subscription instead.

Update DEFAULT_MODEL when Google ships a new flagship tier (via
config/council-models.json, see scripts/council-models.py --set).

Usage:
  python scripts/gemini-consult.py --mode independent --question "..." [--context "..."]
  python scripts/gemini-consult.py --mode critique    --draft "..."    [--context "..."]
  python scripts/gemini-consult.py --mode independent --question "..." --model gemini-3-flash

Exit codes:
  0  success, response printed to stdout
  2  argument error or missing API key (argparse + custom validation share this code)
  3  API call failed (network, rate limit, invalid model, etc.)

Tests: tests/test_a_spawn_that_reported_a_daemon_it_never_confirmed.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

# Workspace imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import RED, RESET  # noqa: E402
from scripts.utils.council_models import get_model  # noqa: E402
from scripts.utils.council_prompts import (  # noqa: E402
    DEFAULT_LENGTH_HINT,
    build_independent_prompt,
    build_critique_prompt,
)
from scripts.utils.proxy_transport import call_model  # noqa: E402

# ============================================================
# Configuration
# ============================================================

# Resolved from config/council-models.json (single source of truth). Bump the
# pin with: python scripts/council-models.py --set gemini=<new>. Falls back to
# the baseline in scripts/utils/council_models.py if the config is missing.
DEFAULT_MODEL = get_model("gemini")
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
    """Send the council prompt to Gemini through the proxy; return the answer text."""
    return call_model(model, prompt, temperature=temperature, max_tokens=max_tokens)


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
        "--length-hint",
        default=DEFAULT_LENGTH_HINT,
        help="Closing length instruction appended to the Output section. "
             f"Default: {DEFAULT_LENGTH_HINT!r}. Pass an empty string to omit it "
             "for an enumerating task (\"list every defect\") that must not be "
             "capped at a word count.",
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
        prompt = build_independent_prompt(args.question, args.context,
                                          length_hint=args.length_hint)
    else:
        prompt = build_critique_prompt(args.draft, args.context,
                                       length_hint=args.length_hint)

    try:
        response = consult_gemini(
            prompt,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
    except RuntimeError as e:
        msg = str(e)
        if "is missing from .env" in msg:  # narrow sentinel: missing-key path only, NOT auth failures
            print(f"{RED}Error:{RESET} {msg}", file=sys.stderr)
            return 2
        print(f"{RED}Error:{RESET} {msg}", file=sys.stderr)
        return 3
    except Exception as e:  # noqa: BLE001 - the exit code IS the contract
        # Exit 3 is documented as "API call failed", and the /council skill is
        # written against these codes. A catch limited to RuntimeError let
        # anything the proxy layer did not wrap -- a ValueError from a malformed
        # response, an unwrapped OSError -- escape as a traceback and exit 1,
        # which the skill reads as neither "API failed" nor "ok". The type is
        # named so an unexpected failure is still diagnosable, rather than
        # disappearing into a generic message.
        print(f"{RED}Error:{RESET} unexpected {type(e).__name__} from the proxy "
              f"call: {e}", file=sys.stderr)
        return 3

    # Print response to stdout for the skill to capture
    print(response)
    return 0


if __name__ == "__main__":
    sys.exit(main())
