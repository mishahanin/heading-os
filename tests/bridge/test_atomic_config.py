"""F-M5: bridge_daemon/config.py snapshot and revert must be atomic."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.bridge_daemon.config import snapshot_config, revert_config_to
import scripts.bridge_daemon._atomic as bridge_atomic_mod


def test_snapshot_config_is_atomic(tmp_path):
    """snapshot_config must not leave partial output on os.replace failure."""
    history_dir = tmp_path / ".daemon-state" / "config-history"
    history_dir.mkdir(parents=True)

    def _fail(src, dst):
        raise OSError("disk full")

    with patch.object(bridge_atomic_mod.os, "replace", side_effect=_fail):
        with pytest.raises(OSError):
            snapshot_config(tmp_path, {"key": "original"})

    # No partial snapshot file must exist
    snap_files = list(history_dir.iterdir())
    assert snap_files == [], f"orphan snapshot files: {snap_files}"


def test_revert_config_is_atomic(tmp_path):
    """revert_config_to must not corrupt user_cfg on os.replace failure."""
    history_dir = tmp_path / ".daemon-state" / "config-history"
    history_dir.mkdir(parents=True)
    snap = history_dir / "000000000_20260101T000000_000000Z.yaml"
    snap.write_text("key: backup\n", encoding="utf-8")

    user_cfg = tmp_path / ".daemon-state" / "config.yaml"
    user_cfg.parent.mkdir(parents=True, exist_ok=True)
    user_cfg.write_text("key: current\n", encoding="utf-8")

    def _fail(src, dst):
        raise OSError("disk full")

    with patch.object(bridge_atomic_mod.os, "replace", side_effect=_fail):
        with pytest.raises(OSError):
            revert_config_to(tmp_path, snap.name)

    assert user_cfg.read_text(encoding="utf-8") == "key: current\n"
