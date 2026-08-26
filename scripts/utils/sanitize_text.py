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
