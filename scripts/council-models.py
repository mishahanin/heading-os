#!/usr/bin/env python3
"""
council-models.py - view and bump the /council model pins.

The /council skill consults Gemini, Grok, and Kimi. Their flagship model ids
live in ONE file, config/council-models.json, resolved at runtime by
scripts/utils/council_models.py. This CLI is the console-first way to keep
/council on the latest models: no code edit, one command per bump.

Usage:
  python scripts/council-models.py --show
  python scripts/council-models.py --set grok=grok-4.6
  python scripts/council-models.py --set grok=grok-4.6 kimi=k3
  python scripts/council-models.py --check              # freshness check (human)
  python scripts/council-models.py --check --quiet      # one-line nudge, empty when all OK
  python scripts/council-models.py --check --json       # structured findings

Exit codes:
  0  success
  2  argument error (unknown provider, malformed pair)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import BOLD, CYAN, GREEN, GRAY, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.council_models import (  # noqa: E402
    FALLBACKS,
    PROVIDERS,
    config_path,
    get_model,
    load_all,
    set_model,
)
from scripts.utils import council_freshness as freshness  # noqa: E402


def show() -> int:
    """Print the resolved model per provider, flagging any that fell back."""
    resolved = load_all()
    print(f"{BOLD}Council model pins{RESET} {GRAY}({config_path()}){RESET}")
    for provider in PROVIDERS:
        model = resolved[provider]
        flag = f" {GRAY}(fallback){RESET}" if model == FALLBACKS[provider] and not config_path().exists() else ""
        print(f"  {CYAN}{provider:<8}{RESET} {model}{flag}")
    return 0


def apply_sets(pairs: list[str]) -> int:
    """Apply one or more provider=model bumps, then print the new state."""
    parsed: list[tuple[str, str]] = []
    for pair in pairs:
        if "=" not in pair:
            print(f"{RED}Error:{RESET} '{pair}' is not provider=model.", file=sys.stderr)
            return 2
        provider, model = pair.split("=", 1)
        provider, model = provider.strip(), model.strip()
        if provider not in FALLBACKS:
            print(
                f"{RED}Error:{RESET} unknown provider '{provider}'. "
                f"Known: {', '.join(PROVIDERS)}.",
                file=sys.stderr,
            )
            return 2
        if not model:
            print(f"{RED}Error:{RESET} empty model id for '{provider}'.", file=sys.stderr)
            return 2
        parsed.append((provider, model))

    for provider, model in parsed:
        old = get_model(provider)
        set_model(provider, model)
        print(f"{GREEN}Set{RESET} {CYAN}{provider}{RESET}: {old} {GRAY}->{RESET} {model}")

    print()
    return show()


STATUS_COLOR = {"ok": GREEN, "newer": YELLOW, "broken": RED, "unknown": GRAY}


def check(quiet: bool, as_json: bool) -> int:
    """Read-only freshness check across the three providers.

    --json prints structured findings; --quiet prints only the one-line nudge
    (nothing when all pins are current); default prints a human table plus the
    apply hint. Always exits 0 -- findings carry status, the check itself never
    fails the caller.
    """
    findings = freshness.assess()

    if as_json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
        return 0

    line = freshness.nudge_line(findings)
    if quiet:
        if line:
            print(line)
        return 0

    print(f"{BOLD}Council model freshness{RESET}")
    for f in findings:
        color = STATUS_COLOR.get(f["status"], GRAY)
        print(f"  {CYAN}{f['provider']:<8}{RESET} {color}{f['status']:<8}{RESET} {f['detail']}")
    print()
    if line:
        print(line)
    else:
        print(f"{GREEN}All council pins are current.{RESET}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="council-models.py",
        description="View, bump, and freshness-check the /council model pins (config/council-models.json).",
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--show", action="store_true", help="Print the current model pins.")
    group.add_argument(
        "--set",
        nargs="+",
        metavar="PROVIDER=MODEL",
        help="Set one or more pins, e.g. --set grok=grok-4.6 kimi=k3.",
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="Read-only freshness check: broken pins + newer models available.",
    )
    group.add_argument(
        "--get",
        metavar="PROVIDER",
        help="Print the resolved model id for one provider (for scripts/skills).",
    )
    p.add_argument("--quiet", action="store_true",
                   help="With --check: print only the one-line nudge (empty when all current).")
    p.add_argument("--json", action="store_true",
                   help="With --check: print structured findings as JSON.")
    args = p.parse_args(argv)

    if args.show:
        return show()
    if args.check:
        return check(args.quiet, args.json)
    if args.get:
        if args.get not in FALLBACKS:
            print(f"{RED}Error:{RESET} unknown provider '{args.get}'. "
                  f"Known: {', '.join(PROVIDERS)}.", file=sys.stderr)
            return 2
        print(get_model(args.get))
        return 0
    return apply_sets(args.set)


if __name__ == "__main__":
    sys.exit(main())
