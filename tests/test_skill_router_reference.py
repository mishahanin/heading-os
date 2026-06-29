"""Regression: compound-pattern detail lives in the reference file, not inline in skill-router.md (F-M10).

skill-router.md is an always-active rule loaded every session; the bulky compound-trigger
table + depth-signal examples + channel-scope disambiguation belong in an on-demand
reference file to reduce the always-on context budget. Routing logic is untouched.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTER_PATH = ROOT / ".claude" / "rules" / "skill-router.md"
REFERENCE_PATH = ROOT / "reference" / "skill-router-compound-patterns.md"

# Bulky detail phrases unique to the extracted compound block that must NOT remain
# inline in the always-on router. (Phrases that also appear in the skill registry,
# e.g. "process my comms" in the /email-intel exclusions, are deliberately omitted —
# they are not markers of the extracted compound table.)
_INLINE_DETAIL = [
    "follow up with everyone from",
    "content for the week",
    "win strategy for",
    "HAS depth:",
    "NO depth:",
    "Morning Comms (Pattern 2) fires only on",
]


def test_reference_file_exists():
    assert REFERENCE_PATH.exists(), (
        "reference/skill-router-compound-patterns.md must exist (F-M10 extraction target)"
    )


def test_reference_has_compound_content():
    content = REFERENCE_PATH.read_text(encoding="utf-8")
    required = [
        "Meeting depth", "Morning comms", "Post-event", "Weekly content",
        "Deal depth", "Session boot", "Push & backup",
        "Depth signal examples", "Channel-scope disambiguation",
    ]
    missing = [r for r in required if r not in content]
    assert not missing, f"reference file missing compound content: {missing}"


def test_router_has_no_inline_compound_detail():
    router = ROUTER_PATH.read_text(encoding="utf-8")
    found = [p for p in _INLINE_DETAIL if p in router]
    assert not found, (
        "skill-router.md still carries inline compound-pattern detail that belongs in "
        "reference/skill-router-compound-patterns.md (F-M10):\n  " + "\n  ".join(found)
    )


def test_router_points_to_reference():
    router = ROUTER_PATH.read_text(encoding="utf-8")
    assert "skill-router-compound-patterns.md" in router, (
        "skill-router.md must point to reference/skill-router-compound-patterns.md"
    )


def test_reference_has_standard_headers():
    content = REFERENCE_PATH.read_text(encoding="utf-8")
    assert content.startswith("#"), "reference file must start with an H1 heading"
    assert "Last Updated:" in content, "reference file must carry a Last Updated date"
