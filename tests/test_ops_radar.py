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
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}")
    return bool(cond)


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
    ok = True
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        signals = [sig("backup", tier="B", due=False, severity="ok"),
                   sig("ollama", tier="A", due=False, severity="ok")]
        r = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal={})
        ok &= _check("nothing due -> empty quiet line", r["quiet_line"] == "")
        ok &= _check("nothing due -> no displayed", not r["displayed"])
    return ok


def test_tier_b_surfaces():
    ok = True
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        signals = [sig("backup", tier="B", due=True, severity="warn", summary="backup: 2 uncommitted (30h old)")]
        r = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal={})
        ok &= _check("due Tier-B surfaces", any(s["key"] == "backup" for s in r["displayed"]))
        ok &= _check("quiet line carries summary", "backup" in r["quiet_line"])
    return ok


def test_tier_a_silent_below_escalation():
    ok = True
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        signals = [sig("ollama", tier="A", due=True, severity="high")]
        # 1 failure < AUTOHEAL_ESCALATE -> silent
        r = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal={"ollama": {"failures": 1}})
        ok &= _check("Tier-A 1 failure -> silent", not r["displayed"])
        # 2 failures -> escalates as critical
        r = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal={"ollama": {"failures": 2}})
        crit = [s for s in r["displayed"] if s["key"] == "ollama_autoheal"]
        ok &= _check("Tier-A 2 failures -> escalates critical",
                     len(crit) == 1 and crit[0]["severity"] == "critical")
    return ok


def test_ack_suppresses_then_worsening_resurfaces():
    ok = True
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        now = time.time()
        # ack backup at band warn
        orad.save_json_atomic(sd / orad.ACK_FILE,
                              {"backup": {"acked_at": now, "ttl_seconds": 24 * 3600, "acked_band": "warn"}})
        warn = [sig("backup", tier="B", due=True, severity="warn")]
        r = orad.assess(ROOT, ROOT, sd, signals=warn, autoheal={}, now=now + 10)
        ok &= _check("ack suppresses at same band", not r["displayed"]
                     and any(s["suppressed_by"] == "ack" for s in r["suppressed"]))
        # worsening to high re-surfaces
        high = [sig("backup", tier="B", due=True, severity="high")]
        r = orad.assess(ROOT, ROOT, sd, signals=high, autoheal={}, now=now + 10)
        ok &= _check("worsening past acked band re-surfaces",
                     any(s["key"] == "backup" for s in r["displayed"]))
        # expired ack re-surfaces
        r = orad.assess(ROOT, ROOT, sd, signals=warn, autoheal={}, now=now + 25 * 3600)
        ok &= _check("expired ack re-surfaces",
                     any(s["key"] == "backup" for s in r["displayed"]))
    return ok


def test_crunch_suppresses_tier_b_but_critical_pierces():
    ok = True
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        orad.save_json_atomic(sd / orad.CRUNCH_FILE, {"on": True})
        signals = [sig("backup", tier="B", due=True, severity="warn"),
                   sig("ollama", tier="A", due=True, severity="high")]
        r = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal={"ollama": {"failures": 2}})
        keys = {s["key"] for s in r["displayed"]}
        ok &= _check("crunch suppresses Tier-B backup", "backup" not in keys)
        ok &= _check("crunch lets critical auto-heal pierce", "ollama_autoheal" in keys)
    return ok


def test_success_signal():
    """The plan's Success Signal, end to end via injection."""
    ok = True
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
        ok &= _check("SS: backup AND auto-heal both surface",
                     "backup" in keys and "ollama_autoheal" in keys)
        ok &= _check("SS: quiet line names both",
                     "backup" in r["quiet_line"] and "auto-heal FAILED" in r["quiet_line"])

        # ack backup -> only backup silenced, auto-heal remains
        now = time.time()
        orad.save_json_atomic(sd / orad.ACK_FILE,
                              {"backup": {"acked_at": now, "ttl_seconds": 24 * 3600, "acked_band": "warn"}})
        r = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal=autoheal, now=now + 10)
        keys = {s["key"] for s in r["displayed"]}
        ok &= _check("SS: ack backup silences only backup",
                     "backup" not in keys and "ollama_autoheal" in keys)

        # crunch on -> backup suppressed, auto-heal (critical) still shows
        orad.save_json_atomic(sd / orad.CRUNCH_FILE, {"on": True})
        # clear the ack so the only suppressor under test is crunch
        orad.save_json_atomic(sd / orad.ACK_FILE, {})
        r = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal=autoheal)
        keys = {s["key"] for s in r["displayed"]}
        ok &= _check("SS: crunch suppresses backup, auto-heal floor pierces",
                     "backup" not in keys and "ollama_autoheal" in keys)
    return ok


def test_record_heal_result():
    ok = True
    a = {}
    a = orad.record_heal_result(a, "ollama", False)
    ok &= _check("fail -> failures=1", a["ollama"]["failures"] == 1)
    a = orad.record_heal_result(a, "ollama", False)
    ok &= _check("fail again -> failures=2", a["ollama"]["failures"] == 2)
    a = orad.record_heal_result(a, "ollama", True)
    ok &= _check("success -> failures reset 0", a["ollama"]["failures"] == 0)
    return ok


def test_autoheal_user_unit_absent_increments():
    """A restart that leaves ollama down (e.g. user unit absent) MUST increment
    the counter, not no-op silently."""
    ok = True
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        signals = [sig("ollama", tier="A", due=True, severity="high"),
                   sig("memory_index", tier="A", due=False, severity="ok")]
        fail_restart = lambda: (False, "Unit not found (user)")
        r = orad.run_autoheal(sd, ROOT, ROOT, signals=signals, restart_fn=fail_restart,
                              rebuild_fn=lambda: (True, "n/a"))
        ok &= _check("user-unit-absent -> failures=1", r["autoheal"]["ollama"]["failures"] == 1)
        r = orad.run_autoheal(sd, ROOT, ROOT, signals=signals, restart_fn=fail_restart,
                              rebuild_fn=lambda: (True, "n/a"))
        ok &= _check("second failure -> failures=2 (escalates)", r["autoheal"]["ollama"]["failures"] == 2)
        # escalation now surfaces via assess
        a = orad.assess(ROOT, ROOT, sd, signals=signals, autoheal=r["autoheal"])
        ok &= _check("escalation surfaces as critical auto-heal line",
                     any(s["key"] == "ollama_autoheal" for s in a["displayed"]))
    return ok


def test_autoheal_privilege_denied_increments():
    ok = True
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        signals = [sig("ollama", tier="A", due=True, severity="high"),
                   sig("memory_index", tier="A", due=False, severity="ok")]
        denied = lambda: (False, "Failed to restart: access denied (polkit)")
        r = orad.run_autoheal(sd, ROOT, ROOT, signals=signals, restart_fn=denied,
                              rebuild_fn=lambda: (True, "n/a"))
        ok &= _check("privilege-denied -> counted as failure (not no-op)",
                     r["autoheal"]["ollama"]["failures"] == 1)
    return ok


def test_autoheal_success_resets():
    ok = True
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        # pre-seed 3 prior failures
        orad.save_json_atomic(sd / orad.AUTOHEAL_FILE, {"ollama": {"failures": 3}})
        signals = [sig("ollama", tier="A", due=True, severity="high"),
                   sig("memory_index", tier="A", due=False, severity="ok")]
        good = lambda: (True, "restarted")
        r = orad.run_autoheal(sd, ROOT, ROOT, signals=signals, restart_fn=good,
                              rebuild_fn=lambda: (True, "n/a"))
        ok &= _check("success after failures -> reset 0", r["autoheal"]["ollama"]["failures"] == 0)
    return ok


def test_autoheal_index_skipped_when_ollama_down():
    ok = True
    with tempfile.TemporaryDirectory() as td:
        sd = _state(td)
        signals = [sig("ollama", tier="A", due=True, severity="high"),
                   sig("memory_index", tier="A", due=True, severity="high")]
        # ollama restart fails -> index rebuild must be skipped AND counted failed
        r = orad.run_autoheal(sd, ROOT, ROOT, signals=signals,
                              restart_fn=lambda: (False, "down"),
                              rebuild_fn=lambda: (True, "should-not-run"))
        ok &= _check("index counted failed when ollama down",
                     r["autoheal"]["memory_index"]["failures"] == 1)
        idx_action = next(a for a in r["actions"] if a["target"] == "memory_index")
        ok &= _check("index action notes the skip", "skipped" in idx_action["note"])
    return ok


def main():
    ok = True
    for fn in (
        test_quiet_empty_when_nothing_due,
        test_tier_b_surfaces,
        test_tier_a_silent_below_escalation,
        test_ack_suppresses_then_worsening_resurfaces,
        test_crunch_suppresses_tier_b_but_critical_pierces,
        test_success_signal,
        test_record_heal_result,
        test_autoheal_user_unit_absent_increments,
        test_autoheal_privilege_denied_increments,
        test_autoheal_success_resets,
        test_autoheal_index_skipped_when_ollama_down,
    ):
        print(f"\n{fn.__name__}:")
        ok &= fn()
    print("\nALL PASS" if ok else "\nSOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
