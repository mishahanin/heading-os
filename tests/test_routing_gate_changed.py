"""Regression tests for the changed-scope routing gate in skill-trigger-test.py.

Encodes the plan's Success Signal deterministically (no real API):
  - changed_routing_skills scopes to exactly the changed skill(s); a skill-router.md
    change widens to all skills with a triggers.json; an unrelated change -> [].
  - main(--changed) with an empty scope exits 0 with zero judge calls (no API key).
  - main(--changed --strict) exits 1 when the (stubbed) judge fails a changed skill.

The module is hyphenated, so it is loaded by path (importlib) and patched via the
loaded module object, following the tests/test_next_signal.py precedent.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "skill_trigger_test", ROOT / "scripts" / "skill-trigger-test.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# changed_routing_skills scoping
# ---------------------------------------------------------------------------

def test_scope_single_skill(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "_git_changed_files",
                        lambda base="origin/main": {".claude/skills/osint/triggers.json"})
    assert mod.changed_routing_skills() == ["osint"]


def test_scope_router_change_widens_to_all(monkeypatch):
    mod = _load()
    monkeypatch.setattr(
        mod, "_git_changed_files",
        lambda base="origin/main": {".claude/rules/skill-router.md",
                                    ".claude/skills/osint/SKILL.md"},
    )
    assert mod.changed_routing_skills() == mod.list_skills_with_triggers()


def test_scope_unrelated_change_empty(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "_git_changed_files",
                        lambda base="origin/main": {"scripts/foo.py", "README.md"})
    assert mod.changed_routing_skills() == []


def test_scope_ignores_skill_without_triggers(monkeypatch):
    mod = _load()
    # A SKILL.md change for a skill that has NO triggers.json must not enter scope.
    monkeypatch.setattr(
        mod, "_git_changed_files",
        lambda base="origin/main": {".claude/skills/__definitely_not_a_skill__/SKILL.md"},
    )
    assert mod.changed_routing_skills() == []


# ---------------------------------------------------------------------------
# main(--changed) wiring (Success Signal)
# ---------------------------------------------------------------------------

def test_main_changed_empty_scope_exits_zero_no_judge(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "changed_routing_skills", lambda base="origin/main": [])

    def _boom(*a, **k):
        raise AssertionError("judge_query must not be called on an empty scope")

    monkeypatch.setattr(mod, "judge_query", _boom)
    # No ANTHROPIC_API_KEY needed - empty scope returns before the key check.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert mod.main(["--changed"]) == 0


def test_main_changed_strict_fails_on_breaking_skill(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "changed_routing_skills", lambda base="origin/main": ["osint"])
    # Non-empty scope legitimately needs a key + client; supply a dummy key and a
    # stub client so the run reaches run_skill, then stub the judge to fail every case.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **k: object())
    monkeypatch.setattr(
        mod, "judge_query",
        lambda *a, **k: {"routes_to_target": None, "skill": "?", "reason": "stub"},
    )
    # Every case scores as a miss -> pass rate 0 < 0.85 -> strict breach -> exit 1.
    assert mod.main(["--changed", "--strict", "--threshold", "0.85"]) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
