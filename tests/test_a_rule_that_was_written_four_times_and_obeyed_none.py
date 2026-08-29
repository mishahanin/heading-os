#!/usr/bin/env python3
"""A standing instruction with no mechanism behind it is advice, not a rule.

"ALWAYS START WITH THE GRAPH" is the operator's instruction, given on
2026-08-27 twice (the second time in capitals) and on 2026-08-29 twice more.
The fourth telling asked for something other than a fourth note:

    сам реши как записать, чтобы не позволять самому себе нарушать правила

MEASURED, and this is the point of the shard. The workspace ALREADY had a
reminder: a `UserPromptSubmit` hook that names matching indexed symbols on
every code-shaped prompt, roughly 16 KB of injected context. Every relapse
happened with that text on screen. On 2026-08-29 the relapse also produced a
WRONG number, 22 root-anchored tree sweeps, from a hand-rolled `ast` matcher
that only recognised four receiver names, where the graph resolves receivers
properly.

So the reminder is not a weaker version of this control. It is a different
kind of thing. This one refuses.

Scope, deliberately small. It refuses the FIRST code-shaped search of a
session while no `codegraph_explore` has been ATTEMPTED. One attempt unlocks
the session, including an attempt that errors, because the rule is "ask the
graph first" and not "the graph must answer": a control that can wedge a
session over an outage would be turned off, and a control that is off is worth
nothing. A repository with no `.codegraph/` index is out of scope entirely,
matching the standing instruction to skip CodeGraph where it is not indexed.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DISPATCH = ROOT / ".claude" / "hooks" / "_dispatch.py"


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("graph_first_probe", DISPATCH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["graph_first_probe"] = module
    spec.loader.exec_module(module)
    return module


# ============================================================
# The predicate, on synthetic input, in both directions
# ============================================================

CODE_SEARCHES = [
    ("Grep", {"pattern": "def build_skill_command", "path": "scripts"}),
    ("Grep", {"pattern": "SEND_DENY"}),
    ("Glob", {"pattern": "scripts/**/*.py"}),
    ("Bash", {"command": "grep -rn 'get_data_root' scripts/"}),
    ("Bash", {"command": "rg --files-with-matches pid_is_running tests/"}),
    ("Bash", {"command": "ast-grep --pattern 'os.kill($P, 0)' scripts/x.py"}),
    # A `.py` target that is NOT under scripts/ or tests/. Mutation found that
    # every other code fixture also carried a code-tree name, so the `.py` hint
    # was decorative: deleting it changed no verdict.
    ("Grep", {"pattern": "import os", "path": "conftest.py"}),
]

NOT_CODE_SEARCHES = [
    # Reading this session's own scratch output, not locating code.
    ("Bash", {"command": "grep -c caught .tmp/audit/mut84.log"}),
    ("Grep", {"pattern": "SURVIVED", "path": ".tmp/audit"}),
    ("Bash", {"command": "grep -n 'version' docs/QUICKSTART.md"}),
    ("Grep", {"pattern": "deadline", "path": "outputs/notes.md"}),
    # Not a search at all.
    ("Bash", {"command": "git status --short"}),
    ("Bash", {"command": "python -m pytest tests/test_x.py -q"}),
    ("Read", {"file_path": "scripts/sentinel.py"}),
    ("Write", {"file_path": "scripts/sentinel.py", "content": "x"}),
    # A `.py` file that is scratch, not source. Mutation found that no fixture
    # had BOTH a code hint and a scratch hint, so the whole scratch list could
    # be deleted without changing a verdict. This one decides it.
    ("Grep", {"pattern": "def main", "path": ".tmp/audit/probe_turncheck.py"}),
    # These two reach the final line of the predicate, which no earlier fixture
    # did. A `return True` there would have made a Glob of everything, and any
    # unanchored shell grep, into refusals.
    ("Glob", {"pattern": "**/*"}),
    ("Bash", {"command": "grep -rn TODO ."}),
]


@pytest.mark.parametrize("tool, payload", CODE_SEARCHES,
                         ids=[f"{t}-{i}" for i, (t, _) in enumerate(CODE_SEARCHES)])
def test_the_predicate_calls_a_code_lookup_a_code_lookup(hook, tool, payload):
    assert hook.is_code_search(tool, payload) is True


@pytest.mark.parametrize("tool, payload", NOT_CODE_SEARCHES,
                         ids=[f"{t}-{i}" for i, (t, _) in enumerate(NOT_CODE_SEARCHES)])
def test_the_predicate_leaves_everything_else_alone(hook, tool, payload):
    """The wider half. A guard that answered True for everything would pass
    every case above and make the session unusable, which is how a control
    ends up switched off."""
    assert hook.is_code_search(tool, payload) is False


def test_the_predicate_separates_the_two_lists(hook):
    """Both directions over one call, so a body that ignored its arguments
    cannot satisfy the two parametrised suites separately."""
    yes = [(t, p) for t, p in CODE_SEARCHES if hook.is_code_search(t, p)]
    no = [(t, p) for t, p in NOT_CODE_SEARCHES if hook.is_code_search(t, p)]
    assert len(yes) == len(CODE_SEARCHES)
    assert no == []


# ============================================================
# The check, driven through the real dispatcher
# ============================================================

def _run_hook(payload: dict, state_dir: Path) -> dict:
    """Drive `.claude/hooks/_dispatch.py` as the harness does.

    A subprocess, not an import, because the wiring is the thing under test:
    a check can be correct and never reach `CHECKS`, which this repo has hit
    before.
    """
    proc = subprocess.run(
        [sys.executable, str(DISPATCH)],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60,
        cwd=str(ROOT),
        env=dict(os.environ, WS_RATE_LIMIT_STATE=str(state_dir / "rate.json")),
    )
    if not proc.stdout.strip():
        return {}
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {"_unparsed": proc.stdout, "_stderr": proc.stderr}


def _decision(result: dict) -> str:
    return ((result.get("hookSpecificOutput") or {}).get("permissionDecision")
            or result.get("decision") or "allow")


@pytest.fixture()
def fresh_session(tmp_path, monkeypatch):
    """A session id nothing has stamped, and a private rate-limit file.

    The rate limiter is shared state keyed on one path. Left alone, the
    twentieth hook call in a suite is refused by the limiter and the assertion
    reads that as this check's verdict. Measured on 2026-08-29 in a sibling
    shard, where it silently made a wall test order-dependent.
    """
    import uuid
    return {"id": f"graph-first-test-{uuid.uuid4()}", "state": tmp_path}


@pytest.mark.skipif(not (ROOT / ".codegraph").is_dir(),
                    reason="no .codegraph index here, so the check is out of scope")
def test_the_first_code_search_of_a_session_is_refused(fresh_session):
    result = _run_hook({
        "session_id": fresh_session["id"],
        "tool_name": "Grep",
        "tool_input": {"pattern": "def pid_is_running", "path": "scripts"},
    }, fresh_session["state"])

    assert _decision(result) == "deny", result
    reason = (result.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
    assert "codegraph_explore" in reason


@pytest.mark.skipif(not (ROOT / ".codegraph").is_dir(),
                    reason="no .codegraph index here, so the check is out of scope")
def test_an_explore_unlocks_the_session(fresh_session):
    """The escape, and the whole reason this is usable. Stamped at PreToolUse
    on the explore itself, so an explore that errors still unlocks."""
    first = _run_hook({
        "session_id": fresh_session["id"],
        "tool_name": "Grep",
        "tool_input": {"pattern": "def pid_is_running", "path": "scripts"},
    }, fresh_session["state"])
    assert _decision(first) == "deny"

    _run_hook({
        "session_id": fresh_session["id"],
        "tool_name": "mcp__codegraph__codegraph_explore",
        "tool_input": {"query": "pid_is_running"},
    }, fresh_session["state"])

    after = _run_hook({
        "session_id": fresh_session["id"],
        "tool_name": "Grep",
        "tool_input": {"pattern": "def pid_is_running", "path": "scripts"},
    }, fresh_session["state"])
    assert _decision(after) == "allow", after


@pytest.mark.skipif(not (ROOT / ".codegraph").is_dir(),
                    reason="no .codegraph index here, so the check is out of scope")
def test_a_scratch_log_search_is_never_refused(fresh_session):
    """The mirror on the live wall, not only on the predicate. Reading a
    mutation log is how a shard checks its own result, and a session that
    could not do it before consulting the graph would be absurd."""
    result = _run_hook({
        "session_id": fresh_session["id"],
        "tool_name": "Bash",
        "tool_input": {"command": "grep -c caught .tmp/audit/mut84.log"},
    }, fresh_session["state"])

    assert _decision(result) == "allow", result


@pytest.mark.skipif(not (ROOT / ".codegraph").is_dir(),
                    reason="no .codegraph index here, so the check is out of scope")
def test_a_write_is_not_this_checks_business(fresh_session):
    """Scope. This wall is about how code is LOCATED. Anything else it touches
    is a false refusal, and false refusals are what get a control disabled."""
    result = _run_hook({
        "session_id": fresh_session["id"],
        "tool_name": "Read",
        "tool_input": {"file_path": str(ROOT / "scripts" / "firecrawl.py")},
    }, fresh_session["state"])

    assert _decision(result) == "allow", result


def test_an_unindexed_repository_is_out_of_scope(hook, tmp_path, monkeypatch):
    """The global instruction says to skip CodeGraph entirely where there is no
    `.codegraph/`. A wall that fired there would block every fresh clone."""
    monkeypatch.setattr(hook, "WORKSPACE", tmp_path)
    monkeypatch.setattr(hook, "_GRAPH_STATE_DIR", tmp_path / "state")

    verdict = hook.check_graph_first({
        "session_id": "no-index-session",
        "tool_name": "Grep",
        "tool_input": {"pattern": "def anything", "path": "scripts"},
    })

    assert verdict is None


# ============================================================
# The wall must never become a cage
# ============================================================

def test_the_wall_yields_after_a_bounded_number_of_refusals(hook, tmp_path,
                                                            monkeypatch):
    """MEASURED 2026-08-29, and this nearly shipped as a cage.

    The dispatcher's PreToolUse matchers were `Bash`, `Read|Grep|Glob` and the
    write family. An MCP tool call reached none of them, so a real
    `codegraph_explore` could never stamp the marker: the wall refused, the
    explore ran, the wall refused again, with no way through. A matcher for
    `mcp__codegraph__.*` is the real fix and it lives in a gitignored,
    machine-local settings file. This bound is the half that cannot be lost
    with a config file.
    """
    monkeypatch.setattr(hook, "_GRAPH_STATE_DIR", tmp_path / "state")
    (tmp_path / ".codegraph").mkdir()
    monkeypatch.setattr(hook, "WORKSPACE", tmp_path)
    call = {
        "session_id": "cage-probe",
        "tool_name": "Grep",
        "tool_input": {"pattern": "def something", "path": "scripts"},
    }

    verdicts = [hook.check_graph_first(dict(call))
                for _ in range(hook.MAX_GRAPH_REFUSALS + 1)]

    blocked = [v for v in verdicts if (v or {}).get("decision") == "block"]
    assert len(blocked) == hook.MAX_GRAPH_REFUSALS, verdicts
    last = verdicts[-1] or {}
    assert last.get("decision") != "block", "the wall never yielded: it is a cage"
    assert "unlock path is broken" in last.get("additionalContext", "")


def test_an_explore_still_unlocks_before_the_bound_is_reached(hook, tmp_path,
                                                              monkeypatch):
    """The mirror. If the bound were the ONLY way through, the wall would be a
    three-refusal speed bump rather than a rule."""
    monkeypatch.setattr(hook, "_GRAPH_STATE_DIR", tmp_path / "state")
    (tmp_path / ".codegraph").mkdir()
    monkeypatch.setattr(hook, "WORKSPACE", tmp_path)
    search = {
        "session_id": "unlock-probe",
        "tool_name": "Grep",
        "tool_input": {"pattern": "def something", "path": "scripts"},
    }

    assert (hook.check_graph_first(dict(search)) or {}).get("decision") == "block"
    hook.check_graph_first({
        "session_id": "unlock-probe",
        "tool_name": "mcp__codegraph__codegraph_explore",
        "tool_input": {"query": "something"},
    })
    assert hook.check_graph_first(dict(search)) is None


def test_the_settings_route_the_graph_tool_into_the_dispatcher():
    """The real unlock path, asserted where it lives.

    `.claude/settings.local.json` is gitignored and machine-local, so this
    cannot run on a fresh clone and skips there rather than pretending. On the
    machine that HAS the file, a missing matcher is the exact defect measured
    above and must fail loudly.
    """
    settings = ROOT / ".claude" / "settings.local.json"
    if not settings.is_file():
        pytest.skip("machine-local settings absent, so there is nothing to route")

    hooks = json.loads(settings.read_text(encoding="utf-8")).get("hooks") or {}
    matchers = [entry.get("matcher") or ""
                for entry in (hooks.get("PreToolUse") or [])]

    import re as _re
    reaching = [m for m in matchers
                if _re.fullmatch(m, "mcp__codegraph__codegraph_explore")]
    assert reaching, (
        "no PreToolUse matcher reaches the codegraph tool, so an explore can "
        f"never unlock check_graph_first and the wall is a cage. Matchers: "
        f"{matchers}")

    routed = [entry for entry in hooks["PreToolUse"]
              if (entry.get("matcher") or "") in reaching
              and any("_dispatch.py" in (h.get("command") or "")
                      for h in entry.get("hooks") or [])]
    assert routed, "the matcher exists but does not point at _dispatch.py"


def test_the_check_is_wired_into_the_dispatcher(hook):
    """A rule can be correct and unreached. Found by mutation in an earlier
    shard of this audit, where every ancestor test called the function directly
    and nothing exercised the registry."""
    assert hook.check_graph_first in hook.CHECKS
