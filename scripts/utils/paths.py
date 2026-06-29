"""Canonical workspace path resolution -- the single source of truth.

This module hardens workspace-root discovery so every downstream script,
hook, daemon, and shell launcher resolves the same root the same way, on
Windows, WSL2, native Linux, and macOS, regardless of where the repo is
cloned or which user runs it.

Resolution order for ``get_workspace_root()`` (first hit wins):

1. ``WORKSPACE_ROOT`` environment variable (explicit override -- used by
   systemd units, CI, containers, and tests). Must point at a real dir.
2. Marker walk: starting from this file, walk parent directories until one
   contains BOTH stable markers ``CLAUDE.md`` and ``.claude/``. This is the
   structural identity of a 31C workspace and survives relocation.
3. Labeled fallback constant ``_FALLBACK_ROOT`` (three levels up from this
   file: ``scripts/utils/paths.py`` -> workspace root). Clearly labeled as
   a last resort, NOT a hardcoded absolute path.

There is intentionally no hardcoded ``/mnt/c/...`` or ``/home/<user>`` value
anywhere in the primary resolution path.

Backward compatibility: ``scripts/utils/workspace.py`` re-exports
``get_workspace_root`` and ``load_env`` from here, so the long-standing
``from scripts.utils.workspace import get_workspace_root`` import keeps
working unchanged. New code may import directly from this module.

Shell callers (``.sh`` scripts) can resolve the root without sourcing this
package via the documented one-liner::

    ROOT="$(python3 "$WS/scripts/utils/paths.py")"

or, when WORKSPACE_ROOT may already be set::

    ROOT="$(WORKSPACE_ROOT="${WORKSPACE_ROOT:-}" python3 -c \
      'from scripts.utils.paths import get_workspace_root as r; print(r())')"

See ``scripts/install-bridge-service.sh`` for the systemd install-time
templating pattern (it resolves the root from its own location and bakes it
into ``WorkingDirectory=`` and ``ExecStart=`` so the unit is self-contained).
"""

import logging
import os
from pathlib import Path

_log = logging.getLogger(__name__)

# ============================================================
# Constants
# ============================================================

# Stable markers that identify a 31C workspace root. Both must be present.
_ROOT_MARKERS = ("CLAUDE.md", ".claude")

# Labeled last-resort fallback: three parents up from this file.
#   scripts/utils/paths.py -> scripts/utils -> scripts -> <workspace root>
# This is a STRUCTURAL fallback derived from __file__, NOT a hardcoded
# absolute path. It is only used if the env override is unset and the
# marker walk finds nothing (e.g. markers were renamed).
_FALLBACK_ROOT = Path(__file__).resolve().parent.parent.parent


# ============================================================
# Root resolution
# ============================================================

def _has_markers(candidate: Path) -> bool:
    """True if every marker in _ROOT_MARKERS exists under candidate."""
    return all((candidate / marker).exists() for marker in _ROOT_MARKERS)


def get_workspace_root() -> Path:
    """Return the workspace root as an absolute pathlib.Path.

    Resolution order (first hit wins):
      1. WORKSPACE_ROOT env override (if set and the directory exists).
      2. Walk up from this file to the dir containing CLAUDE.md AND .claude/.
      3. Labeled structural fallback (_FALLBACK_ROOT).

    Never returns a hardcoded absolute literal as the primary value.
    """
    # 1. Explicit environment override.
    env_root = os.environ.get("WORKSPACE_ROOT")
    if env_root:
        candidate = Path(env_root).expanduser()
        # Resolve even if it does not exist yet, but only honour it when real.
        if candidate.is_dir():
            return candidate.resolve()

    # 2. Marker walk up from this file.
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if parent.is_dir() and _has_markers(parent):
            return parent

    # 3. Labeled fallback constant.
    return _FALLBACK_ROOT


# ============================================================
# Data-root seam (HEADING OS engine/data separation, spec Section 2)
# ============================================================

# Bump when the on-disk private-data format changes in a way that needs migration.
DATA_SCHEMA_VERSION = 1


class DataRootError(RuntimeError):
    """Raised when a write is attempted with no real data root (demo mode)."""


def get_data_root() -> Path:
    """Resolve the private-data root. First hit wins:

      1. HEADING_OS_DATA env override (when it points at a real dir).
      2. Legacy in-tree: the workspace root itself, when private data already
         lives there (transitional ceo-main). A workspace carrying its own data
         is authoritative for itself -- so creating the ../.heading-os-data
         sibling does NOT prematurely flip live ceo-main onto it (cutover is a
         deliberate later step that removes ceo-main's in-tree data).
      3. Sibling ``../.heading-os-data`` (the dedicated data repo). A data-less
         engine clone (.heading-os) has no in-tree data, so it lands here.
      4. Demo mode: ``<workspace_root>/examples`` (bundled, read-only).

    Order note (spec Section 2 refinement): in-tree precedes sibling. The spec's
    original order had sibling first; reordered during Plan 4 because the only
    workspace that ever has BOTH is the transitional ceo-main, which must keep
    its own data until cutover. The env override still wins, so verification can
    point the engine clone at the real sibling explicitly.
    """
    env = os.environ.get("HEADING_OS_DATA")
    if env:
        cand = Path(env).expanduser()
        if cand.is_dir():
            return cand.resolve()
    root = get_workspace_root()
    if (root / "crm" / "contacts").is_dir() or (root / "knowledge").is_dir():
        _log.warning(
            "get_data_root(): in-tree data-root heuristic fired — private data "
            "detected inside the engine clone at %s. This is expected only on the "
            "transitional ceo-main workspace. On a data-less engine clone this "
            "indicates a misconfiguration: set the HEADING_OS_DATA env var or use "
            "the sibling .heading-os-data repository.",
            root,
        )
        return root
    sibling = root.parent / ".heading-os-data"
    if sibling.is_dir():
        return sibling.resolve()
    return (root / "examples").resolve()


def data_root_is_demo() -> bool:
    """True when get_data_root() resolved to the bundled read-only examples."""
    return get_data_root() == (get_workspace_root() / "examples").resolve()


def read_data_schema_version() -> int:
    """Read the data root's .schema-version. Missing/unreadable -> assume current
    (legacy in-tree and demo roots carry no marker and must not be blocked)."""
    f = get_data_root() / ".schema-version"
    try:
        return int(f.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return DATA_SCHEMA_VERSION


def check_schema_compatible() -> tuple[bool, str]:
    """Return (ok, message). ok=False only when the engine schema is NEWER than
    the data on disk -- i.e. a migration is required before the workspace runs."""
    data_v = read_data_schema_version()
    if data_v < DATA_SCHEMA_VERSION:
        return (
            False,
            f"Engine data schema v{DATA_SCHEMA_VERSION} is newer than data v{data_v}; "
            "run a data migration before continuing.",
        )
    return (True, "")


def require_writable_data_root() -> Path:
    """Return the data root, or raise DataRootError if running on read-only examples."""
    if data_root_is_demo():
        raise DataRootError(
            "No private data folder found - running on read-only examples. "
            "Run `python scripts/init-data.py` to create your data folder."
        )
    return get_data_root()


# ============================================================
# Home + data/state/log dir helpers
# ============================================================

def home() -> Path:
    """Return the current user's home directory (cross-platform).

    Honours the HOME env var on POSIX and USERPROFILE on Windows via
    pathlib's own resolution. Never embeds a literal username.
    """
    return Path.home()


def data_dir(*parts: str) -> Path:
    """Return a workspace data directory, creating it if needed.

    Override base with the WORKSPACE_DATA_DIR env var; otherwise defaults to
    ``<workspace_root>/.data``. Optional path *parts* are appended.
    """
    base = os.environ.get("WORKSPACE_DATA_DIR")
    root = Path(base).expanduser() if base else get_workspace_root() / ".data"
    target = root.joinpath(*parts) if parts else root
    target.mkdir(parents=True, exist_ok=True)
    return target


def state_dir(*parts: str) -> Path:
    """Return a workspace state directory, creating it if needed.

    Override base with the WORKSPACE_STATE_DIR env var; otherwise defaults to
    ``<workspace_root>/.state``. Optional path *parts* are appended.
    """
    base = os.environ.get("WORKSPACE_STATE_DIR")
    root = Path(base).expanduser() if base else get_workspace_root() / ".state"
    target = root.joinpath(*parts) if parts else root
    target.mkdir(parents=True, exist_ok=True)
    return target


def log_dir(*parts: str) -> Path:
    """Return a workspace log directory, creating it if needed.

    Override base with the WORKSPACE_LOG_DIR env var; otherwise defaults to
    ``<workspace_root>/.logs``. Optional path *parts* are appended.
    """
    base = os.environ.get("WORKSPACE_LOG_DIR")
    root = Path(base).expanduser() if base else get_workspace_root() / ".logs"
    target = root.joinpath(*parts) if parts else root
    target.mkdir(parents=True, exist_ok=True)
    return target


# ============================================================
# .env loading (canonical; re-exported by workspace.py)
# ============================================================

def load_env(workspace_root: Path = None) -> None:
    """Load .env variables into os.environ (without overwriting existing vars).

    Strips matching surrounding single/double quotes from values per dotenv
    convention, so KEY="value" and KEY=value both yield 'value'. Without this,
    callers that pass the value straight into libraries expecting bare strings
    (e.g. a URL handed to httpx) hit "missing scheme" errors when the literal
    '"https://..."' arrives intact.
    """
    root = workspace_root or get_workspace_root()
    env_path = root / ".env"
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


# ============================================================
# Shell-callable resolver
# ============================================================
# Running this module directly prints the resolved workspace root, so .sh
# scripts can do:  ROOT="$(python3 scripts/utils/paths.py)"
if __name__ == "__main__":
    print(get_workspace_root())
