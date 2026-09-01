#!/usr/bin/env python3
"""scripts/run-skill-eval.py blamed the model for the fixture, and crashed after paying.

`CaseFileError`'s own docstring says a case file that "is the wrong shape" is a
SETUP error, reported as exit 2. `load_cases` checked one thing: that the file
decodes to a JSON object. Everything below that was unguarded.

A case with no `"input"` key reached `case["input"]` INSIDE the try whose handler
labels every exception `API ERROR` and returns OUTCOME_API_ERROR - exit 3. The
reader is sent to the model call, the API key and the rate limiter, for a typo in
a fixture. And `--dry-run`, the advertised validation mode, could not catch it:
that branch reads `case.get('input', '')`, so the broken case passed cleanly and
the wrong exit code appeared only on the run that costs money.

A non-dict `"checks"` was worse: `checks.get(...)` in `run_checks` sits OUTSIDE
the try, so it raised AttributeError - a traceback and exit 1 - after the call
had been made and paid for.

The benchmark sidecar had the same shape of hole. Only `json.JSONDecodeError` was
handled, so a file holding valid JSON that is not an object (`[]`, `"reset"`)
loaded fine and `existing["last_run"] = {...}` raised TypeError: every case
graded, every token spent, no benchmark written, traceback.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location(
        "run_skill_eval", ROOT / "scripts" / "run-skill-eval.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_skill_eval"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def skills(tmp_path, runner, monkeypatch):
    """A skills tree at the seam every test in this suite redirects."""
    root = tmp_path / ".claude" / "skills"
    (root / "q-branch" / "evals" / "cases").mkdir(parents=True)
    (root / "q-branch" / "SKILL.md").write_text(
        "---\nname: q-branch\nmodel: haiku\n---\nBody of the skill.\n",
        encoding="utf-8")
    monkeypatch.setattr(runner, "SKILLS_DIR", root)
    return root


def _write_case(skills, name, payload):
    path = skills / "q-branch" / "evals" / "cases" / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def never_called_api(runner, monkeypatch):
    """Any API call at all is a failure for these cases: the fixture is broken."""
    calls = []

    def spy(system_prompt, user_input, model):
        calls.append(user_input)
        return ("gadget briefing text here", {
            "input_tokens": 1, "output_tokens": 1,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}, 0.1)

    monkeypatch.setattr(runner, "call_skill", spy)
    return calls


# ============================================================
# A wrong-shaped case is a setup error, on both paths
# ============================================================

@pytest.mark.parametrize("payload,fragment", [
    ({"id": "case-9", "description": "no input key",
      "checks": {"min_words": 1}}, 'must carry an "input" key'),
    ({"id": "case-9", "input": 7}, '"input" must be a string'),
    ({"id": "case-9", "input": "hi",
      "checks": ["min_words"]}, '"checks" must be a JSON object'),
    ({"id": "case-9", "input": "hi",
      "checks": {"must_mention": "sovereign"}}, '"must_mention" must be a list'),
    ({"id": "case-9", "input": "hi",
      "checks": {"must_not_mention": "dual-use"}},
     '"must_not_mention" must be a list'),
])
def test_a_wrong_shaped_case_is_refused_by_the_loader(runner, skills, payload,
                                                      fragment):
    _write_case(skills, "case-9.json", payload)
    with pytest.raises(runner.CaseFileError) as exc:
        runner.load_cases(skills / "q-branch")
    assert fragment in str(exc.value)


def test_the_paid_run_reports_a_setup_error_not_an_api_error(runner, skills,
                                                             never_called_api,
                                                             monkeypatch, capsys):
    _write_case(skills, "case-9.json",
                {"id": "case-9", "description": "no input key",
                 "checks": {"min_words": 1}})
    monkeypatch.setattr(sys, "argv", ["run-skill-eval.py", "--skill", "q-branch",
                                      "--no-write"])
    assert runner.main() == 2
    err = _plain(capsys.readouterr().err)
    assert "case file error" in err
    assert "API ERROR" not in err


def test_no_api_call_is_ever_made_for_a_broken_fixture(runner, skills,
                                                       never_called_api,
                                                       monkeypatch):
    """It reached `case["input"]` inside the call's try, so the fixture bug was
    only ever discovered on the paid path. It is now discovered before it."""
    _write_case(skills, "case-9.json", {"id": "case-9", "checks": {"min_words": 1}})
    monkeypatch.setattr(sys, "argv", ["run-skill-eval.py", "--skill", "q-branch",
                                      "--no-write"])
    runner.main()
    assert never_called_api == []


def test_dry_run_now_catches_what_only_the_paid_run_used_to(runner, skills,
                                                            monkeypatch, capsys):
    """The advertised validation mode read `case.get('input', '')` and passed."""
    _write_case(skills, "case-9.json", {"id": "case-9", "checks": {"min_words": 1}})
    monkeypatch.setattr(sys, "argv", ["run-skill-eval.py", "--skill", "q-branch",
                                      "--dry-run"])
    assert runner.main() == 2
    assert "case file error" in _plain(capsys.readouterr().err)


def test_a_non_dict_checks_block_no_longer_tracebacks_mid_grade(runner, skills,
                                                                never_called_api,
                                                                monkeypatch):
    """`checks.get(...)` sat outside the try and raised AttributeError."""
    _write_case(skills, "case-1.json",
                {"id": "case-1", "input": "brief me", "checks": ["min_words"]})
    monkeypatch.setattr(sys, "argv", ["run-skill-eval.py", "--skill", "q-branch",
                                      "--no-write"])
    assert runner.main() == 2


def test_a_well_shaped_case_still_loads_and_grades(runner, skills, never_called_api,
                                                   monkeypatch):
    """The validator must not refuse the corpus it exists to protect."""
    _write_case(skills, "case-1.json", {
        "id": "case-1", "input": "brief me",
        "checks": {"must_mention": ["gadget"], "min_words": 2}})
    monkeypatch.setattr(sys, "argv", ["run-skill-eval.py", "--skill", "q-branch",
                                      "--no-write"])
    assert runner.main() == 0
    assert never_called_api == ["brief me"]


def test_a_case_with_no_checks_key_at_all_is_still_valid(runner, skills):
    """`checks` is optional; only a present-and-wrong one is refused."""
    _write_case(skills, "case-1.json", {"id": "case-1", "input": "brief me"})
    assert len(runner.load_cases(skills / "q-branch")) == 1


def test_a_nested_list_of_spellings_is_still_accepted(runner, skills):
    """`must_mention` may hold lists of accepted spellings; that shape stays."""
    _write_case(skills, "case-1.json", {
        "id": "case-1", "input": "brief me",
        "checks": {"must_mention": [["persist", "persistence"], "daemon"]}})
    assert len(runner.load_cases(skills / "q-branch")) == 1


# ============================================================
# A benchmark that parses but is not an object
# ============================================================

@pytest.fixture
def graded_run(runner, skills, never_called_api, monkeypatch):
    _write_case(skills, "case-1.json", {
        "id": "case-1", "input": "brief me", "checks": {"min_words": 2}})
    monkeypatch.setattr(sys, "argv", ["run-skill-eval.py", "--skill", "q-branch"])
    return skills / "q-branch" / "evals" / "benchmark.json"


@pytest.mark.parametrize("text", ["[]", '"reset"', "42", "null"])
def test_a_parseable_but_wrong_shaped_benchmark_does_not_kill_the_run(runner,
                                                                      graded_run,
                                                                      text):
    graded_run.write_text(text, encoding="utf-8")
    assert runner.main() == 0


@pytest.mark.parametrize("text", ["[]", '"reset"'])
def test_the_wrong_shaped_benchmark_is_replaced_by_a_usable_one(runner, graded_run,
                                                                text):
    graded_run.write_text(text, encoding="utf-8")
    runner.main()
    written = json.loads(graded_run.read_text(encoding="utf-8"))
    assert isinstance(written, dict)
    assert set(written) == {"last_run", "baseline", "baseline_is_self_seed"}


def test_the_wrong_shaped_benchmark_is_kept_not_deleted(runner, graded_run):
    """The corrupt branch's whole point: the operator's file is preserved."""
    graded_run.write_text("[]", encoding="utf-8")
    runner.main()
    kept = list((graded_run.parent / ".quarantine").glob("benchmark.json.corrupt*"))
    assert len(kept) == 1
    assert kept[0].read_text(encoding="utf-8") == "[]"


def test_the_message_names_the_shape_it_found(runner, graded_run, capsys):
    graded_run.write_text('"reset"', encoding="utf-8")
    runner.main()
    err = _plain(capsys.readouterr().err)
    assert "a JSON str, not an object" in err


def test_an_unparseable_benchmark_still_takes_the_same_route(runner, graded_run,
                                                             capsys):
    """Regression guard on the branch that already worked."""
    graded_run.write_text("{not json", encoding="utf-8")
    assert runner.main() == 0
    assert "unparseable" in _plain(capsys.readouterr().err)


@pytest.mark.parametrize("baseline", [None, [], ["promoted"], "seeded-from-first-run",
                                      42, True])
def test_a_baseline_that_is_not_an_object_does_not_kill_the_write(runner,
                                                                  graded_run,
                                                                  baseline):
    """`existing["baseline"].get(...)` raised AttributeError one line before the
    write, losing a run that had already been paid for.

    Parametrized on 2026-09-01, and `None` alone was the reason. The guard is
    `isinstance(baseline, dict)`; the case list was the single value `None`,
    which is the ONE non-object for which the strictly weaker
    `baseline is not None` behaves identically. So the test named for "a
    baseline that is not an object" bound only the one non-object that needed
    no isinstance check at all.

    Measured 2026-09-01 in a copy of this tree, with `isinstance(baseline,
    dict)` in `scripts/run-skill-eval.py` replaced by `baseline is not None`:

        .venv/bin/python -m pytest -q tests/test_a_broken_fixture_that_billed_\\
            itself_as_an_api_error.py
        23 passed

    and green across every other file in `tests/` that names run-skill-eval.
    A hand-edited `"baseline": []` or `"baseline": "promoted"` would still
    reach `.get` on a list or a string and raise AttributeError one line before
    the write, losing a run that had already been graded and paid for. That is
    verbatim the defect in the first line of this docstring.

    `True` is here because `isinstance(True, dict)` is False while `bool` is
    the type most likely to slip through a check written against `int`.
    """
    graded_run.write_text(json.dumps({"baseline": baseline}), encoding="utf-8")
    assert runner.main() == 0
    written = json.loads(graded_run.read_text(encoding="utf-8"))
    assert written["baseline_is_self_seed"] is False
    assert written["baseline"] == baseline, "the operator's file was rewritten"


def test_a_good_benchmark_is_still_updated_in_place(runner, graded_run):
    graded_run.write_text(json.dumps({
        "baseline": {"passed_total": 1, "check_total": 1, "source": "promoted"},
    }), encoding="utf-8")
    assert runner.main() == 0
    written = json.loads(graded_run.read_text(encoding="utf-8"))
    assert written["baseline"]["source"] == "promoted"
    assert written["baseline_is_self_seed"] is False
    assert written["last_run"]["check_total"] == 1
