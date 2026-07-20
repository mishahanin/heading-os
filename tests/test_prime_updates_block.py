# tests/test_prime_updates_block.py
import importlib.util, json
from pathlib import Path

MOD = Path(__file__).resolve().parent.parent / "scripts" / "prime-health-parallel.py"
spec = importlib.util.spec_from_file_location("prime_health", MOD)
ph = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ph)

def _state(tmp, comps):
    d = tmp / "operations" / "updates"
    d.mkdir(parents=True)
    (d / "state.json").write_text(json.dumps({"generated": "2026-07-20", "components": comps}))
    return tmp

def test_waiting_notify_renders(tmp_path, monkeypatch):
    root = _state(tmp_path, {"cliproxyapi": {"display": "CLIProxyAPI", "tier": "notify",
                    "current": "7.2.92", "latest": "7.2.95", "delta": True, "status": "waiting"}})
    monkeypatch.setattr(ph, "get_outputs_dir", lambda: root)
    res = ph.run_updates(Path("/nonexistent"))
    assert "CLIProxyAPI" in res["output"]
    assert "7.2.95" in res["output"]

def test_all_current_is_silent(tmp_path, monkeypatch):
    root = _state(tmp_path, {"yt-dlp": {"display": "yt-dlp", "tier": "auto",
                    "current": "2026.7.20", "latest": "2026.7.20", "delta": False, "status": "current"}})
    monkeypatch.setattr(ph, "get_outputs_dir", lambda: root)
    res = ph.run_updates(Path("/nonexistent"))
    assert res["output"] == ""
    assert res["omit_if_empty"] is True

def test_no_state_is_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "get_outputs_dir", lambda: tmp_path)
    res = ph.run_updates(Path("/nonexistent"))
    assert res["output"] == ""

def test_failed_status_renders_with_fail_count(tmp_path, monkeypatch):
    root = _state(tmp_path, {"yt-dlp": {"display": "yt-dlp", "tier": "auto",
        "current": "2026.1.1", "latest": "2026.2.2", "delta": True,
        "status": "failed", "fail_count": 2}})
    monkeypatch.setattr(ph, "get_outputs_dir", lambda: root)
    res = ph.run_updates(Path("/nonexistent"))
    assert "yt-dlp" in res["output"]
    assert "FAILED" in res["output"]
    assert "2" in res["output"]   # fail_count surfaced

def test_observed_stale_renders_without_apply_hint(tmp_path, monkeypatch):
    root = _state(tmp_path, {"ollama": {"display": "Ollama", "tier": "observed",
                    "current": "0.1.0", "latest": "0.2.0", "delta": True, "status": "observed-stale"}})
    monkeypatch.setattr(ph, "get_outputs_dir", lambda: root)
    res = ph.run_updates(Path("/nonexistent"))
    assert "Ollama" in res["output"]
    assert "self-updates" in res["output"]
    assert "update-manager apply" not in res["output"]   # no bogus apply command
