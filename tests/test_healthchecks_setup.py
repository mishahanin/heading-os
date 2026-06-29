"""Tests for the shared HC.io provisioning util and the daemon check registry.

Guards two things the deadman monitoring depends on:
  1. write_env upserts keys atomically and idempotently (no dup lines, no .tmp
     leak) -- it edits the cred-bearing .env, so a botched write is a real risk.
  2. The three steward-daemon check specs are well-formed: each carries an
     env_key the daemon pings, and exactly one cadence (timeout XOR schedule).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import healthchecks_setup  # noqa: E402

SETUP_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "setup-daemon-healthchecks.py"
)


def _load_daemon_checks() -> list:
    spec = importlib.util.spec_from_file_location("_setup_daemon_hc", SETUP_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CHECKS


def test_write_env_appends_then_replaces_idempotently(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("EXISTING=1\n", encoding="utf-8")
    monkeypatch.setattr(healthchecks_setup, "_ENV_FILE", env)

    healthchecks_setup.write_env({"STEWARD_HC_SENTINEL": "https://hc-ping.com/aaa"})
    body = env.read_text()
    assert "EXISTING=1" in body
    assert "STEWARD_HC_SENTINEL=https://hc-ping.com/aaa" in body

    # Re-running with a new value replaces the line, never duplicates it.
    healthchecks_setup.write_env({"STEWARD_HC_SENTINEL": "https://hc-ping.com/bbb"})
    body = env.read_text()
    assert body.count("STEWARD_HC_SENTINEL=") == 1
    assert "https://hc-ping.com/bbb" in body
    assert "https://hc-ping.com/aaa" not in body

    # Atomic write leaves no temp file behind.
    assert not (tmp_path / ".env.tmp").exists()


def test_daemon_checks_wellformed():
    checks = _load_daemon_checks()
    assert len(checks) == 3
    env_keys = {c["env_key"] for c in checks}
    names = {c["name"] for c in checks}
    assert len(env_keys) == 3, "env_keys must be unique"
    assert len(names) == 3, "check names must be unique"
    for c in checks:
        for field in ("env_key", "name", "tags", "desc", "grace"):
            assert c.get(field), f"{c.get('name')} missing {field}"
        has_timeout = "timeout" in c
        has_schedule = "schedule" in c
        assert has_timeout != has_schedule, (
            f"{c['name']} must have exactly one of timeout/schedule"
        )
        if has_schedule:
            assert c.get("tz"), f"{c['name']} cron check needs a tz"
        assert c["env_key"].startswith("STEWARD_HC_")
