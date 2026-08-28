#!/usr/bin/env python3
"""
sanitize_text - Library module for invisible-character detection and removal.

Extracted from scripts/sanitize-text.py during the 2026-05-12 perf v2 sprint
(Phase 2.1) so .claude/hooks/post-write-sanitize.py can import directly
instead of spawning a subprocess on every Write/Edit. The CLI front-end at
scripts/sanitize-text.py is now a thin wrapper over this module.

Functions:
  sanitize(text) -> str          Strip invisible chars from text.
  scan(text, filename, out)      Print findings to `out`, return count.
  scan_file(path) -> (int, str)  Read file, return (count, formatted report).

Constants:
  INVISIBLE_CHARS  Characters removed entirely.
  REPLACE_MAP      Characters replaced (e.g., NBSP -> space).
  CHAR_NAMES       Human-readable names for diagnostics.
"""

from __future__ import annotations

import io
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Tuple

# ============================================================
# Constants
# ============================================================

# Characters to remove entirely
INVISIBLE_CHARS = (
    "​"  # Zero-width space
    "‌"  # Zero-width non-joiner
    "‍"  # Zero-width joiner
    "‎"  # Left-to-right mark
    "‏"  # Right-to-left mark; sanitizer intentionally lists bidi/invisible chars to strip them  # nosec B613
    "­"  # Soft hyphen
    "⁠"  # Word joiner
    "﻿"  # Byte order mark (when not at file start)
    "⁢"  # Invisible times
    "⁣"  # Invisible separator
    "⁤"  # Invisible plus
    "͏"  # Combining grapheme joiner
    "؜"  # Arabic letter mark
    "᠎"  # Mongolian vowel separator
    " "  # Line separator
    " "  # Paragraph separator
    "⁡"  # Function application
    "⁦"  # Left-to-right isolate (Trojan Source)
    "⁧"  # Right-to-left isolate (Trojan Source)
    "⁨"  # First strong isolate (Trojan Source)
    "⁩"  # Pop directional isolate (Trojan Source)
    # The bidi EMBEDDINGS and OVERRIDES, U+202A to U+202E. Written as escapes so
    # an editor that normalises invisible characters cannot silently drop them,
    # which is why the isolates above are also duplicated into CHAR_NAMES by
    # codepoint. This half of the family was absent until 2026-08-28: the table
    # carried the four isolates, labelled them "(Trojan Source)", and omitted
    # U+202E RIGHT-TO-LEFT OVERRIDE, the character CVE-2021-42574 is named for.
    # `scripts/marp_render.py` has stripped all five for its own rendering the
    # whole time, so the canonical sanitizer every rule points at was the
    # shorter of the two tables.
    "\u202a"  # Left-to-right embedding
    "\u202b"  # Right-to-left embedding
    "\u202c"  # Pop directional formatting
    "\u202d"  # Left-to-right override
    "\u202e"  # Right-to-left override; the Trojan Source vector  # nosec B613
)

# Characters to replace (not remove)
REPLACE_MAP = {
    " ": " ",  # Non-breaking space -> regular space
}

INVISIBLE_PATTERN = re.compile("[" + re.escape(INVISIBLE_CHARS) + "]")
REPLACE_PATTERN = re.compile("[" + re.escape("".join(REPLACE_MAP.keys())) + "]")

CHAR_NAMES = {
    "​": "Zero-width space",
    "‌": "Zero-width non-joiner",
    "‍": "Zero-width joiner",
    "‎": "Left-to-right mark",
    "‏": "Right-to-left mark",
    "­": "Soft hyphen",
    "⁠": "Word joiner",
    "﻿": "Byte order mark",
    "⁢": "Invisible times",
    "⁣": "Invisible separator",
    "⁤": "Invisible plus",
    "͏": "Combining grapheme joiner",
    "؜": "Arabic letter mark",
    "᠎": "Mongolian vowel separator",
    " ": "Line separator",
    " ": "Paragraph separator",
    "⁡": "Function application",
    " ": "Non-breaking space",
}

# The Trojan Source isolates, keyed by codepoint rather than typed literally so
# the entry cannot be lost to an editor that normalises invisible characters.
CHAR_NAMES.update({
    chr(0x2066): "Left-to-right isolate (Trojan Source)",
    chr(0x2067): "Right-to-left isolate (Trojan Source)",
    chr(0x2068): "First strong isolate (Trojan Source)",
    chr(0x2069): "Pop directional isolate (Trojan Source)",
    # The embeddings and overrides. `_name_for` would fall back to the Unicode
    # database and report "Right-To-Left Override", which is accurate and tells
    # the reader nothing about why the finding matters. These are the other half
    # of the same attack, so they carry the same label as the isolates.
    chr(0x202A): "Left-to-right embedding (Trojan Source)",
    chr(0x202B): "Right-to-left embedding (Trojan Source)",
    chr(0x202C): "Pop directional formatting (Trojan Source)",
    chr(0x202D): "Left-to-right override (Trojan Source)",
    chr(0x202E): "Right-to-left override (Trojan Source)",
})

# What `scan` looks for. Derived from what `sanitize` acts on, not hand-listed.
#
# `scan` used to iterate CHAR_NAMES while `sanitize` acts on INVISIBLE_CHARS.
# Both tables are maintained by hand, and they drifted: the four isolates above
# were added to INVISIBLE_CHARS and never to CHAR_NAMES. For each of them
# `sanitize()` stripped the character while `scan()` printed
# "Clean - no hidden characters found." Measured 2026-08-26.
#
# That is the worst direction for this defect to point. `.claude/rules/
# hidden-chars.md` makes this scan the validation line carried on every
# deliverable, and these four are the Trojan Source family: they reorder what a
# reviewer SEES on a line without changing what the parser reads. A tool whose
# only job is to say "nothing is hidden here" was saying it over the one class
# of character built to hide. The names above close today's gap; this set closes
# tomorrow's, because a character added to INVISIBLE_CHARS is now scanned
# whether or not anyone remembers to name it.
SCANNED_CHARS = frozenset(INVISIBLE_CHARS) | frozenset(REPLACE_MAP)


def _name_for(char: str) -> str:
    """A name for a scanned character, including one nobody has named yet.

    Falling back to the Unicode database keeps an unnamed character REPORTED
    instead of silently dropped, which is the failure this section exists for.
    The name is diagnostic text; the finding is what matters.
    """
    if char in CHAR_NAMES:
        return CHAR_NAMES[char]
    try:
        return unicodedata.name(char).title()
    except ValueError:
        return f"Unnamed invisible character U+{ord(char):04X}"


# ============================================================
# Core functions
# ============================================================

def sanitize(text: str) -> str:
    """Remove invisible characters and replace problematic ones."""
    text = INVISIBLE_PATTERN.sub("", text)
    text = REPLACE_PATTERN.sub(lambda m: REPLACE_MAP[m.group()], text)
    return text


def sanitize_report(text: str) -> Tuple[str, int, int]:
    """`sanitize(text)` plus what it acted on: (clean, removed, replaced).

    The CLI derived its count as `len(text) - len(clean)`, which sees deletions
    and nothing else. Every REPLACE_MAP substitution preserves length, so a file
    whose only contamination was a non-breaking space was rewritten on disk and
    then reported "already clean" - the one word the hidden-character policy is
    built around, printed over a file that had just been modified. Measured
    2026-08-28, and `--scan` on the same bytes reported the character, because
    SCANNED_CHARS is deliberately INVISIBLE_CHARS | REPLACE_MAP.

    Both counts come from the patterns `sanitize` itself uses, so a character
    added to either table is counted without anyone remembering to.
    """
    removed = len(INVISIBLE_PATTERN.findall(text))
    replaced = len(REPLACE_PATTERN.findall(text))
    return sanitize(text), removed, replaced


def scan(text: str, filename: str = "stdin", out=None) -> int:
    """Scan text and report all hidden characters found.

    Returns the count of findings. Writes a formatted report to `out`
    (default sys.stdout) matching the CLI's historical output exactly.
    Pass an io.StringIO to capture the report in-process.
    """
    if out is None:
        out = sys.stdout

    findings = []
    for i, char in enumerate(text):
        # SCANNED_CHARS, not CHAR_NAMES: what the scan reports is now tied to
        # what the sanitizer acts on, so the two cannot drift apart again.
        if char in SCANNED_CHARS:
            line_num = text[:i].count("\n") + 1
            col = i - text[:i].rfind("\n")
            findings.append({
                "char": char,
                "name": _name_for(char),
                "unicode": f"U+{ord(char):04X}",
                "line": line_num,
                "col": col,
            })

    if findings:
        print(f"\n  {filename}: Found {len(findings)} hidden character(s):\n", file=out)
        for f in findings:
            print(f"    Line {f['line']}, Col {f['col']}: {f['unicode']} {f['name']}", file=out)
        counts = Counter(f["name"] for f in findings)
        print("\n  Summary:", file=out)
        for name, count in counts.most_common():
            print(f"    {name}: {count}", file=out)
    else:
        print(f"\n  {filename}: Clean - no hidden characters found.", file=out)

    return len(findings)


def scan_file(path) -> Tuple[int, str]:
    """Read a file, scan it, return (count, formatted_report).

    Designed for in-process invocation from PostToolUse hooks. The report
    string matches the CLI output; callers can forward it as feedback to
    Claude when contamination is detected. Returns (0, "") if the file
    does not exist or cannot be decoded (caller treats as clean).
    """
    path = Path(path)
    if not path.is_file():
        return 0, ""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0, ""

    buf = io.StringIO()
    count = scan(text, str(path), out=buf)
    return count, buf.getvalue().strip()


def word_count(text: str) -> int:
    """Words as a human counts them in prose. The workspace's one definition.

    A whitespace-separated run counts when it contains at least one letter or
    digit, so a bare bullet, a lone dash or a `|` table rule does not inflate
    the figure.

    It lived as `_word_count` inside `scripts/sanitize-text.py`, which is a
    kebab-case CLI and cannot be imported. So four other counters were written
    instead, and on one ordinary sentence the five disagreed 11 / 12 / 15 / 15 /
    17. `.claude/rules/hidden-chars.md` is the rule that settles this and it is
    explicit that the number "comes from the tool, never from an estimate" - a
    line that means little while the tools answer differently. This is the tool.

    Prose only. An HTML document must go through
    `scripts.utils.html_text.strip_html` first, which removes `<style>` and
    `<script>` BODIES; a bare tag-stripping regex leaves the stylesheet behind
    and counts it as text.
    """
    return sum(1 for tok in text.split() if any(ch.isalnum() for ch in tok))
