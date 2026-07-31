#!/usr/bin/env python3
"""Credential patterns, and the two operations over them.

The single source of truth for what this workspace calls a secret. Two consumers
import it today: scripts/secret-scanner.py (the push-time and commit-time wall),
and .claude/hooks/checkpoint-save.py, which calls `redact` below so a generated
handoff can never block that wall.

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
# Doubling the input quadruples the time. The guard changes no verdict, and what
# it buys depends entirely on whether the needle is present:
#
#   needle ABSENT   200,000 chars 0.00015s   1,000,000 chars 0.00068s
#   needle PRESENT   12,500 chars 0.51s         50,000 chars 7.98s
#                                             200,000 chars 39.79s
#
# So the guard protects only text carrying no "://" at all, and "://" appears in
# essentially every real markdown file. On realistic input the quadratic cost is
# still paid in full. It predates this module and fixing it is separate work;
# what is written here is only what the guard actually does.
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


REDACTION_TEMPLATE = "[REDACTED: {description}]"


def _redact_segment(segment: str) -> str:
    """Redact one segment: every span, computed on the ORIGINAL text, merged.

    Both halves of that sentence are load-bearing, and the second is the one
    that was missing. Substituting pattern by pattern is DESTRUCTIVE across
    patterns: the marker carries a space and a bracket, so redacting an inner
    span breaks the character-class run an enclosing pattern depends on, and the
    enclosing pattern then matches nothing. Measured on an API key sitting where
    a connection string's username goes - the password after the colon walked
    out intact, and the wall accepted it, because the broken shape no longer
    matched anything at all. A fixpoint loop does not help: that output is
    already a fixpoint.

    So every span is collected first, overlapping spans are merged into one
    interval, and each interval is replaced in a single right-to-left pass. A
    merged interval is named after the WIDEST pattern that contributed to it,
    which for a nested credential is the enclosing one.
    """
    spans = []
    for pattern, description in iter_patterns(segment):
        for match in pattern.finditer(segment):
            start, end = match.span()
            if end > start:
                spans.append((start, end, description))
    if not spans:
        return segment

    spans.sort(key=lambda span: (span[0], -span[1]))
    merged = []
    for start, end, description in spans:
        if merged and start < merged[-1][1]:
            prev_start, prev_end, prev_description, prev_width = merged[-1]
            if end - start > prev_width:
                prev_description, prev_width = description, end - start
            merged[-1] = (prev_start, max(prev_end, end), prev_description, prev_width)
        else:
            merged.append((start, end, description, end - start))

    out = []
    cursor = 0
    for start, end, description, _width in merged:
        out.append(segment[cursor:start])
        out.append(REDACTION_TEMPLATE.format(description=description))
        cursor = end
    out.append(segment[cursor:])
    return "".join(out)


def redact(text: str) -> str:
    """Replace every credential-shaped SPAN with a marker naming what it was.

    "The redactor's output passes the scanner" rests on four things, none of
    them the allowlist:

      1. The same pattern vocabulary, iterated through the same `iter_patterns`
         prefilter, over the same line boundaries scan_file reads.
      2. Every span computed on the original segment and merged before any
         substitution, so no substitution can disarm a sibling pattern and no
         partial residue is left behind (see `_redact_segment`).
      3. No marker text containing anything the vocabulary matches. The
         prefilter half of that is pinned by
         test_no_redaction_marker_reintroduces_a_prefilter_needle.
      4. Measurement rather than argument: a differential fuzz nests every
         credential family inside every container family in both orders and
         checks that nothing the vocabulary covered in the input survives.

    THE ALLOWLIST IS DELIBERATELY NOT HONOURED HERE, and that is a difference
    from scan_file, not an oversight. `ALLOWLIST_TOKEN` marks a human-authored,
    reviewed pattern in a SOURCE file. A compact summary is machine-generated
    and reviewed by nobody, and the marker lands in one by accident - most
    likely in a session ABOUT the secret scanner, which is exactly the session
    shape that caused the incident this redactor exists for. Honouring it there
    let a live credential through the redactor AND the wall together. Ignoring
    it only ever redacts MORE, so the invariant above is strictly stronger for
    it: whatever the scanner would flag is a subset of what this removes.

    The span is replaced rather than the line, so the caller's prose survives:
    the handoff exists to let the next session resume, and a gutted summary
    fails at that.

    ONE PATTERN IS AN EXCEPTION, and it is stated here rather than discovered
    later. "Plaintext password in markdown" ends in [^\\n]{8,}, which is greedy
    to end of line, so a line beginning with a bolded Password label loses
    everything after that label, prose included. Measured: a sentence about a
    rotation policy came back as the marker alone. The pattern is doing its job,
    since text after that label is exactly where a password would sit, and
    narrowing it here would weaken detection to protect prose. Pinned by
    test_the_markdown_password_pattern_eats_its_whole_line.

    Returns the input unchanged, byte for byte, when nothing matches.
    """
    if not text:
        return text

    out = []
    # split("\n"), NOT splitlines(). str.splitlines() over-splits on eight
    # code points readlines() does not treat as a break (vertical tab, form
    # feed, the three ASCII file/group/record separators, NEL, and the
    # Unicode LINE and PARAGRAPH SEPARATOR), so it cannot be used here: a
    # credential spanning one of those would be cut in two by the redactor,
    # left unmatched, and then caught by the scanner, which is the exact
    # failure this slice exists to remove.
    #
    # split("\n") alone still under-splits relative to readlines(), which
    # (via universal newlines) also treats a lone "\r" as a line break. So each
    # "\n"-segment is split again on "\r" and redacted per sub-segment, which
    # reproduces readlines()'s line boundaries exactly. That matters for prose,
    # not for safety: the markdown-password pattern runs greedily to end of
    # LINE, so on an under-split segment it would eat every "\r"-separated line
    # after the label too. Sub-segments rejoin with "\r" and lines with "\n",
    # which restores the input byte for byte.
    for line in text.split("\n"):
        out.append("\r".join(_redact_segment(sub_line) for sub_line in line.split("\r")))
    return "\n".join(out)
