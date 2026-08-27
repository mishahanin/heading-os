#!/usr/bin/env python3
"""SEC-005: the sanitizer strips and reports the Trojan Source isolates.

Vulnerability: U+2066-U+2069 reorder what a reviewer SEES on a line without
changing what the parser reads, so a file can be made to display one thing and
mean another. Expected safe behavior: `sanitize()` removes them and `scan()`
reports them.

Until 2026-08-27 this file asserted something weaker than its own docstring. It
read `scripts/utils/sanitize_text.py` as TEXT and asserted that each codepoint
appeared somewhere in it, as a literal or as a `\\uXXXX` escape. That cannot
tell membership in the live `INVISIBLE_CHARS` tuple - the one compiled into
`INVISIBLE_PATTERN`, which is what `sanitize()` actually applies - from presence
in a comment, a docstring, a retired constant, or a different table entirely.

The defeating edit: move the four isolates out of `INVISIBLE_CHARS` into a
`_TROJAN_ISOLATES` tuple that nothing compiles. All four literals are still in
the file, so the old assertion passed, while `sanitize()` stopped stripping them
and `scan()` (whose SCANNED_CHARS is derived from INVISIBLE_CHARS) called a
Trojan-Source-bearing file clean. The repository was not exposed, because
tests/test_a_scan_that_called_trojan_source_clean.py would have caught it - but
SEC-005, the test that owns this control, certified nothing.

The module is importable, so the control is now asserted against behaviour.
"""

import pytest

from scripts.utils import sanitize_text as st


# Written as escapes, never as literals. A test file carrying the raw isolates
# would be reordered in every reviewer's editor, and .claude/rules/hidden-chars.md
# forbids invisible characters in generated text. The characters under test are
# built at run time from these codepoints.
TROJAN_SOURCE_CHARS = [
    ("\u2066", "LEFT-TO-RIGHT ISOLATE"),
    ("\u2067", "RIGHT-TO-LEFT ISOLATE"),
    ("\u2068", "FIRST STRONG ISOLATE"),
    ("\u2069", "POP DIRECTIONAL ISOLATE"),
]


@pytest.mark.parametrize("char,name", TROJAN_SOURCE_CHARS)
def test_the_isolate_is_in_the_tuple_the_sanitizer_compiles(char, name):
    """Membership in the live constant, not presence in the file's bytes."""
    assert char in st.INVISIBLE_CHARS, (
        f"{name} (U+{ord(char):04X}) is not in INVISIBLE_CHARS, the tuple "
        f"INVISIBLE_PATTERN is compiled from. It may still appear elsewhere in "
        f"the file; that is what the old text-scan version of this test was "
        f"measuring, and it is not the same claim."
    )
    assert st.INVISIBLE_PATTERN.search(char), (
        f"{name} is in INVISIBLE_CHARS but the compiled pattern does not match "
        f"it, so the tuple is no longer what sanitize() applies"
    )


@pytest.mark.parametrize("char,name", TROJAN_SOURCE_CHARS)
def test_sanitize_removes_the_isolate(char, name):
    """The whole point of the control, stated as what the function does."""
    dirty = f"if user{char}.is_admin():"
    clean = st.sanitize(dirty)
    assert char not in clean, f"sanitize() left {name} (U+{ord(char):04X}) in place"
    assert clean == "if user.is_admin():", clean


@pytest.mark.parametrize("char,name", TROJAN_SOURCE_CHARS)
def test_scan_reports_the_isolate_rather_than_calling_the_text_clean(char, name, capsys):
    """`scan` is the arm the validation line in .claude/rules/hidden-chars.md
    carries on every deliverable. A tool whose only job is to say "nothing is
    hidden here" must not say it over the one character class built to hide.

    This is a real regression, not a hypothetical: the four isolates were added
    to INVISIBLE_CHARS and never to CHAR_NAMES, and until 2026-08-26 `scan()`
    iterated CHAR_NAMES. It printed "Clean" for all four.
    """
    assert char in st.SCANNED_CHARS, (
        f"{name} is not in SCANNED_CHARS, so scan() cannot report it"
    )
    found = st.scan(f"a{char}b", filename="probe.txt")
    assert found >= 1, f"scan() found nothing in text containing {name}"
    out = capsys.readouterr().out
    assert "clean" not in out.lower(), out


def test_a_file_with_no_isolate_is_still_called_clean(capsys):
    """Anchor. Every assertion above is satisfied by a scanner that reports a
    finding on any input at all, and such a scanner is useless."""
    found = st.scan("plain ascii, nothing hidden", filename="probe.txt")
    assert found == 0, found


def test_sanitize_leaves_ordinary_text_alone():
    """Anchor for the other half: `sanitize` returning "" would pass every
    removal test above."""
    text = "if user.is_admin():"
    assert st.sanitize(text) == text
