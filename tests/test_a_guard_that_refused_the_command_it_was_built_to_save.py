"""Shard hooks-h1: seven defects in the PreToolUse dispatcher, the process that
runs on every Write, Edit, Bash and Read this workspace performs.

* ``check_cwd_anchor`` exists to catch a root-relative script path that a drifted
  shell cannot resolve. ``WORKSPACE_REL_SCRIPT_RE`` had no left anchor, so it also
  matched the ``scripts/...py`` TAIL inside a fully-qualified absolute path - which
  resolves from any directory. Every absolutely-pathed workspace script invocation
  was refused whenever the shell was parked below root, and the refusal said the
  command "would fail with ENOENT", a cause the code had not established.
  ``.logs/denials/denials.jsonl`` records a real refusal of that shape at
  ts 1785739896.95.

* ``_is_subdirectory_target`` counted raw path segments, so the absolute spelling
  of the suite root read as a narrow run and the full serial suite passed the
  guard built to stop it. A value-taking flag's value was read as a target too.

* ``_load_rate_state`` zeroed the daily write cap, the runaway-loop window and
  ``check_tool_budget``'s rolling history on any unreadable file, printing nothing.
  ``_save_rate_state`` staged through a FIXED tmp name, so two hook processes could
  produce exactly that unreadable file.

* ``check_rate_limit``'s day rollover rebound the whole state dict, dropping the
  ``tool_history`` key another check owns.

* ``check_protect_corporate`` read ``.workspace-identity.json`` at the live shell
  cwd and allowed the write when it was not there - so the exec-workspace
  corporate wall switched off on the first ``cd``.

* The module docstring claimed three deleted scripts "remain as thin shims", and
  enumerated two of the three registered matchers.

Run: python3 -m pytest tests/test_a_guard_that_refused_the_command_it_was_built_to_save.py
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DISPATCH = ROOT / ".claude" / "hooks" / "_dispatch.py"


def _load(name: str = "dispatch_under_test"):
    spec = importlib.util.spec_from_file_location(name, DISPATCH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def hook():
    return _load()


# ============================================================
# The guard that refused the command it was built to save
# ============================================================

def _anchor(hook, command: str, cwd: Path | str) -> dict | None:
    return hook.check_cwd_anchor(
        {"tool_name": "Bash", "cwd": str(cwd), "tool_input": {"command": command}})


def test_an_absolute_script_path_is_not_refused(hook):
    """It resolves from any directory, so there is nothing to anchor."""
    drifted = ROOT / "tests"
    assert _anchor(hook, f".venv/bin/python {ROOT}/scripts/run-tests.py --help", drifted) is None


def test_the_command_the_denial_log_actually_recorded_is_allowed(hook):
    """`.logs/denials/denials.jsonl` ts 1785739896.95 - a real refusal."""
    command = (f'grep -n "def get_default_tz" -A 30 {ROOT}/scripts/utils/workspace.py; '
               f'echo done')
    assert _anchor(hook, command, ROOT / "scripts" / "bridge_daemon") is None


@pytest.mark.parametrize("command", [
    "python scripts/run-tests.py --help",
    "./scripts/run-tests.py",
    'python "scripts/run-tests.py"',
    "true && scripts/run-tests.py",
])
def test_a_genuinely_root_relative_path_is_still_refused(hook, command):
    result = _anchor(hook, command, ROOT / "tests")
    assert result is not None, f"{command!r} would fail from a drifted cwd"
    assert result["decision"] == "block"
    assert "git rev-parse --show-toplevel" in result["reason"]


def test_a_relative_path_from_the_root_itself_is_allowed(hook):
    assert _anchor(hook, "python scripts/run-tests.py", ROOT) is None


def test_a_shell_outside_the_workspace_is_left_alone(hook, tmp_path):
    assert _anchor(hook, "python scripts/run-tests.py", tmp_path) is None


def test_a_path_that_also_resolves_from_the_drifted_cwd_is_allowed(hook, tmp_path):
    """Condition (c) is the whole justification for blocking.

    The drift only matters when the command would actually fail. A subdirectory
    that happens to carry the same relative path resolves it fine, and refusing
    there would be the false positive the block message swears it never makes.
    The scratch lives under `.tmp/`, which is gitignored and which no guard
    walks; `tests/` is deliberately avoided after a scratch file there raced the
    LFS-fixture guard on 2026-08-25.
    """
    scratch = ROOT / ".tmp" / f"anchor-probe-{tmp_path.name}" / "scripts"
    scratch.mkdir(parents=True)
    (scratch / "run-tests.py").write_text("# stand-in\n", encoding="utf-8")
    try:
        assert _anchor(hook, "python scripts/run-tests.py", scratch.parent) is None
    finally:
        shutil.rmtree(scratch.parent, ignore_errors=True)


@pytest.mark.parametrize("command,expected", [
    ("python scripts/x.py", ["scripts/x.py"]),
    ("./scripts/x.py", ["./scripts/x.py"]),
    ("cmd --file=scripts/x.py", ["scripts/x.py"]),
    ("python .claude/hooks/_dispatch.py", [".claude/hooks/_dispatch.py"]),
    (f"python {ROOT}/scripts/x.py", []),
    ("python ~/anywhere/scripts/x.py", []),
    ("python /opt/.claude/skills/a/b.py", []),
])
def test_the_pattern_cannot_begin_inside_a_path(hook, command, expected):
    assert [m.group(1) for m in hook.WORKSPACE_REL_SCRIPT_RE.finditer(command)] == expected


def test_the_dead_absolute_path_branch_is_gone():
    """It read as the guard against this defect while doing nothing about it."""
    # The removal is explained in a comment that quotes the deleted line, so a
    # bare substring test can never pass. Look for it as live CODE instead.
    live = [ln for ln in DISPATCH.read_text(encoding="utf-8").splitlines()
            if ln.strip().startswith("if os.path.isabs(rel):")]
    assert live == []


# ============================================================
# The full suite that passed by spelling its own path differently
# ============================================================

@pytest.mark.parametrize("command,blocked,why", [
    (f"pytest {ROOT}/tests", True, "absolute suite root"),
    ("pytest tests/", True, "relative suite root"),
    ("pytest tests", True, "bare suite root"),
    ("pytest ./tests/", True, "dot-slash suite root"),
    ("pytest .", True, "the cwd"),
    ("pytest --rootdir /a/b", True, "a flag value is not a target"),
    ("pytest --rootdir=/a/b", True, "attached flag value"),
    ("pytest -p no:cacheprovider", True, "another value-taking flag"),
    ("pytest tests/security", False, "a narrow subdirectory"),
    (f"pytest {ROOT}/tests/security", False, "the same subdirectory, absolute"),
    ("pytest tests/test_x.py", False, "one file"),
    ("pytest tests/test_x.py::test_y", False, "one node id"),
    ("pytest -n auto tests/", False, "distributed"),
    ("pytest --rootdir /a/b tests/security", False, "flag value plus a real target"),
])
def test_every_spelling_of_the_full_suite_is_judged_the_same(hook, command, blocked, why):
    argv = hook._pytest_argv(command)
    assert argv is not None, command
    assert hook._is_serial_full_suite(argv) is blocked, why


def test_a_path_outside_this_workspace_is_not_this_guards_suite(hook):
    assert hook._is_subdirectory_target("/somewhere/else/tests") is True


# ============================================================
# The counters that reset in silence
# ============================================================

@pytest.fixture
def rate_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("WS_RATE_LIMIT_STATE", str(tmp_path / "dispatch-rate.json"))
    return _load("dispatch_rate_under_test"), tmp_path / "dispatch-rate.json"


def test_an_unreadable_state_file_says_the_counters_were_lost(rate_hook, capsys):
    hook, state_file = rate_hook
    state_file.write_text('{"date": "2026-08-25", "count": 99', encoding="utf-8")

    assert hook._load_rate_state() == {"date": "", "count": 0, "recent": []}
    err = capsys.readouterr().err
    assert "counters reset to zero" in err, "a runaway loop would arrive as a fresh day"


def test_a_readable_state_file_is_returned_quietly(rate_hook, capsys):
    hook, state_file = rate_hook
    state_file.write_text(json.dumps({"date": "2026-08-25", "count": 7, "recent": []}),
                          encoding="utf-8")
    assert hook._load_rate_state()["count"] == 7
    assert capsys.readouterr().err == ""


def test_two_writers_do_not_share_one_staging_path(rate_hook):
    """A fixed `.json.tmp` is what produced the unreadable file above."""
    hook, state_file = rate_hook
    hook._save_rate_state({"date": "2026-08-25", "count": 1, "recent": []})
    assert json.loads(state_file.read_text(encoding="utf-8"))["count"] == 1
    src = DISPATCH.read_text(encoding="utf-8")
    assert 'with_suffix(".json.tmp")' not in src
    assert "os.getpid()" in src


def test_the_day_rollover_keeps_the_other_checks_window(rate_hook):
    """`tool_history` is a rolling 30-minute window owned by check_tool_budget."""
    hook, state_file = rate_hook
    state_file.write_text(json.dumps({
        "date": "2020-01-01", "count": 500,
        "recent": [["Write", "/x", 1]],
        "tool_history": [["Read", "/y", 1]] * 500,
    }), encoding="utf-8")

    hook.check_rate_limit({"tool_name": "Write", "tool_input": {"file_path": "/z"}})

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert len(saved["tool_history"]) == 500, "the rolling tool window was wiped"
    assert saved["count"] == 1, "the daily write count did not roll over"
    assert saved["date"] != "2020-01-01"


# ============================================================
# The corporate wall that switched off on the first `cd`
# ============================================================

@pytest.fixture
def exec_workspace(tmp_path):
    ws = tmp_path / "exec"
    (ws / "corporate").mkdir(parents=True)
    (ws / "knowledge").mkdir()
    (ws / ".workspace-identity.json").write_text(
        json.dumps({"type": "exec-workspace"}), encoding="utf-8")
    return ws


@pytest.mark.parametrize("subdir", ["", "knowledge", "corporate"])
def test_the_corporate_wall_holds_from_any_directory(hook, exec_workspace, subdir):
    cwd = exec_workspace / subdir if subdir else exec_workspace
    result = hook.check_protect_corporate({
        "tool_name": "Write", "cwd": str(cwd),
        "tool_input": {"file_path": str(exec_workspace / "corporate" / "x.md")}})
    assert result is not None, "an exec corporate write was allowed"
    assert result["decision"] == "block"


def test_a_write_outside_corporate_is_still_allowed(hook, exec_workspace):
    assert hook.check_protect_corporate({
        "tool_name": "Write", "cwd": str(exec_workspace / "knowledge"),
        "tool_input": {"file_path": str(exec_workspace / "knowledge" / "note.md")}}) is None


def test_a_tree_with_no_identity_file_anywhere_is_a_quiet_no_op(hook, tmp_path):
    assert hook.check_protect_corporate({
        "tool_name": "Write", "cwd": str(tmp_path),
        "tool_input": {"file_path": str(tmp_path / "corporate" / "x.md")}}) is None


def test_the_identity_walk_stops_at_the_nearest_marker(hook, exec_workspace):
    deep = exec_workspace / "knowledge" / "a" / "b"
    deep.mkdir(parents=True)
    assert hook._identity_root(str(deep)) == exec_workspace.resolve()


def test_an_unknown_cwd_falls_back_to_the_workspace_root(hook, exec_workspace,
                                                         monkeypatch):
    """The payload may carry no cwd at all; the wall must still classify.

    Dropping this fallback leaves an exec workspace unprotected for every
    payload whose `cwd` is missing or unreadable, which is the same silent
    allow this shard set out to close.
    """
    monkeypatch.setattr(hook, "WORKSPACE", exec_workspace)
    assert hook._identity_root("") == exec_workspace
    assert hook._identity_root("/nonexistent/path/xyz") == exec_workspace


def test_a_read_never_reaches_the_corporate_wall(hook, exec_workspace):
    assert hook.check_protect_corporate({
        "tool_name": "Read", "cwd": str(exec_workspace),
        "tool_input": {"file_path": str(exec_workspace / "corporate" / "x.md")}}) is None


# ============================================================
# The docstring that described a workspace that no longer exists
# ============================================================

def test_the_docstring_does_not_promise_shims_that_were_deleted():
    doc = " ".join(_load("dispatch_doc_under_test").__doc__.split())
    correction = doc.index("There are NO delegating shims")
    assert correction < doc.index("remain as thin shims"), (
        "the false claim is still the first thing a reader meets"
    )
    assert "MUST be re-provisioned" in doc


@pytest.mark.parametrize("matcher", ["Write|Edit|MultiEdit|NotebookEdit", "Bash", "Read"])
def test_every_registered_matcher_is_named_in_the_docstring(matcher):
    """Backticked, because the bare word is not evidence of an enumeration.

    "Read" also appears in the prose that follows ("Read carries a `file_path`"),
    so a plain substring test passed even with the matcher struck from the list
    itself. The enumeration writes each name in backticks; that is the token.
    """
    doc = " ".join(_load("dispatch_doc_under_test2").__doc__.split())
    assert f"`{matcher}`" in doc


def test_the_docstring_matches_what_settings_actually_registers():
    """The enumeration is only worth anything if it tracks the registration."""
    settings = json.loads((ROOT / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    registered = {
        entry.get("matcher")
        for entry in settings.get("hooks", {}).get("PreToolUse", [])
        if any("_dispatch.py" in (h.get("command") or "")
               for h in entry.get("hooks", []))
    }
    assert registered, "the dispatcher is registered under no matcher at all"
    doc = " ".join(_load("dispatch_doc_under_test3").__doc__.split())
    for matcher in registered:
        assert f"`{matcher}`" in doc, f"{matcher} is registered and undocumented"
