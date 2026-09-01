#!/usr/bin/env python3
"""Regression: skill reference files that are loaded at runtime must exist on disk.

If a reference file is deleted or renamed, the skill that depends on it silently
degrades at runtime. This test fails fast at the repo level.

The docstring above states a rule about a class of file. Until 2026-09-01 the
code below stated it about ONE file: `_REQUIRED_SKILL_REFERENCES` was a
hand-written list holding a single tuple, for `brain-audit/references/
modalities.md`, over a tree of 94 SKILL.md files. Every other skill's reference
files were unguarded, and a hand-written list is exactly the thing that stops
being extended - nobody adding a skill in 2026-08 went looking for a registry in
`tests/`.

Derived instead: every `references/<file>.md` a SKILL.md cites is resolved
against that skill's own `references/` directory. MEASURED 2026-09-01: 78
citations across 94 skills, 0 missing, and the single hand-listed entry is one
of the 78. The floors below pin the walk, because a citation regex that stops
matching passes everything in silence.
"""
import re

import pytest
from pathlib import Path

from scripts.utils.paths import data_overlay_present, get_data_root

ROOT = Path(__file__).resolve().parent.parent

# A `references/<name>.md` citation. The lookbehind keeps it from firing inside a
# longer path (`skills/foo/references/bar.md`, `docs/references/x.md`) - those
# resolve somewhere else and are not this rule's business.
_OWN_REFERENCE = re.compile(r"(?<![\w./-])references/([A-Za-z0-9][A-Za-z0-9_.-]*\.md)")


def _cited_references() -> list[tuple[str, Path]]:
    """(skill name, expected path) for every own-references citation in the tree."""
    cited = []
    for skill_md in _skill_md_files():
        text = skill_md.read_text(encoding="utf-8")
        for name in sorted(set(_OWN_REFERENCE.findall(text))):
            cited.append((skill_md.parent.name, skill_md.parent / "references" / name))
    return cited


def test_the_citation_walk_reaches_the_tree():
    """A guard is green over an empty corpus, and over a regex that stopped matching.

    Both floors are measured (94 SKILL.md, 78 citations on 2026-09-01) and set
    well below, so retiring a skill cannot fail this while a collapsed walk does.
    """
    skills = _skill_md_files()
    assert len(skills) > 50, f"only {len(skills)} SKILL.md files found under {SKILLS_DIR}"
    cited = _cited_references()
    assert len(cited) > 40, f"only {len(cited)} references/*.md citations parsed"


def test_every_reference_a_skill_cites_exists():
    """The class the docstring claims, not the one file it used to check."""
    missing = [f"{skill}: {path.relative_to(ROOT).as_posix()}"
               for skill, path in _cited_references() if not path.is_file()]
    assert missing == [], (
        "SKILL.md files cite reference files that are not on disk, so the skill "
        "degrades silently at runtime:\n  " + "\n  ".join(missing)
    )


def test_the_citation_matcher_would_catch_a_deletion(tmp_path: Path):
    """Positive control: a regex that matches nothing passes the check above forever."""
    assert _OWN_REFERENCE.findall("Read `references/modalities.md` first.") == [
        "modalities.md"]
    assert _OWN_REFERENCE.findall("see references/a-b_c.1.md") == ["a-b_c.1.md"]
    # And it does NOT claim a path that belongs to someone else's tree.
    assert _OWN_REFERENCE.findall("docs/references/other.md") == []
    assert _OWN_REFERENCE.findall(".claude/skills/x/references/y.md") == []


# The one entry that used to BE this file. Kept as a named case with its reason,
# because the derived walk above proves the citation resolves and this proves the
# workspace still depends on it: a skill that stopped citing the file would pass
# the derived check by no longer being in its corpus.
_REQUIRED_SKILL_REFERENCES = [
    (
        "brain-audit",
        ".claude/skills/brain-audit/references/modalities.md",
        "Phase 2.2 reads this file to get the canonical modality list for source coverage",
    ),
]


@pytest.mark.parametrize("skill,rel_path,reason", _REQUIRED_SKILL_REFERENCES)
def test_skill_reference_file_exists(skill, rel_path, reason):
    """Assert that each skill reference file exists on disk."""
    target = ROOT / rel_path
    assert target.is_file(), (
        f"/{skill}: required reference file missing: {rel_path}\n"
        f"Reason: {reason}\n"
        f"Do not delete this file without updating the skill and this test."
    )


@pytest.mark.parametrize("skill,rel_path,reason", _REQUIRED_SKILL_REFERENCES)
def test_the_named_reference_is_still_cited_by_its_skill(skill, rel_path, reason):
    """A file that exists but nothing reads is not what this entry claims."""
    name = Path(rel_path).name
    text = (ROOT / ".claude" / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    assert name in _OWN_REFERENCE.findall(text), (
        f"/{skill} no longer cites references/{name}, so the requirement recorded "
        f"here ({reason}) describes a dependency that is gone"
    )


# ---------------------------------------------------------------------------
# F-L11: SKILL.md must not carry bare engine-path references to
# docs/superpowers/specs/ — those specs live in the data overlay
# (.heading-os-data/docs/superpowers/specs/), not the engine clone.
# ---------------------------------------------------------------------------
# `re` is imported at the top of this module; the local re-import that used to
# sit here shadowed nothing and hid the fact that the file already had it.

SKILLS_DIR = ROOT / ".claude" / "skills"

# A docs/superpowers/specs/ ref NOT followed by a data-overlay annotation.
_BARE_SPEC_REF = re.compile(
    r"(?<!heading-os-data/)docs/superpowers/specs/[A-Za-z0-9_.-]+\.md(?!`?\s*\(data overlay:)"
)
_OVERLAY_REF = re.compile(r"\.heading-os-data/(docs/superpowers/specs/[A-Za-z0-9_.-]+\.md)")


def _skill_md_files():
    """Every SKILL.md on disk, tracked or not.

    Deliberately NOT routed through `tests.repo_files.tracked_paths`. The usual
    reason for that routing is an agent worktree under `.claude/worktrees/`
    doubling a corpus, and this glob cannot reach one: it is rooted at
    `.claude/skills`, a sibling. What routing through git WOULD cost is the case
    this file exists for - a skill added in the working tree and not yet staged,
    citing a reference file its author has not written. That is the SKILL.md most
    likely to carry the defect, and the one a tracked-paths walk cannot see.
    `test_the_citation_walk_reaches_the_tree` pins the floor instead.
    """
    return sorted(SKILLS_DIR.rglob("SKILL.md"))


def test_no_bare_superpowers_spec_references():
    """No SKILL.md may reference docs/superpowers/specs/ without a data-overlay note."""
    violations = []
    skill_mds = _skill_md_files()
    assert len(skill_mds) > 50, (
        f"only {len(skill_mds)} SKILL.md files walked; this check reports clean "
        f"over an empty corpus, so the floor is the guard")
    for skill_md in skill_mds:
        for lineno, line in enumerate(skill_md.read_text(encoding="utf-8").splitlines(), 1):
            if _BARE_SPEC_REF.search(line):
                violations.append(f"{skill_md.relative_to(ROOT).as_posix()}:{lineno}: {line.strip()}")
    assert not violations, (
        f"{len(violations)} bare docs/superpowers/specs/ reference(s) in SKILL.md files "
        f"(these resolve in the data overlay, not the engine clone). Append "
        f"' (data overlay: .heading-os-data/docs/superpowers/specs/<slug>.md)' to each (F-L11):\n  "
        + "\n  ".join(violations)
    )


def test_annotated_spec_paths_exist_in_data_overlay():
    """Each data-overlay annotation must point to a file that exists in the data sibling."""
    if not data_overlay_present():
        pytest.skip("Data root not present on this machine — skipping data-path existence check")
    data_root = get_data_root()
    missing = []
    for skill_md in _skill_md_files():
        for m in _OVERLAY_REF.finditer(skill_md.read_text(encoding="utf-8")):
            if not (data_root / m.group(1)).exists():
                missing.append(f"{skill_md.relative_to(ROOT).as_posix()}: {data_root / m.group(1)}")
    assert not missing, "Data-overlay spec paths that do not exist:\n  " + "\n  ".join(missing)
