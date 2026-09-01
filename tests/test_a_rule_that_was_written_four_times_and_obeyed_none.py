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

Scope. It refuses every call of a session that reaches source code while no
codegraph query has been ATTEMPTED. One attempt unlocks the session, including
an attempt that errors, because the rule is "ask the graph first" and not "the
graph must answer". A repository with no `.codegraph/` index is out of scope
entirely, matching the standing instruction to skip CodeGraph where it is not
indexed.

FIVE HOLES CLOSED 2026-08-29, after the operator asked for confirmation that
the rule could no longer be broken and the honest answer was that it could. The
first four were measured against the armed wall, in the session that wrote it.

1. `Read` WAS NOT COVERED. The predicate answered False for every Read, and the
   first version of THIS FILE pinned `Read scripts/sentinel.py` in the
   NOT-a-search list, so the hole was asserted as correct behaviour. Measured:
   a fresh session refused `Grep scripts/` and allowed `Read
   scripts/sentinel.py`. The instruction names both ("before any grep or
   Read"), so this was the rule walked around in one tool call.

2. FIVE SHELL READERS WERE NOT COVERED. `sed`, `awk`, `cat`, `head` and `tail`
   open a source file without searching it. This is not a hypothetical: earlier
   in that same session, with the wall armed, `sed -n '1,40p' tests/<file>.py`
   read a source file and the wall never saw it.

3. `find` WAS NAMED IN A COMMENT AND ABSENT FROM THE CODE. The comment above
   `_SEARCH_BINARIES` said "find is here for `find ... -name` over source". It
   was not in the tuple.

4. THE RULE HAD A COUNTER, SO IT COULD BE WAITED OUT. After three refusals the
   check yielded and allowed the search. Measured: refusals 1-3 denied, refusal
   4 allowed. The operator's answer was "если есть жёсткое правило, оно ВСЕГДА
   выполнялось, безоговорочно", so the hatch is gone.

5. THE UNLOCK MATCHER WAS MACHINE-LOCAL ONLY. It lived in
   `.claude/settings.local.json`, which is gitignored; all three TRACKED
   platform templates lacked it, so on any other machine an explore could never
   stamp the marker and the wall was a cage. That is WHY the hatch existed, and
   removing the hatch demanded fixing the cause: the matcher is now in the
   three tracked templates, AND `codegraph explore` in a Bash command is a
   second, independent unlock door riding the `Bash` matcher, which is present
   in every settings file in the repository. Both doors are asserted below. A
   session can be caged only if both are shut, which no single missing config
   can achieve.
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
    # Hole 1: opening a source file IS the lookup the graph answers. This exact
    # payload sat in the NOT-a-search list until 2026-08-29.
    ("Read", {"file_path": "scripts/sentinel.py"}),
    ("Read", {"file_path": ".claude/hooks/turn-check.py"}),
    ("Read", {"file_path": "conftest.py"}),
    # Hole 2: five shell readers that open source without searching it. The sed
    # case is the one that actually happened, with the wall armed.
    ("Bash", {"command": "sed -n '1,40p' tests/test_content_guard.py"}),
    ("Bash", {"command": "cat scripts/utils/air_gap.py"}),
    ("Bash", {"command": "head -50 scripts/leak-guard.py"}),
    ("Bash", {"command": "tail -20 scripts/memory.py"}),
    ("Bash", {"command": "awk '/def /' scripts/memory.py"}),
    # Hole 3: named in the comment, absent from the tuple.
    ("Bash", {"command": "find scripts -name '*.py'"}),
    # The hooks tree, named WITHOUT a `.py` on the end. Every other fixture
    # that reaches `.claude/hooks` also carries `.py`, so the `.claude/hooks`
    # entry in `_CODE_HINTS` was a twin of the `.py` entry and deleting it
    # changed no verdict here: MEASURED 2026-09-01, `_CODE_HINTS = (".py",)`
    # left all 69 cases in this file green. A grep of the hooks DIRECTORY is
    # the shape this hint exists for, and the hooks are not under `scripts/`
    # or `tests/`, so the code-tree regex does not reach them either.
    ("Grep", {"pattern": "check_graph_first", "path": ".claude/hooks"}),
    # The Windows spelling is a second alternative with the same twin problem:
    # dropping only `".claude\\hooks"` also left the file green.
    ("Grep", {"pattern": "check_graph_first", "path": ".claude\\hooks"}),
    ("Glob", {"pattern": ".claude/hooks/*"}),
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
    # Running a CLI and reading its OUTPUT. `re.split` returned every word, so
    # the `tail` at the end of the pipe marked the whole line a code lookup and
    # `.py` in `scripts/thread.py` then confirmed it. MEASURED 2026-08-31: this
    # exact command answered True and was refused as a session's first code
    # lookup. A search binary carrying nothing but flags reads a stream.
    ("Bash", {"command": "python scripts/thread.py list | tail -5"}),
    ("Bash", {"command": ".venv/bin/python scripts/ops-radar.py | head -20"}),
    ("Write", {"file_path": "scripts/sentinel.py", "content": "x"}),
    # The five shell readers over things that are NOT code. Without these the
    # whole `_NOT_CODE_HINTS` filter could be deleted for them and no verdict
    # would change, which would make `cat` of any log a refusal and get the
    # wall switched off.
    ("Bash", {"command": "cat .tmp/audit/ci40.log"}),
    ("Bash", {"command": "head -20 docs/ARCHITECTURE.md"}),
    ("Bash", {"command": "tail -5 .claude/settings.json"}),
    ("Bash", {"command": "sed -n '1,5p' outputs/notes.txt"}),
    # A Read of something that is not code. Read must not become a blanket.
    ("Read", {"file_path": ".tmp/audit/mut86b.log"}),
    ("Read", {"file_path": "docs/ARCHITECTURE.md"}),
    ("Read", {"file_path": "config/routing-map.yaml"}),
    # A Read with no path at all cannot be judged, and must not inherit the
    # Grep catch-all on the predicate's last line.
    ("Read", {}),
    # A `.py` file that is scratch, not source. Mutation found that no fixture
    # had BOTH a code hint and a scratch hint, so the whole scratch list could
    # be deleted without changing a verdict. This one decides it.
    ("Grep", {"pattern": "def main", "path": ".tmp/audit/probe_turncheck.py"}),
    # These two reach the final line of the predicate, which no earlier fixture
    # did. A `return True` there would have made a Glob of everything, and any
    # unanchored shell grep, into refusals.
    ("Glob", {"pattern": "**/*"}),
    ("Bash", {"command": "grep -rn TODO ."}),
    # A search binary whose arguments are ALL flags, and whose flags name code.
    # This is the only shape in which the "flags only: reading a stream, not a
    # corpus" rule decides anything: every other flags-only segment in this
    # list (`tail -5`, `head -20`) carries no code hint, so it answers False
    # with or without the rule. MEASURED 2026-09-01 -- deleting the flags-only
    # `continue` left all 69 cases green, and with it deleted these two become
    # REFUSALS. A wall that refuses `rg --glob=*.py` is a wall that gets
    # switched off, which is the failure mode the rule was written against.
    ("Bash", {"command": "rg --glob=*.py"}),
    ("Bash", {"command": "python scripts/gen.py | rg --glob=*.py"}),
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
    """Scope. This wall is about how code is LOCATED and READ. Editing a file
    you already have is downstream of that, and refusing it would only stop
    work the graph has nothing to add to.

    The payload used to be a `Read` of `scripts/firecrawl.py`, asserted to be
    allowed, under this name. It documented hole 1 as intended behaviour while
    reading as a scope test about writes.
    """
    result = _run_hook({
        "session_id": fresh_session["id"],
        "tool_name": "Write",
        "tool_input": {"file_path": str(ROOT / "scripts" / "firecrawl.py"),
                       "content": "# edited\n"},
    }, fresh_session["state"])

    assert _decision(result) == "allow", result


@pytest.mark.skipif(not (ROOT / ".codegraph").is_dir(),
                    reason="no .codegraph index here, so the check is out of scope")
def test_reading_a_source_file_is_refused_on_the_live_wall(fresh_session):
    """Hole 1, on the wall rather than the predicate.

    Measured before the fix: this exact call was ALLOWED in a session whose
    `Grep scripts/` had just been refused.
    """
    result = _run_hook({
        "session_id": fresh_session["id"],
        "tool_name": "Read",
        "tool_input": {"file_path": str(ROOT / "scripts" / "firecrawl.py")},
    }, fresh_session["state"])

    assert _decision(result) == "deny", result


@pytest.mark.skipif(not (ROOT / ".codegraph").is_dir(),
                    reason="no .codegraph index here, so the check is out of scope")
def test_a_shell_reader_over_source_is_refused_on_the_live_wall(fresh_session):
    """Hole 2. This command shape was actually used, with the wall armed, in
    the session that wrote the wall."""
    result = _run_hook({
        "session_id": fresh_session["id"],
        "tool_name": "Bash",
        "tool_input": {"command": "sed -n '1,40p' tests/test_content_guard.py"},
    }, fresh_session["state"])

    assert _decision(result) == "deny", result


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
# Unconditional, and still not a cage
# ============================================================

def _walled(hook, tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "_GRAPH_STATE_DIR", tmp_path / "state")
    (tmp_path / ".codegraph").mkdir()
    monkeypatch.setattr(hook, "WORKSPACE", tmp_path)


def test_the_wall_never_yields_however_long_it_is_pushed(hook, tmp_path,
                                                         monkeypatch):
    """Hole 4. The rule used to yield after three refusals, and a rule with a
    counter is a rule the caller waits out. Measured before the fix: refusals
    1-3 denied, refusal 4 allowed.

    Twenty is not a magic number. It is far past any bound a future author
    might reintroduce, so a returning hatch fails here rather than passing
    quietly at a higher setting.
    """
    _walled(hook, tmp_path, monkeypatch)
    call = {
        "session_id": "no-hatch-probe",
        "tool_name": "Grep",
        "tool_input": {"pattern": "def something", "path": "scripts"},
    }

    verdicts = [hook.check_graph_first(dict(call)) for _ in range(20)]

    allowed = [i for i, v in enumerate(verdicts, 1)
               if (v or {}).get("decision") != "block"]
    assert not allowed, f"the wall yielded on attempt(s) {allowed}"


def test_no_refusal_counter_survives_in_the_module(hook):
    """The hatch is gone from the code, not merely unreachable. A counter left
    in place is a counter someone re-wires."""
    assert not hasattr(hook, "MAX_GRAPH_REFUSALS")
    assert not hasattr(hook, "_graph_refusals")


@pytest.mark.parametrize("unlock", [
    pytest.param({"tool_name": "mcp__codegraph__codegraph_explore",
                  "tool_input": {"query": "something"}}, id="mcp-tool"),
    pytest.param({"tool_name": "Bash",
                  "tool_input": {"command": 'codegraph explore "something"'}},
                 id="shell-cli"),
])
def test_either_door_unlocks_the_session(hook, tmp_path, monkeypatch, unlock):
    """Hole 5, and the reason the hatch could be removed at all.

    The MCP door needs a `mcp__codegraph__.*` matcher, which lives in
    machine-local settings and can be absent. The shell door rides the `Bash`
    matcher, which is in every settings file in the repository. A session is
    caged only if BOTH are shut, and no single missing config does that.
    """
    _walled(hook, tmp_path, monkeypatch)
    search = {
        "session_id": f"unlock-{unlock['tool_name']}",
        "tool_input": {"pattern": "def something", "path": "scripts"},
        "tool_name": "Grep",
    }

    assert (hook.check_graph_first(dict(search)) or {}).get("decision") == "block"
    hook.check_graph_first({"session_id": search["session_id"], **unlock})
    assert hook.check_graph_first(dict(search)) is None


def test_a_bash_command_merely_naming_the_graph_does_not_unlock(hook, tmp_path,
                                                                monkeypatch):
    """The mirror on the shell door. If any mention of the word unlocked the
    session, writing `# ask codegraph first` into a file would open it, and the
    second door would be a hole rather than a door.

    This test was green over the input class it was written to exclude. All
    three of its original cases are near-misses that the whitespace-delimited
    regex rejected for unrelated reasons: `mycodegraph` failed the left
    boundary, `codegraph_explore` failed the right, `.codegraph/` failed both.
    None of them was a BARE, whitespace-delimited `codegraph` in argument
    position, which is the only spelling the mention hazard actually has.

    MEASURED 2026-08-31, before the fix: `grep -rn codegraph scripts/` was
    allowed AND stamped the marker, so a session's first code grep passed the
    wall and disarmed it permanently. The cases below now lead with that one.
    """
    _walled(hook, tmp_path, monkeypatch)
    search = {
        "session_id": "mention-probe",
        "tool_name": "Grep",
        "tool_input": {"pattern": "def something", "path": "scripts"},
    }

    assert (hook.check_graph_first(dict(search)) or {}).get("decision") == "block"
    for command in (
            # The real hazard: a bare `codegraph` in ARGUMENT position.
            "grep -rn codegraph scripts/",
            "echo codegraph ; grep -rn foo scripts/",
            "rg --files-with-matches codegraph .",
            "cat <<'EOF' > notes.md\ncodegraph explore foo\nEOF",
            # The three near-misses, kept: they must stay refused too.
            "grep -rn mycodegraph scripts/",
            "echo 'use codegraph_explore' >> notes.md",
            "ls .codegraph/"):
        hook.check_graph_first({"session_id": "mention-probe",
                                "tool_name": "Bash",
                                "tool_input": {"command": command}})
    assert (hook.check_graph_first(dict(search)) or {}).get("decision") == "block"


@pytest.mark.parametrize("command", [
    'codegraph explore "something"',
    "codegraph explore foo | head -20",
    "timeout 60 codegraph explore foo",
    "cd /tmp && codegraph explore foo",
    "python scripts/x.py && codegraph explore foo",
    "./codegraph explore foo",
])
def test_a_real_graph_invocation_still_unlocks(hook, tmp_path, monkeypatch, command):
    """The other direction, so tightening the door did not weld it shut.

    Program-position resolution has to keep reading through the wrappers an
    agent actually types: a pipe, a `timeout`, a `cd &&` prefix, a relative
    `./`. A door that only opens for one bare spelling is the cage this wall's
    own refusal text promises it is not.
    """
    _walled(hook, tmp_path, monkeypatch)
    search = {
        "session_id": f"unlock-{abs(hash(command))}",
        "tool_name": "Grep",
        "tool_input": {"pattern": "def something", "path": "scripts"},
    }

    assert (hook.check_graph_first(dict(search)) or {}).get("decision") == "block"
    hook.check_graph_first({"session_id": search["session_id"],
                            "tool_name": "Bash",
                            "tool_input": {"command": command}})
    assert hook.check_graph_first(dict(search)) is None, (
        f"a real graph invocation did not unlock the session: {command!r}")


def test_a_payload_with_no_session_is_not_walled(hook, tmp_path, monkeypatch):
    """Found by mutation: deleting the session guard changed no test result.

    "The first code lookup of the SESSION" needs a session to be the first of.
    Without one, every caller would share a single `unknown` marker, so one
    session's explore would silently unlock every other, AND the suites that
    drive other walls through this dispatcher would depend on each other's
    order. The rate limiter's single shared state file did exactly that to a
    wall test earlier the same day.

    Walling instead is not the stricter option, it is a cage: a caller with no
    session has no marker to stamp, so no explore could ever open it.
    """
    _walled(hook, tmp_path, monkeypatch)
    search = {"tool_name": "Grep",
              "tool_input": {"pattern": "def something", "path": "scripts"}}

    for sessionless in ({}, {"session_id": ""}, {"session_id": "   "}):
        assert hook.check_graph_first({**search, **sessionless}) is None, sessionless


def test_the_same_lookup_with_a_session_is_walled(hook, tmp_path, monkeypatch):
    """The mirror. Without it the test above is satisfied by a check that
    returns None for everything."""
    _walled(hook, tmp_path, monkeypatch)
    verdict = hook.check_graph_first({
        "session_id": "has-a-session",
        "tool_name": "Grep",
        "tool_input": {"pattern": "def something", "path": "scripts"},
    })
    assert (verdict or {}).get("decision") == "block"


def _pretooluse_matchers(path: Path) -> list[str]:
    hooks = json.loads(path.read_text(encoding="utf-8")).get("hooks") or {}
    return [entry.get("matcher") or "" for entry in (hooks.get("PreToolUse") or [])]


TRACKED_SETTINGS = ["linux", "macos", "windows"]


@pytest.mark.parametrize("platform", TRACKED_SETTINGS)
def test_every_tracked_platform_template_routes_the_graph_tool(platform):
    """Hole 5 at its source.

    This assertion used to read `.claude/settings.local.json`, which is
    gitignored, and SKIP when it was absent. So it passed on the one machine
    that was already correct and said nothing about any other. Measured
    2026-08-29: all three tracked templates lacked the matcher, meaning a fresh
    clone got a wall whose MCP door was nailed shut.

    These files ARE tracked, so there is nothing to skip and no machine where
    this cannot run.
    """
    import re as _re

    path = ROOT / ".claude" / f"settings.local.{platform}.json"
    assert path.is_file(), f"{path} is tracked and must exist"

    matchers = _pretooluse_matchers(path)
    reaching = [m for m in matchers
                if _re.fullmatch(m, "mcp__codegraph__codegraph_explore")]
    assert reaching, (
        f"{path.name} has no PreToolUse matcher reaching the codegraph tool, so "
        f"a clone using it cannot unlock check_graph_first through the MCP "
        f"door. Matchers: {matchers}")

    hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    routed = [e for e in hooks
              if (e.get("matcher") or "") in reaching
              and any("_dispatch.py" in (h.get("command") or "")
                      for h in e.get("hooks") or [])]
    assert routed, f"{path.name}: the matcher exists but misses _dispatch.py"


@pytest.mark.parametrize("platform", TRACKED_SETTINGS)
def test_every_tracked_platform_template_routes_bash(platform):
    """The second door's wiring. It is the one that cannot be lost, and that
    claim is worth an assertion rather than a comment."""
    import re as _re

    matchers = _pretooluse_matchers(ROOT / ".claude" / f"settings.local.{platform}.json")
    assert any(_re.fullmatch(m, "Bash") for m in matchers), (
        f"{platform}: no Bash matcher, so the shell unlock door is shut too")


def test_the_live_settings_route_the_graph_tool():
    """The machine-local file, where it exists. Kept as a separate assertion
    from the tracked templates: copying a template is a manual step, and a
    machine that skipped it must fail here rather than inherit the templates'
    green."""
    settings = ROOT / ".claude" / "settings.local.json"
    if not settings.is_file():
        pytest.skip("machine-local settings absent, so there is nothing to route")

    import re as _re
    matchers = _pretooluse_matchers(settings)
    assert any(_re.fullmatch(m, "mcp__codegraph__codegraph_explore")
               for m in matchers), (
        f"this machine's settings do not route the codegraph tool into the "
        f"dispatcher. Matchers: {matchers}")


def test_the_check_is_wired_into_the_dispatcher(hook):
    """A rule can be correct and unreached. Found by mutation in an earlier
    shard of this audit, where every ancestor test called the function directly
    and nothing exercised the registry."""
    assert hook.check_graph_first in hook.CHECKS


# ============================================================
# Hole 6: an agent held the session's key
#
# MEASURED 2026-08-29. A dispatched agent's payload carries the dispatching
# session's `session_id`, so a subagent that called `codegraph_explore`
# stamped the SESSION's marker and the session never had to ask the graph
# itself. Every dispatch reopened the hole this file closed that morning.
# ============================================================

def test_an_agents_explore_does_not_unlock_the_session(hook, tmp_path, monkeypatch):
    _walled(hook, tmp_path, monkeypatch)
    session = "s-actor-graph"

    assert hook.check_graph_first({
        "session_id": session, "agent_id": "a1",
        "tool_name": "mcp__codegraph__codegraph_explore",
        "tool_input": {"query": "build_denylist"}}) is None

    verdict = hook.check_graph_first({
        "session_id": session, "tool_name": "Grep",
        "tool_input": {"pattern": "def build_denylist", "path": "scripts/"}})
    assert (verdict or {}).get("decision") == "block"


def test_the_sessions_explore_does_not_unlock_an_agent(hook, tmp_path, monkeypatch):
    """The other direction. Each actor asks the graph for itself."""
    _walled(hook, tmp_path, monkeypatch)
    session = "s-actor-graph-rev"

    assert hook.check_graph_first({
        "session_id": session,
        "tool_name": "mcp__codegraph__codegraph_explore",
        "tool_input": {"query": "build_denylist"}}) is None

    verdict = hook.check_graph_first({
        "session_id": session, "agent_id": "a1", "tool_name": "Grep",
        "tool_input": {"pattern": "def build_denylist", "path": "scripts/"}})
    assert (verdict or {}).get("decision") == "block"


def test_an_agent_that_asks_the_graph_unlocks_itself(hook, tmp_path, monkeypatch):
    """Not a cage for the agent either: its own door still opens its own lock."""
    _walled(hook, tmp_path, monkeypatch)
    session = "s-actor-graph-own"

    hook.check_graph_first({
        "session_id": session, "agent_id": "a1",
        "tool_name": "mcp__codegraph__codegraph_explore",
        "tool_input": {"query": "build_denylist"}})

    verdict = hook.check_graph_first({
        "session_id": session, "agent_id": "a1", "tool_name": "Grep",
        "tool_input": {"pattern": "def build_denylist", "path": "scripts/"}})
    assert verdict is None


def test_two_actors_in_one_session_get_two_graph_stamps(hook):
    assert hook._graph_marker("s", "main") != hook._graph_marker("s", "a1")
    assert hook._graph_marker("s", "a1") != hook._graph_marker("s", "a2")
