#!/usr/bin/env python3
"""`apply_sanitize_map` and `scan_for_terms` are two halves of one pipeline.

The sanitizer removes a banned term; the pre-publish scanner then confirms none
survived. Until 2026-08-30 the two halves disagreed about what a word-boundary
term IS, and the disagreement ran in the unsafe direction.

`scan_for_terms` compiles its boundary pattern with `re.IGNORECASE`.
`apply_sanitize_map` did not, and it also gated every term behind a
case-SENSITIVE `find not in result` pre-check. So a term configured for removal
was left in the content in any casing but the configured one, and the scanner
reported it as a leak.

The second defect is in the same two lines: `re.sub` reads its replacement as a
TEMPLATE, and the replacement was never escaped, so one `(find, replace)` pair
meant two different things depending only on whether the term was a boundary
term.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.sanitize import apply_sanitize_map, scan_for_terms  # noqa: E402


def test_a_boundary_term_is_removed_in_every_casing_the_scanner_looks_for():
    """The measured case: "ODIN" survived and `scan_for_terms` then flagged it."""
    content = "ODIN settled the ledger; odin approved; Odin signed."
    cleaned = apply_sanitize_map(content, [("odin", "[redacted]")], {"odin"})

    assert "odin" not in cleaned.lower(), (
        f"a boundary term configured for removal survived: {cleaned!r}")
    assert cleaned.count("[redacted]") == 3


def test_the_sanitizer_leaves_nothing_its_own_scanner_reports():
    """The differential the two halves exist to satisfy, run end to end."""
    content = "ODIN settled the ledger; odin approved; Odin signed."
    cleaned = apply_sanitize_map(content, [("odin", "[redacted]")], {"odin"})

    residue = scan_for_terms(cleaned, set(), {"odin"})
    assert residue == [], (
        f"the scanner flagged what the sanitizer was configured to remove: {residue!r}")


def test_a_term_present_only_in_another_casing_is_not_skipped_outright():
    """The pre-check was a case-sensitive substring test, so this replaced nothing."""
    assert apply_sanitize_map("ODIN alone.", [("odin", "[redacted]")], {"odin"}) == (
        "[redacted] alone.")


def test_case_insensitivity_did_not_cost_the_word_boundary():
    """The boundary is the whole reason this branch exists; it still holds."""
    assert apply_sanitize_map(
        "decoding an ODIN report", [("odin", "[redacted]")], {"odin"}
    ) == "decoding an [redacted] report"


def test_a_backslash_in_the_replacement_is_inserted_literally():
    """r"R:\\new" landed as "R:" + newline + "ew" through the boundary branch."""
    out = apply_sanitize_map("odin", [("odin", r"R:\new")], {"odin"})
    assert out == r"R:\new"
    assert "\n" not in out


def test_a_group_reference_in_the_replacement_does_not_raise():
    """r"\\1x" raised `re.error: invalid group reference` out of the sanitizer."""
    assert apply_sanitize_map("odin", [("odin", r"\1x")], {"odin"}) == r"\1x"


def test_one_replacement_pair_means_the_same_thing_in_both_branches():
    """Boundary and non-boundary handling of the SAME pair must agree."""
    pair = [("odin", r"R:\new")]
    boundary = apply_sanitize_map("odin", pair, {"odin"})
    plain = apply_sanitize_map("odin", pair, set())
    assert boundary == plain == r"R:\new"


def test_the_scanner_still_finds_a_term_that_was_never_sanitized():
    """The negative case: without this the differential above passes vacuously."""
    findings = scan_for_terms("ODIN settled the ledger.", set(), {"odin"})
    assert len(findings) == 1
    assert findings[0][0] == "odin"
    assert findings[0][3] == "word-boundary"


def test_the_boundary_branch_is_compiled_case_insensitively():
    """A term differing only in case must not be treated as a different term."""
    # Two configured terms, one content token: whichever pair runs first wins,
    # and neither may leave the token behind.
    out = apply_sanitize_map("ODIN", [("Odin", "[a]")], {"Odin"})
    assert out == "[a]"
    assert not re.search(r"\bODIN\b", out)
