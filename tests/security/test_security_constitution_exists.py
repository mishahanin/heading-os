#!/usr/bin/env python3
"""Regression guard: the Security Constitution must exist in the DATA repo.

F-M8 was reported as "docs/security/ absent" by the engine-only audit, which
cannot see the sibling data repo. The constitution DOES exist at
`<data_root>/docs/security/SECURITY-CONSTITUTION.md` (referenced as "Full law"
by the global CLAUDE.md). This test pins that fact so the document cannot be
silently deleted or gutted. It reads via the data-root seam and skips on a
data-less (demo) engine clone, exactly like test_findings_registry.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.paths import data_root_is_demo, get_data_root  # noqa: E402

CONSTITUTION = get_data_root() / "docs" / "security" / "SECURITY-CONSTITUTION.md"

# Anchors that must survive any edit — the constitution is worthless if these
# sections are removed.
_REQUIRED_ANCHORS = [
    "Non-Negotiable Principles",
    "Forbidden Patterns Registry",
    "Forbidden Patterns",
]


@pytest.mark.skipif(data_root_is_demo(), reason="no data root (demo clone): constitution is a CEO/exec concern")
def test_security_constitution_exists():
    assert CONSTITUTION.is_file(), f"Security Constitution missing at {CONSTITUTION}"
    text = CONSTITUTION.read_text(encoding="utf-8")
    assert len(text) > 1000, "Security Constitution is suspiciously short — likely gutted"
    missing = [a for a in _REQUIRED_ANCHORS if a not in text]
    assert not missing, f"Security Constitution missing required sections: {missing}"
