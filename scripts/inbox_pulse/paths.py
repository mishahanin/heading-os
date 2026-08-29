#!/usr/bin/env python3
"""Runtime path detection for the Inbox Pulse daemon.

Centralises VM-vs-laptop detection so every daemon module imports one
function rather than re-implementing the INBOX_PULSE_STATE_DIR pattern.

The workspace root is found by walking parent directories up to the dir that
contains both config/ and scripts/, so it resolves correctly whether the code
runs on the laptop checkout or on the always-on service host - no host-specific
path literal is embedded.

Tests: tests/test_a_day_that_could_not_be_read_and_was_called_quiet.py

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
from scripts.utils.paths import (  # noqa: E402
    get_workspace_root as _shared_workspace_root,
)

__all__ = ["get_workspace_root", "get_state_dir", "get_data_root"]

_THIS_FILE = Path(__file__).resolve()


# ---------------------------------------------------------------------------
# Module-level cache (path is stable for the process lifetime)
# ---------------------------------------------------------------------------

_state_dir_cache: Path | None = None
# The env value the cached state dir was resolved FROM. The cache used to key
# on nothing, so the first call in a process fixed the answer for its lifetime
# and INBOX_PULSE_STATE_DIR -- documented one line below as a "test/dev
# override" -- stopped overriding anything once any other caller had resolved
# first. A cache that ignores its own input is not a cache of that input.
_state_dir_cache_key: str | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_workspace_root() -> Path:
    """Return the workspace root directory.

    Delegates to `scripts.utils.paths.get_workspace_root`, which is the one
    implementation. This module used to carry a private copy that walked up
    from its own file looking for a directory holding both `config/` and
    `scripts/`. That copy answered correctly on the laptop and on the service
    host, so nothing ever looked at it again, and it silently ignored the
    `WORKSPACE_ROOT` environment override the shared helper honours.

    MEASURED 2026-08-29 with `WORKSPACE_ROOT=/tmp/pretend-workspace` exported
    and that directory seeded with `config/` and `scripts/`:

        scripts.utils.paths        -> /tmp/pretend-workspace
        scripts.inbox_pulse.paths  -> the real checkout

    Two answers to one question, and the daemon read the one that cannot be
    redirected. The caching also moved to the shared helper, which resolves in
    microseconds and needs none.
    """
    return _shared_workspace_root()


def get_state_dir() -> Path:
    """Return the state directory for daemon files (logs, ledger, cost tracker).

    Resolution order (highest priority first):
      1. INBOX_PULSE_STATE_DIR env var (test/dev override)
      2. <data_root>/state/email-triage/   (runtime state is DATA, never engine)

    Auto-creates the directory (parents=True, exist_ok=True) so callers
    never have to. Cached against the env value it was resolved from, so a
    changed override re-resolves instead of returning the previous answer.
    """
    global _state_dir_cache, _state_dir_cache_key

    env_override = os.environ.get("INBOX_PULSE_STATE_DIR", "").strip()
    if _state_dir_cache is not None and _state_dir_cache_key == env_override:
        return _state_dir_cache

    if env_override:
        path = Path(env_override)
    else:
        path = get_data_root() / "state" / "email-triage"

    path.mkdir(parents=True, exist_ok=True)
    _state_dir_cache = path
    _state_dir_cache_key = env_override
    return _state_dir_cache
