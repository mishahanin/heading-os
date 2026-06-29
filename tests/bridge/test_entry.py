"""Unit tests for the daemon entry point CLI. Focused on testable pure-Python
helpers; the full daemon lifecycle is exercised by manual smoke test (Task 18
plan step 2) and end-to-end smoke (Task 24)."""
import importlib.util
import socket
import sys
from pathlib import Path

import pytest

# scripts/bridge-daemon.py contains a hyphen which is illegal in Python module
# names; load it via importlib so the test can still import _pick_port.
_ENTRY_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bridge-daemon.py"


def _load_entry_module():
    spec = importlib.util.spec_from_file_location("bridge_daemon_entry", _ENTRY_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bridge_daemon_entry"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def entry_module():
    return _load_entry_module()


def test_pick_port_returns_free_port(entry_module):
    """_pick_port returns a port within the requested range when one is free."""
    pick = entry_module._pick_port
    p = pick(40000)
    assert 40000 <= p < 40050


def test_pick_port_skips_occupied_port(entry_module):
    """When the starting port is occupied, _pick_port advances to the next free port."""
    pick = entry_module._pick_port
    # Bind a listening socket on a known port to force _pick_port to skip it.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupier:
        occupier.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupier.bind(("127.0.0.1", 41000))
        occupier.listen(1)
        chosen = pick(41000)
        # Must not have returned the occupied port.
        assert chosen != 41000
        assert 41000 < chosen < 41050


def test_pick_port_raises_when_range_exhausted(entry_module):
    """If all 50 ports in the range are occupied, _pick_port raises RuntimeError.
    Phase 1 unit test: instead of binding 50 sockets (slow + flaky), monkeypatch
    connect_ex so every port appears occupied (returns 0 = 'connection succeeded')."""
    pick = entry_module._pick_port
    import unittest.mock as mock
    with mock.patch.object(socket.socket, "connect_ex", return_value=0):
        with pytest.raises(RuntimeError, match="no free port"):
            pick(42000)


# Phase S - --port override + _verify_port_free tests.


def test_verify_port_free_returns_port_when_free(entry_module):
    """A free port is returned unchanged."""
    verify = entry_module._verify_port_free
    assert verify(43000) == 43000


def test_verify_port_free_raises_when_port_busy(entry_module):
    """An occupied port raises RuntimeError with the port number in the message."""
    verify = entry_module._verify_port_free
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupier:
        occupier.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupier.bind(("127.0.0.1", 43100))
        occupier.listen(1)
        with pytest.raises(RuntimeError, match="already in use"):
            verify(43100)


def test_verify_port_free_rejects_out_of_range(entry_module):
    """Ports outside 1..65535 are rejected (fail-fast on bad CLI input)."""
    verify = entry_module._verify_port_free
    for bad in (0, -1, 65536, 99999):
        with pytest.raises(RuntimeError, match="out of range"):
            verify(bad)


def test_version_flag_prints_and_exits(entry_module, capsys):
    """--version prints 'bridge-daemon <version>' and exits 0 (argparse standard)."""
    import importlib
    import scripts.bridge_daemon.version as ver_mod
    importlib.reload(ver_mod)  # ensure fresh import
    with pytest.raises(SystemExit) as exc:
        entry_module.main.__wrapped__() if hasattr(entry_module.main, "__wrapped__") else (
            # main() reads sys.argv via argparse; inject --version
            _run_main_with_args(entry_module, ["--version"])
        )
    # argparse exits 0 on --version.
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "bridge-daemon" in out
    assert ver_mod.__version__ in out


def _run_main_with_args(mod, argv):
    """Helper: run mod.main() with sys.argv replaced."""
    import sys as _sys
    saved = _sys.argv
    try:
        _sys.argv = ["bridge-daemon.py", *argv]
        mod.main()
    finally:
        _sys.argv = saved


# Phase W - --status CLI flag tests.


def test_status_exits_1_when_neither_port_nor_heartbeat(entry_module, tmp_path, monkeypatch, capsys):
    """No daemon ever started -> --status exits 1 with stderr note."""
    (tmp_path / ".daemon-state").mkdir()  # dir exists but empty
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.show_status()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "not started" in err.lower()


def test_status_prints_full_fields_when_both_present(entry_module, tmp_path, monkeypatch, capsys):
    """Port file + heartbeat -> grep-friendly single line with all fields."""
    import json as _json
    daemon_state = tmp_path / ".daemon-state"
    daemon_state.mkdir()
    (daemon_state / "port").write_text("31415")
    hb = {
        "pid": 12345,
        "version": "0.1.0",
        "config_loaded_version": "7",
        "uptime_s": 3600,
        "last_heartbeat": "2026-05-20T12:00:00+00:00",
        "active_sessions": 2,
        "recent_error_count": 0,
    }
    (daemon_state / "heartbeat.json").write_text(_json.dumps(hb))
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)

    entry_module.show_status()
    out = capsys.readouterr().out
    # All fields land on one line, tab/space-separated.
    assert "\n" not in out.rstrip("\n")
    for fragment in ["port=31415", "pid=12345", "uptime=3600s", "version=0.1.0",
                     "config_v=7", "sessions=2", "errors=0"]:
        assert fragment in out


def test_status_uses_dashes_when_heartbeat_missing(entry_module, tmp_path, monkeypatch, capsys):
    """Port file exists but no heartbeat yet -> port populated, other fields dash."""
    daemon_state = tmp_path / ".daemon-state"
    daemon_state.mkdir()
    (daemon_state / "port").write_text("31999")
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)

    entry_module.show_status()
    out = capsys.readouterr().out
    assert "port=31999" in out
    # Heartbeat-derived fields all '-'
    assert "pid=-" in out
    assert "version=-" in out
    assert "config_v=-" in out


def test_status_uses_dashes_for_port_when_only_heartbeat(entry_module, tmp_path, monkeypatch, capsys):
    """Heartbeat survives but port file missing (daemon crashed mid-write) ->
    port='-', heartbeat fields populated."""
    import json as _json
    daemon_state = tmp_path / ".daemon-state"
    daemon_state.mkdir()
    (daemon_state / "heartbeat.json").write_text(_json.dumps({"pid": 1, "version": "0.1.0"}))
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)

    entry_module.show_status()
    out = capsys.readouterr().out
    assert "port=-" in out
    assert "pid=1" in out
    assert "version=0.1.0" in out


def test_status_handles_malformed_heartbeat_gracefully(entry_module, tmp_path, monkeypatch, capsys):
    """Malformed heartbeat JSON -> treated as missing, all fields dash (no raise)."""
    daemon_state = tmp_path / ".daemon-state"
    daemon_state.mkdir()
    (daemon_state / "port").write_text("32000")
    (daemon_state / "heartbeat.json").write_text("{not json")
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)

    entry_module.show_status()
    out = capsys.readouterr().out
    assert "port=32000" in out
    assert "pid=-" in out


def test_check_health_handles_missing_port_file(entry_module, tmp_path, monkeypatch, capsys):
    """When .daemon-state/port AND heartbeat.json are absent, --health exits 2.

    Phase 1.161: --health splits its exit codes:
    - 0 live probe succeeded
    - 1 live probe failed, heartbeat.json fallback used
    - 2 neither path worked (no port file + no heartbeat)
    """
    (tmp_path / ".daemon-state").mkdir()
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.check_health()
    assert exc.value.code == 2
    assert "not running" in capsys.readouterr().err.lower()


def test_check_health_falls_back_to_heartbeat_when_no_port(entry_module, tmp_path, monkeypatch, capsys):
    """Phase 1.161: heartbeat.json fallback when port file is missing."""
    import json
    state_dir = tmp_path / ".daemon-state"
    state_dir.mkdir()
    (state_dir / "heartbeat.json").write_text(json.dumps({
        "pid": 1234, "version": "0.1.0", "config_loaded_version": "v1",
        "uptime_s": 600, "last_heartbeat": "2026-05-19T15:00:00Z",
        "last_error": None, "recent_error_count": 0, "active_sessions": 0,
    }))
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.check_health()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower()
    assert "1234" in captured.out  # heartbeat pid surfaced on stdout


def test_check_health_handles_corrupted_port_file(entry_module, tmp_path, monkeypatch, capsys):
    """Corrupted port file -> exit 2 (no fallback attempted at this stage)."""
    state_dir = tmp_path / ".daemon-state"
    state_dir.mkdir()
    (state_dir / "port").write_text("not-a-number")
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.check_health()
    assert exc.value.code == 2
    assert "corrupted" in capsys.readouterr().err.lower()


def test_check_health_rejects_out_of_range_port(entry_module, tmp_path, monkeypatch, capsys):
    """A port outside the TCP valid range (e.g., 99999) is rejected as corruption."""
    state_dir = tmp_path / ".daemon-state"
    state_dir.mkdir()
    (state_dir / "port").write_text("99999")
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.check_health()
    assert exc.value.code == 2


def test_rotate_token_prints_restart_warning(entry_module, tmp_path, monkeypatch, capsys):
    """rotate_token prints a clear warning that the running daemon must be restarted."""
    (tmp_path / ".daemon-state").mkdir()
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    entry_module.rotate_token()
    out = capsys.readouterr().out.lower()
    assert "restart" in out
    assert "warning" in out
    # And the token file actually got written.
    token_file = tmp_path / ".daemon-state" / "token"
    assert token_file.exists()
    assert len(token_file.read_text()) > 16


# Phase Y - revert_to_prior_config CLI wrapper tests.
# The Phase 1.154 + 1.159 + 1.165 tests cover the config.py functions
# directly (revert_config, revert_config_to). Until now the CLI wrapper
# (revert_to_prior_config in scripts/bridge-daemon.py) had zero
# coverage, so a regression in the user-facing output, sys.exit
# semantics, or the listing-with-marker logic would slip past the suite.


def test_revert_to_prior_config_exits_1_when_no_snapshots(entry_module, tmp_path, monkeypatch, capsys):
    """No snapshots on disk -> stderr note + sys.exit(1)."""
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.revert_to_prior_config()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "no snapshots" in err.lower()


def test_revert_to_prior_config_default_restores_index_1(entry_module, tmp_path, monkeypatch, capsys):
    """With >= 2 snapshots and no target_name, restores index 1 (most-recent
    prior). Output marks index 0 'current boot' and index 1 'will restore'."""
    import time
    from scripts.bridge_daemon.config import snapshot_config
    snapshot_config(tmp_path, {"refresh": {"email": 100}})
    time.sleep(1.05)
    snapshot_config(tmp_path, {"refresh": {"email": 200}})

    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    entry_module.revert_to_prior_config()  # must not raise

    out = capsys.readouterr().out
    assert "current boot" in out
    assert "will restore" in out
    assert "Restored" in out
    assert "WARNING" in out
    # User override config now exists with the older value.
    user_cfg = tmp_path / ".daemon-state" / "config.yaml"
    assert user_cfg.exists()


def test_revert_to_prior_config_explicit_target_uses_revert_to(entry_module, tmp_path, monkeypatch, capsys):
    """With target_name, restores that specific snapshot and marks it in the
    listing (no 'current boot' marker)."""
    import time
    from scripts.bridge_daemon.config import list_snapshots, snapshot_config
    snapshot_config(tmp_path, {"refresh": {"email": 100}})
    time.sleep(1.05)
    snapshot_config(tmp_path, {"refresh": {"email": 200}})
    time.sleep(1.05)
    snapshot_config(tmp_path, {"refresh": {"email": 300}})
    snaps = list_snapshots(tmp_path)
    oldest_name = snaps[-1].name  # newest-first sort, so [-1] is oldest

    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    entry_module.revert_to_prior_config(target_name=oldest_name)

    out = capsys.readouterr().out
    assert oldest_name in out
    # When target_name is explicit, 'current boot' marker is suppressed.
    assert "current boot" not in out
    assert "will restore" in out


def test_revert_to_prior_config_exits_1_on_runtime_error(entry_module, tmp_path, monkeypatch, capsys):
    """If revert_config_to raises RuntimeError (e.g. unknown snapshot name),
    print 'revert failed:' to stderr and sys.exit(1)."""
    from scripts.bridge_daemon.config import snapshot_config
    snapshot_config(tmp_path, {"a": 1})

    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.revert_to_prior_config(target_name="does-not-exist.yaml")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "revert failed" in err.lower()
    assert "not found" in err.lower()


def test_revert_to_prior_config_exits_1_when_only_one_snapshot(entry_module, tmp_path, monkeypatch, capsys):
    """Default mode (no target_name) needs >= 2 snapshots. With only 1,
    revert_config raises RuntimeError -> wrapper prints + exits 1."""
    from scripts.bridge_daemon.config import snapshot_config
    snapshot_config(tmp_path, {"a": 1})

    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.revert_to_prior_config()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "revert failed" in err.lower()
    assert "at least 2" in err.lower()


# _prime_all_components - regression guard for the 2026-05-20 fix.
# Before this fix, the dashboard's freshness indicator showed '-' for
# 11 of 20 components because they were never bumped at boot. The fix:
# iterate COMPONENTS at boot so every component gets a data_time.


def test_prime_all_components_bumps_every_component(entry_module):
    """Every COMPONENT in state.COMPONENTS gets a non-None data_time after
    _prime_all_components(). This is the regression guard - if a future
    component is added to state.COMPONENTS, this test ensures the boot
    primer keeps up automatically (the loop iterates COMPONENTS so a
    NEW component is covered for free)."""
    from scripts.bridge_daemon.state import COMPONENTS, State
    state = State()
    # Pre-condition: all data_times start None
    snap_before = state.snapshot()
    assert all(snap_before["data_times"][c] is None for c in COMPONENTS)

    entry_module._prime_all_components(state)

    snap_after = state.snapshot()
    for c in COMPONENTS:
        assert snap_after["data_times"][c] is not None, \
            f"component {c} not bumped at boot - dashboard will show '-' for freshness"
        assert snap_after["components"][c] == 1, \
            f"component {c} version not at 1 after single bump"


def test_prime_all_components_covers_known_late_additions(entry_module):
    """Spot-check the late-added components that were specifically missing
    from the original boot-list. If any of these regress to null
    data_time, the dashboard's freshness indicator silently breaks for
    that page."""
    from scripts.bridge_daemon.state import State
    state = State()
    entry_module._prime_all_components(state)
    snap = state.snapshot()
    # These 11 components were added to COMPONENTS in Phases 1.5+ but
    # never to the original boot-bump list. All must now show non-null.
    late_additions = ["inflight", "investors", "approvals", "calendar",
                      "crm", "prime", "status", "conversations",
                      "threads", "signals", "critical"]
    for c in late_additions:
        assert snap["data_times"][c] is not None, \
            f"late-added component {c} not primed at boot"
