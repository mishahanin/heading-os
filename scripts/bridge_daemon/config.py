"""Merged config loader: corporate defaults + per-user overrides.

Phase 1.154 adds config snapshot/revert support per spec section 3.6:
- snapshot_config() writes the loaded merged config to
  .daemon-state/config-history/{seq:09d}_YYYYMMDDTHHMMSS_ffffffZ.yaml on
  daemon boot, keeping only the last 3 snapshots. The `seq` prefix is what
  orders them; the timestamp is for humans.
- list_snapshots() returns sorted snapshot paths (newest first).
- revert_config() restores the most recent snapshot to
  .daemon-state/config.yaml (per-user override path). NO RESTART IS NEEDED:
  ConfigState.reconcile() stats both layers on a 60-second tick and reloads on
  an mtime change, so a revert applies within that tick. This said "CEO must
  restart the daemon to apply", which sent an operator to restart something
  that was about to reload itself.
- a snapshot stores the two layers SEPARATELY (schema 2), so a revert restores
  only the user layer and corporate keeps flowing. Until 2026-08-24 a snapshot
  was the single merged blob and the revert wrote all of it into the user layer,
  which froze every corporate-owned key as a user override: corporate pushes
  stopped reaching those keys, silently and permanently. Snapshots written
  before that date have no `schema` key; they are still restorable, still
  restore the way they always did, and still log the shadowed-key warning.
"""
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any
import yaml

from ._atomic import atomic_write_text

CONFIG_HISTORY_DIR = ".daemon-state/config-history"
SNAPSHOT_KEEP = 3
# 1 = one merged blob (pre-2026-08-24, still readable). 2 = layers held apart.
SNAPSHOT_SCHEMA = 2

DEFAULTS = {
    "version": 0,
    "refresh": {
        "default": 30,
        "status": 30,
        "email": 300,      # 5 min, locked 2026-05-17
        "calendar": 300,
        "crm": 300,
        "inflight": 60,
        # Locked 2026-05-24 against a ~7 s rglob over outputs/. That number was
        # measured on WSL /mnt/c and no longer describes this machine: the
        # workspace moved to ext4, and the same walk is 68 ms over 5,910 entries
        # (re-measured 2026-08-20). The interval is left at 60 s because nothing
        # here has re-derived what it SHOULD be, only that the old reason is
        # gone. Do not quote the 7 s figure as current. See refreshers/pulse.py.
        "pulse": 60,
        "prime": 14400,
        "heartbeat": 60,   # spec section 3.7 - locked 60s
        "config_reconcile": 60,  # spec section 3.6 - 60s reconciliation tick
    },
    "stop_prompt_timeout_s": 5,   # locked 2026-05-17
    "port_range_start": 31415,
    # Resolved through the operator seam in load_config() (see below); empty here
    # so no operator-identity literal lives in the engine defaults.
    "user_slug": "",
    # R2 (2026-06-03): spine daemon jobs - default OFF fleet-wide (scrutiny H1).
    # The shared daemon ships to execs where the CEO-only core is absent; these
    # flags keep the jobs unscheduled there. Enable only on the CEO workspace
    # via .daemon-state/config.yaml during the prove-out.
    "daemon": {
        "cold_sweep": {"enabled": False},
        "action_queue": {"executor": {"enabled": False}},
    },
}

logger = logging.getLogger(__name__)


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out

def _load_layer(path: Path, label: str) -> dict:
    """Parse one config layer. A broken layer is SKIPPED, never fatal.

    `yaml.safe_load` errors used to propagate, so a `.daemon-state/config.yaml`
    saved mid-edit stopped the daemon from booting at all and killed the
    reconcile tick that would have picked up the corrected file. A config
    override is the least important input here; refusing to start over one is
    the wrong trade. `read_text()` also used the locale default encoding, which
    misdecodes a UTF-8 file on a non-UTF-8 host.
    """
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        logger.warning("%s config layer at %s is unreadable; ignoring it and "
                       "keeping the layers below", label, path, exc_info=True)
        return {}
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        logger.warning("%s config layer at %s is a %s, not a mapping; ignoring it",
                       label, path, type(parsed).__name__)
        return {}
    return parsed


def load_config(workspace_root: Path) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    if corp.exists():
        cfg = _deep_merge(cfg, _load_layer(corp, "corporate"))
    user = workspace_root / ".daemon-state" / "config.yaml"
    if user.exists():
        cfg = _deep_merge(cfg, _load_layer(user, "user"))
    # Resolve the operator's slug through the identity seam when no config layer
    # set it (operator.yaml / env; generic "operator" on a fresh clone).
    if not cfg.get("user_slug"):
        from scripts.utils.operator_identity import operator_slug
        cfg["user_slug"] = operator_slug()
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


def _snapshot_order_key(path: Path) -> tuple[int, int, str]:
    """Oldest-first ordering key for a snapshot filename.

    Plain `sorted()` on the names was wrong wherever both naming schemes are on
    disk, and `_next_snapshot_seq` above exists precisely because they can be.
    A legacy name starts with the year (`20260519T...`) and a current one with
    a zero-padded sequence (`000000007_...`), so `"2" > "0"` sorted every
    legacy file AFTER every new one. Two consequences, both silent: the trim in
    `snapshot_config` deleted the newest snapshots and kept the pre-2026-08-24
    ones, and `list_snapshots` called a legacy file the newest, so
    `revert_config`'s "index 1" could hand back the CURRENT boot's snapshot and
    revert to nothing.

    The legacy scheme predates the sequence scheme, so legacy files sort first
    as a group; inside each group the name is already chronological.
    """
    prefix = path.name.split("_", 1)[0]
    if prefix.isdigit():
        return (1, int(prefix), path.name)
    return (0, 0, path.name)


def _corp_path(workspace_root: Path) -> Path:
    return workspace_root / "corporate" / "daemon" / "config.yaml"


def _user_path(workspace_root: Path) -> Path:
    return workspace_root / ".daemon-state" / "config.yaml"


def snapshot_config(workspace_root: Path, cfg: dict[str, Any]) -> Path:
    """Atomically write both config layers, separately, to a history file.

    Keeps only the last SNAPSHOT_KEEP files, newest by SEQUENCE PREFIX. This
    line said "by mtime" and nothing here has ever read an mtime. Called once
    per daemon boot from start_daemon() right after load_config().

    The file holds `corporate` and `user` verbatim, plus `merged` for reading:
    only `user` is ever restored. Storing one merged blob was the whole defect
    -- a revert could not tell an operator's override from a corporate default,
    so it wrote back both and the corporate half stopped updating forever.

    `merged` is the caller's `cfg`, captured when the daemon loaded it; the two
    layers are re-read here. A corporate push landing between those two moments
    makes `merged` marginally stale. It is informational and nothing restores
    from it, so that is harmless.

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
    corp = _corp_path(workspace_root)
    user = _user_path(workspace_root)
    document = {
        "schema": SNAPSHOT_SCHEMA,
        "corporate": _load_layer(corp, "corporate") if corp.exists() else {},
        "user": _load_layer(user, "user") if user.exists() else {},
        "merged": cfg,
    }
    text = yaml.safe_dump(document, sort_keys=True, default_flow_style=False)
    atomic_write_text(out_path, text)
    # Trim to the most-recent SNAPSHOT_KEEP files, oldest first. The key, not
    # a plain name sort: see `_snapshot_order_key` for why a name sort deleted
    # the newest snapshots on any directory holding both naming schemes.
    snapshots = sorted(history_dir.glob("*.yaml"), key=_snapshot_order_key)
    for old in snapshots[:-SNAPSHOT_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    return out_path


def list_snapshots(workspace_root: Path) -> list[Path]:
    """Return snapshot files sorted newest-first (by sequence, not by name)."""
    history_dir = workspace_root / CONFIG_HISTORY_DIR
    if not history_dir.is_dir():
        return []
    return sorted(history_dir.glob("*.yaml"), key=_snapshot_order_key,
                  reverse=True)


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
    to index 1). Pass the snapshot's filename exactly as `snapshot_config`
    wrote it, sequence prefix included -- e.g.
    '000000007_20260519T154808_123456Z.yaml'. The example here used to omit
    the prefix, so an operator following it got "snapshot not found".

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
    raw = target.read_text(encoding="utf-8")
    user_layer, is_layered = _user_layer_of_snapshot(raw)
    if is_layered:
        # Schema 2: the snapshot kept the layers apart, so the revert puts back
        # exactly what the operator had overridden and nothing else. Corporate
        # keys are not written, so corporate pushes keep reaching them.
        restored = yaml.safe_dump(user_layer, sort_keys=True,
                                  default_flow_style=False)
    else:
        # Schema 1: one merged blob, and no way to tell an override from a
        # default inside it. Restoring it freezes the corporate keys as user
        # overrides. That is what this snapshot has always restored and what
        # the operator chose it for, so it is not silently reinterpreted here
        # -- it is restored as written, and the consequence is named in the log.
        logger.warning(
            "snapshot %s predates layered snapshots (no schema key), so it "
            "holds one merged blob and reverting to it writes corporate values "
            "into the user layer. Boot the daemon once to write a schema-%d "
            "snapshot if you want a clean revert.",
            snapshot_name, SNAPSHOT_SCHEMA,
        )
        restored = raw
        # Only on this path. On schema 2 the restored keys ARE the operator's
        # own overrides, so naming them as "frozen corporate keys" would be a
        # false alarm on every deliberate override.
        _warn_about_shadowed_corporate_keys(workspace_root, restored, snapshot_name)
    atomic_write_text(_user_path(workspace_root), restored)
    return target


def _user_layer_of_snapshot(raw: str) -> tuple[dict, bool]:
    """The user layer inside a snapshot, and whether the snapshot was layered.

    A schema-2 document carries `schema`, `corporate`, `user` and `merged`.
    Anything else -- including an unparseable file -- is treated as a legacy
    merged blob, because guessing "layered" wrongly would DISCARD the operator's
    overrides, while guessing "merged" wrongly only reproduces the old
    behaviour the warning already describes.
    """
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}, False
    if not isinstance(doc, dict) or doc.get("schema") != SNAPSHOT_SCHEMA:
        return {}, False
    user = doc.get("user")
    return (user if isinstance(user, dict) else {}), True


def _warn_about_shadowed_corporate_keys(workspace_root: Path, restored_text: str,
                                        snapshot_name: str) -> list[str]:
    """Name the corporate keys a legacy revert is about to freeze as overrides.

    Schema-1 snapshots hold the MERGED config (defaults + corporate + user), so
    restoring one writes every corporate value into the per-user layer and
    future corporate pushes stop reaching those keys -- the opposite of what
    "revert" promises. Schema 2 keeps the layers apart and does not call this.

    Returns the shadowed key names, for the caller and for tests.
    """
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    if not corp.exists():
        return []
    corp_cfg = _load_layer(corp, "corporate")
    try:
        restored_cfg = yaml.safe_load(restored_text) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(restored_cfg, dict):
        return []
    shadowed = sorted(k for k in corp_cfg if k in restored_cfg)
    if shadowed:
        logger.warning(
            "reverting to %s writes the MERGED config into the user layer, so "
            "these corporate-owned keys become frozen user overrides and future "
            "corporate pushes will not reach them: %s",
            snapshot_name, ", ".join(shadowed),
        )
    return shadowed
