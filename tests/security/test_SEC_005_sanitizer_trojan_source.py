#!/usr/bin/env python3
"""SEC-005: Verify sanitizer detects Trojan Source bidirectional isolate characters.

Vulnerability: Missing U+2066-U+2069 allows Trojan Source attacks.
Expected safe behavior: All bidirectional isolate characters are detected and removed.
"""

import pytest

from tests.security.conftest import read_file_content


TROJAN_SOURCE_CHARS = [
    ("\u2066", "LEFT-TO-RIGHT ISOLATE"),
    ("\u2067", "RIGHT-TO-LEFT ISOLATE"),
    ("\u2068", "FIRST STRONG ISOLATE"),
    ("\u2069", "POP DIRECTIONAL ISOLATE"),
]


def test_sanitizer_includes_trojan_source_chars(scripts_dir):
    """Sanitizer INVISIBLE_CHARS must include all Trojan Source characters.

    After the 2026-05-12 perf-v2 sprint (Phase 2.1), the INVISIBLE_CHARS
    constant moved from scripts/sanitize-text.py (now a thin CLI wrapper)
    to scripts/utils/sanitize_text.py. We verify the constant in its
    authoritative location and accept either the literal Unicode char or
    a \\uXXXX escape representation.
    """
    content = read_file_content(scripts_dir / "utils" / "sanitize_text.py")

    for char_code, name in TROJAN_SOURCE_CHARS:
        hex_repr = f"\\u{ord(char_code):04X}".lower()
        alt_hex = f"\\u{ord(char_code):04x}"
        literal_present = char_code in content
        escape_present = hex_repr in content.lower() or alt_hex in content.lower()
        assert literal_present or escape_present, (
            f"scripts/utils/sanitize_text.py missing Trojan Source character "
            f"{name} ({hex_repr})"
        )
