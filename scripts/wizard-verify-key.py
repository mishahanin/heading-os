#!/usr/bin/env python3
"""wizard-verify-key.py -- optional API-key live-ping via stdlib only.

Does NOT import the Anthropic SDK. Safe on fresh clones.

Exit codes:
    0 = validated
    1 = invalid (401/403)
    2 = rate-limited (429)
    3 = network/timeout
    4 = bad arguments
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The resolver's transitive imports are stdlib-only TODAY, which is what keeps
# this script runnable on a fresh clone before `uv sync`. It holds by luck of
# two workspace modules keeping their non-stdlib imports lazy, so
# tests/test_wizard_verify_key.py asserts it in a subprocess rather than
# trusting this comment.
from scripts.utils import claude_models  # noqa: E402

TIMEOUT = 5.0

# Family used for the live-ping test. Resolved to the newest Haiku at call
# time, so a retired model can never strand the setup wizard. Override at
# runtime via the WIZARD_PING_MODEL environment variable.
PING_FAMILY = "haiku"


def verify_anthropic(key: str):
    model = os.environ.get("WIZARD_PING_MODEL") or claude_models.latest(PING_FAMILY)
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        method="POST",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        data=json.dumps({
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ok"}],
        }).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status == 200:
                return "ok", "Key validated."
            return "unknown", f"Unexpected HTTP {resp.status}; stored as-is."
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return "invalid", "Key appears invalid. Retry or skip."
        if e.code == 429:
            return "rate_limited", "Rate-limited; key likely valid. Stored as-is."
        return "unknown", f"HTTP {e.code}; stored as-is."
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return "unknown", f"Could not reach api.anthropic.com ({e}); stored as-is."


def main(argv=None):
    parser = argparse.ArgumentParser(description="Optional API-key live-ping helper")
    parser.add_argument("--provider", choices=["anthropic"], required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args(argv)

    if args.provider == "anthropic":
        status, msg = verify_anthropic(args.key)
    else:
        print(f"ERROR: unknown provider {args.provider!r}", file=sys.stderr)
        return 4

    print(json.dumps({"status": status, "message": msg}))
    return {"ok": 0, "invalid": 1, "rate_limited": 2, "unknown": 3}[status]


if __name__ == "__main__":
    sys.exit(main())
