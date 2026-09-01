#!/usr/bin/env python3
"""Six defects in `.claude/hooks/_dispatch.py`, all found on 2026-08-31.

Each one is a guard reading the wrong thing: a word instead of a program, a
spelling instead of a path, a field it did not know existed. None was a logic
bug, and none of the eight test files already driving this dispatcher had a case
that reached any of them.

1. `reader_path_tokens` raised IndexError on a segment whose first word ends in
   `/`. `words[i].split("/")[-1].split()[0]`: the basename of `scripts/` is the
   empty string, `"".split()` is `[]`, and `[0]` raises. MEASURED on
   `grep -rn foo \\` + newline + `  scripts/`, an ordinary backslash-continued
   grep whose continuation line is a bare directory. `check_fanout_first` died,
   `main()` caught it, printed one stderr line, ALLOWED the tool, and the paths
   were never charged to the budget.

2. `_is_serial_full_suite` refused `pytest -m acceptance`. `-k` was in
   `_PYTEST_NARROWING_FLAGS` and `-m` was not, in a repository that defines the
   markers itself and whose own `scripts/run-tests.py` runs `-m acceptance`.
   MEASURED: `-m acceptance` BLOCKED, `-k test_foo` allowed. The refusal text
   promises "Narrow runs are untouched".

3. `check_cwd_anchor` refused `cd <root> && python scripts/x.py`, and told the
   operator it "would fail with ENOENT", a cause the code had not established.
   Only the literal `git rev-parse --show-toplevel` counted as self-anchoring.
   MEASURED from `<root>/tests`. Same false-cause claim as the 2026-08-25
   absolute-path defect this guard already records, reached by the other door.

4. `_load_rate_state` had no `isinstance` guard, so valid JSON that is not an
   object took BOTH counter checks down. MEASURED with the state file holding
   `[]`: `check_rate_limit` and `check_tool_budget` each raised AttributeError,
   which `main()` then failed open on. `check_protect_corporate` had already
   learned this exact lesson for the identity file.

5. `check_protect_corporate` and `check_protect_docs` read only `file_path`, so
   `NotebookEdit`, which carries its target in `notebook_path` and nowhere else,
   was outside both walls. MEASURED with an exec-workspace identity file:
   `Write` BLOCKED, `Edit` BLOCKED, `NotebookEdit` ALLOWED. The module docstring
   claims "every payload shape reaches every check", and the two siblings
   `check_prevent_secrets` and `check_protect_personal_threads` both already
   read the field.

6. `_QUOTED_RE` was not escape-aware, so a real push hid between two escaped
   apostrophes. `echo can\\'t; git push; echo won\\'t` is valid shell, the two
   escaped quotes paired as a span, and `release_action` returned None.
   Contrived, and still a hole in the release wall.

The other two fixes of the same pass are pinned where their fixtures already
live: the graph wall's program-position unlock and the `is_code_search` pipe
case in `test_a_rule_that_was_written_four_times_and_obeyed_none.py`, and the
doc-path allowlist anchoring in `test_protect_personal_threads_hook.py`.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOOK = ROOT / ".claude" / "hooks" / "_dispatch.py"


def _load():
    spec = importlib.util.spec_from_file_location("dispatch_six", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load()


# ============================================================
# 1. A reader token scan that crashed on a bare directory
# ============================================================

# (command, expected tokens). The continuation forms are the ones that raised.
_READER_CASES = [
    # The crash case. It now returns nothing rather than raising: the
    # continuation segment `  scripts/` is not led by a reader binary, so the
    # pre-existing design does not read its arguments. Charging it to the budget
    # would be a wider change than a crash fix, and is noted rather than made.
    ("grep -rn foo \\\n  scripts/", []),
    # A reader whose own argument ends in `/` was the other raiser, and here the
    # path IS charged, because `cat` leads the segment.
    ("cat scripts/\\\n", ["scripts/"]),
    ("cat scripts/foo.py", ["scripts/foo.py"]),
    ("grep -rn x tests/", ["tests/"]),
    ("sudo cat scripts/a.py", ["scripts/a.py"]),
    ("FOO=1 cat scripts/b.py", ["scripts/b.py"]),
    ("grep x scripts/ tests/", ["scripts/", "tests/"]),
    ("scripts/", []),
    ("cat", []),
    ("", []),
]


@pytest.mark.parametrize("command, expected", _READER_CASES,
                         ids=[c.replace("\n", "<NL>") or "<empty>"
                              for c, _ in _READER_CASES])
def test_a_bare_directory_does_not_crash_the_token_scan(command, expected):
    assert hook.reader_path_tokens(command) == expected


def test_the_reader_case_list_measures_both_answers():
    """Green over a fixture list that only ever expects `[]` otherwise."""
    assert any(exp for _, exp in _READER_CASES)
    assert any(not exp for _, exp in _READER_CASES)


def test_the_wall_charges_the_call_instead_of_dying(tmp_path, monkeypatch):
    """Through the real check, because the crash's consequence was a FAIL OPEN.

    The token scan returning `[]` is not the point. The point is that
    `check_fanout_first` reaches a verdict at all rather than raising into
    `main()`'s catch-all, which allowed the tool and charged nothing.
    """
    monkeypatch.setattr(hook, "_FANOUT_STATE_DIR", tmp_path)
    payload = {
        "session_id": "reader-crash-probe",
        "tool_name": "Bash",
        "tool_input": {"command": "grep -rn foo \\\n  scripts/"},
    }
    # No exception, and a verdict of some kind (None is a verdict: under budget).
    assert hook.check_fanout_first(payload) is None


# ============================================================
# 2. A suite guard that refused the shape its own text exempts
# ============================================================

_PYTEST_CASES = [
    (".venv/bin/python -m pytest -m acceptance", False),
    (".venv/bin/python -m pytest -macceptance", False),
    ('.venv/bin/python -m pytest -m "not slow and acceptance"', False),
    ("uv run pytest -m acceptance", False),
    ("pytest -m acceptance", False),
    (".venv/bin/python -m pytest -k test_foo", False),
    (".venv/bin/python -m pytest tests/security", False),
    (".venv/bin/python -m pytest -n auto tests/", False),
    (".venv/bin/python scripts/run-tests.py", False),
    # Still the full suite, so still refused. A marker reached through `not`
    # only DESELECTS, and six thousand tests minus a handful is six thousand.
    ('.venv/bin/python -m pytest -m "not slow"', True),
    (".venv/bin/python -m pytest tests/", True),
    (".venv/bin/python -m pytest", True),
    # `-m` with no value at all must not read as narrow.
    (".venv/bin/python -m pytest -m", True),
    ("pytest tests/", True),
    ("uv run pytest tests/", True),
]


@pytest.mark.parametrize("command, blocked", _PYTEST_CASES,
                         ids=[c for c, _ in _PYTEST_CASES])
def test_a_marker_that_selects_is_a_narrow_run(command, blocked):
    result = hook.check_slow_shell({"tool_name": "Bash",
                                    "tool_input": {"command": command}})
    assert bool(result) is blocked


def test_the_pytest_case_list_measures_both_answers():
    assert sum(1 for _, b in _PYTEST_CASES if b) >= 4
    assert sum(1 for _, b in _PYTEST_CASES if not b) >= 6


def test_the_interpreters_own_dash_m_is_not_read_as_a_marker():
    """The trap that would have deleted the Bash half of the guard.

    `python -m pytest` puts an unrelated `-m` in the same argv. Reading the
    first one would make every `python -m pytest tests/` look narrow.
    """
    assert hook._pytest_marker_expression(
        ["python", "-m", "pytest", "tests/"]) is None
    assert hook._pytest_marker_expression(
        ["python", "-m", "pytest", "-m", "acceptance"]) == "acceptance"


def test_the_marker_predicate_reports_both_ways():
    assert hook._marker_selects_a_subset("acceptance") is True
    assert hook._marker_selects_a_subset("not slow and acceptance") is True
    assert hook._marker_selects_a_subset("slow or acceptance") is True
    assert hook._marker_selects_a_subset("not slow") is False
    assert hook._marker_selects_a_subset("not slow and not acceptance") is False
    assert hook._marker_selects_a_subset("") is False


# ============================================================
# 3. An anchor guard that refused the command that anchors itself
# ============================================================

_SUB = (ROOT / "tests").as_posix()

_ANCHOR_CASES = [
    (f"cd {ROOT.as_posix()} && .venv/bin/python scripts/run-tests.py", False),
    (f'cd "{ROOT.as_posix()}" && .venv/bin/python scripts/run-tests.py', False),
    (f"cd {ROOT.as_posix()}/ && python scripts/run-tests.py", False),
    ('cd "$(git rev-parse --show-toplevel)" && python scripts/run-tests.py',
     False),
    (f".venv/bin/python {ROOT.as_posix()}/scripts/run-tests.py", False),
    ("echo hi", False),
    # A cd to somewhere that is NOT the root anchors nothing.
    (".venv/bin/python scripts/run-tests.py", True),
    (f"cd {_SUB} && .venv/bin/python scripts/run-tests.py", True),
    ("cd /etc && .venv/bin/python scripts/run-tests.py", True),
    ("cd /nonexistent-directory-xyz && python scripts/run-tests.py", True),
]


@pytest.mark.parametrize("command, blocked", _ANCHOR_CASES,
                         ids=[c[:60] for c, _ in _ANCHOR_CASES])
def test_a_command_that_cds_to_root_is_left_alone(command, blocked):
    result = hook.check_cwd_anchor({"tool_name": "Bash", "cwd": _SUB,
                                    "tool_input": {"command": command}})
    assert bool(result) is blocked


def test_the_anchor_case_list_measures_both_answers():
    assert sum(1 for _, b in _ANCHOR_CASES if b) >= 3
    assert sum(1 for _, b in _ANCHOR_CASES if not b) >= 4


# ============================================================
# 4. A counter that reset itself on a shape it never checked
# ============================================================

@pytest.mark.parametrize("body", ["[]", '"x"', "3", "null", "[1, 2]"])
def test_a_non_object_state_file_does_not_take_the_counters_down(
        body, tmp_path, monkeypatch, capsys):
    state = tmp_path / "dispatch-rate.json"
    state.write_text(body, encoding="utf-8")
    monkeypatch.setattr(hook, "RATE_LIMIT_STATE_FILE", state)

    loaded = hook._load_rate_state()
    assert isinstance(loaded, dict)
    assert loaded == {"date": "", "count": 0, "recent": []}
    # Announced, never silent: a counter that resets itself without saying so is
    # the shape `_save_rate_state` has always refused.
    assert "not an object" in capsys.readouterr().err

    # And the two callers survive it, which is the consequence that mattered.
    for check in (hook.check_rate_limit, hook.check_tool_budget):
        check({"tool_name": "Write", "session_id": "rate-probe",
               "tool_input": {"file_path": "x.md", "content": "y"}})


def test_a_real_object_state_file_is_returned_unchanged(tmp_path, monkeypatch):
    """The other direction, so the guard above cannot pass over a loader that
    returns the default for everything."""
    state = tmp_path / "dispatch-rate.json"
    state.write_text(json.dumps({"date": "2026-08-31", "count": 7,
                                 "recent": ["a"]}), encoding="utf-8")
    monkeypatch.setattr(hook, "RATE_LIMIT_STATE_FILE", state)
    assert hook._load_rate_state() == {"date": "2026-08-31", "count": 7,
                                       "recent": ["a"]}


# ============================================================
# 5. Two walls that did not know NotebookEdit exists
# ============================================================

def _run_hook(payload: dict, cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload),
        capture_output=True, text=True, cwd=str(cwd), timeout=60,
    )
    return proc.returncode, proc.stdout


def _denied(stdout: str) -> bool:
    if not stdout.strip():
        return False
    try:
        data = json.loads(stdout)
    except ValueError:
        return False
    if data.get("decision") == "block":
        return True
    return (data.get("hookSpecificOutput") or {}).get(
        "permissionDecision") == "deny"


@pytest.fixture
def exec_workspace(tmp_path):
    """A tree the corporate wall recognises as an executive workspace."""
    (tmp_path / "corporate").mkdir()
    (tmp_path / ".workspace-identity.json").write_text(
        json.dumps({"type": "exec-workspace", "role": "exec", "slug": "e1"}),
        encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("tool, field, extra", [
    ("Write", "file_path", {"content": "x"}),
    ("Edit", "file_path", {"new_string": "x"}),
    ("NotebookEdit", "notebook_path", {"new_source": "x"}),
])
def test_every_write_tool_reaches_the_corporate_wall(
        tool, field, extra, exec_workspace, monkeypatch):
    monkeypatch.setattr(hook, "WORKSPACE", exec_workspace)
    payload = {
        "tool_name": tool, "cwd": str(exec_workspace),
        "tool_input": {field: str(exec_workspace / "corporate" / "x.md"),
                       **extra},
    }
    assert hook.check_protect_corporate(payload) is not None, (
        f"{tool} carries its target in {field} and was outside the wall")


def test_the_corporate_wall_still_lets_a_non_corporate_write_through(
        exec_workspace, monkeypatch):
    monkeypatch.setattr(hook, "WORKSPACE", exec_workspace)
    payload = {
        "tool_name": "NotebookEdit", "cwd": str(exec_workspace),
        "tool_input": {"notebook_path": str(exec_workspace / "notes.ipynb"),
                       "new_source": "x"},
    }
    assert hook.check_protect_corporate(payload) is None


def test_notebook_edit_reaches_the_synced_docs_wall():
    payload = {
        "tool_name": "NotebookEdit",
        "tool_input": {"notebook_path": "docs/GETTING-STARTED.md",
                       "new_source": "x"},
    }
    assert hook.check_protect_docs(payload) is not None


def test_the_docs_wall_still_lets_an_unsynced_doc_through():
    payload = {
        "tool_name": "NotebookEdit",
        "tool_input": {"notebook_path": "docs/ARCHITECTURE.md",
                       "new_source": "x"},
    }
    assert hook.check_protect_docs(payload) is None


# ============================================================
# 6. A release wall blinded by two escaped apostrophes
# ============================================================

_QUOTE_CASES = [
    (r"echo can\'t; git push; echo won\'t", "push"),
    (r'echo "a\"b"; git push', "push"),
    ("git push origin main", "push"),
    ("echo hi && git push", "push"),
    # Genuinely quoted: not a command, and must stay invisible.
    ("echo 'git push'", None),
    ('echo "git push"', None),
    ("grep -rn 'git push' scripts/", None),
]


@pytest.mark.parametrize("command, action", _QUOTE_CASES,
                         ids=[c[:45] for c, _ in _QUOTE_CASES])
def test_an_escaped_quote_does_not_open_a_span(command, action):
    assert hook.release_action(command) == action


def test_the_quote_case_list_measures_both_answers():
    assert any(a for _, a in _QUOTE_CASES)
    assert any(a is None for _, a in _QUOTE_CASES)


def test_the_span_stripper_reports_both_ways():
    """Directly, because the wall above could pass for another reason.

    Blanking LESS is the conservative direction for a wall: a missed span costs
    a false refusal, a missed push costs the whole control.
    """
    assert "git push" in hook._strip_quoted(r"echo can\'t; git push; echo won\'t")
    assert "git push" not in hook._strip_quoted("echo 'git push'")
    assert "git push" not in hook._strip_quoted('echo "git push"')
