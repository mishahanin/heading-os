#!/usr/bin/env python3
"""
grok-consult.py - Council second-opinion API wrapper.

Calls Grok through the local CLIProxyAPI proxy seam (scripts/utils/proxy_transport)
and returns the response on stdout. Pure API wrapper - no disk writes, no
orchestration logic. The /council skill handles inputs, output formatting,
and transcript persistence.

Transport, auth, retry, and error classification all live in proxy_transport.call_model;
this module is a thin delegate that owns only the Grok-specific prompt/CLI surface.

Update DEFAULT_MODEL when xAI ships a new flagship model (via
config/council-models.json, see scripts/council-models.py --set).

Usage:
  python scripts/grok-consult.py --mode independent --question "..." [--context "..."]
  python scripts/grok-consult.py --mode critique    --draft "..."    [--context "..."]
  python scripts/grok-consult.py --mode independent --question "..." --model grok-4.5

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
# pin with: python scripts/council-models.py --set grok=<new>. Falls back to the
# baseline in scripts/utils/council_models.py if the config is missing.
DEFAULT_MODEL = get_model("grok")
DEFAULT_TEMPERATURE = 0.7              # Independent mode: room to reason creatively
DEFAULT_CRITIQUE_TEMPERATURE = 0.4     # Critique mode: more deterministic, less paraphrasing
DEFAULT_MAX_TOKENS = 8192


# ============================================================
# Grok API call
# ============================================================

def consult_grok(
    prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: Optional[float] = None,
) -> str:
    """Send the council prompt to Grok through the proxy; return the answer text.

    `timeout` overrides the proxy socket timeout (seconds). Leave None to inherit
    proxy_transport.DEFAULT_TIMEOUT. grok-4.5 is a thinking model, so a large
    critique can exceed the default in the reasoning phase, and the truncation
    error the transport raises tells the operator to raise this exact flag.
    """
    kwargs = {"temperature": temperature, "max_tokens": max_tokens}
    if timeout is not None:
        kwargs["timeout"] = timeout
    return call_model(model, prompt, **kwargs)


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
    p.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Socket timeout in seconds for ONE call. Omit to inherit the transport "
             "default. Raise it (e.g. 480) for a large grok-4.5 critique that would "
             "otherwise time out. A truncation retry makes a second call at up to "
             "twice this value, so the worst-case wall time is about 3x what you pass.",
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
        prompt = build_independent_prompt(args.question, args.context,
                                          length_hint=args.length_hint)
    else:
        prompt = build_critique_prompt(args.draft, args.context,
                                       length_hint=args.length_hint)

    try:
        response = consult_grok(
            prompt,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
        )
    except RuntimeError as e:
        msg = str(e)
        if "is missing from .env" in msg:  # narrow sentinel: missing-key path only, NOT auth failures
            print(f"{RED}Error:{RESET} {msg}", file=sys.stderr)
            return 2
        print(f"{RED}Error:{RESET} {msg}", file=sys.stderr)
        return 3
    except Exception as e:  # noqa: BLE001 - the exit code IS the contract
        # Exit 3 is documented above as "API call failed", and the /council
        # skill is written against these codes. A catch limited to RuntimeError
        # let anything the proxy layer did not wrap escape as a traceback and
        # exit 1, a code this docstring does not define. Both siblings gained
        # this branch and this file did not, which is the shape that keeps
        # happening here: the fix reached two of three copies. The type is named
        # so an unexpected failure stays diagnosable.
        print(f"{RED}Error:{RESET} unexpected {type(e).__name__} from the proxy "
              f"call: {e}", file=sys.stderr)
        return 3

    # Print response to stdout for the skill to capture
    print(response)
    return 0


if __name__ == "__main__":
    sys.exit(main())
