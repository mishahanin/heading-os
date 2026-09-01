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

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CANONICAL = ROOT / ".claude" / "rules" / "hidden-chars.md"
LITERAL = "Hidden characters: clean"

# The 2026-08-29 audit found the guard was a plain substring test, so the
# BOLDED form `**Hidden characters:** clean` walked straight through it: the
# markdown emphasis sits between the words the literal joins. The matcher now
# tolerates emphasis and code runs anywhere inside the phrase and around the
# colon, which is the only difference.
#
# What it deliberately still does NOT match, and why the files that document
# this trap keep passing: the pattern requires the word "clean" to be the
# value, immediately after the colon. `.claude/rules/hidden-chars.md` and
# `.claude/rules/humanization.md` both write a PLACEHOLDER there
# (`<what the scan reported>`), and only mention "clean" later in the sentence
# as one possible value. The guard hunts a pre-written outcome, never the
# phrase, so documenting the trap is not the same as committing it. Widening
# it to "any line naming the phrase near the word clean" would fail exactly
# the rule files that define the correct behaviour.
EMPH = r"[\s*_`]"
PREWRITTEN = re.compile(
    rf"hidden{EMPH}+characters{EMPH}*:{EMPH}*clean", re.IGNORECASE
)

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
                     if PREWRITTEN.search(line)]
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
    for n, line in enumerate(text.splitlines(), 1):
        assert not PREWRITTEN.search(line), (
            f"the canonical rule now hands the writer a pre-filled outcome too, "
            f"at {CANONICAL.name}:{n}"
        )


def test_the_matcher_sees_through_markdown_emphasis():
    """The hole this guard had until 2026-08-29.

    A plain `LITERAL in line` test cannot see `**Hidden characters:** clean`,
    because the emphasis markers sit inside the phrase. One such line lived in
    `workspace-deep-audit/references/output-template.md` for the guard's whole
    life. Without this positive control, a future edit could quietly narrow the
    matcher back to a substring test and every file would still pass.
    """
    must_match = (
        LITERAL,
        "**Hidden characters:** clean (sanitizer-verified)",
        "*Hidden characters*: clean",
        "`Hidden characters: clean`",
        "__Hidden characters:__  clean",
        "**Hidden** **characters:** clean",
        "- **hidden characters:**clean",
    )
    for sample in must_match:
        assert PREWRITTEN.search(sample), f"matcher missed the pre-filled outcome: {sample!r}"

    # The other direction. These DOCUMENT the trap instead of committing it,
    # and a matcher that fails them would fail the rules that define the
    # correct behaviour. Both are real lines from the tree.
    must_not_match = (
        "run the sanitizer and carry its result: `Word count: X. Hidden characters: "
        '<what the scan reported>.` "clean" is one possible value, not the template.',
        "> Word count: X. Hidden characters: <what the scan found>. "
        "Humanisation audit: clean / N findings (one-line summary of fixes if any).",
        "**Hidden characters:** {what `scripts/sanitize-text.py --scan` reported}",
        "Hidden characters: 3 zero-width spaces removed",
    )
    for sample in must_not_match:
        assert not PREWRITTEN.search(sample), (
            f"matcher punishes a file for documenting the trap: {sample!r}"
        )


def test_the_detector_reads_real_files():
    """A path list that resolves to nothing would pass the first test forever."""
    scanned = sum(1 for base in SEARCHED if base.is_dir()
                  for _ in base.rglob("*.md"))
    assert scanned > 100, f"the sweep only looked at {scanned} files"
    assert CANONICAL.is_file()


def test_every_searched_root_contributes_to_the_sweep():
    """A floor over the union is satisfied while a root contributes zero.

    `.claude/skills` alone holds 201 markdown files, so the `> 100` above is met
    by that one root and says nothing about the other four. `_sites()` skips a
    root that is not a directory in silence, so a rename of `.claude/agents` or
    of `reference/` would remove those files from the guard and leave every
    assertion in this module green. Measured 2026-09-01: rules 26, skills 201,
    agents 4, reference 36, docs 23.

    Per root, not in total, and with no per-root number to maintain: each of the
    five must exist and hold at least one markdown file.
    """
    empty = []
    for base in SEARCHED:
        rel = base.relative_to(ROOT).as_posix()
        if not base.is_dir():
            empty.append(f"{rel} (missing)")
            continue
        if not any(base.rglob("*.md")):
            empty.append(f"{rel} (no .md)")
    assert not empty, (
        f"these roots contribute nothing to the confirmation-line sweep, so "
        f"anything under them can pre-write the outcome unnoticed: {empty}")


def test_the_sweep_deliberately_does_not_ask_git():
    """Why this is a hand walk and not `tests.repo_files.tracked_paths`.

    Every other corpus sweep in this suite routes through the git-aware walker,
    because a worktree under `.claude/worktrees/` doubles the corpus. None of
    the five roots below is that path, and here the untracked files are the
    point: a skill or rule an agent has just written and not yet staged is
    exactly the file most likely to carry a pre-filled outcome, and a
    tracked-only sweep would not see it until after it was committed.

    This test exists so that reasoning is on the record rather than inferred
    from the absence of an import, and so a future migration to the walker is a
    deliberate change with this note in front of it.
    """
    assert not any("worktrees" in base.as_posix() for base in SEARCHED)
    for base in SEARCHED:
        assert base.is_relative_to(ROOT)
