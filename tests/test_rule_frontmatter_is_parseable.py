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


@pytest.mark.parametrize("path", _rule_files(), ids=lambda p: p.name)
def test_a_rule_declaring_paths_starts_with_its_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    declares_scoping = any(
        line.strip() == "paths:" for line in text.splitlines()
    )
    if not declares_scoping:
        return
    assert text.startswith("---\n"), (
        f"{path.name} declares `paths:` but does not begin with `---`, so Claude "
        f"Code never parses the declaration and the rule loads in EVERY session "
        f"({len(text)} bytes) instead of only when its paths match. Move any "
        f"leading HTML comment below the closing `---` of the frontmatter.")
