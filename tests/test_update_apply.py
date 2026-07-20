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

def test_script_apply_trusts_exit_code_skips_outer_health(monkeypatch):
    from scripts.utils.update_registry import Component
    comp = Component(name="cpx", tier="notify", current={"via": "shell", "cmd": "echo 1"},
                     latest={"via": "github_release", "repo": "x/y"},
                     apply={"script": "scripts/updaters/x.py"},
                     health={"cmd": "false", "expect_substr": "HTTP 200"})
    monkeypatch.setattr(ua, "run_health", lambda comp: False)  # outer probe flaps
    calls = []
    result = ua.apply_one(comp, applier=lambda: calls.append("applied"),
                          rollback=lambda: calls.append("rolled-back"))
    assert result == "applied"      # trusted the script's exit 0
    assert calls == ["applied"]     # rollback NOT called

def test_run_health_returns_false_when_probe_raises(monkeypatch):
    comp = _comp(health={"cmd": "does-not-matter"})
    def _boom(*a, **k):
        raise FileNotFoundError("bash gone")
    monkeypatch.setattr(ua.subprocess, "run", _boom)
    assert ua.run_health(comp) is False   # probe error -> unhealthy, never raises

def test_cmd_apply_isolates_failure_and_marks_state(tmp_path, monkeypatch):
    import json, types
    from scripts.utils.update_registry import Component
    c1 = Component(name="a", tier="auto", current={"via": "shell", "cmd": "echo 1"},
                   latest={"via": "pypi", "package": "a"},
                   apply={"cmd": "true", "rollback_cmd": "true"})
    c2 = Component(name="b", tier="auto", current={"via": "shell", "cmd": "echo 1"},
                   latest={"via": "pypi", "package": "b"},
                   apply={"cmd": "true", "rollback_cmd": "true"})
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"components": {
        "a": {"status": "pending-auto", "fail_count": 0},
        "b": {"status": "pending-auto", "fail_count": 0}}}))
    monkeypatch.setattr(ua, "resolve_current", lambda comp: "1")
    monkeypatch.setattr(ua, "_default_applier", lambda comp: (lambda: None))
    monkeypatch.setattr(ua, "run_health", lambda comp: comp.name != "a")  # a fails, b passes
    args = types.SimpleNamespace(auto=True, name=None)
    rc = ua.cmd_apply(args, [c1, c2], state)
    data = json.loads(state.read_text())
    assert data["components"]["a"]["status"] == "failed"
    assert data["components"]["a"]["fail_count"] == 1
    assert data["components"]["b"]["status"] == "pending-auto"  # b applied cleanly, not marked
    assert rc == 1
