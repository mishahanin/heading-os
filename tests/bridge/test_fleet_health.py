"""Tests for daemon-fleet-health.py exit-code classification (Phase 1.163)."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# The CLI is at scripts/daemon-fleet-health.py (kebab-case, not importable
# as a module). Load it through importlib so we can test its helpers.
SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT.parent))

import importlib.util
spec = importlib.util.spec_from_file_location(
    "daemon_fleet_health",
    SCRIPTS_ROOT / "daemon-fleet-health.py",
)
fh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fh)


def _record(status="ok", version="0.1.0", age_s=0, recent_errors=0):
    """Build a heartbeat record fixture."""
    now = datetime.now(timezone.utc)
    return {
        "workspace": "synthetic-workspace",
        "pid": 1234,
        "version": version,
        "config_loaded_version": "v1",
        "uptime_s": 600,
        "last_heartbeat": (now - timedelta(seconds=age_s)).isoformat(),
        "last_error": None,
        "recent_error_count": recent_errors,
        "active_sessions": 0,
    }


def _missing_record():
    return {"workspace": "synthetic-workspace", "status": "missing", "detail": "no heartbeat"}


def _error_record():
    return {"workspace": "synthetic-workspace", "status": "error", "detail": "parse failed"}


def test_classify_ok():
    r = _record(age_s=10)
    assert fh._classify(r, 120, "0.1.0") == "ok"


def test_classify_stale():
    r = _record(age_s=300)
    assert fh._classify(r, 120, "0.1.0") == "stale"


def test_classify_version_mismatch():
    r = _record(age_s=10, version="0.1.0")
    assert fh._classify(r, 120, "0.2.0") == "version-mismatch"


def test_classify_error_from_recent_count():
    r = _record(age_s=10, recent_errors=3)
    assert fh._classify(r, 120, "0.1.0") == "error"


def test_classify_synthetic_missing():
    assert fh._classify(_missing_record(), 120, "0.1.0") == "missing"


def test_classify_synthetic_error():
    assert fh._classify(_error_record(), 120, "0.1.0") == "error"


# Exit-code classifier tests (the cron / monitoring contract).


def test_exit_code_empty_fleet():
    assert fh._classify_fleet_exit_code([], 120, None) == 0


def test_exit_code_all_ok():
    records = [_record(age_s=10), _record(age_s=20)]
    assert fh._classify_fleet_exit_code(records, 120, "0.1.0") == 0


def test_exit_code_drift_stale():
    records = [_record(age_s=10), _record(age_s=500)]
    assert fh._classify_fleet_exit_code(records, 120, "0.1.0") == 1


def test_exit_code_drift_version_mismatch():
    records = [_record(age_s=10, version="0.1.0"), _record(age_s=10, version="0.2.0")]
    assert fh._classify_fleet_exit_code(records, 120, "0.1.0") == 1


def test_exit_code_broken_missing():
    records = [_record(age_s=10), _missing_record()]
    assert fh._classify_fleet_exit_code(records, 120, "0.1.0") == 2


def test_exit_code_broken_error():
    records = [_record(age_s=10), _error_record()]
    assert fh._classify_fleet_exit_code(records, 120, "0.1.0") == 2


def test_exit_code_error_outweighs_drift():
    """Broken (exit 2) takes precedence over drift (exit 1)."""
    records = [_record(age_s=500), _error_record()]
    assert fh._classify_fleet_exit_code(records, 120, "0.1.0") == 2


# Verdict formatter tests.


def test_verdict_empty():
    text, _ = fh._verdict([], 120, None)
    assert "No workspaces" in text


def test_verdict_healthy():
    text, _ = fh._verdict([_record(age_s=10), _record(age_s=20)], 120, "0.1.0")
    assert "healthy" in text.lower()
    assert "2 workspace(s) ok" in text


def test_verdict_drift():
    text, _ = fh._verdict([_record(age_s=10), _record(age_s=500)], 120, "0.1.0")
    assert "drift" in text.lower()


def test_verdict_broken():
    text, _ = fh._verdict([_record(age_s=10), _missing_record()], 120, "0.1.0")
    assert "broken" in text.lower()


# Phase 1.166 - _read_heartbeat filesystem-reader tests.
# These cover the two kinds (local, crm-mirror) and the three outcomes
# (missing, malformed, valid). The classifier tests above use synthetic
# dicts; these exercise the actual disk-reading path.


def test_read_heartbeat_local_missing(tmp_path):
    """No .daemon-state/heartbeat.json -> synthetic missing record."""
    record = fh._read_heartbeat(tmp_path, "local")
    assert record["status"] == "missing"
    assert record["workspace"] == str(tmp_path)
    assert "heartbeat.json" in record["detail"]


def test_read_heartbeat_crm_mirror_missing(tmp_path):
    """No bridge-heartbeat.json -> synthetic missing record naming the file."""
    record = fh._read_heartbeat(tmp_path, "crm-mirror")
    assert record["status"] == "missing"
    assert "bridge-heartbeat.json" in record["detail"]


def test_read_heartbeat_local_malformed(tmp_path):
    """Garbage JSON -> synthetic error record (not a raise)."""
    daemon_state = tmp_path / ".daemon-state"
    daemon_state.mkdir()
    (daemon_state / "heartbeat.json").write_text("{not json", encoding="utf-8")
    record = fh._read_heartbeat(tmp_path, "local")
    assert record["status"] == "error"
    assert "parse failed" in record["detail"]
    assert record["workspace"] == str(tmp_path)


def test_read_heartbeat_local_valid(tmp_path):
    """Valid heartbeat: returned dict has workspace + kind injected."""
    daemon_state = tmp_path / ".daemon-state"
    daemon_state.mkdir()
    payload = {
        "pid": 99,
        "version": "0.1.0",
        "config_loaded_version": "1",
        "uptime_s": 60,
        "last_heartbeat": "2026-05-19T00:00:00+00:00",
        "last_error": None,
        "recent_error_count": 0,
        "active_sessions": 2,
    }
    (daemon_state / "heartbeat.json").write_text(json.dumps(payload), encoding="utf-8")
    record = fh._read_heartbeat(tmp_path, "local")
    assert record["pid"] == 99
    assert record["version"] == "0.1.0"
    assert record["active_sessions"] == 2
    assert record["workspace"] == str(tmp_path)
    assert record["kind"] == "local"
    assert "status" not in record  # untouched on valid path


def test_read_heartbeat_crm_mirror_valid(tmp_path):
    """crm-mirror kind reads from <ws>/bridge-heartbeat.json (not .daemon-state/)."""
    payload = {"pid": 7, "version": "0.2.0", "active_sessions": 0}
    (tmp_path / "bridge-heartbeat.json").write_text(json.dumps(payload), encoding="utf-8")
    record = fh._read_heartbeat(tmp_path, "crm-mirror")
    assert record["pid"] == 7
    assert record["version"] == "0.2.0"
    assert record["kind"] == "crm-mirror"
    # The .daemon-state/heartbeat.json path must NOT be consulted for this kind.
    daemon_state_hb = tmp_path / ".daemon-state" / "heartbeat.json"
    assert not daemon_state_hb.exists()


# Phase 1.167 - corporate config drift detection.


def _record_with_cfg(config_version, age_s=10, daemon_version="0.1.0"):
    """Heartbeat fixture with explicit config_loaded_version field."""
    r = _record(age_s=age_s, version=daemon_version)
    r["config_loaded_version"] = config_version
    return r


def test_read_corporate_config_version_missing(tmp_path):
    """No corporate/daemon/config.yaml -> None."""
    assert fh._read_corporate_config_version(tmp_path) is None


def test_read_corporate_config_version_parses(tmp_path):
    """Standard YAML with `version: N` -> string form."""
    corp = tmp_path / "corporate" / "daemon"
    corp.mkdir(parents=True)
    (corp / "config.yaml").write_text("version: 7\nrefresh:\n  email: 300\n", encoding="utf-8")
    assert fh._read_corporate_config_version(tmp_path) == "7"


def test_read_corporate_config_version_malformed(tmp_path):
    """Unparseable YAML -> None (no raise)."""
    corp = tmp_path / "corporate" / "daemon"
    corp.mkdir(parents=True)
    (corp / "config.yaml").write_text("not: valid: yaml: at all: [", encoding="utf-8")
    assert fh._read_corporate_config_version(tmp_path) is None


def test_read_corporate_config_version_no_version_field(tmp_path):
    """Valid YAML but no `version:` key -> None."""
    corp = tmp_path / "corporate" / "daemon"
    corp.mkdir(parents=True)
    (corp / "config.yaml").write_text("refresh:\n  email: 300\n", encoding="utf-8")
    assert fh._read_corporate_config_version(tmp_path) is None


def test_classify_config_drift():
    """Heartbeat config_loaded_version != expected -> config-drift."""
    r = _record_with_cfg("5")
    assert fh._classify(r, 120, "0.1.0", "7") == "config-drift"


def test_classify_config_matches():
    """When config matches, status is ok."""
    r = _record_with_cfg("7")
    assert fh._classify(r, 120, "0.1.0", "7") == "ok"


def test_classify_config_drift_skipped_when_expected_none():
    """No corporate config (or unreadable) -> config-drift check skipped."""
    r = _record_with_cfg("5")
    assert fh._classify(r, 120, "0.1.0", None) == "ok"


def test_classify_version_mismatch_outweighs_config_drift():
    """If daemon code drift AND config drift, version-mismatch wins (earlier branch)."""
    r = _record_with_cfg("5", daemon_version="0.1.0")
    assert fh._classify(r, 120, "0.2.0", "7") == "version-mismatch"


def test_classify_stale_outweighs_config_drift():
    """Stale heartbeat short-circuits before config check."""
    r = _record_with_cfg("5", age_s=500)
    assert fh._classify(r, 120, "0.1.0", "7") == "stale"


def test_exit_code_config_drift_is_drift():
    """config-drift maps to exit code 1 (drift bucket)."""
    records = [_record(age_s=10), _record_with_cfg("5")]
    assert fh._classify_fleet_exit_code(records, 120, "0.1.0", "7") == 1


def test_exit_code_config_drift_loses_to_broken():
    """Broken (exit 2) takes precedence over config-drift (exit 1)."""
    records = [_record_with_cfg("5"), _missing_record()]
    assert fh._classify_fleet_exit_code(records, 120, "0.1.0", "7") == 2


def test_verdict_config_drift():
    """config-drift surfaces in the drift verdict text."""
    records = [_record(age_s=10), _record_with_cfg("5")]
    text, _ = fh._verdict(records, 120, "0.1.0", "7")
    assert "drift" in text.lower()
    assert "config-drift" in text.lower()


# Phase Q - end-to-end integration test for Phase 1.167.
# All upstream tests use synthetic dicts. This one builds a real workspace
# on disk (heartbeat.json + corporate/daemon/config.yaml), runs main()
# through --json + --exit-zero, and asserts the wire output flags the
# drift correctly.


def test_main_end_to_end_flags_config_drift(monkeypatch, tmp_path, capsys):
    """End-to-end: workspace has a heartbeat.json with old config_loaded_version
    and a corporate/daemon/config.yaml at a newer version. main() must:
    - Read both
    - Classify the workspace as 'config-drift'
    - Surface 'config-drift' in the JSON verdict text
    - Emit expected_config_version at the top level
    - Mark the workspace record's 'classified' field as 'config-drift'
    """
    import json as _json

    # Build a synthetic workspace on disk.
    workspace = tmp_path / "synthetic-workspace"
    daemon_state = workspace / ".daemon-state"
    daemon_state.mkdir(parents=True)

    # Heartbeat: daemon booted against corporate config version "3" but
    # the corporate repo has since moved on to version "7".
    now_iso = datetime.now(timezone.utc).isoformat()
    heartbeat = {
        "pid": 12345,
        "version": "0.1.0",
        "config_loaded_version": "3",  # STALE
        "uptime_s": 3600,
        "last_heartbeat": now_iso,
        "last_error": None,
        "recent_error_count": 0,
        "active_sessions": 1,
    }
    (daemon_state / "heartbeat.json").write_text(_json.dumps(heartbeat), encoding="utf-8")

    # Corporate config: version 7 (drift target).
    corp_dir = workspace / "corporate" / "daemon"
    corp_dir.mkdir(parents=True)
    (corp_dir / "config.yaml").write_text("version: 7\nrefresh:\n  email: 300\n", encoding="utf-8")

    # Point both _candidate_workspaces() and get_workspace_root() at our
    # synthetic workspace so main() reads from it.
    monkeypatch.setattr(fh, "_candidate_workspaces", lambda: [(workspace, "local")])
    monkeypatch.setattr(fh, "get_workspace_root", lambda: workspace)

    exit_code = fh.main(["--json", "--exit-zero"])
    assert exit_code == 0  # --exit-zero forces 0 regardless of fleet posture
    out = _json.loads(capsys.readouterr().out)

    assert out["expected_config_version"] == "7"
    assert "config-drift" in out["verdict"].lower()
    # Workspace record carries the classified field set to config-drift.
    workspaces = out["workspaces"]
    assert len(workspaces) == 1
    assert workspaces[0]["classified"] == "config-drift"
    assert workspaces[0]["config_loaded_version"] == "3"


def test_main_end_to_end_classifies_ok_when_versions_match(monkeypatch, tmp_path, capsys):
    """Counter-test: same setup but heartbeat's config_loaded_version
    matches the corporate config. The workspace must classify as 'ok',
    not 'config-drift'."""
    import json as _json

    workspace = tmp_path / "synthetic-workspace"
    daemon_state = workspace / ".daemon-state"
    daemon_state.mkdir(parents=True)

    now_iso = datetime.now(timezone.utc).isoformat()
    heartbeat = {
        "pid": 12345,
        "version": "0.1.0",
        "config_loaded_version": "7",  # MATCHES corporate
        "uptime_s": 3600,
        "last_heartbeat": now_iso,
        "last_error": None,
        "recent_error_count": 0,
        "active_sessions": 1,
    }
    (daemon_state / "heartbeat.json").write_text(_json.dumps(heartbeat), encoding="utf-8")

    corp_dir = workspace / "corporate" / "daemon"
    corp_dir.mkdir(parents=True)
    (corp_dir / "config.yaml").write_text("version: 7\nrefresh:\n  email: 300\n", encoding="utf-8")

    monkeypatch.setattr(fh, "_candidate_workspaces", lambda: [(workspace, "local")])
    monkeypatch.setattr(fh, "get_workspace_root", lambda: workspace)

    fh.main(["--json", "--exit-zero"])
    out = _json.loads(capsys.readouterr().out)
    assert out["workspaces"][0]["classified"] == "ok"
    assert "healthy" in out["verdict"].lower()


def test_main_exit_code_signals_drift_when_config_drifted(monkeypatch, tmp_path, capsys):
    """Without --exit-zero, main() returns 1 (drift bucket) when any
    workspace classifies as config-drift."""
    import json as _json

    workspace = tmp_path / "synthetic-workspace"
    daemon_state = workspace / ".daemon-state"
    daemon_state.mkdir(parents=True)

    now_iso = datetime.now(timezone.utc).isoformat()
    heartbeat = {
        "pid": 12345,
        "version": "0.1.0",
        "config_loaded_version": "1",  # 6 versions behind
        "uptime_s": 3600,
        "last_heartbeat": now_iso,
        "last_error": None,
        "recent_error_count": 0,
        "active_sessions": 1,
    }
    (daemon_state / "heartbeat.json").write_text(_json.dumps(heartbeat), encoding="utf-8")

    corp_dir = workspace / "corporate" / "daemon"
    corp_dir.mkdir(parents=True)
    (corp_dir / "config.yaml").write_text("version: 7\n", encoding="utf-8")

    monkeypatch.setattr(fh, "_candidate_workspaces", lambda: [(workspace, "local")])
    monkeypatch.setattr(fh, "get_workspace_root", lambda: workspace)

    exit_code = fh.main(["--json"])
    # Drift bucket (1), not healthy (0) or broken (2).
    assert exit_code == 1
