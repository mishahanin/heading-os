#!/usr/bin/env python3
"""`cmd_apply`'s boundary handler asserted a restore that never happened.

The module's invariant is that "an auto update never leaves a component broken:
health passes on the new version, or the old version is restored". `apply_one`
catches only `CalledProcessError` and `TimeoutExpired` from the applier, so an
`OSError` from the spawn, or the `apply block has neither cmd nor script`
`ValueError`, propagated to `cmd_apply`'s broad `except Exception`. That handler
recorded `result = "rolled-back"` and never called `rollback()`.

Measured 2026-08-30 with an applier raising OSError: the outcome printed
"rolled-back" and the rollback closure was never invoked. The component's state
is UNKNOWN there, and calling it "rolled-back" is the same conflation
`RollbackFailed` was introduced one class earlier to eliminate.
"""
import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils import update_apply  # noqa: E402
from scripts.utils.update_apply import (  # noqa: E402
    FAILED_RESULTS,
    apply_one,
    cmd_apply,
)
from scripts.utils.update_registry import Component  # noqa: E402


def _comp(**over):
    base = {
        "name": "quantum-tool", "tier": "auto",
        "current": {"cmd": "echo 1.0"}, "latest": {"via": "pypi", "package": "x"},
        "apply": {"cmd": "true", "rollback_cmd": "true"},
        "health": {"cmd": "true"},
    }
    base.update(over)
    return Component(**base)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """A one-component run with a scripted applier and a recorded rollback."""
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"components": {
        "quantum-tool": {"status": "pending-auto", "fail_count": 0}}}), encoding="utf-8")

    calls = {"rollback": 0}
    recorded = {}

    def rollback_factory(comp, prev):
        def _rb():
            calls["rollback"] += 1
        return _rb

    real_mark = update_apply._mark_state

    def capturing(path, results):
        # `results` is the outcome map `cmd_apply` computes and hands to its
        # persister. That value, not stdout, is what the defect corrupted: the
        # boundary handler printed "error (...)" while RECORDING "rolled-back".
        recorded.update(results)
        real_mark(path, results)

    monkeypatch.setattr(update_apply, "_build_rollback", rollback_factory)
    monkeypatch.setattr(update_apply, "resolve_current", lambda comp: "1.0")
    monkeypatch.setattr(update_apply, "_mark_state", capturing)
    return state_path, calls, recorded


def _args():
    return argparse.Namespace(auto=False, name="quantum-tool")


def test_an_unexpected_error_is_not_recorded_as_a_rollback(harness, monkeypatch):
    """The measured case: OSError from the spawn, RECORDED as "rolled-back"."""
    state_path, calls, recorded = harness

    def exploding(comp):
        def _run():
            raise OSError("spawn failed")
        return _run

    monkeypatch.setattr(update_apply, "_default_applier", exploding)
    rc = cmd_apply(_args(), [_comp()], state_path)

    assert recorded["quantum-tool"] != "rolled-back", (
        "a restore that never ran was recorded as one")
    assert recorded["quantum-tool"] == "error"
    assert calls["rollback"] == 0, "the fixture's rollback should not have run"
    assert rc == 1, "an unknown-state component must still raise the exit code"


def test_the_error_outcome_is_visible_to_the_state_file_and_the_exit_code(harness,
                                                                         monkeypatch):
    """A new outcome must not be invisible to BOTH, which is what FAILED_RESULTS is for."""
    state_path, _calls, _recorded = harness

    def exploding(comp):
        def _run():
            raise OSError("spawn failed")
        return _run

    monkeypatch.setattr(update_apply, "_default_applier", exploding)
    assert cmd_apply(_args(), [_comp()], state_path) == 1

    entry = json.loads(state_path.read_text(encoding="utf-8"))["components"]["quantum-tool"]
    assert entry["status"] == "failed"
    assert entry["fail_count"] == 1


def test_the_config_error_path_reaches_the_same_outcome(harness, monkeypatch):
    """`apply block has neither cmd nor script` is the other measured route in."""
    state_path, calls, recorded = harness
    rc = cmd_apply(_args(), [_comp(apply={"rollback_cmd": "true"})], state_path)
    assert recorded["quantum-tool"] == "error"
    assert rc == 1
    assert calls["rollback"] == 0


def test_a_real_rollback_is_still_recorded_as_rolled_back(harness, monkeypatch):
    """The control: a health-gate failure DOES restore, and must still say so."""
    state_path, calls, recorded = harness
    monkeypatch.setattr(update_apply, "_default_applier", lambda comp: (lambda: None))
    monkeypatch.setattr(update_apply, "run_health", lambda comp: False)

    rc = cmd_apply(_args(), [_comp()], state_path)
    assert recorded["quantum-tool"] == "rolled-back"
    assert calls["rollback"] == 1
    assert rc == 1


def test_a_healthy_apply_is_still_applied(harness, monkeypatch):
    """The negative case: not every outcome became a failure."""
    state_path, calls, recorded = harness
    monkeypatch.setattr(update_apply, "_default_applier", lambda comp: (lambda: None))
    monkeypatch.setattr(update_apply, "run_health", lambda comp: True)

    rc = cmd_apply(_args(), [_comp()], state_path)
    assert recorded["quantum-tool"] == "applied"
    assert calls["rollback"] == 0
    assert rc == 0


def test_an_undecodable_state_file_does_not_take_down_the_apply(harness,
                                                                monkeypatch):
    """The decode class, on the read that runs AFTER the appliers have fired.

    `_mark_state` caught `(OSError, json.JSONDecodeError)`. `UnicodeDecodeError`
    is a `ValueError` and a SIBLING of `json.JSONDecodeError`, and the decode
    fails inside `read_text` before json sees a character, so neither arm caught
    it. One bad byte in `state.json` therefore raised out of `cmd_apply` after
    the swap had already happened: past the per-component outcome lines, past
    the `FAILED_RESULTS` exit code, and past the `fail_count` increment the
    circuit breaker depends on. A corrupt state file silently disabled the
    breaker AND crashed the run.

    `scripts/utils/update_registry.py` already carried `UnicodeDecodeError` in
    its handler; these two reads in `update_apply` were the copies that fix
    missed.
    """
    state_path, calls, recorded = harness
    state_path.write_bytes(b'{"components": {"quantum-tool": \xff\xfe}}')
    monkeypatch.setattr(update_apply, "_default_applier", lambda comp: (lambda: None))
    monkeypatch.setattr(update_apply, "run_health", lambda comp: True)

    # No exception, and the in-memory outcome still reports what happened.
    assert cmd_apply(_args(), [_comp()], state_path) == 0
    assert recorded["quantum-tool"] == "applied"
    assert calls["rollback"] == 0
    with pytest.raises(UnicodeDecodeError):
        state_path.read_text(encoding="utf-8")


def test_an_undecodable_state_file_does_not_take_down_the_auto_gate(tmp_path,
                                                                    monkeypatch):
    """The other copy of the same read, on the `--auto` delta gate.

    An unreadable state file means the gate has nothing to gate on, which is the
    `{}` the JSON arm already degrades to. It raised instead.
    """
    state_path = tmp_path / "state.json"
    state_path.write_bytes(b"\xff\xfe not utf-8 at all")
    monkeypatch.setattr(update_apply, "_build_rollback",
                        lambda comp, prev: (lambda: None))
    monkeypatch.setattr(update_apply, "resolve_current", lambda comp: "1.0")
    monkeypatch.setattr(update_apply, "_default_applier", lambda comp: (lambda: None))
    monkeypatch.setattr(update_apply, "run_health", lambda comp: True)

    args = argparse.Namespace(auto=True, name=None)
    assert cmd_apply(args, [_comp()], state_path) == 0


def test_error_is_a_failed_result():
    """The outcome must be in the set that drives status and the exit code."""
    assert "error" in FAILED_RESULTS
    assert "rolled-back" in FAILED_RESULTS
    assert "applied" not in FAILED_RESULTS


def test_apply_one_still_rolls_back_a_failed_apply_command():
    """The pre-existing contract of the function underneath, untouched."""
    calls = []

    def applier():
        import subprocess
        raise subprocess.CalledProcessError(1, "cmd")

    assert apply_one(_comp(), applier=applier,
                     rollback=lambda: calls.append("rb")) == "rolled-back"
    assert calls == ["rb"]
