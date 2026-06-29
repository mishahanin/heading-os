"""Regression test: bandit must not exclude .claude/skills from analysis (F-L9)."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_bandit_does_not_exclude_skills():
    """skills/ contains Python invocations; bandit must scan them."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    bandit = data.get("tool", {}).get("bandit", {})
    excluded = bandit.get("exclude_dirs", [])
    assert ".claude/skills" not in excluded, (
        ".claude/skills must not be in bandit exclude_dirs — "
        "skill files can contain Python patterns that need scanning (F-L9)"
    )


def test_bandit_excludes_tests():
    """tests/ is the only directory that should be excluded."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    bandit = data.get("tool", {}).get("bandit", {})
    excluded = bandit.get("exclude_dirs", [])
    assert "tests" in excluded, "tests/ must remain excluded from bandit"
