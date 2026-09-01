"""Tests for the shared HC.io provisioning util and the check registries.

Guards three things the deadman monitoring depends on:
  1. write_env upserts keys atomically and idempotently (no dup lines, no .tmp
     leak) -- it edits the cred-bearing .env, so a botched write is a real risk.
  2. Every provisioned check spec is well-formed: it carries an env_key, a name,
     tags, a desc, a grace, and exactly one cadence (timeout XOR schedule).
  3. Every provisioned check is matched by a ping call site in the tree, and
     every ping call site is matched by a provisioned check.

Point 2 said "the three steward-daemon check specs" and covered the two in
scripts/setup-daemon-healthchecks.py. The five in
scripts/setup-fireside-healthchecks.py had no shape check at all, so both the
count and the scope were wrong in the same sentence.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import healthchecks_setup  # noqa: E402

# The one derivation of "which checks can this repository actually ping",
# imported rather than copied. It walks the tree's `hc_ping(...)` /
# `healthchecks.ping(...)` call sites through the AST and through the git-aware
# walker; a second copy here would be the copy that stops being fixed.
from tests.test_deadman_ping_containment import _pingable_env_names  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SETUP_SCRIPT = SCRIPTS / "setup-daemon-healthchecks.py"
FIRESIDE_SCRIPT = SCRIPTS / "setup-fireside-healthchecks.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_daemon_checks() -> list:
    return _load(SETUP_SCRIPT, "_setup_daemon_hc").CHECKS


def _load_fireside_checks() -> list:
    return _load(FIRESIDE_SCRIPT, "_setup_fireside_hc").build_checks()


def _all_provisioned() -> list:
    return _load_daemon_checks() + _load_fireside_checks()


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


@pytest.mark.parametrize("loader,prefix", [
    (_load_daemon_checks, "STEWARD_HC_"),
    (_load_fireside_checks, "FIRESIDE_HC_"),
])
def test_every_provisioned_check_is_wellformed(loader, prefix):
    """The shape check, applied to BOTH registries rather than one of two.

    Five fireside specs were provisioned by the same `run_setup` this module
    tests and had no shape assertion anywhere. A spec missing `grace` is
    accepted by healthchecks.io with its default, and a spec carrying both
    `timeout` and `schedule` silently drops one of them, so the check ends up
    watching a cadence nobody chose.
    """
    checks = loader()
    assert checks, "the registry is empty; nothing is being provisioned"
    assert len({c["env_key"] for c in checks}) == len(checks), "env_keys must be unique"
    assert len({c["name"] for c in checks}) == len(checks), "check names must be unique"
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
        assert c["env_key"].startswith(prefix)


def test_every_provisioned_check_is_pinged_by_something_in_this_tree():
    """An orphan deadman alerts forever, and this file never asked.

    scripts/setup-daemon-healthchecks.py records the incident in its own
    docstring: on 2026-08-03 `steward-eval-drift` sent a DOWN to the operator
    after both its daemon and its entry here were gone, because removing the
    entry does nothing to healthchecks.io: that script only creates and
    updates. The guard against the next one is not a hand-maintained count; it
    is that every env_key provisioned here is still a key some code path pings.

    `len(checks) == 2` stood in for this and cannot do the job: an entry that
    is REPLACED by a new orphan keeps the count at two.
    """
    provisioned = {c["env_key"] for c in _all_provisioned()}
    pingable = set(_pingable_env_names())
    assert len(provisioned) >= 6, (
        f"only {sorted(provisioned)} provisioned; the registries stopped loading")
    orphans = sorted(provisioned - pingable)
    assert not orphans, (
        f"these checks are provisioned but nothing in the tree pings them: "
        f"{orphans}. Delete the check through the HC.io API in the same change "
        f"as the daemon, or it counts down and alerts forever.")


def test_every_ping_call_site_has_a_check_provisioned_for_it():
    """The other direction, which fails silently rather than loudly.

    `ping()` returns False on an env var that is not set and logs at INFO, so a
    daemon pinging a check nobody ever created looks exactly like a daemon
    pinging a healthy one: no alert, no monitoring, no evidence either way.
    """
    provisioned = {c["env_key"] for c in _all_provisioned()}
    pingable = set(_pingable_env_names())
    assert pingable, "the AST walk found no ping call sites at all"
    unprovisioned = sorted(pingable - provisioned)
    assert not unprovisioned, (
        f"these env keys are pinged by code in this tree but no setup script "
        f"provisions a check for them, so the ping goes nowhere: {unprovisioned}")


def test_daemon_check_count_is_still_two():
    """A deliberate tripwire on the steward registry's size, and nothing else.

    Two, not three: the eval-drift check went with its daemon on 2026-08-03. A
    deadman whose daemon cannot run alerts forever, so the entry is removed in
    the same change as the code it watched, and this number makes that change
    visible in review rather than silent.

    The SHAPE half of this test moved into `test_every_provisioned_check_is_
    wellformed`, which now runs over the fireside registry too; leaving a second
    copy here is how one of them stops being fixed. What a count cannot do is
    catch a retirement paired with an addition, which is why the orphan guard
    above exists beside it rather than instead of it.
    """
    assert len(_load_daemon_checks()) == 2
