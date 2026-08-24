"""The floor audit, and the conflation it exists to prevent.

One defect motivates this whole file. On 2026-08-19 a figure of 45 974 was
recorded as "the skill catalogue's name and description entries" when it was the
ENTIRE YAML frontmatter of 96 skills. A plan then set a 15 000-token reduction
target against a surface that holds about 12 300 tokens in total, so the target
was arithmetically unreachable and nobody noticed for a full revision.

The split is therefore the property under test, not a formatting nicety.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "context_floor_audit", str(ROOT / "scripts" / "context-floor-audit.py")
)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


SKILL = """---
name: example-skill
description: >
  A folded description that runs across
  several lines, as most of them do.
allowed-tools: "Read"
metadata:
  version: "1.0"
---

# Body text that is never part of the frontmatter.
"""


@pytest.fixture()
def tree(tmp_path):
    skills = tmp_path / ".claude" / "skills" / "example-skill"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(SKILL, encoding="utf-8")
    rules = tmp_path / ".claude" / "rules"
    rules.mkdir(parents=True)
    return tmp_path


def _rule(tree: Path, name: str, body: str) -> None:
    (tree / ".claude" / "rules" / name).write_text(body, encoding="utf-8")


# ============================================================
# The split
# ============================================================

def test_description_is_measured_apart_from_the_rest_of_the_frontmatter(tree):
    result = audit.measure_skills(tree)
    assert result["skills"] == 1
    assert result["description_bytes"] > 0
    assert result["other_frontmatter_bytes"] > 0
    assert (
        result["description_bytes"] + result["other_frontmatter_bytes"]
        == result["frontmatter_bytes"]
    ), "the two components must partition the frontmatter, not overlap it"


def test_a_folded_description_is_measured_whole(tree):
    """A single-line match would undercount most of the catalogue.

    Skill descriptions are usually folded scalars spanning several lines, and
    reading only the first would report a fraction of the real surface - the same
    class of error the whole script exists to correct.
    """
    result = audit.measure_skills(tree)
    assert result["description_bytes"] > 60, (
        "only the first line of a folded description was counted: "
        f"{result['description_bytes']} bytes"
    )


def test_the_body_is_not_counted(tree):
    """Skill BODIES are not loaded; counting them would inflate the floor."""
    result = audit.measure_skills(tree)
    assert b"Body text" not in SKILL.encode()[: result["frontmatter_bytes"]]
    assert result["frontmatter_bytes"] < len(SKILL.encode("utf-8"))


# ============================================================
# Always-on versus path-scoped
# ============================================================

def test_a_rule_without_frontmatter_is_always_on(tree):
    _rule(tree, "voice.md", "# Voice\n\nSome text.\n")
    result = audit.measure_rules(tree)
    assert [r["rule"] for r in result["always_on"]] == ["voice.md"]
    assert result["path_scoped"] == []


def test_a_rule_with_an_empty_paths_list_is_always_on(tree):
    _rule(tree, "router.md", "---\npaths: []\nalways_active: true\n---\n\nbody\n")
    result = audit.measure_rules(tree)
    assert [r["rule"] for r in result["always_on"]] == ["router.md"]


def test_a_rule_with_paths_is_scoped_and_left_out_of_the_total(tree):
    _rule(
        tree, "scope.md",
        '---\npaths:\n  - "scripts/**"\n  - ".claude/hooks/**"\n---\n\nbody\n',
    )
    result = audit.measure_rules(tree)
    assert [r["rule"] for r in result["path_scoped"]] == ["scope.md"]
    assert result["always_on_bytes"] == 0

    whole = audit.measure(tree)
    assert whole["components"]["always_on_rules"] == 0
    assert whole["path_scoped_rules_bytes"] > 0, (
        "path-scoped rules must be reported, just not inside the total"
    )


# ============================================================
# The baseline comparison
# ============================================================

def test_the_baseline_flags_growth_beyond_tolerance(tree, monkeypatch, capsys):
    monkeypatch.setattr(audit, "get_workspace_root", lambda: tree)
    baseline = tree / audit.BASELINE_PATH
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text(json.dumps({"total_bytes": 10}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["context-floor-audit.py", "--baseline"])
    assert audit.main() == 1
    # stderr since 2026-08-24. `--json --baseline` printed the verdict onto
    # stdout AFTER the JSON document, so a machine caller got an unparseable
    # mix; this test asserted the stream rather than the invariant, which is
    # "the verdict is reported".
    assert "Floor grew" in capsys.readouterr().err


def test_the_baseline_passes_when_the_floor_holds(tree, monkeypatch, capsys):
    monkeypatch.setattr(audit, "get_workspace_root", lambda: tree)
    baseline = tree / audit.BASELINE_PATH
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text(json.dumps({"total_bytes": 10_000_000}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["context-floor-audit.py", "--baseline"])
    assert audit.main() == 0


def test_a_missing_baseline_is_reported_not_assumed_clean(tree, monkeypatch, capsys):
    """scope-claims: absent evidence must not read as a pass."""
    monkeypatch.setattr(audit, "get_workspace_root", lambda: tree)
    monkeypatch.setattr(sys, "argv", ["context-floor-audit.py", "--baseline"])
    assert audit.main() == 1
    assert "No baseline" in capsys.readouterr().err  # stderr since 2026-08-24


def test_the_output_states_what_it_cannot_see(tree, monkeypatch, capsys):
    """The system prompt and tool schemas are not on disk and are not counted."""
    monkeypatch.setattr(audit, "get_workspace_root", lambda: tree)
    monkeypatch.setattr(sys, "argv", ["context-floor-audit.py"])
    assert audit.main() == 0
    out = capsys.readouterr().out
    assert "system prompt" in out and "NOT counted" in out
    assert "estimate" in out, "token figures must not be presented as exact"
