"""Pulse data refresher.

Phase 2 cache realising the TODO that has sat in sources/pulse.py since
Phase 1.5: "Phase 2 will swap to a refresh_prime cache for performance."

Compute the full /pulse payload on a schedule and write it to
.daemon-state/pulse-snapshot.json. The /pulse endpoint reads from this
snapshot instead of walking the tree on every poll.

The ~7 s per-request figure this cache was built against is HISTORICAL. It
was measured in May 2026, when the workspace sat on WSL /mnt/c and every
stat() crossed the 9P bridge. The workspace has since moved to ext4, where
the same rglob over outputs/ is 68 ms across 5,910 entries (re-measured
2026-08-20). The cache is still the right shape - it keeps a filesystem
walk off the request path - but it is no longer buying two orders of
magnitude, and nobody should size a decision on the old number.

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

    Always bumps the pulse component so the freshness UI advances, even when
    compute or write fails. This is DELIBERATELY not what mail.py does, though
    this line claimed to match "email.py" until 2026-08-24 -- a file that does
    not exist, describing a policy that is the opposite of the one in the file
    that does. `mail.py` calls ``bump("inbox", fresh=fetched)``, so its
    freshness clock advances only on a run that actually fetched. Pulse can
    bump unconditionally because its snapshot carries its own ``computed_at``,
    which is what the UI reads for real data age.
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
        data = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # `UnicodeDecodeError` is a `ValueError` raised by `read_text` before
        # `json.loads` runs, so neither of the other two ever covered it. A
        # snapshot torn mid multi-byte character raised out of a function
        # whose whole contract is "or None if missing/corrupt", and `/pulse`
        # 500'd instead of taking its inline-compute fallback.
        return None
    # Shape check, not just parse. A snapshot holding valid non-object JSON --
    # a bare list from a partial or hand-rolled write -- came back as-is, and
    # `/pulse` then called `.get` on a list and 500'd with AttributeError
    # instead of taking the designed inline-compute fallback.
    #
    # Required keys too, not just "is a dict". The fallback only ever fired on
    # missing or unparseable JSON, so a snapshot written by an older schema was
    # served as-is and the endpoint answered with a payload shaped for a
    # different version of the page. A schema miss IS a miss.
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("data"), dict) or "computed_at" not in data:
        logging.warning("bridge.pulse: snapshot at %s has an unexpected shape "
                        "(keys=%s); treating it as a miss", f, sorted(data)[:8])
        return None
    return data
