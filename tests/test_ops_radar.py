#!/usr/bin/env python3
"""Tests for the ops-radar aggregator (scripts/ops-radar.py).

Standalone-runnable, plain asserts. Signals are INJECTED into assess() so the
suppression / escalation logic is tested without live git/ollama/CRM. Anchored
to the plan's Success Signal and the ack/crunch/auto-heal invariants.
"""

import importlib.util
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("ops_radar", ROOT / "scripts" / "ops-radar.py")
orad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(orad)


def _check(name, cond):
    """Fail the test when `cond` is false. The name is the failure message.

    This used to `return bool(cond)`. Every caller accumulated the result into
    an `ok` flag and closed with `return ok`, which is how these files ran
    before they were renamed `test_*.py`: as standalone scripts, under a
    `main()` that read the return value.

    Under pytest a test that RETURNS False still PASSES. Pytest only emits
    `PytestReturnNotNoneWarning` and moves on. So the rename made the runner
    redundant and the conditions blind at the same time, and nothing said so.
    Measured 2026-08-20 across the three files that shared this helper: 25 test
    functions, 78 conditions, none able to fail the suite.

    An assert is the whole fix. The `main()` runner went with it, because its
    only job was to read a return value that no longer exists.
    """
    assert cond, name


def sig(key, *, tier, due, severity, summary=None):
    return {
        "key": key, "value": None, "threshold": None, "due": due,
        "severity": severity, "tier": tier,
        "summary": summary or f"{key}: due",
    }


def _state(td):
    d = Path(td) / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_quiet_empty_when_nothing_due():
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        signals = [sig("backup", tier="B", due=False, severity="ok"),
                   sig("ollama", tier="A", due=False, severity="ok")]
        r = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal={})
        _check("nothing due -> empty quiet line", r["quiet_line"] == "")
        _check("nothing due -> no displayed", not r["displayed"])


def test_tier_b_surfaces():
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        signals = [sig("backup", tier="B", due=True, severity="warn", summary="backup: 2 uncommitted (30h old)")]
        r = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal={})
        _check("due Tier-B surfaces", any(s["key"] == "backup" for s in r["displayed"]))
        _check("quiet line carries summary", "backup" in r["quiet_line"])


def test_tier_a_silent_below_escalation():
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        signals = [sig("ollama", tier="A", due=True, severity="high")]
        # 1 failure < AUTOHEAL_ESCALATE -> silent
        r = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal={"ollama": {"failures": 1}})
        _check("Tier-A 1 failure -> silent", not r["displayed"])
        # 2 failures -> escalates as critical
        r = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal={"ollama": {"failures": 2}})
        crit = [s for s in r["displayed"] if s["key"] == "ollama_autoheal"]
        _check("Tier-A 2 failures -> escalates critical",
                     len(crit) == 1 and crit[0]["severity"] == "critical")


def test_ack_suppresses_then_worsening_resurfaces():
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        now = time.time()
        # ack backup at band warn
        orad.save_json_atomic(sd / orad.ACK_FILE,
                              {"backup": {"acked_at": now, "ttl_seconds": 24 * 3600, "acked_band": "warn"}})
        warn = [sig("backup", tier="B", due=True, severity="warn")]
        r = orad.assess(ROOT, ROOT, sd, signals=warn, autoheal={}, now=now + 10)
        _check("ack suppresses at same band", not r["displayed"]
                     and any(s["suppressed_by"] == "ack" for s in r["suppressed"]))
        # worsening to high re-surfaces
        high = [sig("backup", tier="B", due=True, severity="high")]
        r = orad.assess(ROOT, ROOT, sd, signals=high, autoheal={}, now=now + 10)
        _check("worsening past acked band re-surfaces",
                     any(s["key"] == "backup" for s in r["displayed"]))
        # expired ack re-surfaces
        r = orad.assess(ROOT, ROOT, sd, signals=warn, autoheal={}, now=now + 25 * 3600)
        _check("expired ack re-surfaces",
                     any(s["key"] == "backup" for s in r["displayed"]))


def test_crunch_suppresses_tier_b_but_critical_pierces():
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        orad.save_json_atomic(sd / orad.CRUNCH_FILE, {"on": True})
        signals = [sig("backup", tier="B", due=True, severity="warn"),
                   sig("ollama", tier="A", due=True, severity="high")]
        r = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal={"ollama": {"failures": 2}})
        keys = {s["key"] for s in r["displayed"]}
        _check("crunch suppresses Tier-B backup", "backup" not in keys)
        _check("crunch lets critical auto-heal pierce", "ollama_autoheal" in keys)


def test_success_signal():
    """The plan's Success Signal, end to end via injection."""
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        # (a) backup uncommitted older than threshold (warn), (b) ollama down +
        # auto-heal failed twice, (c) crunch off.
        signals = [
            sig("backup", tier="B", due=True, severity="warn", summary="backup: 1 uncommitted (30h old)"),
            sig("ollama", tier="A", due=True, severity="high"),
        ]
        autoheal = {"ollama": {"failures": 2}}
        r = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal=autoheal)
        keys = {s["key"] for s in r["displayed"]}
        _check("SS: backup AND auto-heal both surface",
                     "backup" in keys and "ollama_autoheal" in keys)
        _check("SS: quiet line names both",
                     "backup" in r["quiet_line"] and "auto-heal FAILED" in r["quiet_line"])

        # ack backup -> only backup silenced, auto-heal remains
        now = time.time()
        orad.save_json_atomic(sd / orad.ACK_FILE,
                              {"backup": {"acked_at": now, "ttl_seconds": 24 * 3600, "acked_band": "warn"}})
        r = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal=autoheal, now=now + 10)
        keys = {s["key"] for s in r["displayed"]}
        _check("SS: ack backup silences only backup",
                     "backup" not in keys and "ollama_autoheal" in keys)

        # crunch on -> backup suppressed, auto-heal (critical) still shows
        orad.save_json_atomic(sd / orad.CRUNCH_FILE, {"on": True})
        # clear the ack so the only suppressor under test is crunch
        orad.save_json_atomic(sd / orad.ACK_FILE, {})
        r = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal=autoheal)
        keys = {s["key"] for s in r["displayed"]}
        _check("SS: crunch suppresses backup, auto-heal floor pierces",
                     "backup" not in keys and "ollama_autoheal" in keys)


def test_record_heal_result():
    a = {}
    a = orad.record_heal_result(a, "ollama", False)
    _check("fail -> failures=1", a["ollama"]["failures"] == 1)
    a = orad.record_heal_result(a, "ollama", False)
    _check("fail again -> failures=2", a["ollama"]["failures"] == 2)
    a = orad.record_heal_result(a, "ollama", True)
    _check("success -> failures reset 0", a["ollama"]["failures"] == 0)


def test_autoheal_user_unit_absent_increments():
    """A restart that leaves ollama down (e.g. user unit absent) MUST increment
    the counter, not no-op silently."""
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        signals = [sig("ollama", tier="A", due=True, severity="high"),
                   sig("memory_index", tier="A", due=False, severity="ok")]
        fail_restart = lambda: (False, "Unit not found (user)")
        r = orad.run_autoheal(sd, ROOT, ROOT, signals=signals, restart_fn=fail_restart,
                              rebuild_fn=lambda: (True, "n/a"))
        _check("user-unit-absent -> failures=1", r["autoheal"]["ollama"]["failures"] == 1)
        r = orad.run_autoheal(sd, ROOT, ROOT, signals=signals, restart_fn=fail_restart,
                              rebuild_fn=lambda: (True, "n/a"))
        _check("second failure -> failures=2 (escalates)", r["autoheal"]["ollama"]["failures"] == 2)
        # escalation now surfaces via assess
        a = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal=r["autoheal"])
        _check("escalation surfaces as critical auto-heal line",
                     any(s["key"] == "ollama_autoheal" for s in a["displayed"]))


def test_autoheal_privilege_denied_increments():
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        signals = [sig("ollama", tier="A", due=True, severity="high"),
                   sig("memory_index", tier="A", due=False, severity="ok")]
        denied = lambda: (False, "Failed to restart: access denied (polkit)")
        r = orad.run_autoheal(sd, ROOT, ROOT, signals=signals, restart_fn=denied,
                              rebuild_fn=lambda: (True, "n/a"))
        _check("privilege-denied -> counted as failure (not no-op)",
                     r["autoheal"]["ollama"]["failures"] == 1)


def test_autoheal_success_resets():
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        # pre-seed 3 prior failures
        orad.save_json_atomic(sd / orad.AUTOHEAL_FILE, {"ollama": {"failures": 3}})
        signals = [sig("ollama", tier="A", due=True, severity="high"),
                   sig("memory_index", tier="A", due=False, severity="ok")]
        good = lambda: (True, "restarted")
        r = orad.run_autoheal(sd, ROOT, ROOT, signals=signals, restart_fn=good,
                              rebuild_fn=lambda: (True, "n/a"))
        _check("success after failures -> reset 0", r["autoheal"]["ollama"]["failures"] == 0)


def test_autoheal_index_skipped_when_ollama_down():
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        signals = [sig("ollama", tier="A", due=True, severity="high"),
                   sig("memory_index", tier="A", due=True, severity="high")]
        # ollama restart fails -> index rebuild must be skipped AND counted failed
        r = orad.run_autoheal(sd, ROOT, ROOT, signals=signals,
                              restart_fn=lambda: (False, "down"),
                              rebuild_fn=lambda: (True, "should-not-run"))
        _check("index counted failed when ollama down",
                     r["autoheal"]["memory_index"]["failures"] == 1)
        idx_action = next(a for a in r["actions"] if a["target"] == "memory_index")
        _check("index action notes the skip", "skipped" in idx_action["note"])
