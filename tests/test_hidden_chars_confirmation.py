"""The confirmation line lives in one rule, and nowhere pre-writes its outcome.

Found by the 2026-08-23 audit. `.claude/rules/hidden-chars.md` declares itself the
canonical owner of the line every deliverable carries, and says any other rule or
skill "defers to this file rather than restating it". Sixteen places restated it,
and every one of them baked in the clean outcome:

    Word count: X. Hidden characters: clean.

The rule itself says to report what the scan found and to say so explicitly when
it was not clean. A skill that hands the writer a pre-filled "clean" is nudging
toward stating an outcome instead of reading one — the same shape as the word
count that nothing computed (fixed the same day, see `sanitize-text.py --scan`).

Not a live lie: `hidden-chars.md` is always-on and corrects it every turn. A
nudge, in sixteen places, in the exact spot where honesty is being asserted.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CANONICAL = ROOT / ".claude" / "rules" / "hidden-chars.md"
LITERAL = "Hidden characters: clean"

SEARCHED = (
    ROOT / ".claude" / "rules",
    ROOT / ".claude" / "skills",
    ROOT / ".claude" / "agents",
    ROOT / "reference",
    ROOT / "docs",
)


def _sites() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for base in SEARCHED:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            lines = [n for n, line in
                     enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                     if LITERAL in line]
            if lines:
                found[path.relative_to(ROOT).as_posix()] = lines
    return found


def test_no_rule_or_skill_pre_writes_the_clean_outcome():
    sites = _sites()
    assert sites == {}, (
        "these restate the confirmation line with the outcome already written in; "
        "point them at .claude/rules/hidden-chars.md instead: "
        + ", ".join(f"{path}:{lines}" for path, lines in sites.items())
    )


def test_the_canonical_rule_still_defines_the_line():
    """The other direction: a sweep that deleted the definition would also pass."""
    text = CANONICAL.read_text(encoding="utf-8")
    assert "Word count: X" in text
    assert "Hidden characters:" in text
    assert "canonical owner" in text


def test_the_canonical_rule_does_not_present_clean_as_the_template():
    """It is one possible value the scan can report, not the shape to copy."""
    text = CANONICAL.read_text(encoding="utf-8")
    assert LITERAL not in text, (
        "the canonical rule now hands the writer a pre-filled outcome too"
    )


def test_the_detector_reads_real_files():
    """A path list that resolves to nothing would pass the first test forever."""
    scanned = sum(1 for base in SEARCHED if base.is_dir()
                  for _ in base.rglob("*.md"))
    assert scanned > 100, f"the sweep only looked at {scanned} files"
    assert CANONICAL.is_file()
