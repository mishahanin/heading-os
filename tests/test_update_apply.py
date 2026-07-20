import pytest
from scripts.utils.update_registry import Component
from scripts.utils import update_apply as ua

def _comp(tier="auto", hold=False, health=None):
    return Component(name="fake", tier=tier, display="Fake",
                     current={"via": "shell", "cmd": "echo 1"},
                     latest={"via": "pypi", "package": "fake"},
                     apply={"cmd": "true"}, health=health, hold=hold)

def test_health_failure_triggers_rollback(monkeypatch):
    calls = []
    monkeypatch.setattr(ua, "run_health", lambda comp: False)
    result = ua.apply_one(_comp(health={"cmd": "false"}),
                          applier=lambda: calls.append("applied"),
                          rollback=lambda: calls.append("rolled-back"))
    assert result == "rolled-back"
    assert calls == ["applied", "rolled-back"]

def test_health_pass_keeps_new_version(monkeypatch):
    calls = []
    monkeypatch.setattr(ua, "run_health", lambda comp: True)
    result = ua.apply_one(_comp(health={"cmd": "true"}),
                          applier=lambda: calls.append("applied"),
                          rollback=lambda: calls.append("rolled-back"))
    assert result == "applied"
    assert calls == ["applied"]

def test_held_component_is_skipped():
    result = ua.apply_one(_comp(hold=True),
                          applier=lambda: (_ for _ in ()).throw(AssertionError("must not apply")),
                          rollback=lambda: None)
    assert result == "skipped"

def test_observed_component_is_skipped():
    comp = Component(name="ollama", tier="observed", display="Ollama",
                     current={"via": "shell", "cmd": "echo 1"},
                     latest={"via": "github_release", "repo": "x/y"})
    result = ua.apply_one(comp, applier=lambda: 1/0, rollback=lambda: None)
    assert result == "skipped"

def test_build_rollback_substitutes_prev_and_runs(tmp_path):
    marker = tmp_path / "rolled-back-to.txt"
    comp = Component(name="fake", tier="auto", display="Fake",
                     current={"via": "shell", "cmd": "echo 1"},
                     latest={"via": "pypi", "package": "fake"},
                     apply={"cmd": "true", "rollback_cmd": f"echo {{prev}} > {marker}"})
    rollback = ua._build_rollback(comp, "9.9.9")
    rollback()
    assert marker.read_text().strip() == "9.9.9"

def test_build_rollback_noop_for_script_apply():
    comp = Component(name="cpx", tier="notify", display="CPX",
                     current={"via": "shell", "cmd": "echo 1"},
                     latest={"via": "github_release", "repo": "x/y"},
                     apply={"script": "scripts/updaters/cpx.py"})
    # no rollback_cmd -> closure is a harmless no-op (script self-rolls-back)
    ua._build_rollback(comp, "1.0")()  # must not raise

def test_auto_due_circuit_breaker():
    assert ua._auto_due({"status": "pending-auto"}) is True
    assert ua._auto_due({"status": "current"}) is False
    assert ua._auto_due({"status": "waiting"}) is False          # notify, not auto's job
    assert ua._auto_due({"status": "failed", "fail_count": 2}) is True
    assert ua._auto_due({"status": "failed", "fail_count": 3}) is False  # breaker tripped
