import importlib.util
from pathlib import Path

MOD = Path(__file__).resolve().parent.parent / "scripts" / "update-manager.py"
spec = importlib.util.spec_from_file_location("update_manager", MOD)
um = importlib.util.module_from_spec(spec)
spec.loader.exec_module(um)

def test_build_state_flags_delta(monkeypatch):
    from scripts.utils.update_registry import Component
    comps = [Component(name="c", tier="notify", display="C",
                       current={"via": "shell", "cmd": "echo 1.0.0"},
                       latest={"via": "github_release", "repo": "x/y"})]
    monkeypatch.setattr(um, "resolve_current", lambda comp: "1.0.0")
    monkeypatch.setattr(um, "resolve_latest", lambda comp: "1.1.0")
    state = um.build_state(comps)
    entry = state["components"]["c"]
    assert entry["current"] == "1.0.0"
    assert entry["latest"] == "1.1.0"
    assert entry["delta"] is True
    assert entry["status"] == "waiting"

def test_build_state_no_delta_is_current(monkeypatch):
    from scripts.utils.update_registry import Component
    comps = [Component(name="c", tier="auto", display="C",
                       current={"via": "shell", "cmd": "echo 2.0"},
                       latest={"via": "pypi", "package": "c"})]
    monkeypatch.setattr(um, "resolve_current", lambda comp: "2.0")
    monkeypatch.setattr(um, "resolve_latest", lambda comp: "2.0")
    state = um.build_state(comps)
    assert state["components"]["c"]["delta"] is False
    assert state["components"]["c"]["status"] == "current"

def test_write_state_is_atomic(tmp_path):
    p = tmp_path / "state.json"
    um.write_state({"components": {}}, p)
    assert p.exists()
    assert not (tmp_path / "state.json.tmp").exists()

def _auto_comp():
    from scripts.utils.update_registry import Component
    return Component(name="c", tier="auto", display="C",
                     current={"via": "shell", "cmd": "x"},
                     latest={"via": "pypi", "package": "c"},
                     apply={"cmd": "true", "rollback_cmd": "r"})

def test_build_state_carries_failed_forward(monkeypatch):
    monkeypatch.setattr(um, "resolve_current", lambda comp: "1.0")
    monkeypatch.setattr(um, "resolve_latest", lambda comp: "1.1")
    prior = {"components": {"c": {"status": "failed", "fail_count": 2, "latest": "1.1"}}}
    e = um.build_state([_auto_comp()], prior)["components"]["c"]
    assert e["status"] == "failed"      # still lagging the same latest -> stays failed
    assert e["fail_count"] == 2

def test_fail_count_resets_on_new_latest(monkeypatch):
    monkeypatch.setattr(um, "resolve_current", lambda comp: "1.0")
    monkeypatch.setattr(um, "resolve_latest", lambda comp: "2.0")   # new upstream release
    prior = {"components": {"c": {"status": "failed", "fail_count": 3, "latest": "1.1"}}}
    e = um.build_state([_auto_comp()], prior)["components"]["c"]
    assert e["fail_count"] == 0             # breaker resets for a fresh release
    assert e["status"] == "pending-auto"

def test_fail_count_clears_when_current(monkeypatch):
    monkeypatch.setattr(um, "resolve_current", lambda comp: "1.1")
    monkeypatch.setattr(um, "resolve_latest", lambda comp: "1.1")   # caught up
    prior = {"components": {"c": {"status": "failed", "fail_count": 2, "latest": "1.1"}}}
    e = um.build_state([_auto_comp()], prior)["components"]["c"]
    assert e["status"] == "current"
    assert e["fail_count"] == 0

def test_empty_current_is_unknown_not_current(monkeypatch):
    monkeypatch.setattr(um, "resolve_current", lambda comp: "")   # broken probe
    monkeypatch.setattr(um, "resolve_latest", lambda comp: "1.1")
    e = um.build_state([_auto_comp()])["components"]["c"]
    assert e["status"] == "unknown"   # NOT "current"

def test_transient_latest_failure_preserves_fail_count(monkeypatch):
    monkeypatch.setattr(um, "resolve_current", lambda comp: "1.0")
    monkeypatch.setattr(um, "resolve_latest", lambda comp: "")     # transient network blip
    prior = {"components": {"c": {"status": "failed", "fail_count": 2, "latest": "1.1"}}}
    e = um.build_state([_auto_comp()], prior)["components"]["c"]
    assert e["status"] == "unknown"
    assert e["fail_count"] == 2        # breaker memory preserved, NOT reset
