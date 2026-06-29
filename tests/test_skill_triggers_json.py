"""F-M13: routing-sensitive skills must have valid triggers.json files."""
import json
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent
REQUIRED_SKILLS = ["odin", "email-draft", "thread", "linkedin-series"]


@pytest.mark.parametrize("skill_name", REQUIRED_SKILLS)
def test_triggers_json_exists(skill_name):
    path = ENGINE / ".claude" / "skills" / skill_name / "triggers.json"
    assert path.exists(), f"{skill_name}/triggers.json must exist (F-M13)"


@pytest.mark.parametrize("skill_name", REQUIRED_SKILLS)
def test_triggers_json_is_valid_json(skill_name):
    path = ENGINE / ".claude" / "skills" / skill_name / "triggers.json"
    if not path.exists():
        pytest.skip(f"{skill_name}/triggers.json missing")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, list), f"{skill_name}/triggers.json must be a JSON array"


@pytest.mark.parametrize("skill_name", REQUIRED_SKILLS)
def test_triggers_json_has_required_fields(skill_name):
    path = ENGINE / ".claude" / "skills" / skill_name / "triggers.json"
    if not path.exists():
        pytest.skip(f"{skill_name}/triggers.json missing")
    data = json.loads(path.read_text(encoding="utf-8"))
    for i, entry in enumerate(data):
        assert "query" in entry, f"{skill_name} entry[{i}] missing 'query'"
        assert "should_trigger" in entry, f"{skill_name} entry[{i}] missing 'should_trigger'"
        assert isinstance(entry["query"], str), f"{skill_name} entry[{i}]['query'] must be a string"
        assert isinstance(entry["should_trigger"], bool), \
            f"{skill_name} entry[{i}]['should_trigger'] must be a bool"


@pytest.mark.parametrize("skill_name", REQUIRED_SKILLS)
def test_triggers_json_has_positives_and_negatives(skill_name):
    path = ENGINE / ".claude" / "skills" / skill_name / "triggers.json"
    if not path.exists():
        pytest.skip(f"{skill_name}/triggers.json missing")
    data = json.loads(path.read_text(encoding="utf-8"))
    positives = [e for e in data if e.get("should_trigger") is True]
    negatives = [e for e in data if e.get("should_trigger") is False]
    assert len(positives) >= 6, \
        f"{skill_name}/triggers.json must have at least 6 positive cases (got {len(positives)})"
    assert len(negatives) >= 6, \
        f"{skill_name}/triggers.json must have at least 6 negative cases (got {len(negatives)})"
