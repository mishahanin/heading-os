"""Merged config loader: corporate defaults + per-user overrides.

Phase 1.154 adds config snapshot/revert support per spec section 3.6:
- snapshot_config() writes the loaded merged config to
  .daemon-state/config-history/YYYYMMDDTHHMMSS.yaml on daemon boot,
  keeping only the last 3 snapshots.
- list_snapshots() returns sorted snapshot paths (newest first).
- revert_config() restores the most recent snapshot to
  .daemon-state/config.yaml (per-user override path) so the next
  daemon boot picks it up. CEO must restart the daemon to apply.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import yaml

from ._atomic import atomic_write_text

CONFIG_HISTORY_DIR = ".daemon-state/config-history"
SNAPSHOT_KEEP = 3

DEFAULTS = {
    "version": 0,
    "refresh": {
        "default": 30,
        "status": 30,
        "email": 300,      # 5 min, locked 2026-05-17
        "calendar": 300,
        "crm": 300,
        "inflight": 60,
        "pulse": 60,       # locked 2026-05-24 - WSL /mnt/c rglob over outputs/ is ~7s, see refreshers/pulse.py
        "prime": 14400,
        "heartbeat": 60,   # spec section 3.7 - locked 60s
        "config_reconcile": 60,  # spec section 3.6 - 60s reconciliation tick
    },
    "stop_prompt_timeout_s": 5,   # locked 2026-05-17
    "port_range_start": 31415,
    "user_slug": "misha",
    # R2 (2026-06-03): spine daemon jobs - default OFF fleet-wide (scrutiny H1).
    # The shared daemon ships to execs where the CEO-only core is absent; these
    # flags keep the jobs unscheduled there. Enable only on the CEO workspace
    # via .daemon-state/config.yaml during the prove-out.
    "daemon": {
        "cold_sweep": {"enabled": False},
        "action_queue": {"executor": {"enabled": False}},
    },
}

def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out

def load_config(workspace_root: Path) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    if corp.exists():
        cfg = _deep_merge(cfg, yaml.safe_load(corp.read_text()) or {})
    user = workspace_root / ".daemon-state" / "config.yaml"
    if user.exists():
        cfg = _deep_merge(cfg, yaml.safe_load(user.read_text()) or {})
    return cfg


def _config_mtimes(workspace_root: Path) -> dict[str, float | None]:
    """Return current mtimes for both config layers. Missing -> None."""
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    user = workspace_root / ".daemon-state" / "config.yaml"
    return {
        "corporate": corp.stat().st_mtime if corp.is_file() else None,
        "user": user.stat().st_mtime if user.is_file() else None,
    }


class ConfigState:
    """In-memory config holder with mtime-based reconciliation.

    Spec section 3.6: 'Each daemon's 60-second reconciliation tick stats
    corporate/daemon/config.yaml; if mtime is newer than loaded, reload
    and log config_reloaded version=N.'

    Reload semantics: the in-memory dict is replaced atomically so any
    code holding a reference to the old dict keeps reading the old
    values until it dereferences `state.config` again. APScheduler jobs
    are NOT rescheduled - cadence changes still require a daemon
    restart. What DOES update live:
    - /settings endpoint payload (it reads cfg_state.config on each call)
    - heartbeat's config_loaded_version (next 60s tick reads .config)
    - any future code path that reads cfg_state.config directly

    reconcile() is safe to call at any cadence. The 60-second tick is the
    spec default; the test suite calls it inline.
    """

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root
        self.config = load_config(workspace_root)
        self._mtimes = _config_mtimes(workspace_root)
        self.last_reload_at: datetime | None = None
        self.reload_count = 0

    def reconcile(self) -> bool:
        """Stat both config layers; reload if either mtime moved.

        Returns True iff a reload happened. Safe to call frequently;
        a stat()+dict-compare is cheap.
        """
        current = _config_mtimes(self.workspace_root)
        if current == self._mtimes:
            return False
        self.config = load_config(self.workspace_root)
        self._mtimes = current
        self.last_reload_at = datetime.now(timezone.utc)
        self.reload_count += 1
        return True


def _next_snapshot_seq(history_dir: Path) -> int:
    """Return one past the highest sequence prefix among existing snapshots.

    Snapshot names are '{seq:09d}_{stamp}.yaml'. The leading numeric prefix
    is the sort/monotonicity key. Files predating this scheme (no leading
    digits before the first '_') are ignored when computing the max, so the
    next sequence starts at 0 on a fresh directory.
    """
    highest = -1
    for p in history_dir.glob("*.yaml"):
        prefix = p.name.split("_", 1)[0]
        if prefix.isdigit():
            highest = max(highest, int(prefix))
    return highest + 1


def snapshot_config(workspace_root: Path, cfg: dict[str, Any]) -> Path:
    """Atomically write the loaded merged config to a history file.

    Keeps only the last SNAPSHOT_KEEP files (newest 3 by mtime). Called
    once per daemon boot from start_daemon() right after load_config().

    Returns the path of the written snapshot.
    """
    history_dir = workspace_root / CONFIG_HISTORY_DIR
    history_dir.mkdir(parents=True, exist_ok=True)
    # Filename = monotonic sequence prefix + UTC timestamp. The revert/list
    # logic sorts snapshots lexicographically by name and treats that order
    # as chronological, so the prefix that drives the sort must increase
    # with every write. A wall-clock stamp alone cannot guarantee that:
    # snapshots written inside the same second collide, and on WSL the
    # clock can even step backward across writes - both leave the newest
    # file sorting before an older one. A monotonic sequence derived from
    # the highest sequence already on disk makes ordering correct by
    # construction regardless of clock behaviour, while the timestamp is
    # retained for human readability. Zero-padded to 9 digits so the
    # prefix sorts numerically as a string.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    seq = _next_snapshot_seq(history_dir)
    out_path = history_dir / f"{seq:09d}_{stamp}.yaml"
    text = yaml.safe_dump(cfg, sort_keys=True, default_flow_style=False)
    atomic_write_text(out_path, text)
    # Trim to the most-recent SNAPSHOT_KEEP files. Sort by filename
    # because the stamp prefix is lexicographically time-ordered.
    snapshots = sorted(history_dir.glob("*.yaml"))
    for old in snapshots[:-SNAPSHOT_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    return out_path


def list_snapshots(workspace_root: Path) -> list[Path]:
    """Return snapshot files sorted newest-first."""
    history_dir = workspace_root / CONFIG_HISTORY_DIR
    if not history_dir.is_dir():
        return []
    return sorted(history_dir.glob("*.yaml"), reverse=True)


def revert_config(workspace_root: Path) -> Path:
    """Restore the most recent prior snapshot to .daemon-state/config.yaml.

    'Most recent prior' means: snapshots sorted newest-first, skip index 0
    (that's the current boot's snapshot - reverting to it is a no-op),
    take index 1.

    Returns the path of the snapshot that was restored.
    Raises RuntimeError if there's no prior snapshot to revert to.
    """
    snaps = list_snapshots(workspace_root)
    if len(snaps) < 2:
        raise RuntimeError(
            f"need at least 2 config snapshots to revert (have {len(snaps)}). "
            f"Start the daemon at least twice before reverting."
        )
    return revert_config_to(workspace_root, snaps[1].name)


def revert_config_to(workspace_root: Path, snapshot_name: str) -> Path:
    """Restore a specific snapshot by filename to .daemon-state/config.yaml.

    Phase 1.159: explicit snapshot selection for cases where the CEO
    wants to roll back further than --revert-config (which only goes
    to index 1). Pass the snapshot's filename (e.g.,
    '20260519T154808Z.yaml') as written by snapshot_config.

    Phase 1.165: hardened against path traversal. snapshot_name must
    be a bare filename - no separators, no '..', no leading dot or
    slash. The resolved path is verified to live inside the history
    dir.

    Raises RuntimeError if the named snapshot is invalid or missing.
    """
    if not isinstance(snapshot_name, str) or not snapshot_name:
        raise RuntimeError("snapshot name is required")
    # Reject anything that looks like a path. We only accept the
    # bare filename of a file already inside CONFIG_HISTORY_DIR.
    if "/" in snapshot_name or "\\" in snapshot_name or snapshot_name in ("..", "."):
        raise RuntimeError(
            f"snapshot {snapshot_name!r} contains path separators; "
            f"pass only the bare filename"
        )
    if snapshot_name.startswith("."):
        raise RuntimeError(
            f"snapshot {snapshot_name!r} starts with '.'; refused"
        )
    history_dir = workspace_root / CONFIG_HISTORY_DIR
    target = history_dir / snapshot_name
    # Belt-and-braces: resolve and confirm we stayed inside the history
    # dir. Catches OS-level cleverness even if the prefix checks above
    # somehow let something through.
    try:
        resolved = target.resolve(strict=False)
        history_resolved = history_dir.resolve(strict=False)
        resolved.relative_to(history_resolved)
    except (ValueError, OSError):
        raise RuntimeError(
            f"snapshot {snapshot_name!r} escapes history directory; refused"
        )
    if not target.is_file():
        available = [p.name for p in list_snapshots(workspace_root)]
        raise RuntimeError(
            f"snapshot {snapshot_name!r} not found. Available: {available}"
        )
    user_cfg = workspace_root / ".daemon-state" / "config.yaml"
    atomic_write_text(user_cfg, target.read_text(encoding="utf-8"))
    return target
