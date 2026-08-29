"""A second standing instruction with no mechanism behind it.

"агенты и workflow разрешены всегда и везде", then the escalation: "не только
разрешены, они MUST BE USED, если это даёт скорость и оптимизацию". Said three
times across this audit and lapsed each time. On 2026-08-29 the operator asked
for the same kind of mechanism `check_graph_first` gives the graph rule, in
those words: "сделай правило, как ты сделал для Граф ... чтобы ты не 'забывал'
их включать и использовать".

WHY THIS WALL IS SHAPED DIFFERENTLY, and the difference is the design.
`check_graph_first` refuses one specific WRONG ACTION, a code search. "Did not
use an agent" is not an action, it is an ABSENCE: there is no single tool call
to refuse. So this wall watches the SHAPE OF A STRETCH -- how many distinct
files the session has investigated by hand since it last considered fanning
out.

DISTINCT PATHS, NOT CALL COUNT, and that is the whole precision of it. Measured
on the live hook:

    15 reads of 15 different files   -> allowed 12, refused from the 13th
    40 reads of ONE file             -> allowed, every one
    30 reads of .tmp/ scratch logs   -> allowed, every one

Forty calls against one file is deep work and inherently serial. A counter
keyed on CALLS would refuse it, and that is the kind of false refusal that gets
a control switched off, taking the true positives with it.

TWO DOORS, both measured open on the live hook:

    Agent dispatch                     DENY -> allow
    Workflow                           DENY -> allow
    python scripts/fanout-note.py ...  DENY -> allow

The third is deliberately kept, because "this is serial" is sometimes TRUE and
a wall that refused it would be wrong. What it is not is silent: it appends a
dated reason to `.claude/state/fanout/serial-claims.jsonl`, so the judgement is
recorded rather than assumed and the operator can read it back and disagree.
`check_graph_first` carried an invisible refusal counter with the opposite
property and it was deleted the same day for exactly that reason.

THE CAGE BUG, caught before it shipped this time. The dispatcher's PreToolUse
matchers were `Bash`, `Read|Grep|Glob`, the write family and
`mcp__codegraph__.*`. `Agent` reached none of them, so a real agent dispatch
would never have cleared the budget and the wall would have been a cage -- the
identical defect the graph wall shipped with in the morning. A matcher for
`Agent|Task|Workflow` is in all four settings files, and the two tracked
templates are asserted below so a fresh clone carries it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DISPATCH = ROOT / ".claude" / "hooks" / "_dispatch.py"
PY = sys.executable


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("fanout_probe", DISPATCH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fanout_probe"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def walled(hook, tmp_path, monkeypatch):
    """A private state dir, so one test's budget is not another's."""
    monkeypatch.setattr(hook, "_FANOUT_STATE_DIR", tmp_path / "fanout")
    return hook


# ============================================================
# The predicate, on synthetic input, in both directions
# ============================================================

INVESTIGATES = [
    ("Read", {"file_path": "scripts/sentinel.py"}, "scripts/sentinel.py"),
    ("Grep", {"pattern": "x", "path": "scripts/utils"}, "scripts/utils"),
    ("Glob", {"pattern": "scripts/**/*.py"}, "scripts/**/*.py"),
    ("Bash", {"command": "wc -l scripts/memory.py"}, "scripts/memory.py"),
    ("Read", {"file_path": ".claude/hooks/turn-check.py"},
     ".claude/hooks/turn-check.py"),
]

INVESTIGATES_NOTHING = [
    # Scratch and logs: a session must be able to read its own output without
    # spending a budget it did not mean to spend.
    ("Read", {"file_path": ".tmp/audit/mut86b.log"}),
    ("Bash", {"command": "tail -5 .tmp/audit/ci40.log"}),
    ("Read", {"file_path": "outputs/x/notes.jsonl"}),
    # No path at all.
    ("Bash", {"command": "git status --short"}),
    ("Read", {}),
    # A bare word is not a file. Mutation found that every other fixture was
    # already excluded by something else, so deleting the slash test changed no
    # verdict: a Grep PATTERN would then have been charged as a file.
    ("Read", {"file_path": "README"}),
    ("Grep", {"pattern": "def build_denylist"}),
    ("Glob", {"pattern": "SKILL.md"}),
    # Not an investigating tool.
    ("Write", {"file_path": "scripts/a.py", "content": "x"}),
    ("Edit", {"file_path": "scripts/a.py"}),
]


@pytest.mark.parametrize("tool, payload, expected", INVESTIGATES,
                         ids=[p[2] for p in INVESTIGATES])
def test_the_predicate_sees_a_file_being_investigated(hook, tool, payload, expected):
    assert expected in hook.investigated_paths(tool, payload)


@pytest.mark.parametrize("tool, payload", INVESTIGATES_NOTHING,
                         ids=[f"{t}-{i}" for i, (t, _) in enumerate(INVESTIGATES_NOTHING)])
def test_the_predicate_charges_nothing_for_everything_else(hook, tool, payload):
    assert hook.investigated_paths(tool, payload) == set()


def test_the_predicate_separates_the_two_lists(hook):
    """Both directions over one call, so a body ignoring its arguments cannot
    satisfy the two parametrised suites separately."""
    yes = [t for t, p, _ in INVESTIGATES if hook.investigated_paths(t, p)]
    no = [t for t, p in INVESTIGATES_NOTHING if hook.investigated_paths(t, p)]
    assert len(yes) == len(INVESTIGATES)
    assert no == []


def test_one_bash_command_naming_three_files_counts_three(hook):
    got = hook.investigated_paths(
        "Bash", {"command": "diff scripts/a.py scripts/b.py > tests/c.py"})
    assert got == {"scripts/a.py", "scripts/b.py", "tests/c.py"}


# ============================================================
# The budget: wide work is refused, deep work is not
# ============================================================

def _read(walled, session, path):
    return walled.check_fanout_first({
        "session_id": session, "tool_name": "Read",
        "tool_input": {"file_path": path}})


def _blocked(verdict) -> bool:
    return (verdict or {}).get("decision") == "block"


def test_a_wide_stretch_is_refused_once_the_budget_is_spent(walled):
    budget = walled.FANOUT_PATH_BUDGET
    verdicts = [_read(walled, "wide", f"scripts/m_{i}.py")
                for i in range(budget + 4)]

    assert not any(_blocked(v) for v in verdicts[:budget]), (
        "the wall fired inside its own budget")
    assert all(_blocked(v) for v in verdicts[budget:]), (
        "the wall never fired, so the budget means nothing")


def test_deep_work_on_one_file_is_never_refused(walled):
    """The mirror, and the reason the count is of PATHS and not of CALLS.

    Forty reads of one file is a dependency chain. A call-counting wall would
    refuse it, and a wall that refuses correct work gets switched off.
    """
    verdicts = [_read(walled, "deep", "scripts/one.py") for _ in range(40)]
    assert not any(_blocked(v) for v in verdicts)


def test_reading_scratch_output_is_never_refused(walled):
    verdicts = [_read(walled, "scratch", f".tmp/audit/probe_{i}.log")
                for i in range(40)]
    assert not any(_blocked(v) for v in verdicts)


def test_a_payload_with_no_session_is_not_walled(walled):
    """Same reason as the graph wall: "this session's budget" needs a session,
    and keying every caller on one shared marker would make unrelated suites
    depend on each other's order.

    Spends WELL PAST the budget before asserting. One call could never fire the
    wall whether the guard is there or not, and mutation caught exactly that:
    deleting the guard changed no result because the test never reached the
    threshold it was supposed to be exempt from.
    """
    for sessionless in ({}, {"session_id": ""}, {"session_id": "   "}):
        verdicts = [walled.check_fanout_first({
            "tool_name": "Read",
            "tool_input": {"file_path": f"scripts/a_{i}.py"}, **sessionless})
            for i in range(walled.FANOUT_PATH_BUDGET + 5)]
        assert not any(_blocked(v) for v in verdicts), sessionless


# ============================================================
# The doors
# ============================================================

def _spend(walled, session):
    for i in range(walled.FANOUT_PATH_BUDGET + 2):
        _read(walled, session, f"scripts/m_{i}.py")
    return _read(walled, session, "scripts/final.py")


@pytest.mark.parametrize("tool, payload", [
    pytest.param("Agent", {"prompt": "go"}, id="agent"),
    pytest.param("Task", {"prompt": "go"}, id="task"),
    pytest.param("Workflow", {"script": "x"}, id="workflow"),
    pytest.param("Bash", {"command": 'python scripts/fanout-note.py "a chain"'},
                 id="serial-note"),
])
def test_each_door_clears_the_budget(walled, tool, payload):
    session = f"door-{tool}-{payload!r:.20}"
    assert _blocked(_spend(walled, session)), "the wall did not fire, so the door proves nothing"

    walled.check_fanout_first({"session_id": session, "tool_name": tool,
                               "tool_input": payload})

    assert not _blocked(_read(walled, session, "scripts/next.py"))


def test_a_bash_command_merely_naming_the_script_still_clears(walled):
    """Deliberate and worth stating: the hook matches the script NAME in a
    command, so `python scripts/fanout-note.py --show` clears too. Reading the
    claims log is itself an act of considering the question, and a stricter
    match would only invite the model to spell the command differently."""
    session = "show-door"
    assert _blocked(_spend(walled, session))
    walled.check_fanout_first({
        "session_id": session, "tool_name": "Bash",
        "tool_input": {"command": "python scripts/fanout-note.py --show"}})
    assert not _blocked(_read(walled, session, "scripts/next.py"))


def test_an_unrelated_bash_command_does_not_clear(walled):
    """The mirror on the third door. If any command cleared the budget the wall
    would reset itself constantly and never fire."""
    session = "no-door"
    assert _blocked(_spend(walled, session))
    for command in ("git status --short", "echo fanout", "ls .claude/state"):
        walled.check_fanout_first({"session_id": session, "tool_name": "Bash",
                                   "tool_input": {"command": command}})
    assert _blocked(_read(walled, session, "scripts/next.py"))


def test_the_budget_starts_again_after_a_door(walled):
    """Cleared, not disabled. A door that switched the rule off for the session
    would make one agent dispatch buy unlimited serial work afterwards."""
    session = "restart"
    assert _blocked(_spend(walled, session))
    walled.check_fanout_first({"session_id": session, "tool_name": "Agent",
                               "tool_input": {"prompt": "go"}})
    assert _blocked(_spend(walled, session)), (
        "the wall never fired again, so the door disabled it rather than "
        "clearing it")


# ============================================================
# The wiring, which is where the graph wall shipped a cage
# ============================================================

def test_the_check_is_wired_into_the_dispatcher(hook):
    assert hook.check_fanout_first in hook.CHECKS


def _matchers(path: Path) -> list[str]:
    hooks = json.loads(path.read_text(encoding="utf-8")).get("hooks") or {}
    return [e.get("matcher") or "" for e in (hooks.get("PreToolUse") or [])]


@pytest.mark.parametrize("platform", ["linux", "macos", "windows"])
@pytest.mark.parametrize("tool", ["Agent", "Task", "Workflow"])
def test_every_tracked_template_routes_the_fanout_tools(platform, tool):
    """The cage check. A dispatch that never reaches the dispatcher cannot
    clear the budget, and the wall becomes a wall with no door. These files are
    TRACKED, so there is nothing to skip and no machine where this cannot run.
    """
    path = ROOT / ".claude" / f"settings.local.{platform}.json"
    matchers = _matchers(path)
    reaching = [m for m in matchers if re.fullmatch(m, tool)]
    assert reaching, (
        f"{path.name} routes no PreToolUse matcher to {tool}, so dispatching "
        f"one could never clear the fan-out budget. Matchers: {matchers}")

    hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    routed = [e for e in hooks
              if (e.get("matcher") or "") in reaching
              and any("_dispatch.py" in (h.get("command") or "")
                      for h in e.get("hooks") or [])]
    assert routed, f"{path.name}: the matcher exists but misses _dispatch.py"


def test_the_live_settings_route_the_fanout_tools():
    settings = ROOT / ".claude" / "settings.local.json"
    if not settings.is_file():
        pytest.skip("machine-local settings absent, so there is nothing to route")
    matchers = _matchers(settings)
    assert any(re.fullmatch(m, "Agent") for m in matchers), matchers


# ============================================================
# The recorded escape
# ============================================================

def _note(*args, env=None):
    return subprocess.run([PY, str(ROOT / "scripts" / "fanout-note.py"), *args],
                          capture_output=True, text=True, cwd=str(ROOT),
                          env=env or dict(os.environ))


def test_a_blank_or_thin_reason_is_refused():
    """The escape is the sentence, not the reset. A wall whose bypass takes one
    keystroke is a wall nobody thinks at."""
    for thin in ("", "x", "serial", "n/a"):
        proc = _note(thin)
        assert proc.returncode == 2, (thin, proc.stdout, proc.stderr)


def test_a_real_reason_is_recorded_with_a_timestamp(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / ".claude" / "state").mkdir(parents=True)
    spec = importlib.util.spec_from_file_location(
        "fanout_note_probe", ROOT / "scripts" / "fanout-note.py")
    note = importlib.util.module_from_spec(spec)
    sys.modules["fanout_note_probe"] = note
    spec.loader.exec_module(note)

    log = note.record("one dependency chain: each edit feeds the next measurement")
    entries = [json.loads(ln) for ln in
               log.read_text(encoding="utf-8").splitlines() if ln.strip()]

    assert entries[-1]["reason"].startswith("one dependency chain")
    assert entries[-1]["at"].endswith("+00:00"), "the claim carries no UTC stamp"


def test_the_claims_log_can_be_read_back():
    """The whole point of preferring this to a silent counter: the operator can
    audit what was claimed."""
    proc = _note("--show", "--limit", "3")
    assert proc.returncode == 0, proc.stderr
