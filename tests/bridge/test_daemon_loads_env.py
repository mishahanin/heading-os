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
    """The ORDER, not just the fact.

    This recorded a single boolean and asserted it after `main()` returned,
    which proves `load_env` ran somewhere inside `main()` and nothing about
    when. A `main()` that dispatched first and loaded the `.env` second passed
    it while fully restoring the 2026-06-22 regression this file names: TZ read
    at dispatch time, missing, silently falling back to UTC, and the dashboard
    showing the wrong greeting, the wrong tz label and the wrong meeting
    countdowns. Both stubs now append to one list, so the sequence is the
    thing measured.
    """
    mod = _load_daemon_module()
    order: list[str] = []
    monkeypatch.setattr(mod, "load_env", lambda *a, **k: order.append("load_env"))
    # Stub the dispatch target so main() returns without starting a server.
    monkeypatch.setattr(mod, "check_health", lambda: order.append("check_health"))
    monkeypatch.setattr(sys, "argv", ["bridge-daemon.py", "--health"])

    mod.main()

    assert "load_env" in order, "bridge-daemon main() must call load_env() at startup"
    assert "check_health" in order, (
        "the --health subcommand never dispatched, so this run proves no ordering")
    assert order.index("load_env") < order.index("check_health"), (
        f"load_env must run BEFORE the subcommand dispatch; observed {order}")
