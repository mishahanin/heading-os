"""Regression: bridge-daemon must load .env at startup so HEADING_OS_TZ resolves.

Bug (2026-06-22): bridge-daemon.py was the only daemon that never called
load_env(). Launched by systemd-user without HEADING_OS_TZ in the process
environment, get_default_tz_name() fell back to UTC, so the dashboard rendered
the wrong time-of-day greeting, a "UTC" tz label, and miscomputed meeting
countdowns (a past meeting showed as "in 41m"). This locks main() loading the
.env before it dispatches to any subcommand.
"""
import importlib.util
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]
DAEMON = WORKSPACE / "scripts" / "bridge-daemon.py"


def _load_daemon_module():
    """Import scripts/bridge-daemon.py (hyphenated, not importable by name)."""
    spec = importlib.util.spec_from_file_location("bridge_daemon_cli", DAEMON)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_main_loads_env_before_dispatch(monkeypatch):
    mod = _load_daemon_module()
    called = {"load_env": False}
    monkeypatch.setattr(mod, "load_env", lambda *a, **k: called.__setitem__("load_env", True))
    # Stub the dispatch target so main() returns without starting a server.
    monkeypatch.setattr(mod, "check_health", lambda: None)
    monkeypatch.setattr(sys, "argv", ["bridge-daemon.py", "--health"])

    mod.main()

    assert called["load_env"], "bridge-daemon main() must call load_env() at startup"
