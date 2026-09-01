"""F-M12: llm_fallback.py and observability_safe.py must not walk to a root by hand.

Widened 2026-09-01 from a substring search to a behavioural one, because the
substring search was satisfiable by a comment.

The presence half of this file read `"get_state_dir" in src` over the WHOLE
file. `scripts/utils/observability_safe.py` mentions that name in six comments
and docstrings explaining why the resolver is asked for, so the assertion was
answered by the prose about the fix rather than by the fix. MEASURED 2026-09-01
against `tests/test_workspace_root_paths.py`, `tests/inbox_pulse/
test_observability_safe.py` and `tests/test_sensitive_mode.py` together:

    delete the `from scripts.inbox_pulse.paths import get_state_dir` line
        -> SURVIVED (all three files green)
    return Path("/dev/shm/...") / "debug-trace.jsonl"
        -> SURVIVED
    return Path(__file__).resolve().parents[2] / "state" / "email-triage"
                                               / "debug-trace.jsonl"
        -> SURVIVED

The third is the point. It is the exact pre-2026-08-25 defect - raw e-mail
bodies, subjects and sender addresses written to `<engine>/state/email-triage/`,
which `get_routing_destination` answers `engine`, the PUBLIC repository - put
back in a spelling the `parent.parent.parent` absence check cannot see, and
nothing in the tree went red. `parents[2]` is the same walk with different
punctuation.

So the presence half now CALLS the function and asserts where the path lands,
in each of its three branches, plus the one property that holds across all
three: it is never inside the engine clone. `parents[N]` is banned by pattern
beside `parent.parent.parent`, since a source-level ban is only worth the
spellings it knows.

`llm_fallback.WORKSPACE_ROOT` is asserted by value for the same reason. That one
IS caught by `tests/test_llm_fallback.py` (measured: hardcoding it goes red
there), so this file was not the only guard; it was still asserting the wrong
thing.
"""
from __future__ import annotations

import ast
import re
import sys
import tempfile
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

OBS_SRC = ENGINE / "scripts/utils/observability_safe.py"
LLM_SRC = ENGINE / "scripts/utils/llm_fallback.py"

# `parent.parent.parent` and `parents[2]` are the same walk. A ban that knows
# only the first spelling is a ban on one author's habit.
_HAND_WALK = re.compile(r"parent\.parent\.parent|parents\[\s*\d+\s*\]")


def _strip_comments_and_docstrings(src: str) -> str:
    """Source with every comment and every string literal removed.

    The whole finding is that prose satisfied a search over code, so any search
    that stays in this file runs over what the interpreter would execute.
    """
    tree = ast.parse(src)
    spans: list[tuple[int, int, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            spans.append((node.lineno, node.col_offset,
                          node.end_lineno, node.end_col_offset))
    lines = src.splitlines()
    for start_l, start_c, end_l, end_c in spans:
        for i in range(start_l - 1, end_l):
            line = lines[i]
            lo = start_c if i == start_l - 1 else 0
            hi = end_c if i == end_l - 1 else len(line)
            lines[i] = line[:lo] + " " * (hi - lo) + line[hi:]
    return "\n".join(ln.split("#", 1)[0] for ln in lines)


def _function(src: str, name: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() no longer exists")


# --------------------------------------------------------------- source shape

def test_llm_fallback_no_hand_walked_root():
    code = _strip_comments_and_docstrings(LLM_SRC.read_text(encoding="utf-8"))
    hit = _HAND_WALK.search(code)
    assert hit is None, (
        f"llm_fallback.py walks to a root by hand ({hit.group(0)!r}) instead of "
        "asking the resolver (F-M12)")


def test_observability_safe_no_hand_walked_root():
    code = _strip_comments_and_docstrings(OBS_SRC.read_text(encoding="utf-8"))
    hit = _HAND_WALK.search(code)
    assert hit is None, (
        f"observability_safe.py walks to a root by hand ({hit.group(0)!r}); "
        "that walk is what put raw e-mail bodies in the engine tree (F-M12)")


def test_the_comment_stripper_can_tell_prose_from_code():
    """The control this file exists because of.

    If `_strip_comments_and_docstrings` returned its input unchanged, the two
    tests above would be the same whole-file substring search they replaced, and
    would pass over a file whose only mention of the walk is an explanation of
    why it was removed.
    """
    sample = ('"""A docstring naming parent.parent.parent."""\n'
              "x = 1  # and a comment naming parents[2]\n"
              "y = 'a literal with parent.parent.parent in it'\n")
    assert _HAND_WALK.search(sample), "the sample does not contain the pattern"
    assert _HAND_WALK.search(_strip_comments_and_docstrings(sample)) is None
    # And it must not blind itself to real code.
    assert _HAND_WALK.search(
        _strip_comments_and_docstrings("root = Path(__file__).parents[2]\n"))


def test_the_debug_trace_path_calls_the_resolver_rather_than_naming_it():
    """AST, not a substring: the name must be CALLED inside the function.

    Six comments in this module mention `get_state_dir`. The assertion this
    replaced was answered by any one of them.
    """
    fn = _function(OBS_SRC.read_text(encoding="utf-8"), "_debug_trace_path")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "get_state_dir" in called, (
        "_debug_trace_path no longer calls get_state_dir(); it names it at most "
        "in prose")


# ------------------------------------------------------------------ behaviour

@pytest.fixture
def obs():
    from scripts.utils import observability_safe

    return observability_safe


def test_the_state_dir_override_is_honoured(obs, tmp_path, monkeypatch):
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    assert obs._debug_trace_path() == tmp_path / "debug-trace.jsonl"


def test_the_resolver_decides_where_the_trace_lands(obs, tmp_path, monkeypatch):
    """The branch the mutations walked straight through.

    Both collaborators are imported INSIDE the function, so patching the module
    attributes reaches the real call. A hardcoded return value, or a deleted
    import, changes this answer; a comment cannot.
    """
    from scripts.inbox_pulse import paths as ip_paths
    from scripts.utils import paths as u_paths

    state = tmp_path / "overlay-state"
    monkeypatch.delenv("INBOX_PULSE_STATE_DIR", raising=False)
    monkeypatch.setattr(u_paths, "data_overlay_present", lambda: True)
    monkeypatch.setattr(ip_paths, "get_state_dir", lambda: state)

    assert obs._debug_trace_path() == state / "debug-trace.jsonl"


def test_no_overlay_diverts_to_a_private_temp_file(obs, monkeypatch, capsys):
    """`get_state_dir()` falls back INSIDE the clone when no overlay backs it.

    That is why the module asks `data_overlay_present()` first, and why this
    branch cannot be left to a source grep either.
    """
    from scripts.utils import paths as u_paths

    monkeypatch.delenv("INBOX_PULSE_STATE_DIR", raising=False)
    monkeypatch.setattr(u_paths, "data_overlay_present", lambda: False)

    path = obs._debug_trace_path()
    capsys.readouterr()
    assert path.parent == Path(tempfile.gettempdir())


@pytest.mark.parametrize("branch", ["override", "overlay", "no-overlay"])
def test_the_trace_never_lands_inside_the_engine_clone(obs, branch, tmp_path,
                                                       monkeypatch, capsys):
    """The invariant, asked of every branch rather than of one.

    The engine repository is public. This file holds raw args, kwargs and return
    values, which for the decorated callers means e-mail bodies, subjects and
    sender addresses.
    """
    from scripts.inbox_pulse import paths as ip_paths
    from scripts.utils import paths as u_paths

    monkeypatch.delenv("INBOX_PULSE_STATE_DIR", raising=False)
    if branch == "override":
        monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path / "override"))
    elif branch == "overlay":
        monkeypatch.setattr(u_paths, "data_overlay_present", lambda: True)
        monkeypatch.setattr(ip_paths, "get_state_dir", lambda: tmp_path / "ov")
    else:
        monkeypatch.setattr(u_paths, "data_overlay_present", lambda: False)

    path = obs._debug_trace_path().resolve()
    capsys.readouterr()
    assert ENGINE not in path.parents and path != ENGINE, (
        f"the debug trace resolves to {path}, inside the public engine clone")


def test_llm_fallback_workspace_root_is_the_resolver_answer():
    from scripts.utils.llm_fallback import WORKSPACE_ROOT
    from scripts.utils.workspace import get_workspace_root

    assert get_workspace_root() == WORKSPACE_ROOT
