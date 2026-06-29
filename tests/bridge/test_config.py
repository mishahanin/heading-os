import time

import pytest
import yaml

from scripts.bridge_daemon.config import (
    ConfigState,
    list_snapshots,
    load_config,
    revert_config,
    revert_config_to,
    snapshot_config,
)


def test_load_corporate_only(workspace_root, monkeypatch):
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    corp.parent.mkdir(parents=True)
    corp.write_text("version: 1\nrefresh:\n  default: 30\n  email: 300\n")
    cfg = load_config(workspace_root)
    assert cfg["refresh"]["default"] == 30
    assert cfg["refresh"]["email"] == 300
    assert cfg["version"] == 1


def test_user_overrides_corporate(workspace_root):
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    corp.parent.mkdir(parents=True)
    corp.write_text("refresh:\n  email: 300\n  inflight: 60\n")
    user = workspace_root / ".daemon-state" / "config.yaml"
    user.write_text("refresh:\n  email: 60\n")  # user overrides email to 60s
    cfg = load_config(workspace_root)
    assert cfg["refresh"]["email"] == 60   # user wins
    assert cfg["refresh"]["inflight"] == 60  # corporate retained


# Phase 1.154 - snapshot + revert tests.

def test_snapshot_writes_yaml(workspace_root):
    cfg = load_config(workspace_root)
    out = snapshot_config(workspace_root, cfg)
    assert out.exists()
    assert out.suffix == ".yaml"
    assert out.parent.name == "config-history"
    # Verify it can be round-tripped through YAML.
    import yaml as _y
    reloaded = _y.safe_load(out.read_text())
    assert reloaded["refresh"]["email"] == cfg["refresh"]["email"]


def test_snapshot_trims_to_keep_3(workspace_root):
    for i in range(5):
        snapshot_config(workspace_root, {"iteration": i})
        time.sleep(1.05)  # ensure distinct timestamps (resolution: seconds)
    snaps = list_snapshots(workspace_root)
    assert len(snaps) == 3, snaps


def test_list_snapshots_newest_first(workspace_root):
    snapshot_config(workspace_root, {"order": "first"})
    time.sleep(1.05)
    snapshot_config(workspace_root, {"order": "second"})
    snaps = list_snapshots(workspace_root)
    assert len(snaps) == 2
    assert snaps[0].name > snaps[1].name  # newest first by ts-prefix sort


def test_revert_config_restores_prior(workspace_root):
    snapshot_config(workspace_root, {"refresh": {"email": 100}})
    time.sleep(1.05)
    snapshot_config(workspace_root, {"refresh": {"email": 999}})
    restored = revert_config(workspace_root)
    user_cfg = workspace_root / ".daemon-state" / "config.yaml"
    assert user_cfg.exists()
    assert user_cfg.read_text() == restored.read_text()
    # Re-loading should now see the reverted value.
    cfg = load_config(workspace_root)
    assert cfg["refresh"]["email"] == 100


def test_revert_config_requires_two_snapshots(workspace_root):
    snapshot_config(workspace_root, {"only": 1})
    with pytest.raises(RuntimeError, match="at least 2 config snapshots"):
        revert_config(workspace_root)


def test_revert_config_zero_snapshots(workspace_root):
    with pytest.raises(RuntimeError, match="at least 2 config snapshots"):
        revert_config(workspace_root)


def test_revert_config_to_specific_snapshot(workspace_root):
    snapshot_config(workspace_root, {"refresh": {"email": 100}})
    time.sleep(1.05)
    snapshot_config(workspace_root, {"refresh": {"email": 200}})
    time.sleep(1.05)
    snapshot_config(workspace_root, {"refresh": {"email": 300}})
    snaps = list_snapshots(workspace_root)
    # Pick the oldest snapshot explicitly (newest-first sort -> index 2)
    oldest_name = snaps[2].name
    restored = revert_config_to(workspace_root, oldest_name)
    assert restored.name == oldest_name
    cfg = load_config(workspace_root)
    assert cfg["refresh"]["email"] == 100


def test_revert_config_to_unknown_snapshot(workspace_root):
    snapshot_config(workspace_root, {"a": 1})
    with pytest.raises(RuntimeError, match="not found"):
        revert_config_to(workspace_root, "does-not-exist.yaml")


def test_revert_config_to_writes_user_override(workspace_root):
    snapshot_config(workspace_root, {"refresh": {"email": 100}})
    snaps = list_snapshots(workspace_root)
    revert_config_to(workspace_root, snaps[0].name)
    user_cfg = workspace_root / ".daemon-state" / "config.yaml"
    assert user_cfg.exists()
    # Round-trip: load_config should see the restored value.
    cfg = load_config(workspace_root)
    assert cfg["refresh"]["email"] == 100


# Regression: rapid snapshots within the same wall-clock second must not
# collide, and their lexicographic name-sort (which the revert logic treats
# as chronological) must stay correct even when the wall clock does not
# advance monotonically. Before the monotonic-sequence-prefix fix, three
# writes inside one second shared the same %Y%m%dT%H%M%SZ filename and
# overwrote each other (1 file instead of 3); and on WSL the clock could
# step backward across writes, leaving the newest file sorting before an
# older one. Both broke keep-3 / revert-to-prior assertions.

def test_rapid_snapshots_do_not_collide(workspace_root):
    # Tight loop, no sleep -> all three writes land in the same second.
    for i in range(3):
        snapshot_config(workspace_root, {"refresh": {"email": 100 + i}})
    snaps = list_snapshots(workspace_root)
    assert len(snaps) == 3, [p.name for p in snaps]
    # All filenames distinct.
    names = [p.name for p in snaps]
    assert len(set(names)) == 3, names
    # Newest-first ordering must remain chronological (write order i=0,1,2).
    contents = [yaml.safe_load(p.read_text())["refresh"]["email"] for p in snaps]
    assert contents == [102, 101, 100], contents


def test_rapid_snapshots_revert_to_each(workspace_root):
    # Three rapid snapshots in the same second, then revert to each by name.
    for i in range(3):
        snapshot_config(workspace_root, {"refresh": {"email": 100 + i}})
    snaps = list_snapshots(workspace_root)
    assert len(snaps) == 3
    for snap in snaps:
        expected = yaml.safe_load(snap.read_text())["refresh"]["email"]
        restored = revert_config_to(workspace_root, snap.name)
        assert restored.name == snap.name
        assert load_config(workspace_root)["refresh"]["email"] == expected


# Phase 1.165: path-traversal hardening.

@pytest.mark.parametrize("name", [
    "../../etc/passwd",
    "/etc/passwd",
    "sub/file.yaml",
    "back\\slash.yaml",
    "..",
    ".",
    ".hidden.yaml",
    "",
])
def test_revert_config_to_rejects_unsafe_names(workspace_root, name):
    snapshot_config(workspace_root, {"a": 1})  # at least one snapshot exists
    with pytest.raises(RuntimeError):
        revert_config_to(workspace_root, name)


def test_revert_config_to_rejects_none(workspace_root):
    snapshot_config(workspace_root, {"a": 1})
    with pytest.raises(RuntimeError):
        revert_config_to(workspace_root, None)


# Phase B - ConfigState reconciliation tests.


def test_config_state_loads_at_init(workspace_root):
    """ConfigState picks up the same merged config as load_config()."""
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    corp.parent.mkdir(parents=True)
    corp.write_text("version: 2\nrefresh:\n  email: 200\n")
    cs = ConfigState(workspace_root)
    assert cs.config["version"] == 2
    assert cs.config["refresh"]["email"] == 200
    assert cs.reload_count == 0
    assert cs.last_reload_at is None


def test_reconcile_returns_false_when_nothing_changed(workspace_root):
    """No mtime change -> reconcile() is a noop returning False."""
    cs = ConfigState(workspace_root)
    assert cs.reconcile() is False
    assert cs.reload_count == 0


def test_reconcile_returns_true_on_corporate_mtime_change(workspace_root):
    """Touching corporate/daemon/config.yaml mtime triggers a reload."""
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    corp.parent.mkdir(parents=True)
    corp.write_text("version: 1\nrefresh:\n  email: 100\n")
    cs = ConfigState(workspace_root)
    assert cs.config["refresh"]["email"] == 100

    # Simulate /push-updates landing a new corporate config.
    time.sleep(1.05)  # mtime resolution is 1s on FAT/NTFS
    corp.write_text("version: 2\nrefresh:\n  email: 250\n")

    assert cs.reconcile() is True
    assert cs.config["version"] == 2
    assert cs.config["refresh"]["email"] == 250
    assert cs.reload_count == 1
    assert cs.last_reload_at is not None


def test_reconcile_returns_true_on_user_override_change(workspace_root):
    """Touching .daemon-state/config.yaml (per-user override) also triggers a reload."""
    user = workspace_root / ".daemon-state" / "config.yaml"
    user.write_text("refresh:\n  email: 60\n")
    cs = ConfigState(workspace_root)
    assert cs.config["refresh"]["email"] == 60

    time.sleep(1.05)
    user.write_text("refresh:\n  email: 45\n")
    assert cs.reconcile() is True
    assert cs.config["refresh"]["email"] == 45


def test_reconcile_counts_each_reload(workspace_root):
    """reload_count increments on each successful reconcile."""
    user = workspace_root / ".daemon-state" / "config.yaml"
    user.write_text("refresh:\n  email: 60\n")
    cs = ConfigState(workspace_root)
    for i in range(3):
        time.sleep(1.05)
        user.write_text(f"refresh:\n  email: {100 + i}\n")
        assert cs.reconcile() is True
    assert cs.reload_count == 3


def test_reconcile_handles_corporate_appearing_after_boot(workspace_root):
    """Daemon booted without a corporate config; one lands later -> reload."""
    cs = ConfigState(workspace_root)
    # No corporate config exists at boot -> defaults only.
    assert cs.config["version"] == 0

    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    corp.parent.mkdir(parents=True)
    corp.write_text("version: 9\n")
    assert cs.reconcile() is True
    assert cs.config["version"] == 9


def test_reconcile_handles_corporate_disappearing(workspace_root):
    """Corporate config existed at boot, was deleted (someone reverted upstream) ->
    reload returns True and config falls back to defaults + user overrides."""
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    corp.parent.mkdir(parents=True)
    corp.write_text("version: 5\n")
    cs = ConfigState(workspace_root)
    assert cs.config["version"] == 5

    corp.unlink()
    assert cs.reconcile() is True
    assert cs.config["version"] == 0  # back to DEFAULTS
