#!/usr/bin/env python3
"""Regression guard: the Security Constitution must exist in the DATA repo.

F-M8 was reported as "docs/security/ absent" by the engine-only audit, which
cannot see the sibling data repo. The constitution DOES exist at
`<data_root>/docs/security/SECURITY-CONSTITUTION.md` (referenced as "Full law"
by the global CLAUDE.md). This test pins that fact so the document cannot be
silently deleted or gutted. It reads via the data-root seam and skips on a
data-less (demo) engine clone, exactly like test_findings_registry.

Gated on `data_overlay_present()` rather than `not data_root_is_demo()` since
2026-08-10: a stray `knowledge/` directory inside an engine clone makes
`get_data_root()` return the clone itself, which is not demo and not an overlay
either, and this guard then asserted a CEO document against a public checkout.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.paths import data_overlay_present, get_data_root  # noqa: E402

CONSTITUTION = get_data_root() / "docs" / "security" / "SECURITY-CONSTITUTION.md"

# Section anchors that must survive any edit. The constitution is worthless if
# these are removed.
#
# The third entry used to be the bare "Forbidden Patterns", which is a
# SUBSTRING of the second. Whenever "Forbidden Patterns Registry" was present
# the third anchor passed for free, so a gutting edit could delete the
# standalone forbidden-patterns section and the guard would report nothing.
# `test_no_anchor_is_inert` below now makes that shape impossible to
# reintroduce, and the anchor names the section that actually exists.
_REQUIRED_ANCHORS = [
    "Non-Negotiable Principles",
    "Forbidden Patterns Registry",
    "Absolute Forbidden Patterns",
]


def _headings(text: str) -> list[str]:
    """Markdown heading text, `#` markers and numbering prefix left in place."""
    return [ln.lstrip("#").strip() for ln in text.splitlines() if ln.startswith("#")]


def test_no_anchor_is_inert():
    """No anchor may be a substring of another anchor.

    This is the mechanical version of the defect above: an anchor contained in
    a sibling can never fail on its own, so it contributes nothing while
    reading like a third check. Not gated on the overlay - it is a fact about
    this list, decidable on any clone.
    """
    for anchor in _REQUIRED_ANCHORS:
        swallowed_by = [other for other in _REQUIRED_ANCHORS
                        if other != anchor and anchor in other]
        assert not swallowed_by, (
            f"anchor {anchor!r} is a substring of {swallowed_by!r}, so it "
            f"passes whenever the longer one is present and guards nothing")


@pytest.mark.skipif(not data_overlay_present(), reason="no private data overlay: the constitution is a CEO/exec concern")
def test_security_constitution_exists():
    assert CONSTITUTION.is_file(), f"Security Constitution missing at {CONSTITUTION}"
    text = CONSTITUTION.read_text(encoding="utf-8")
    assert len(text) > 1000, "Security Constitution is suspiciously short — likely gutted"
    # A HEADING, not a mention. `a not in text` was satisfied by any prose line
    # that happened to use the phrase, so a deleted section whose name survived
    # in a cross-reference still read as present.
    headings = _headings(text)
    assert headings, "the constitution has no headings at all; it has been gutted"
    missing = [a for a in _REQUIRED_ANCHORS
               if not any(a in heading for heading in headings)]
    assert not missing, f"Security Constitution missing required sections: {missing}"
