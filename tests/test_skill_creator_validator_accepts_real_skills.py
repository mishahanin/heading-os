"""skill-creator's validator must accept the skills this repo actually ships.

Found by the 2026-08-23 audit. `quick_validate.validate_skill` carried an
allowlist of six frontmatter keys — the upstream Anthropic set — and rejected
every key this workspace uses on top of it: `argument-hint`, `model`,
`disable-model-invocation`, `context`, `background`, `effort`, and the four
`x-heading-*` namespaced blocks.

Measured before the fix: 96 of 96 skills failed. That is not cosmetic.
`package_skill.package_skill()` hard-gates on the same function::

    valid, message = validate_skill(skill_path)
    if not valid: ... return None

so packaging was broken for 100% of the repo's skills, and nothing said so.

`scripts/skill-metadata-check.py` is the workspace's real frontmatter contract
and documents WHY the extension keys are namespaced: "This signals 'workspace
extension, not part of Anthropic's standard SKILL.md spec' so future stricter
validation does not strip them." The validator is the stricter validation that
stripped them anyway.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILL_CREATOR = ROOT / ".claude" / "skills" / "skill-creator"
VALIDATOR = SKILL_CREATOR / "scripts" / "quick_validate.py"

# Loaded by path, not by name: the repo root already owns a `scripts` package,
# so `import scripts.quick_validate` resolves to the wrong tree from here.
_spec = importlib.util.spec_from_file_location("_qv_under_test", VALIDATOR)
quick_validate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(quick_validate)

ALLOWED_PROPERTIES = quick_validate.ALLOWED_PROPERTIES
NAMESPACE_PREFIX = quick_validate.NAMESPACE_PREFIX
validate_skill = quick_validate.validate_skill

SKILLS = sorted(p.parent for p in (ROOT / ".claude" / "skills").glob("*/SKILL.md"))


def _frontmatter(skill_dir: Path) -> dict:
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---", 2)[1])


def test_the_repo_has_skills_to_validate():
    """A glob that resolves to nothing would make every other test vacuous."""
    assert len(SKILLS) > 50, f"only found {len(SKILLS)} skills"


@pytest.mark.parametrize("skill_dir", SKILLS, ids=lambda p: p.name)
def test_every_shipped_skill_passes_the_validator(skill_dir: Path):
    valid, message = validate_skill(str(skill_dir))
    assert valid, f"{skill_dir.name}: {message}"


def test_every_frontmatter_key_in_use_is_either_allowed_or_namespaced():
    """The direct claim, independent of validate_skill's other rules."""
    unknown: dict[str, set[str]] = {}
    checked = 0
    for skill_dir in SKILLS:
        for key in _frontmatter(skill_dir):
            checked += 1
            if key in ALLOWED_PROPERTIES or key.startswith(NAMESPACE_PREFIX):
                continue
            unknown.setdefault(key, set()).add(skill_dir.name)
    assert unknown == {}, f"keys the validator would reject: {unknown}"
    # An empty offender dict proves nothing unless keys actually reached the
    # guard. Measured 860 keys on 2026-08-26 across the shipped skills; the
    # floor sits well under that so retiring a skill cannot fail this test.
    # If `_frontmatter` stopped yielding keys (its frontmatter split drifting,
    # or the SKILLS glob resolving to nothing), the body never runs, `unknown`
    # would stay empty, and this assertion is the only thing that would notice.
    assert checked >= 550, f"only {checked} frontmatter keys inspected"


def test_a_genuinely_unknown_key_is_still_rejected(tmp_path: Path):
    """Widening the allowlist must not turn the check into a no-op."""
    skill = tmp_path / "bogus-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: bogus-skill\n"
        "description: A skill with a typo'd key that no contract permits.\n"
        "allowed-tolls: Read\n"
        "---\n\nBody.\n",
        encoding="utf-8",
    )
    valid, message = validate_skill(str(skill))
    assert not valid
    assert "allowed-tolls" in message


def test_an_unnamespaced_vendor_key_is_rejected(tmp_path: Path):
    """`x-heading-*` is the carve-out. A bare vendor key is not."""
    skill = tmp_path / "vendor-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: vendor-skill\n"
        "description: Declares an extension without the namespace prefix.\n"
        "heading-orchestration:\n"
        "  parallel_safe: false\n"
        "---\n\nBody.\n",
        encoding="utf-8",
    )
    valid, message = validate_skill(str(skill))
    assert not valid
    assert "heading-orchestration" in message
