"""A PreToolUse check that raised was logged, advised, and skipped.

The tool call then ran with that wall down. The only thing between the operator
and an unguarded operation was the model reading an advisory and choosing to
stop, which makes the wall a suggestion. It is the same shape as every other
defect the 2026-09-02 campaign removed: a control that reports something other
than what it established. `docs/HOOKS-REFERENCE.md` and an earlier audit both
recorded the fail-open as deliberate; the operator reversed it on 2026-09-02.

What this file pins, in both directions:

* a crashed wall REFUSES an ordinary operation, through the real `main()`, on
  the real stdout contract, not on a restated copy of the rule;
* it does NOT refuse a read, or an edit inside `.claude/hooks/`, because a wall
  that cannot be repaired without being disarmed gets disarmed;
* every remaining wall still runs, so an early crash can never hide a later
  wall's genuine refusal;
* a genuine refusal still wins over a crash, because the block path is terminal
  and its reason is the one the operator needs;
* nothing in the environment turns the crash-block off.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_dispatch():
    path = ROOT / ".claude" / "hooks" / "_dispatch.py"
    spec = importlib.util.spec_from_file_location("dispatch_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["dispatch_under_test"] = module
    spec.loader.exec_module(module)
    return module


dsp = _load_dispatch()


def _boom(_payload):
    raise RuntimeError("the wall itself is broken")


def _quiet(_payload):
    return None


def _run(monkeypatch, payload, checks):
    """Drive `main()` end to end and return (exit_code, parsed stdout)."""
    monkeypatch.setattr(dsp, "CHECKS", checks)
    monkeypatch.setattr(dsp, "_record_denial", lambda *a, **k: None)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    buffer = io.StringIO()
    with pytest.raises(SystemExit) as exit_info, redirect_stdout(buffer):
        dsp.main()
    raw = buffer.getvalue()
    return exit_info.value.code, (json.loads(raw) if raw.strip() else None)


BASH = {"tool_name": "Bash", "tool_input": {"command": "echo hi"}}


# ============================================================
# The refusal, through the real entry point
# ============================================================

def test_a_crashed_wall_denies_an_ordinary_operation(monkeypatch):
    code, out = _run(monkeypatch, BASH, [_boom, _quiet])
    assert code == 0  # a policy deny is rendered on exit 0, like every other
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "HOOK WALL CRASHED" in decision["permissionDecisionReason"]


def test_the_refusal_names_the_wall_and_the_error(monkeypatch):
    """A refusal that does not say which wall broke sends the operator hunting,
    and a wall nobody can find is a wall nobody fixes."""
    _, out = _run(monkeypatch, BASH, [_boom])
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "_boom" in reason
    assert "the wall itself is broken" in reason


def test_no_crash_means_no_denial(monkeypatch):
    """The anchor. A dispatcher that denied unconditionally would satisfy every
    test above and stop the workspace dead."""
    code, out = _run(monkeypatch, BASH, [_quiet, _quiet])
    assert code == 0
    assert out is None


# ============================================================
# The exemptions, so the crash stays repairable
# ============================================================

def test_a_read_is_not_denied_by_a_crashed_wall(monkeypatch):
    payload = {"tool_name": "Read", "tool_input": {"file_path": "scripts/x.py"}}
    code, out = _run(monkeypatch, payload, [_boom])
    assert code == 0
    assert out is None or out["hookSpecificOutput"].get("permissionDecision") != "deny"


@pytest.mark.parametrize("tool", ["Grep", "Glob"])
def test_searching_is_not_denied_by_a_crashed_wall(monkeypatch, tool):
    payload = {"tool_name": tool, "tool_input": {"pattern": "x"}}
    _, out = _run(monkeypatch, payload, [_boom])
    assert out is None or out["hookSpecificOutput"].get("permissionDecision") != "deny"


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_repairing_the_hooks_directory_is_not_denied(monkeypatch, tool):
    """The deadlock this exemption exists to prevent: without it the only way to
    fix a crashed wall is to disarm the dispatcher, and a wall people disarm to
    get work done is worse than no wall."""
    payload = {"tool_name": tool,
               "tool_input": {"file_path": ".claude/hooks/_dispatch.py"}}
    _, out = _run(monkeypatch, payload, [_boom])
    assert out is None or out["hookSpecificOutput"].get("permissionDecision") != "deny"


def test_an_edit_outside_the_hooks_directory_is_still_denied(monkeypatch):
    """The mirror. An exemption that covered every Write would be a hole the
    size of the wall it protects."""
    payload = {"tool_name": "Write",
               "tool_input": {"file_path": "scripts/anything.py"}}
    _, out = _run(monkeypatch, payload, [_boom])
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_a_windows_shaped_hooks_path_is_still_recognised():
    payload = {"tool_name": "Edit",
               "tool_input": {"file_path": r".claude\hooks\_dispatch.py"}}
    assert dsp.crash_block_exemption(payload) is not None


def test_a_write_with_a_malformed_tool_input_is_denied():
    """Absent and malformed are different states. A payload whose tool_input is
    not a dict cannot prove it targets the hooks directory, so it does not get
    the exemption."""
    assert dsp.crash_block_exemption(
        {"tool_name": "Write", "tool_input": "not-a-dict"}) is None


# ============================================================
# Ordering: a crash must not hide a real refusal
# ============================================================

def test_every_wall_after_a_crashed_one_still_runs(monkeypatch):
    ran = []

    def _watcher(_payload):
        ran.append("watcher")
        return None

    _run(monkeypatch, BASH, [_boom, _watcher])
    assert ran == ["watcher"], "a wall after the crashed one did not run"


def test_a_genuine_refusal_wins_over_a_crash(monkeypatch):
    """The block path is terminal and its reason is the one the operator needs.
    Reporting the crash instead would replace a real security refusal with a
    maintenance notice."""
    def _refuses(_payload):
        return {"decision": "block", "_policy_deny": True,
                "reason": "REAL REFUSAL: the actual reason"}

    _, out = _run(monkeypatch, BASH, [_boom, _refuses])
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "REAL REFUSAL" in reason
    assert "HOOK WALL CRASHED" not in reason


def test_two_crashed_walls_are_both_named(monkeypatch):
    def _boom2(_payload):
        raise ValueError("the second one too")

    _, out = _run(monkeypatch, BASH, [_boom, _boom2])
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "_boom" in reason and "_boom2" in reason
    assert "2 PreToolUse check(s) raised" in reason


# ============================================================
# The rule itself, pure
# ============================================================

def test_no_crash_produces_no_decision():
    assert dsp.crashed_wall_block([], None) is None


def test_an_exemption_suppresses_the_decision():
    assert dsp.crashed_wall_block([("a", "b")], "because") is None


def test_a_crash_with_no_exemption_produces_a_policy_deny():
    decision = dsp.crashed_wall_block([("a", "b")], None)
    assert decision["decision"] == "block"
    assert decision["_policy_deny"] is True


def test_no_environment_variable_can_switch_the_crash_block_off(monkeypatch):
    """A gate with an off switch is a gate that gets switched off. The campaign
    wrote this same test for the release gate; the crash-block inherits it.
    Every name the module reads from the environment is set to values that would
    plausibly disable something, and the refusal must survive all of them."""
    import os
    import re

    source = (ROOT / ".claude" / "hooks" / "_dispatch.py").read_text(encoding="utf-8")
    names = set(re.findall(r"environ(?:\.get)?\(\s*[\"']([A-Z0-9_]+)[\"']", source))
    names |= set(re.findall(r"getenv\(\s*[\"']([A-Z0-9_]+)[\"']", source))
    assert names, "found no environment reads at all; the search is wrong"

    for value in ("0", "1", "off", "false", "yes", ""):
        for name in names:
            monkeypatch.setitem(os.environ, name, value)
        assert dsp.crashed_wall_block([("a", "b")], None) is not None, (
            f"the crash-block went away with every env name set to {value!r}")
