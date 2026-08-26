#!/usr/bin/env python3
"""Shard scripts-12-p1: counts asked to carry meaning they never held.

Five of the six defects here are one shape. A number stands in for a fact, the
number arrives from several unrelated places, and the message printed over it
names only one of them. `.claude/rules/scope-claims.md` calls this saying more
than the method established.

  - `run-skill-eval --dry-run --case <valid-id>` resolved the case, printed
    `cases=1`, and then exited 2 with "matched no case". The guard read a zero
    CHECK count, and a dry run produces one on purpose. A case whose `checks`
    block is empty produces one too, after paying for a real API call, and got
    the same false sentence.
  - The same file's `list_skills_with_evals` called `SKILLS_DIR.iterdir()`
    unguarded. `Path.iterdir()` raises on the first iteration, so an absent
    skills tree came out as a traceback - the one setup error escaping the
    exit-code contract that file's own audit had just established.
  - `router-accuracy-nightly`'s module docstring said the runner refuses when
    `is_sensitive()` is true. `is_sensitive` is not imported and not called, and
    the predicate that IS called returns the opposite answer for an unset
    variable: the docstring promised no egress in exactly the configuration
    where egress now happens. It also pointed at `eval-drift-daemon`, retired in
    58aa77d.
  - `resolve_entity` reported `backend_used` as `backends_used[-1]`, the LAST
    backend to answer - a property of how many queries the mode happened to
    build, not of the run. It also set a `backend` key on every search result
    and then dropped it when building `sources`, so a run that fell back named
    two backends and gave no way to map either to a source.
  - The same file's docstring named itself and all four of its usage lines
    `resolve-entity.py`. No such file exists; every documented invocation failed.
  - `run-integration-tests` documented exit codes 0/1/2 while returning pytest's
    verbatim, and printed codes 3-6 as a yellow `[WARN]` with a bare number.
    Code 5 means nothing was measured at all.

Run: .venv/bin/python -m pytest tests/test_a_dry_run_that_said_the_case_did_not_exist.py -q
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "scripts" / stem))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _argv(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["prog", *args])


@pytest.fixture(scope="module")
def evalrun():
    return _load("run-skill-eval.py", "p12p1_run_skill_eval")


@pytest.fixture(scope="module")
def entity():
    return _load("resolve_entity.py", "p12p1_resolve_entity")


@pytest.fixture(scope="module")
def integration():
    return _load("run-integration-tests.py", "p12p1_run_integration")


@pytest.fixture(scope="module")
def nightly():
    return _load("router-accuracy-nightly.py", "p12p1_router_nightly")


def _usage() -> dict:
    return {"input_tokens": 10, "output_tokens": 10,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def _skill(root: Path, name: str, cases: list[dict]) -> Path:
    skill = root / name
    (skill / "evals" / "cases").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\n\nBody.\n", encoding="utf-8")
    for case in cases:
        (skill / "evals" / "cases" / f"{case['id']}.json").write_text(
            json.dumps(case), encoding="utf-8")
    return skill


@pytest.fixture()
def eval_tree(evalrun, tmp_path, monkeypatch):
    """`demo` carries two graded cases; `bare` carries one that grades nothing."""
    _skill(tmp_path, "demo", [
        {"id": "case-1", "input": "hello", "checks": {"must_mention": ["hello"]}},
        {"id": "case-2", "input": "hello", "checks": {"must_mention": ["hello"]}},
    ])
    _skill(tmp_path, "bare", [{"id": "case-bare", "input": "hello", "checks": {}}])
    monkeypatch.setattr(evalrun, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(evalrun, "ROOT", tmp_path)
    monkeypatch.setattr(evalrun, "load_env", lambda: None)
    monkeypatch.setattr(evalrun, "call_skill",
                        lambda *a, **k: ("hello there", _usage(), 0.1))
    return tmp_path


# ============================================================
# 1 - a dry run resolved the case, then denied it existed
# ============================================================
def test_a_dry_run_on_a_real_case_is_not_an_error(evalrun, eval_tree, monkeypatch):
    """THE case. `cases=1` was printed, then `matched no case`, then exit 2."""
    _argv(monkeypatch, "--skill", "demo", "--case", "case-1", "--dry-run", "--no-write")
    assert evalrun.main() == 0


def test_the_dry_run_does_not_deny_the_case_it_just_resolved(
        evalrun, eval_tree, monkeypatch, capsys):
    _argv(monkeypatch, "--skill", "demo", "--case", "case-1", "--dry-run", "--no-write")
    evalrun.main()
    captured = capsys.readouterr()
    assert "matched no case" not in captured.err
    assert "case-1" in captured.out


def test_a_dry_run_on_a_typo_case_still_exits_two(evalrun, eval_tree, monkeypatch, capsys):
    """The guard the fix must not disarm: a typo measures nothing on any run."""
    _argv(monkeypatch, "--skill", "demo", "--case", "case-99", "--dry-run", "--no-write")
    assert evalrun.main() == 2
    assert "matched no case" in capsys.readouterr().err


def test_a_real_run_on_a_typo_case_still_exits_two(evalrun, eval_tree, monkeypatch, capsys):
    _argv(monkeypatch, "--skill", "demo", "--case", "case-99", "--no-write")
    assert evalrun.main() == 2
    assert "matched no case" in capsys.readouterr().err


def test_a_graded_case_still_exits_zero(evalrun, eval_tree, monkeypatch):
    _argv(monkeypatch, "--skill", "demo", "--case", "case-1", "--no-write")
    assert evalrun.main() == 0


def test_a_failing_check_still_exits_one(evalrun, eval_tree, monkeypatch):
    """The fix must not swallow an ordinary failure into a setup error."""
    monkeypatch.setattr(evalrun, "call_skill",
                        lambda *a, **k: ("nothing here", _usage(), 0.1))
    _argv(monkeypatch, "--skill", "demo", "--no-write")
    assert evalrun.main() == 1


def test_an_unfiltered_dry_run_is_still_zero(evalrun, eval_tree, monkeypatch):
    _argv(monkeypatch, "--skill", "demo", "--dry-run", "--no-write")
    assert evalrun.main() == 0


def test_all_with_a_valid_case_and_dry_run_exits_zero(evalrun, eval_tree, monkeypatch):
    """Under --all the named case lives in one skill; the others are ordinary."""
    _argv(monkeypatch, "--all", "--case", "case-1", "--dry-run", "--no-write")
    assert evalrun.main() == 0


def test_all_with_a_typo_case_exits_two(evalrun, eval_tree, monkeypatch, capsys):
    _argv(monkeypatch, "--all", "--case", "case-nope", "--dry-run", "--no-write")
    assert evalrun.main() == 2
    assert "any skill" in capsys.readouterr().err


# ============================================================
# 2 - a case that graded nothing, named as a case that did not exist
# ============================================================
def test_a_checkless_case_is_not_reported_as_unmatched(
        evalrun, eval_tree, monkeypatch, capsys):
    """It matched, it called the API, and it measured nothing. Say THAT."""
    _argv(monkeypatch, "--skill", "bare", "--case", "case-bare", "--no-write")
    assert evalrun.main() == 2
    err = capsys.readouterr().err
    assert "defined no checks" in err
    assert "matched no case" not in err


def test_an_unfiltered_run_of_checkless_cases_also_exits_two(
        evalrun, eval_tree, monkeypatch, capsys):
    """No --case flag, and still a run that spent a call and measured nothing.

    The old branch printed `No checks run` in yellow and returned 0 here.
    """
    _argv(monkeypatch, "--skill", "bare", "--no-write")
    assert evalrun.main() == 2
    assert "defined no checks" in capsys.readouterr().err


def test_a_skill_with_no_cases_at_all_is_still_a_skip(evalrun, tmp_path, monkeypatch):
    """An empty evals/cases/ is not the same event. Unchanged behaviour."""
    _skill(tmp_path, "empty", [])
    monkeypatch.setattr(evalrun, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(evalrun, "ROOT", tmp_path)
    monkeypatch.setattr(evalrun, "load_env", lambda: None)
    _argv(monkeypatch, "--skill", "empty", "--no-write")
    assert evalrun.main() == 0


# ============================================================
# 3 - the two zero-count states are distinct outcomes now
# ============================================================
def test_no_cases_and_a_dry_run_are_different_outcomes(evalrun):
    assert evalrun.OUTCOME_NO_CASES != evalrun.OUTCOME_SKIPPED


def test_run_one_skill_reports_no_cases_when_the_filter_misses(
        evalrun, eval_tree, capsys):
    _, _, outcome = evalrun.run_one_skill("demo", "case-99", None, False, False)
    assert outcome == evalrun.OUTCOME_NO_CASES


def test_run_one_skill_reports_skipped_when_a_dry_run_matched(evalrun, eval_tree):
    _, _, outcome = evalrun.run_one_skill("demo", "case-1", None, True, False)
    assert outcome == evalrun.OUTCOME_SKIPPED


def test_run_one_skill_reports_ok_on_a_graded_case(evalrun, eval_tree):
    passed, total, outcome = evalrun.run_one_skill("demo", "case-1", None, False, False)
    assert (passed, total, outcome) == (1, 1, evalrun.OUTCOME_OK)


def test_a_checkless_case_still_reports_ok_not_no_cases(evalrun, eval_tree):
    """It ran. The zero is the check count, and only `main` may judge that."""
    _, total, outcome = evalrun.run_one_skill("bare", None, None, False, False)
    assert (total, outcome) == (0, evalrun.OUTCOME_OK)


# ============================================================
# 4 - a missing skills tree is a setup error, not a traceback
# ============================================================
def test_a_missing_skills_tree_returns_no_skills_instead_of_raising(
        evalrun, tmp_path, monkeypatch):
    """`Path.iterdir()` raises on the FIRST iteration; it does not yield nothing."""
    monkeypatch.setattr(evalrun, "SKILLS_DIR", tmp_path / "no-such-tree")
    assert evalrun.list_skills_with_evals() == []


def test_all_on_a_missing_skills_tree_exits_two(evalrun, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(evalrun, "SKILLS_DIR", tmp_path / "no-such-tree")
    monkeypatch.setattr(evalrun, "ROOT", tmp_path)
    monkeypatch.setattr(evalrun, "load_env", lambda: None)
    _argv(monkeypatch, "--all", "--no-write")
    assert evalrun.main() == 2


def test_the_missing_tree_message_names_the_path(evalrun, tmp_path, monkeypatch, capsys):
    """A wrong workspace and an uncovered one need different triage."""
    monkeypatch.setattr(evalrun, "SKILLS_DIR", tmp_path / "no-such-tree")
    monkeypatch.setattr(evalrun, "ROOT", tmp_path)
    monkeypatch.setattr(evalrun, "load_env", lambda: None)
    _argv(monkeypatch, "--all", "--no-write")
    evalrun.main()
    assert "no-such-tree" in capsys.readouterr().err


def test_a_present_tree_with_no_evals_keeps_its_own_message(
        evalrun, tmp_path, monkeypatch, capsys):
    (tmp_path / "some-skill").mkdir()
    monkeypatch.setattr(evalrun, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(evalrun, "ROOT", tmp_path)
    monkeypatch.setattr(evalrun, "load_env", lambda: None)
    _argv(monkeypatch, "--all", "--no-write")
    assert evalrun.main() == 2
    captured = capsys.readouterr()
    assert "No skills with evals" in captured.out
    assert "not found" not in captured.err


def test_a_file_beside_the_skills_is_not_mistaken_for_one(
        evalrun, eval_tree, monkeypatch):
    """The guard is `is_dir()` on the TREE; the per-child filter must survive.

    Named, not fixed: BOTH halves of the per-child condition are redundant with
    the `if cases:` filter two lines below them. `Path(missing).glob()` yields
    nothing rather than raising, so a directory with no evals/cases is rejected
    there anyway, and no non-directory child can carry `evals/cases` beneath it.
    Pre-existing, harmless, and left alone per the restraint rule.
    """
    (eval_tree / "loose.txt").write_text("x", encoding="utf-8")
    (eval_tree / "no-evals").mkdir()
    assert evalrun.list_skills_with_evals() == ["bare", "demo"]


# ============================================================
# 5 - the sensitivity docstring described the opposite behaviour
# ============================================================
def test_the_docstring_no_longer_says_is_sensitive_decides(nightly):
    doc = nightly.__doc__ or ""
    assert "refuses to run when the session" not in doc
    assert "sensitivity_is_declared()" in doc


def test_the_docstring_does_not_point_at_the_retired_daemon(nightly):
    """`scripts/eval-drift-daemon.py` was retired in 58aa77d."""
    assert "mirrors ``eval-drift-daemon``" not in (nightly.__doc__ or "")
    assert not (ROOT / "scripts" / "eval-drift-daemon.py").exists()


def test_is_sensitive_is_neither_imported_nor_called(nightly):
    """Walked from the AST, not by substring: the docstring naming the predicate
    it deliberately does NOT use is the fix, and a text search finds it there."""
    tree = ast.parse((ROOT / "scripts" / "router-accuracy-nightly.py").read_text(
        encoding="utf-8"))
    imported = {alias.asname or alias.name
                for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                for alias in node.names}
    called = {node.func.id for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "is_sensitive" not in imported
    assert "is_sensitive" not in called
    assert "sensitivity_is_declared" in imported
    assert not hasattr(nightly, "is_sensitive")


def test_the_two_predicates_disagree_on_an_unset_variable(monkeypatch):
    """The reason the stale docstring was not merely untidy: it was inverted."""
    from scripts.utils.sensitive import is_sensitive, sensitivity_is_declared
    monkeypatch.delenv("SENSITIVE_MODE", raising=False)
    assert is_sensitive() is True
    assert sensitivity_is_declared() is False


def test_an_unset_sensitive_mode_reaches_the_egress_proof(nightly, monkeypatch):
    """Behavioural: unset RUNS the proof, which the old docstring denied."""
    monkeypatch.delenv("SENSITIVE_MODE", raising=False)
    monkeypatch.setattr(nightly, "load_env", lambda: None)
    reached = []
    monkeypatch.setattr(nightly, "outbound_texts", lambda: ["ok"])
    monkeypatch.setattr(nightly, "build_denylist", lambda root: [])
    monkeypatch.setattr(nightly, "dirty_sources", list)
    monkeypatch.setattr(nightly, "get_data_root", lambda: ROOT)

    def _proof(*args, **kwargs):
        reached.append(True)
        return (nightly.EGRESS_CLEAR, "clear")

    monkeypatch.setattr(nightly, "egress_state", _proof)
    monkeypatch.setattr(nightly, "_run_harness", lambda model: 0)
    assert nightly.run("sonnet") == 0
    assert reached, "an unset SENSITIVE_MODE never reached the egress proof"


def test_a_declared_sensitive_mode_still_refuses_outright(nightly, monkeypatch):
    monkeypatch.setenv("SENSITIVE_MODE", "on")
    monkeypatch.setattr(nightly, "load_env", lambda: None)
    monkeypatch.setattr(nightly, "_record_refusal", lambda reason: None)

    def _must_not_run(*args, **kwargs):
        raise AssertionError("the egress proof must not overrule a declaration")

    monkeypatch.setattr(nightly, "egress_state", _must_not_run)
    monkeypatch.setattr(nightly, "_run_harness", _must_not_run)
    assert nightly.run("sonnet") == 0


# ============================================================
# 6 - provenance that named the last backend, and no source
# ============================================================
@pytest.fixture()
def entity_run(entity, monkeypatch):
    """Drive `main` with a scripted backend sequence and a stub extraction."""
    def _install(backend_sequence: list[str]):
        seq = list(backend_sequence)

        def _search(query, max_results=5):
            backend = seq.pop(0) if len(seq) > 1 else seq[0]
            return ([{"url": f"https://{backend}.example/{len(seq)}",
                      "title": f"{backend} result"}], backend)

        monkeypatch.setattr(entity, "search_with_fallback", _search)
        monkeypatch.setattr(entity, "call_anthropic",
                            lambda *a, **k: {"canonical": {"name": "X"}})
        monkeypatch.setattr(entity.claude_models, "latest", lambda fam: "model-x")
        return _search

    return _install


def _run_entity(entity, capsys, monkeypatch, target="ExampleTelco") -> dict:
    _argv(monkeypatch, target, "--mode", "company", "--output", "json")
    assert entity.main() == 0
    return json.loads(capsys.readouterr().out)


def test_the_primary_backend_is_the_first_that_served(
        entity, entity_run, monkeypatch, capsys):
    """THE case. `[-1]` named brave, the fallback, as though it were primary."""
    entity_run(["tavily", "brave", "brave"])
    out = _run_entity(entity, capsys, monkeypatch)
    assert out["backend_used"] == "tavily"
    assert out["backends_used"] == ["tavily", "brave"]


def test_each_source_carries_the_backend_that_returned_it(
        entity, entity_run, monkeypatch, capsys):
    """The key was set on every result and then dropped building `sources`."""
    entity_run(["tavily", "brave", "brave"])
    out = _run_entity(entity, capsys, monkeypatch)
    backends = {s["backend"] for s in out["sources"]}
    assert backends == {"tavily", "brave"}
    assert all(s["url"] and s["backend"] for s in out["sources"])


def test_a_single_backend_run_reports_that_backend_everywhere(
        entity, entity_run, monkeypatch, capsys):
    entity_run(["brave"])
    out = _run_entity(entity, capsys, monkeypatch)
    assert out["backend_used"] == "brave"
    assert out["backends_used"] == ["brave"]
    assert {s["backend"] for s in out["sources"]} == {"brave"}


def test_the_source_shape_still_carries_url_and_title(
        entity, entity_run, monkeypatch, capsys):
    entity_run(["tavily"])
    out = _run_entity(entity, capsys, monkeypatch)
    assert set(out["sources"][0]) == {"url", "title", "backend"}


def test_no_backend_at_all_leaves_the_field_empty_not_missing(entity):
    src = (ROOT / "scripts" / "resolve_entity.py").read_text(encoding="utf-8")
    assert 'backends_used[0] if backends_used else ""' in src


def test_the_osint_skill_documents_all_three_provenance_fields():
    """The only consumer is prose; prose that lists one field of three lies."""
    skill = (ROOT / ".claude" / "skills" / "osint" / "SKILL.md").read_text(
        encoding="utf-8")
    assert "`backends_used`" in skill
    assert "`backend_used` is the PRIMARY" in skill
    assert "carries its own `backend`" in skill


# ============================================================
# 7 - a module that named a file which has never existed
# ============================================================
def test_every_documented_invocation_names_a_file_that_exists():
    doc = (ROOT / "scripts" / "resolve_entity.py").read_text(
        encoding="utf-8").split('"""')[1]
    invoked = re.findall(r"python (scripts/[\w./-]+\.py)", doc)
    assert invoked, "the usage block lost its invocations"
    missing = [p for p in invoked if not (ROOT / p).exists()]
    assert not missing, f"documented but absent: {missing}"


def test_the_module_names_itself_correctly():
    """Line 2 misnamed the module, so fixing only the usage lines was not enough."""
    first = (ROOT / "scripts" / "resolve_entity.py").read_text(
        encoding="utf-8").splitlines()[1]
    assert first.startswith('"""resolve_entity.py')


def test_the_hyphenated_spelling_is_gone_from_the_module_docstring():
    doc = (ROOT / "scripts" / "resolve_entity.py").read_text(
        encoding="utf-8").split('"""')[1]
    # The fix's own note quotes the word "hyphenated"; the FILENAME must be gone.
    assert "resolve-entity.py" not in doc


@pytest.mark.parametrize("stem", ["resolve_entity.py", "resolve_customization.py"])
def test_the_usage_blocks_of_the_resolve_pair_are_runnable(stem):
    """Both siblings use underscores; neither may document a hyphen."""
    src = (ROOT / "scripts" / stem).read_text(encoding="utf-8")
    doc = src.split('"""')[1] if src.count('"""') >= 2 else ""
    assert stem.replace("_", "-") not in doc


# ============================================================
# 8 - an exit-code contract that stopped three codes short
# ============================================================
def _documented_codes() -> set[int]:
    doc = (ROOT / "scripts" / "run-integration-tests.py").read_text(
        encoding="utf-8").split("Exit codes")[1].split('"""')[0]
    return {int(m) for m in re.findall(r"^\s{4}([0-9]) - ", doc, re.MULTILINE)}


def test_every_pytest_exit_code_is_documented():
    """The code is returned verbatim, so the contract is pytest's, not ours."""
    from _pytest.config import ExitCode
    assert _documented_codes() == {int(e) for e in ExitCode}


def test_the_meaning_table_covers_every_code_above_one(integration):
    from _pytest.config import ExitCode
    above_one = {int(e) for e in ExitCode if int(e) > 1}
    assert set(integration.PYTEST_EXIT_MEANING) == above_one


def test_the_return_is_still_pytest_s_own_code(integration, monkeypatch, capsys):
    """No remapping: the documented contract is passthrough, and stays so."""
    class _Result:
        returncode = 5

    monkeypatch.setattr(integration.subprocess, "run", lambda *a, **k: _Result())
    assert integration.run_tests(quiet=True, with_coverage=False) == 5


@pytest.mark.parametrize("code,needle", [
    (2, "interrupted"),
    (3, "internal error"),
    (4, "usage error"),
    (5, "no tests were collected"),
    (6, "max warnings"),
])
def test_the_banner_names_the_code_instead_of_a_bare_number(
        integration, monkeypatch, capsys, code, needle):
    class _Result:
        returncode = code

    monkeypatch.setattr(integration.subprocess, "run", lambda *a, **k: _Result())
    integration.run_tests(quiet=True, with_coverage=False)
    out = capsys.readouterr().out
    assert needle in out
    assert str(code) in out


def test_a_nothing_measured_exit_is_not_called_a_warning(
        integration, monkeypatch, capsys):
    """Exit 5 means the suite never ran. Yellow `[WARN]` was the softest possible
    framing of the hardest possible outcome."""
    class _Result:
        returncode = 5

    monkeypatch.setattr(integration.subprocess, "run", lambda *a, **k: _Result())
    integration.run_tests(quiet=True, with_coverage=False)
    out = capsys.readouterr().out
    assert "[WARN]" not in out
    assert "[ERROR]" in out
    assert "did not complete" in out


def test_an_unknown_code_is_named_as_unknown(integration, monkeypatch, capsys):
    class _Result:
        returncode = 77

    monkeypatch.setattr(integration.subprocess, "run", lambda *a, **k: _Result())
    assert integration.run_tests(quiet=True, with_coverage=False) == 77
    assert "unrecognised" in capsys.readouterr().out


def test_pass_and_fail_banners_are_untouched(integration, monkeypatch, capsys):
    for code, needle in ((0, "[PASS]"), (1, "[FAIL]")):
        class _Result:
            returncode = code

        monkeypatch.setattr(integration.subprocess, "run", lambda *a, **k: _Result())
        integration.run_tests(quiet=True, with_coverage=False)
        assert needle in capsys.readouterr().out


def test_a_missing_pytest_is_still_reported_before_the_run(
        integration, monkeypatch, capsys):
    """The older fix in this file, still standing."""
    monkeypatch.setattr(integration.importlib.util, "find_spec", lambda name: None)

    def _must_not_run(*a, **k):
        raise AssertionError("pytest was probed after being invoked")

    monkeypatch.setattr(integration.subprocess, "run", _must_not_run)
    assert integration.run_tests(quiet=True, with_coverage=False) == 2
    assert "pytest not installed" in capsys.readouterr().out


# ============================================================
# 9 - the exit-code contract of run-skill-eval, walked from the AST
# ============================================================
def test_run_skill_eval_emits_only_documented_codes():
    """Sibling of tests/test_run_skill_eval_exit_codes.py, re-run because this
    shard added two returns to `main`."""
    source = (ROOT / "scripts" / "run-skill-eval.py").read_text(encoding="utf-8")
    documented = {int(tok) for tok in re.findall(
        r"\b([0-9])\s+\w", source.split("Exit codes:", 1)[1].split('"""', 1)[0])}
    main_fn = next(n for n in ast.walk(ast.parse(source))
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    emitted = set()
    for node in ast.walk(main_fn):
        if isinstance(node, ast.Return) and node.value is not None:
            for arm in ([node.value.body, node.value.orelse]
                        if isinstance(node.value, ast.IfExp) else [node.value]):
                if isinstance(arm, ast.Constant) and isinstance(arm.value, int):
                    emitted.add(arm.value)
    assert emitted <= documented, f"undocumented exit code(s): {emitted - documented}"


def test_the_runner_still_starts_from_the_command_line(evalrun):
    """A smoke check that the module is importable and its CLI parses."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run-skill-eval.py"), "--help"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert proc.returncode == 0
    assert "--dry-run" in proc.stdout
