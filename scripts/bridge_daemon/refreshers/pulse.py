"""Pulse data refresher.

Phase 2 cache realising the TODO that has sat in sources/pulse.py since
Phase 1.5: "Phase 2 will swap to a refresh_prime cache for performance."

Compute the full /pulse payload on a schedule and write it to
.daemon-state/pulse-snapshot.json. The /pulse endpoint reads from this
snapshot, so per-request latency collapses from ~7 s (WSL /mnt/c rglob
over outputs/ on every poll) to ~5 ms. The cost was acceptable when the
daemon ran on Windows-native Python; the 2026-05-23 WSL migration
exposed it because every stat() now crosses the 9P bridge.

Failure modes are caught and logged, never raised - a scheduler tick
must not crash the daemon. The endpoint falls back to inline compute
when the snapshot is missing or corrupt.
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.bridge_daemon._atomic import atomic_write_text
from scripts.bridge_daemon.sources.pulse import pulse_data
from scripts.utils.paths import get_data_root

if TYPE_CHECKING:
    from scripts.bridge_daemon.config import ConfigState
    from scripts.bridge_daemon.state import State

SNAPSHOT_FILENAME = ".daemon-state/pulse-snapshot.json"


def snapshot_path(workspace_root: Path) -> Path:
    return workspace_root / SNAPSHOT_FILENAME


def refresh(workspace_root: Path, state_obj: "State", cfg_state: "ConfigState",
            data_root: "Path | None" = None) -> None:
    """Compute the full /pulse payload and persist it atomically.

    Two roots (HEADING OS engine/data split): the payload is computed from
    ``data_root`` (CEO content overlay) while the snapshot is written under
    ``workspace_root`` (machine-local ``.daemon-state``, an engine path - the
    snapshot is a per-machine cache, not data). On ceo-main the two roots are
    identical, so this is a no-op; post-cutover the engine clone reads the data
    sibling but keeps its cache local. ``data_root`` defaults to
    ``get_data_root()`` when not injected.

    Always bumps the pulse component so the freshness UI advances, even
    when compute or write fails - matches the pattern in email.py.
    """
    if data_root is None:
        data_root = get_data_root()
    started = time.perf_counter()
    odin_5 = (cfg_state.config.get("kpi", {}) or {}).get("odin_5_target_date")
    try:
        payload = pulse_data(data_root, odin_5_target=odin_5)
    except Exception as e:
        logging.warning("bridge.pulse: compute failed: %s", e)
        state_obj.bump("pulse")
        return

    compute_ms = (time.perf_counter() - started) * 1000
    # computed_at records when this snapshot's data was actually generated
    # (not when /pulse last bumped state). Endpoint serves this as data_time
    # so the UI's "Computed Xs ago" reflects real data freshness, not
    # version-counter churn. Without this, Watchdog file events and
    # POST /refresh would advance data_time without an actual recompute.
    snapshot = {
        "data": payload,
        "compute_ms": round(compute_ms, 1),
        "odin_5_target": odin_5,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        atomic_write_text(
            snapshot_path(workspace_root),
            json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
            mode=0o600,
        )
        logging.info("bridge.pulse: refreshed in %.0fms", compute_ms)
    except OSError as e:
        logging.warning("bridge.pulse: snapshot write failed: %s", e)

    state_obj.bump("pulse")


def read_snapshot(workspace_root: Path) -> dict | None:
    """Return the latest snapshot dict, or None if missing/corrupt."""
    f = snapshot_path(workspace_root)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
