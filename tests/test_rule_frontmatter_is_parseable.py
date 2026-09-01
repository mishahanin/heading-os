"""A rule that declares `paths:` must actually be path-scoped.

Found by the 2026-08-09 best-practices audit. Claude Code reads a rule's YAML
frontmatter only when the file BEGINS with `---`. Two rules opened with an HTML
version marker instead:

    <!-- version: 1.3.2 | last-updated: 2026-07-20 -->
    ---
    paths:
      - ".claude/skills/**"
    ---

so the block below the comment was body text, not frontmatter, and the scoping
silently did nothing. `datastore.md` and `development-standards.md` loaded into
every session, 29,142 bytes of context, instead of loading when their paths
matched. Nothing failed; the rules simply were not scoped.

The version marker itself is a `documentation.md` convention for the shared docs
in `templates/` and `docs/`, checked by `workspace-health.check_doc_versions`,
which reads NEITHER of these files. The rules had merely inherited the habit at
the 2026-06-29 import. So the marker moves below the frontmatter and nothing
that reads it is affected.

This test states the invariant the workspace could not previously see: if a rule
declares scoping, the harness must be able to read that declaration.
"""
from __future__ import annotations

from pathlib import Path

import pytest

RULES_DIR = Path(__file__).resolve().parent.parent / ".claude" / "rules"


def _rule_files() -> list[Path]:
    return sorted(RULES_DIR.glob("*.md"))


def _declares_scoping(text: str) -> bool:
    """One definition, shared by the case below and the floor at the bottom.

    Written as a helper rather than inline so the floor is counting the same
    thing the assertion is gated on. Two copies of this predicate would let the
    floor stay satisfied while the gate stopped recognising a single rule.
    """
    return any(line.strip() == "paths:" for line in text.splitlines())


@pytest.mark.parametrize("path", _rule_files(), ids=lambda p: p.name)
def test_a_rule_declaring_paths_starts_with_its_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    if not _declares_scoping(text):
        return
    assert text.startswith("---\n"), (
        f"{path.name} declares `paths:` but does not begin with `---`, so Claude "
        f"Code never parses the declaration and the rule loads in EVERY session "
        f"({len(text)} bytes) instead of only when its paths match. Move any "
        f"leading HTML comment below the closing `---` of the frontmatter.")


def test_the_scan_still_finds_the_rules():
    """An empty parametrize is one silent skip, not a failure. `.claude/rules/`
    ships with the engine, so an empty result means the directory moved, never
    a thin clone. 26 on 2026-08-26.
    """
    found = _rule_files()

    assert len(found) >= 15, f"only {len(found)} rules reached the frontmatter gate"


def test_the_gate_still_has_scoped_rules_to_be_a_gate_over():
    """The floor above counts FILES; every case can still assert nothing.

    The parametrized test returns early for a rule that declares no `paths:`, so
    the effective corpus is the scoped subset, and the file-count floor is
    satisfied by 26 unscoped rules. If the declaration ever moves to another key
    (`path_globs:`, a `scope:` block, frontmatter carried some other way), every
    case returns at line one, 26 tests report green, and the invariant is
    measured nowhere.

    12 of 26 rules declared `paths:` on 2026-09-01. The floor is set well under
    that so ordinary churn does not trip it, and well over zero so a change of
    convention does.
    """
    scoped = [p for p in _rule_files()
              if _declares_scoping(p.read_text(encoding="utf-8"))]

    assert len(scoped) >= 6, (
        f"only {len(scoped)} of {len(_rule_files())} rules declare `paths:`, so "
        f"the frontmatter gate above is asserting on almost nothing. If the "
        f"scoping convention changed, change `_declares_scoping` with it.")
