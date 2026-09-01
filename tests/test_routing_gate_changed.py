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


@pytest.mark.parametrize("router_file", [
    ".claude/rules/skill-router.md",
    # The router rule was split into per-category reference files, and a change
    # to one of them widens scope for the same reason the rule itself does. Only
    # the first path was covered until 2026-09-01: MEASURED, deleting the
    # `reference/skill-router/` clause from `changed_routing_skills` left this
    # test green, so an edit to a category file would have silently narrowed the
    # gate to whichever skills happened to change alongside it.
    "reference/skill-router/intel.md",
])
def test_scope_router_change_widens_to_all(monkeypatch, router_file):
    mod = _load()
    monkeypatch.setattr(
        mod, "_git_changed_files",
        lambda base="origin/main": {router_file, ".claude/skills/osint/SKILL.md"},
    )
    assert mod.changed_routing_skills() == mod.list_skills_with_triggers()


def test_scope_a_non_markdown_router_reference_does_not_widen(monkeypatch):
    """The near miss, so the clause above is a rule and not a substring match.

    `reference/skill-router/` is generated; a stray non-markdown artifact under
    it is not a routing change and must not drag all 70-odd skills into a paid
    judge run.
    """
    mod = _load()
    monkeypatch.setattr(
        mod, "_git_changed_files",
        lambda base="origin/main": {"reference/skill-router/.gitkeep"},
    )
    assert mod.changed_routing_skills() == []


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


def _wire_judge(mod, monkeypatch, verdict):
    """Put main() in reach of run_skill with a deterministic two-case skill.

    Non-empty scope legitimately needs a key + client, so a dummy key and a stub
    client stand in. `load_triggers` is stubbed too: reading the real
    `.claude/skills/osint/triggers.json` would make the arithmetic below depend
    on how many of that file's cases happen to be negative, which is a number
    this test has no business pinning.
    """
    monkeypatch.setattr(mod, "changed_routing_skills", lambda base="origin/main": ["osint"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **k: object())
    monkeypatch.setattr(mod, "load_triggers", lambda skill_dir: [
        {"query": "invented probe one", "should_trigger": True},
        {"query": "invented probe two", "should_trigger": True},
    ])
    monkeypatch.setattr(mod, "judge_query", lambda *a, **k: verdict)


def test_main_changed_strict_fails_on_breaking_skill(monkeypatch):
    """A judged pass rate under the threshold breaches the gate.

    The judge answers a real BOOLEAN here. Until 2026-09-01 the stub returned
    `routes_to_target: None`, which `run_skill` scores as errored rather than
    missed: the report came back with `cases: 0`, the `breached` list stayed
    empty, and the exit 1 came from the `unmeasured` clause instead. The test
    named the threshold, passed `--threshold 0.85`, and measured neither.
    MEASURED: with `breached = []` forced, the old test stayed green.
    """
    mod = _load()
    _wire_judge(mod, monkeypatch,
                {"routes_to_target": False, "skill": "other", "reason": "stub"})
    # Both cases expect a trigger and the judge says no: 0/2 = 0.0 < 0.85.
    assert mod.main(["--changed", "--strict", "--threshold", "0.85"]) == 1


def test_main_changed_strict_passes_a_clean_measurement(monkeypatch):
    """The boundary: a gate that always returned 1 would satisfy the test above."""
    mod = _load()
    _wire_judge(mod, monkeypatch,
                {"routes_to_target": True, "skill": "osint", "reason": "stub"})
    # 2/2 = 1.0 >= 0.85, nothing skipped, nothing unmeasured.
    assert mod.main(["--changed", "--strict", "--threshold", "0.85"]) == 0


def test_main_changed_strict_fails_when_the_judge_returns_no_verdict(monkeypatch):
    """The other failing route, named for what it actually is.

    A non-boolean verdict is UNMEASURED, not a miss: `run_skill` counts it under
    `errored` and reports `cases: 0`. Strict must still fail - a skill nobody
    could measure is not a clean measurement - but through the `unmeasured`
    clause, not through the threshold.
    """
    mod = _load()
    _wire_judge(mod, monkeypatch,
                {"routes_to_target": None, "skill": "?", "reason": "stub"})
    assert mod.main(["--changed", "--strict", "--threshold", "0.0"]) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
