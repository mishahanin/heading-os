#!/usr/bin/env python3
"""wizard-verify-key.py -- optional API-key live-ping via stdlib only.

Does NOT import the Anthropic SDK. Safe on fresh clones.

Exit codes:
    0 = validated
    1 = invalid (401/403)
    2 = rate-limited (429)
    3 = network/timeout
    4 = bad arguments, or a key holding a control character (nothing is sent)

The parser below exits 4 on a usage error, not argparse's default 2. 2 means
"rate-limited, key likely valid" to the only caller
(`.claude/skills/setup-wizard/SKILL.md`, which reads the code and proceeds),
so a mistyped flag used to make the wizard store an entirely unverified key
and report that it looked good. A caller that never reached the network must
not be indistinguishable from one that did.

The key is read from the environment by default rather than from `--key`,
because argv is world-readable through /proc/<pid>/cmdline for the life of the
call and lands in shell history. `--key` still works for back-compat.
"""
from __future__ import annotations

import argparse
import json
import os
import re
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

# A header value holding one of these makes `http.client.putheader` raise
# ValueError -- which is not an OSError, so it slipped past both handlers in
# `verify_anthropic`, killed the process with CPython's exit code 1, and that
# code means "invalid (401/403)" to the wizard. A perfectly good key read as
# `WIZARD_VERIFY_KEY=$(cat keyfile)` was reported invalid over a request that
# never left the machine. The ValueError message also echoes the header value,
# putting the credential verbatim into stderr and the wizard transcript.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

# Family used for the live-ping test. Resolved to the newest Haiku at call
# time, so a retired model can never strand the setup wizard. Override at
# runtime via the WIZARD_PING_MODEL environment variable.
PING_FAMILY = "haiku"

# Default place to read the key from, so it never has to appear in argv.
KEY_ENV = "WIZARD_VERIFY_KEY"


class _Parser(argparse.ArgumentParser):
    """argparse, but a usage error exits 4 instead of 2.

    See the exit-code note in the module docstring: 2 is this script's
    "rate-limited" code and the wizard treats it as "proceed, the key is
    probably fine".
    """

    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(4)


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
    except ValueError:
        # Belt and braces behind the `main` guard. The message is NOT included:
        # http.client puts the rejected header value in it, which is the key.
        # And not "invalid": nothing was sent, so nothing established that.
        return "unknown", ("the key could not be placed in a request header; "
                           "nothing was sent")


def main(argv=None):
    parser = _Parser(description="Optional API-key live-ping helper")
    parser.add_argument("--provider", choices=["anthropic"], required=True)
    parser.add_argument(
        "--key",
        help="the key itself; visible in /proc/<pid>/cmdline and shell history. "
             f"Prefer ${KEY_ENV}.")
    parser.add_argument(
        "--key-env", metavar="VAR", default=KEY_ENV,
        help=f"environment variable holding the key (default: {KEY_ENV})")
    args = parser.parse_args(argv)

    key = args.key
    if key is None:
        key = os.environ.get(args.key_env)
    if not key:
        parser.error(f"no key given: pass --key, or set ${args.key_env}")
    # Strip FIRST. A trailing newline is the ordinary artifact of
    # `WIZARD_VERIFY_KEY=$(cat keyfile)` plumbing and of a `.env` line, and it
    # used to reach the header verbatim.
    key = key.strip()
    if not key:
        parser.error(f"no key given: pass --key, or set ${args.key_env}")
    if _CONTROL_CHARS_RE.search(key):
        # Never echo the key. The invocation is what is wrong, and 4 is the
        # code this docstring reserves for that; 1 would tell the wizard the
        # key is invalid over a request that was never made.
        print(json.dumps({
            "status": "unusable",
            "message": "the key holds a control character; nothing was sent",
        }))
        return 4

    if args.provider == "anthropic":
        status, msg = verify_anthropic(key)
    else:
        print(f"ERROR: unknown provider {args.provider!r}", file=sys.stderr)
        return 4

    print(json.dumps({"status": status, "message": msg}))
    return {"ok": 0, "invalid": 1, "rate_limited": 2, "unknown": 3}[status]


if __name__ == "__main__":
    sys.exit(main())
