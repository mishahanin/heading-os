#!/usr/bin/env python3
"""Runtime path detection for the Inbox Pulse daemon.

Centralises VM-vs-laptop detection so every daemon module imports one
function rather than re-implementing the INBOX_PULSE_STATE_DIR pattern.

The workspace root is found by walking parent directories up to the dir that
contains both config/ and scripts/, so it resolves correctly whether the code
runs on the laptop checkout or on the always-on service host - no host-specific
path literal is embedded.

Resolution order for get_state_dir():
  1. INBOX_PULSE_STATE_DIR env var (test/dev override)
  2. <data_root>/state/email-triage/   (runtime state is DATA, never engine)

Usage::

    from scripts.inbox_pulse.paths import get_state_dir, get_workspace_root

    state = get_state_dir()          # Path, auto-created
    root  = get_workspace_root()     # Path to the workspace root
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the foundation path helpers importable when this module is loaded
# directly (e.g. by the daemon) without the workspace root on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.paths import get_data_root  # noqa: E402

__all__ = ["get_workspace_root", "get_state_dir", "get_data_root"]

_THIS_FILE = Path(__file__).resolve()


# ---------------------------------------------------------------------------
# Module-level cache (path is stable for the process lifetime)
# ---------------------------------------------------------------------------

_workspace_root_cache: Path | None = None
_state_dir_cache: Path | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_workspace_root() -> Path:
    """Return the workspace root directory.

    The directory containing both config/ and scripts/, found by walking
    parent directories upward from this file. Resolves correctly on the
    laptop checkout and on the service host alike.

    Cached after the first call.
    """
    global _workspace_root_cache
    if _workspace_root_cache is not None:
        return _workspace_root_cache

    # Walk up from this file until we find a dir with both config/ and scripts/.
    candidate = _THIS_FILE.parent
    while True:
        if (candidate / "config").is_dir() and (candidate / "scripts").is_dir():
            _workspace_root_cache = candidate
            return _workspace_root_cache
        parent = candidate.parent
        if parent == candidate:
            # Reached filesystem root without a match; fall back to the
            # grandparent of scripts/inbox_pulse/ (scripts/ parent).
            _workspace_root_cache = _THIS_FILE.parent.parent.parent
            return _workspace_root_cache
        candidate = parent


def get_state_dir() -> Path:
    """Return the state directory for daemon files (logs, ledger, cost tracker).

    Resolution order (highest priority first):
      1. INBOX_PULSE_STATE_DIR env var (test/dev override)
      2. <data_root>/state/email-triage/   (runtime state is DATA, never engine)

    Auto-creates the directory (parents=True, exist_ok=True) so callers
    never have to. Cached after the first call.
    """
    global _state_dir_cache
    if _state_dir_cache is not None:
        return _state_dir_cache

    env_override = os.environ.get("INBOX_PULSE_STATE_DIR", "").strip()
    if env_override:
        path = Path(env_override)
    else:
        path = get_data_root() / "state" / "email-triage"

    path.mkdir(parents=True, exist_ok=True)
    _state_dir_cache = path
    return _state_dir_cache
