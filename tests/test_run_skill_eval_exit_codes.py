"""A regression harness that exits 0 on an API failure is a green light over nothing.

Found by the 2026-08-23 audit. `scripts/run-skill-eval.py` documents four exit
codes — `0 all checks passed, 1 one or more failed, 2 setup error, 3 API error` —
and could only ever return 0, 1 or 2, and 2 only in one narrow case.

`run_one_skill` caught every API exception, printed `API ERROR`, and returned
`(0, 0)`. `main` summed those zeros, saw `overall_total == 0`, printed
"No checks run" and returned 0. So a missing API key, a rate limit, or a
mistyped `--skill` all produced a silent green — from the harness whose entire
purpose is to notice when a model update or a skill edit quietly degrades an
output.

The failure that matters is not a crash. It is the run that says nothing is
wrong because it measured nothing.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_SCRIPT = ROOT / "scripts" / "run-skill-eval.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("run_skill_eval", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_skill_eval"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def skills_dir(runner, tmp_path, monkeypatch):
    """One skill with one case, in a throwaway tree."""
    skill = tmp_path / "demo"
    (skill / "evals" / "cases").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: d\n---\n\nBody.\n", encoding="utf-8")
    (skill / "evals" / "cases" / "case-1.json").write_text(
        json.dumps({"id": "case-1", "input": "hello",
                    "checks": {"must_mention": ["hello"]}}),
        encoding="utf-8")
    monkeypatch.setattr(runner, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(runner, "ROOT", tmp_path)   # case paths print relative to it
    return tmp_path


def _argv(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["run-skill-eval.py", *args])


def test_an_api_error_exits_three(runner, skills_dir, monkeypatch, capsys):
    """THE case. A model call that raises must not read as a passing run."""
    def _boom(*a, **k):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(runner, "call_skill", _boom)
    monkeypatch.setattr(runner, "load_env", lambda: None)
    _argv(monkeypatch, "--skill", "demo", "--no-write")

    assert runner.main() == 3, (
        "the API failed and the harness reported success"
    )
    assert "API ERROR" in capsys.readouterr().out


def test_an_unknown_skill_exits_two(runner, skills_dir, monkeypatch, capsys):
    """A typo in --skill measured nothing and said so with exit 0."""
    monkeypatch.setattr(runner, "load_env", lambda: None)
    _argv(monkeypatch, "--skill", "no-such-skill", "--no-write")
    assert runner.main() == 2
    assert "not found" in capsys.readouterr().err


def test_a_passing_run_still_exits_zero(runner, skills_dir, monkeypatch):
    """The mutation guard on the green path."""
    monkeypatch.setattr(
        runner, "call_skill",
        lambda *a, **k: ("hello there", _usage(), 0.1))
    monkeypatch.setattr(runner, "load_env", lambda: None)
    _argv(monkeypatch, "--skill", "demo", "--no-write")
    assert runner.main() == 0


def test_a_failing_check_still_exits_one(runner, skills_dir, monkeypatch):
    """And on the red path, which must stay distinguishable from an API error."""
    monkeypatch.setattr(
        runner, "call_skill",
        lambda *a, **k: ("goodbye", _usage(), 0.1))
    monkeypatch.setattr(runner, "load_env", lambda: None)
    _argv(monkeypatch, "--skill", "demo", "--no-write")
    assert runner.main() == 1


def test_a_dry_run_is_not_an_error(runner, skills_dir, monkeypatch):
    """--dry-run runs no checks on purpose; that is 0, not 3."""
    monkeypatch.setattr(runner, "load_env", lambda: None)
    _argv(monkeypatch, "--skill", "demo", "--dry-run", "--no-write")
    assert runner.main() == 0


def test_a_skill_with_no_cases_is_not_an_api_error(runner, tmp_path, monkeypatch, capsys):
    """An empty evals/cases/ is a skip. It must not masquerade as a failed call."""
    skill = tmp_path / "empty"
    (skill / "evals" / "cases").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: empty\n---\n", encoding="utf-8")
    monkeypatch.setattr(runner, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "load_env", lambda: None)
    _argv(monkeypatch, "--skill", "empty", "--no-write")
    assert runner.main() == 0
    assert "skip" in capsys.readouterr().out


def test_every_documented_exit_code_is_emitted_by_main():
    """The docstring is a contract, read here by walking `main`'s returns.

    Literal `"return 3" in source` was the first shape of this test and it was
    wrong in the other direction: `return 0 if a == b else 1` emits 1 without
    ever spelling `return 1`. So the returns are collected from the AST,
    including both arms of a conditional expression.
    """
    import ast

    source = _SCRIPT.read_text(encoding="utf-8")
    documented = {int(tok) for tok in
                  __import__("re").findall(r"\b([0-9])\s+\w",
                                           source.split("Exit codes:", 1)[1]
                                           .split('"""', 1)[0])}
    assert documented == {0, 1, 2, 3}, f"the docstring now promises {documented}"

    main_fn = next(n for n in ast.walk(ast.parse(source))
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    emitted = set()
    for node in ast.walk(main_fn):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        arms = ([node.value.body, node.value.orelse]
                if isinstance(node.value, ast.IfExp) else [node.value])
        for arm in arms:
            if isinstance(arm, ast.Constant) and isinstance(arm.value, int):
                emitted.add(arm.value)

    assert documented <= emitted, (
        f"main() promises exit codes {sorted(documented)} and emits "
        f"{sorted(emitted)}; {sorted(documented - emitted)} is unreachable"
    )


def _usage():
    return {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0}
