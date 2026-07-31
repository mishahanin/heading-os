#!/usr/bin/env python3
"""Credential patterns, and the two operations over them.

The single source of truth for what this workspace calls a secret. One
consumer imports it today: scripts/secret-scanner.py (the push-time and
commit-time wall). .claude/hooks/checkpoint-save.py (the redactor that keeps a
generated handoff from ever blocking that wall) is the intended second
consumer, wired in a later slice.

A third consumer does NOT import it, on purpose. .claude/hooks/_dispatch.py is
the blocking PreToolUse gate, and its own comments record why its one external
import is lazy and guarded: any exception from that import would take down the
whole PreToolUse chain, secret detection included, on every tool call. That
degradation is safe for the Canopus deny, whose guarantee lives elsewhere. It
would not be safe here, because at that layer secret detection IS the guarantee,
and a guarded import falling back to an empty list is a fail-open hole in a
security gate. So _dispatch.py keeps an embedded copy and
tests/security/test_SEC_004_credential_patterns.py holds the two in lockstep.

That guard is not theoretical. The copies had already drifted twice before it
existed: once on the {16,} threshold (fixed by hand under F-L4) and once on the
placeholder lookahead of the environment-password entry, found AND closed on
2026-07-31 by parsing both files.
"""
import re

# Inline allowlist token (same convention as Yelp/detect-secrets). A line carrying
# this marker is an intentional, reviewed pattern (test fixtures, docs) and is skipped.
ALLOWLIST_TOKEN = "pragma: allowlist secret"

# Secret patterns: (compiled_regex, description)
# Thresholds tuned to avoid matching placeholders like "sk-ant-your-key-here"
SECRET_PATTERNS = [
    # API key formats - require 16+ chars of key material after prefix (aligned with _dispatch.py, F-L4)
    (re.compile(r'sk-ant-[a-zA-Z0-9_-]{16,}'), "Anthropic API key"),
    (re.compile(r'pplx-[a-zA-Z0-9]{16,}'), "Perplexity API key"),
    (re.compile(r'r8_[a-zA-Z0-9]{16,}'), "Replicate API token"),
    (re.compile(r'fc-[A-Za-z0-9]{16,}'), "Firecrawl API key"),
    (re.compile(r'ctx7sk-[a-zA-Z0-9-]{16,}'), "Context7 API key"),
    (re.compile(r'ghp_[a-zA-Z0-9]{16,}'), "GitHub personal access token"),
    (re.compile(r'gho_[a-zA-Z0-9]{16,}'), "GitHub OAuth token"),
    (re.compile(r'AKIA[0-9A-Z]{16}'), "AWS access key"),
    (re.compile(r'xoxb-[0-9]+-[a-zA-Z0-9]+'), "Slack bot token"),
    (re.compile(r'xoxp-[0-9]+-[a-zA-Z0-9]+'), "Slack user token"),
    (re.compile(r'ya29\.[A-Za-z0-9._-]{50,}'), "Google OAuth token"),
    # JWT, PEM private keys, and credentialed connection strings (F-L3; mirror in _dispatch.py)
    (re.compile(r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'), "JWT bearer token"),
    (re.compile(r'-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----'), "PEM private key"),
    (re.compile(r'[a-zA-Z][a-zA-Z0-9+.-]*://(?!user:pass(?:word)?@|username:password@)[^:@\s/?]{2,}:[^:@\s/?]{2,}@'), "connection string with inline credentials"),
    # Markdown password fields with actual values (not placeholders)
    (re.compile(
        r'\*\*Password:\*\*\s+'
        r'(?!Stored|REDACTED|N/A|See |TBD|Change|Reset|Set |Use |Your )'
        r'[^\n]{8,}'
    ), "Plaintext password in markdown"),
    # Generic env-style password assignments with real values
    (re.compile(
        r'(?:EXCHANGE_PASSWORD|DB_PASSWORD|SMTP_PASSWORD|AUTH_PASSWORD)'
        r'\s*=\s*'
        r'(?!(?i:your[-_]|changeme|example|placeholder|redacted|dummy|xxx|<))'
        r'[A-Za-z0-9!@#$%^&*_+=-]{8,}'
    ), "Password in environment variable assignment"),
]


# A pattern that CANNOT match without some literal substring records it here.
# Testing for the substring first is O(n) and changes no verdict; it only avoids
# the quadratic start-position scan the regex engine performs otherwise.
#
# One entry, because one pattern was measured pathological at the pre-impl gate.
# The connection-string pattern opens with [a-zA-Z][a-zA-Z0-9+.-]*:// , which
# gives the engine no literal to anchor on, so it retries at every position:
#
#     12,500 chars  0.14s      50,000 chars   1.79s
#     25,000 chars  0.46s     100,000 chars   7.21s
#                             200,000 chars  32.20s
#
# Doubling the input quadruples the time. With the guard, 200,000 chars costs
# 0.00015s and 1,000,000 costs 0.00068s, and every verdict is unchanged.
#
# This is LIVE today, not a hazard this slice introduces: check_prevent_secrets
# in .claude/hooks/_dispatch.py passes whole file content as ONE string, so a
# 200 KB Write already spends half a minute inside a blocking PreToolUse gate.
# The scanner escapes it only by accident, because scan_file happens to iterate
# line by line.
#
# A possessive quantifier was tried first and REJECTED by measurement: it gave
# 1.5x and left the growth quadratic. The cost is the start-position scan, not
# backtracking.
#
# The other fifteen patterns open with a literal, so the engine already anchors
# them and none needs an entry. Adding one that is not logically exact would
# silently disable a pattern, so entries are added only with a measurement.
REQUIRED_SUBSTRING = {
    "connection string with inline credentials": "://",
}


def iter_patterns(text: str):
    """Yield (pattern, description) for every pattern that could match `text`.

    The single place the prefilter lives, so a consumer cannot forget it. Both
    the scanner and the redactor iterate this rather than SECRET_PATTERNS.
    """
    for pattern, description in SECRET_PATTERNS:
        needle = REQUIRED_SUBSTRING.get(description)
        if needle is not None and needle not in text:
            continue
        yield pattern, description
