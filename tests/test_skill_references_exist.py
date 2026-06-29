#!/usr/bin/env python3
"""Regression: skill reference files that are loaded at runtime must exist on disk.

If a reference file is deleted or renamed, the skill that depends on it silently
degrades at runtime. This test fails fast at the repo level.
"""
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Each tuple: (skill_slug, relative_path_from_engine_root, why_it_matters)
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


# ---------------------------------------------------------------------------
# F-L11: SKILL.md must not carry bare engine-path references to
# docs/superpowers/specs/ — those specs live in the data overlay
# (.heading-os-data/docs/superpowers/specs/), not the engine clone.
# ---------------------------------------------------------------------------
import re  # noqa: E402

SKILLS_DIR = ROOT / ".claude" / "skills"

# A docs/superpowers/specs/ ref NOT followed by a data-overlay annotation.
_BARE_SPEC_REF = re.compile(
    r"(?<!heading-os-data/)docs/superpowers/specs/[A-Za-z0-9_.-]+\.md(?!`?\s*\(data overlay:)"
)
_OVERLAY_REF = re.compile(r"\.heading-os-data/(docs/superpowers/specs/[A-Za-z0-9_.-]+\.md)")


def _skill_md_files():
    return sorted(SKILLS_DIR.rglob("SKILL.md"))


def test_no_bare_superpowers_spec_references():
    """No SKILL.md may reference docs/superpowers/specs/ without a data-overlay note."""
    violations = []
    for skill_md in _skill_md_files():
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
    data_root = next(
        (d for d in (ROOT.parent / ".heading-os-data", ROOT.parent / "heading-os-data") if d.exists()),
        None,
    )
    if data_root is None:
        pytest.skip("Data root not present on this machine — skipping data-path existence check")
    missing = []
    for skill_md in _skill_md_files():
        for m in _OVERLAY_REF.finditer(skill_md.read_text(encoding="utf-8")):
            if not (data_root / m.group(1)).exists():
                missing.append(f"{skill_md.relative_to(ROOT).as_posix()}: {data_root / m.group(1)}")
    assert not missing, "Data-overlay spec paths that do not exist:\n  " + "\n  ".join(missing)
