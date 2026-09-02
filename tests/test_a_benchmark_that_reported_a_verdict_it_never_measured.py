#!/usr/bin/env python3
"""census-bench exit codes are a VERDICT, and six paths reached one without
measuring anything.

The module docstring's own table says 1 is "a real outcome everywhere it
appears, never an error", 0 is "the run produced the favourable reading", and
2 is instrument failure. Six paths broke that contract in one of three ways.

*A crash that lands on a verdict code.* Three inputs raised an exception nothing
in `main`'s except chain lists, so the interpreter exited 1 - the code this
script reserves for "the ceiling was contradicted" and for REJECTED:

  * `shown[qid]` in the crosscheck handoff file. The grading pass validates the
    record, that `shown` is a dict, and that every crosscheck question has a
    key. It never looked at the VALUE. That file is written by one invocation
    and read by another, sits on disk overnight, and is the one the mode's own
    docstring calls THE MEASUREMENT; a truncated re-save putting a string or a
    null under one question raised AttributeError or TypeError.
  * `grade_one` on a `pairs` answer holding a nested list. `tuple([1, ["a"]])`
    is unhashable, so building the comparison set raised TypeError on the
    acceptance path, from a file an LLM emits.
  * `query_index` and `query_at` both did `.get("hits", [])` on whatever
    `json.loads` returned. `JSONDecodeError` is guarded; a bare list, string or
    number is VALID JSON and is not, so the AttributeError escaped both
    `QUERY_FAILURES` and `main` - exit 1 where the table documents exit 3, "the
    retrieval layer could not be called".

*A favourable verdict over nothing measured.* `--recall-crosscheck` printed
"Ничего не проверено" - no question had a zero ceiling, so the assumption could
not have been falsified by this run - and returned 0, which the table defines as
the favourable reading. The workspace calls this class "a pass over an empty
corpus is not a pass"; `scripts/ste-check.py` and `scripts/validate-crm-schema.py`
both exit 2 for it, and so does this now.

*A grade computed against a world nobody named.* `_today_from` fell back to the
host clock when `run_state.today` was missing or malformed, saying nothing, on a
gate whose oracles are date-sensitive. And an omitted crosscheck answer was read
as `{}` and scored "wrong", although the refused-versus-wrong distinction is
load-bearing in this file: a question nobody answered is not an incorrect
answer.

Run: python3 -m pytest tests/test_a_benchmark_that_reported_a_verdict_it_never_measured.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.census_oracles import OracleAnswer  # noqa: E402


def _load():
    spec = importlib.util.spec_from_file_location(
        "census_bench_verdict_never_measured", ROOT / "scripts" / "census-bench.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load()
TODAY = date(2026, 6, 15)

# The three questions the crosscheck grades, and the truth each one gets here.
# ctl-02 is deliberately given a path the shown hits never carry, so its ceiling
# is 0.000 and the run has something that COULD falsify the assumption. Without
# it every fixture below would be a run that checked nothing, which is the exact
# state finding 2 makes an instrument failure.
SHOWN_PATH = "threads/business/a.md"
UNREACHABLE_PATH = "threads/business/never-shown.md"


def _truth() -> dict[str, OracleAnswer]:
    truth = {qid: OracleAnswer(kind="paths", paths={SHOWN_PATH})
             for qid in bench.CROSSCHECK_QUESTIONS}
    truth[bench.CROSSCHECK_QUESTIONS[-1]] = OracleAnswer(
        kind="paths", paths={UNREACHABLE_PATH})
    return truth


def _questions():
    return [{"id": qid, "group": "g", "question_class": "c", "question_ru": "?"}
            for qid in bench.CROSSCHECK_QUESTIONS]


@pytest.fixture()
def crosscheck(tmp_path, monkeypatch):
    """Wire the grading pass: a fixed oracle, a handoff file, a tmp out dir."""
    shown_path = tmp_path / "recall-crosscheck-shown.json"

    def _write_shown(shown: dict) -> None:
        shown_path.write_text(json.dumps({
            "schema_version": 1, "run_state": {}, "shown": shown,
        }), encoding="utf-8")

    _write_shown({qid: [{"path": SHOWN_PATH, "score": 0.4}]
                  for qid in bench.CROSSCHECK_QUESTIONS})
    monkeypatch.setattr(bench, "_crosscheck_shown_path", lambda: shown_path)
    monkeypatch.setattr(bench, "load_truth", lambda *_a, **_k: _truth())
    monkeypatch.setattr(bench, "_run_state", lambda *_a, **_k: {})
    monkeypatch.setattr(bench, "states_comparable",
                        lambda a, b, pins=None: (True, []))
    monkeypatch.setattr(bench, "get_outputs_dir", lambda: tmp_path / "outputs")
    return _write_shown


def _grade(tmp_path, answers: dict, write: bool = False) -> int:
    path = tmp_path / "cc.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    return bench.mode_recall_crosscheck(_questions(), None, ROOT, TODAY,
                                        str(path), write=write)


def _all_correct() -> dict:
    answers = {qid: {"kind": "paths", "paths": [SHOWN_PATH]}
               for qid in bench.CROSSCHECK_QUESTIONS}
    answers[bench.CROSSCHECK_QUESTIONS[-1]] = {"refused": True}
    return answers


# ============================================================
# Finding 1 - the shown entry nobody shape-checked
# ============================================================

@pytest.mark.parametrize("bad", [
    pytest.param("threads/business/a.md", id="a-string"),
    pytest.param(None, id="a-null"),
    pytest.param(7, id="a-number"),
    pytest.param({"path": SHOWN_PATH}, id="an-object-not-a-list"),
])
def test_a_shown_entry_that_is_not_a_list_exits_two(crosscheck, tmp_path,
                                                    capsys, bad):
    """Exit 1 here means the ceiling was contradicted. A corrupt file is not.

    Key coverage was checked and the value was not, so a truncated re-save of
    the handoff file was filed by any exit-code-reading harness as a falsified
    benchmark.
    """
    shown = {qid: [{"path": SHOWN_PATH, "score": 0.4}]
             for qid in bench.CROSSCHECK_QUESTIONS}
    shown[bench.CROSSCHECK_QUESTIONS[0]] = bad
    crosscheck(shown)

    assert _grade(tmp_path, _all_correct()) == 2
    assert "shown" in capsys.readouterr().err


def test_a_shown_entry_holding_a_non_object_hit_exits_two(crosscheck, tmp_path,
                                                          capsys):
    """One bad element among good ones, which is what a partial write leaves."""
    shown = {qid: [{"path": SHOWN_PATH, "score": 0.4}]
             for qid in bench.CROSSCHECK_QUESTIONS}
    shown[bench.CROSSCHECK_QUESTIONS[0]] = [{"path": SHOWN_PATH}, "oops"]
    crosscheck(shown)

    assert _grade(tmp_path, _all_correct()) == 2
    assert "shown" in capsys.readouterr().err


def test_the_corrupt_question_is_named(crosscheck, tmp_path, capsys):
    """Console-first: the message says which question to look at."""
    qid = bench.CROSSCHECK_QUESTIONS[0]
    shown = {q: [{"path": SHOWN_PATH, "score": 0.4}]
             for q in bench.CROSSCHECK_QUESTIONS}
    shown[qid] = "threads/business/a.md"
    crosscheck(shown)

    _grade(tmp_path, _all_correct())
    assert qid in capsys.readouterr().err


def test_an_empty_shown_entry_is_not_corrupt(crosscheck, tmp_path):
    """A question whose /recall query returned nothing is the NORMAL zero-ceiling
    case, and it is the only case that can falsify the assumption. Refusing it
    would refuse the measurement this mode exists to take."""
    shown = {qid: [] for qid in bench.CROSSCHECK_QUESTIONS}
    crosscheck(shown)

    answers = {qid: {"refused": True} for qid in bench.CROSSCHECK_QUESTIONS}
    assert _grade(tmp_path, answers) == 0


def test_a_well_formed_handoff_is_still_graded(crosscheck, tmp_path, capsys):
    """Anchor: a guard that refused every file would pass every case above."""
    assert _grade(tmp_path, _all_correct()) == 0
    assert "исход" in capsys.readouterr().out


# ============================================================
# Finding 2 - a crosscheck that falsified nothing
# ============================================================

def _nothing_falsifiable(crosscheck) -> None:
    """Every question's truth is inside the shown hits, so every ceiling is 1.0."""
    crosscheck({qid: [{"path": SHOWN_PATH, "score": 0.4},
                      {"path": UNREACHABLE_PATH, "score": 0.3}]
                for qid in bench.CROSSCHECK_QUESTIONS})


def test_a_run_that_could_falsify_nothing_is_not_a_favourable_verdict(
        crosscheck, tmp_path, capsys):
    """0 means "the run produced the favourable reading". Nothing was read."""
    _nothing_falsifiable(crosscheck)
    code = _grade(tmp_path, _all_correct())
    assert code == 2, "a crosscheck that checked nothing reported the good news"
    assert "Ничего не проверено" in capsys.readouterr().out


def test_the_nothing_checked_refusal_survives_no_write(crosscheck, tmp_path):
    """`--no-write` returns early on its own branch, which had the same 0."""
    _nothing_falsifiable(crosscheck)
    assert _grade(tmp_path, _all_correct(), write=False) == 2


def test_the_nothing_checked_refusal_holds_when_the_report_is_written(
        crosscheck, tmp_path):
    _nothing_falsifiable(crosscheck)
    assert _grade(tmp_path, _all_correct(), write=True) == 2


def test_one_falsifiable_question_is_enough_to_report_a_verdict(crosscheck,
                                                                tmp_path):
    """The boundary. The default fixture has exactly one zero-ceiling question,
    and that run is a real measurement, not an instrument failure."""
    assert _grade(tmp_path, _all_correct()) == 0


def test_a_contradiction_still_outranks_the_nothing_checked_refusal(crosscheck,
                                                                    tmp_path):
    """A falsified ceiling is a finding about the benchmark and keeps exit 1."""
    crosscheck({qid: [] for qid in bench.CROSSCHECK_QUESTIONS})
    answers = {qid: {"kind": "paths", "paths": [SHOWN_PATH]}
               for qid in bench.CROSSCHECK_QUESTIONS}
    assert _grade(tmp_path, answers) == 1


def test_the_exit_table_documents_what_the_empty_run_returns():
    """The table is the contract; a new exit path that is not in it is invisible.

    Sliced to the TABLE, not to the whole docstring below it: the prose after
    the table names this file's sibling
    `test_a_typo_that_was_filed_as_a_falsified_benchmark.py`, so a search of the
    docstring for "falsif" passes over a table that says nothing.
    """
    table = bench.__doc__.split("Exit codes")[1].split("Until 2026")[0]
    assert "falsify" in table, (
        "exit 2 now covers a crosscheck that could falsify nothing, and the "
        f"table never says so: {table}")


# ============================================================
# Finding 3 - a nested list inside a pairs answer
# ============================================================

def _pairs_truth() -> OracleAnswer:
    return OracleAnswer(kind="pairs", paths={SHOWN_PATH},
                        value=[["ExampleCo", "2026-01-01"]])


@pytest.mark.parametrize("pair", [
    pytest.param([["ExampleCo"], "2026-01-01"], id="a-nested-list"),
    pytest.param(["ExampleCo", {"date": "2026-01-01"}], id="a-nested-object"),
])
def test_a_pairs_answer_holding_an_unhashable_element_is_graded_not_crashed(pair):
    """`tuple(p)` on a pair holding a list is unhashable: TypeError, uncaught,
    and `--score` exited 1 - which the table reserves for REJECTED. The answers
    file is LLM-emitted, so any JSON shape can arrive here."""
    record = {"answer": {"kind": "pairs", "pairs": [pair], "sources": []}}
    status, why = bench.grade_one(record, _pairs_truth(), {SHOWN_PATH})
    assert status == bench.STATUS_WRONG
    assert why, "a wrong grade must say what was wrong with the answer"


def test_a_well_formed_pairs_answer_is_still_correct():
    """Anchor: the guard must not swallow the pairs comparison itself."""
    record = {"answer": {"kind": "pairs",
                         "pairs": [["ExampleCo", "2026-01-01"]], "sources": []}}
    status, _ = bench.grade_one(record, _pairs_truth(), {SHOWN_PATH})
    assert status == bench.STATUS_CORRECT


def test_a_wrong_pairs_answer_is_still_wrong():
    record = {"answer": {"kind": "pairs",
                         "pairs": [["OtherCo", "2026-01-01"]], "sources": []}}
    status, _ = bench.grade_one(record, _pairs_truth(), {SHOWN_PATH})
    assert status == bench.STATUS_WRONG


# ============================================================
# Finding 4 - the grading date that was swapped in silence
# ============================================================

def _fake_corpus(monkeypatch):
    question = {"id": "agg-01", "group": "aggregate", "question_class": "count"}
    monkeypatch.setattr(bench, "load_questions", lambda root: [question])
    monkeypatch.setattr(bench, "load_truth", lambda qs, corpus, today: {
        "agg-01": OracleAnswer(kind="count", value=3)})
    monkeypatch.setattr(bench, "_corpus_files", lambda corpus: set())
    monkeypatch.setattr(bench, "_baseline_comparison",
                        lambda stated: (None, False, []))
    monkeypatch.setattr(bench.CorpusPaths, "from_workspace",
                        staticmethod(lambda: None))


def _score_file(tmp_path, run_state) -> str:
    path = tmp_path / "answers.json"
    payload = {"schema_version": 1, "answers": [
        {"question_id": "agg-01", "answer": {"kind": "count", "value": 3}}]}
    if run_state is not None:
        payload["run_state"] = run_state
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("run_state", [
    pytest.param({}, id="no-today-field"),
    pytest.param(None, id="no-run-state-at-all"),
    pytest.param({"today": "2026-08-1"}, id="a-typo-in-the-date"),
    pytest.param({"today": 20260815}, id="a-number-not-a-date"),
    pytest.param({"today": None}, id="an-explicit-null"),
])
def test_a_substituted_grading_date_is_announced(tmp_path, monkeypatch, capsys,
                                                 run_state):
    """The oracles are date-sensitive and this is the acceptance gate. Reaching
    for the host clock is defensible; doing it without a word is not, in a file
    whose stated principle is that a dropped measurement is named."""
    _fake_corpus(monkeypatch)
    bench.score_answers(_score_file(tmp_path, run_state))
    err = capsys.readouterr().err
    assert "run_state" in err and "today" in err


def test_a_usable_date_in_the_answers_file_is_used_without_a_warning(
        tmp_path, monkeypatch, capsys):
    """Anchor: the warning must fire on the fallback, not on every run."""
    _fake_corpus(monkeypatch)
    report = bench.score_answers(_score_file(tmp_path, {"today": "2026-08-14"}))
    assert report["questions"]
    assert "today" not in capsys.readouterr().err


def test_the_date_the_caller_passed_still_wins(tmp_path, monkeypatch, capsys):
    """An explicit `today` never reaches the fallback, so it never warns."""
    _fake_corpus(monkeypatch)
    bench.score_answers(_score_file(tmp_path, {}), today=TODAY)
    assert "today" not in capsys.readouterr().err


# ============================================================
# Finding 5 - an omitted crosscheck answer is a refusal, not a wrong answer
# ============================================================

def _report(tmp_path) -> dict:
    out = (tmp_path / "outputs" / "operations" / "census-bench")
    written = sorted(out.glob("*_recall-crosscheck_census.json"))
    assert written, "the crosscheck wrote no report to read the outcome from"
    return json.loads(written[-1].read_text(encoding="utf-8"))


def test_a_question_nobody_answered_is_refused_not_wrong(crosscheck, tmp_path):
    """`given.get(qid) or {}` scored an absent answer as an incorrect one. The
    refused-versus-wrong split is what this file's grading is built on: a
    primitive that says "I could not do this" is usable and one that invents an
    answer is not, and a question nobody was asked is neither."""
    answers = _all_correct()
    omitted = bench.CROSSCHECK_QUESTIONS[0]
    del answers[omitted]

    assert _grade(tmp_path, answers, write=True) == 0
    rows = {row["id"]: row for row in _report(tmp_path)["questions"]}
    assert rows[omitted]["outcome"] == "refused"


def test_an_explicit_refusal_is_still_a_refusal(crosscheck, tmp_path):
    """Anchor: the absent case must not swallow the explicit one."""
    answers = {qid: {"refused": True} for qid in bench.CROSSCHECK_QUESTIONS}
    assert _grade(tmp_path, answers, write=True) == 0
    rows = {row["id"]: row for row in _report(tmp_path)["questions"]}
    assert all(row["outcome"] == "refused" for row in rows.values())


def test_a_wrong_answer_is_still_wrong(crosscheck, tmp_path):
    """The other anchor: an answer that IS present and IS incorrect keeps its
    grade, so "refused" cannot become the universal outcome."""
    answers = _all_correct()
    answers[bench.CROSSCHECK_QUESTIONS[0]] = {"kind": "paths",
                                              "paths": ["threads/business/z.md"]}
    assert _grade(tmp_path, answers, write=True) == 0
    rows = {row["id"]: row for row in _report(tmp_path)["questions"]}
    assert rows[bench.CROSSCHECK_QUESTIONS[0]]["outcome"] == "wrong"


def test_an_omitted_answer_is_distinguishable_from_a_stated_refusal(crosscheck,
                                                                    tmp_path):
    """Both are "refused" for grading, and they are not the same event. The
    scorer beside this one records "not answered" in its `why` column for
    exactly this reason; the row here carries the same fact as a flag."""
    answers = _all_correct()
    omitted = bench.CROSSCHECK_QUESTIONS[0]
    del answers[omitted]

    _grade(tmp_path, answers, write=True)
    rows = {row["id"]: row for row in _report(tmp_path)["questions"]}
    assert rows[omitted]["answered"] is False
    assert rows[bench.CROSSCHECK_QUESTIONS[-1]]["answered"] is True


# ============================================================
# Finding 6 - valid JSON that is not an object
# ============================================================

class _Proc:
    def __init__(self, stdout: str):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


@pytest.mark.parametrize("payload", ["[]", '["a.md"]', '"hits"', "7", "null",
                                     "true"])
@pytest.mark.parametrize("call", ["query_index", "query_at"])
def test_valid_json_that_is_not_an_object_is_a_retrieval_failure(monkeypatch,
                                                                 payload, call):
    """`JSONDecodeError` was guarded and this was not, although a bare list is
    perfectly valid JSON. The AttributeError was in neither `QUERY_FAILURES` nor
    `main`, so the run exited 1 - a benchmark verdict - where the table
    documents 3, "the retrieval layer could not be called".

    Patched on the module object `census-bench.py` imported, undone by
    monkeypatch at teardown.
    """
    monkeypatch.setattr(bench.subprocess, "run",
                        lambda *_a, **_k: _Proc(payload))
    with pytest.raises(bench.QUERY_FAILURES):
        if call == "query_index":
            bench.query_index(ROOT, "вопрос")
        else:
            bench.query_at(ROOT, "вопрос", 8, 0.55)


@pytest.mark.parametrize("call", ["query_index", "query_at"])
def test_a_hits_field_that_is_not_a_list_is_a_retrieval_failure(monkeypatch, call):
    """One level down, same class: every caller iterates the return value and
    calls `.get` on each element, so a string there is the same AttributeError."""
    monkeypatch.setattr(bench.subprocess, "run",
                        lambda *_a, **_k: _Proc('{"hits": "a.md"}'))
    with pytest.raises(bench.QUERY_FAILURES):
        if call == "query_index":
            bench.query_index(ROOT, "вопрос")
        else:
            bench.query_at(ROOT, "вопрос", 8, 0.55)


@pytest.mark.parametrize("call", ["query_index", "query_at"])
def test_a_well_formed_query_response_still_returns_its_hits(monkeypatch, call):
    """Anchor: a guard that raised on everything would pass both cases above."""
    monkeypatch.setattr(bench.subprocess, "run",
                        lambda *_a, **_k: _Proc('{"hits": [{"path": "a.md"}]}'))
    if call == "query_index":
        hits, elapsed = bench.query_index(ROOT, "вопрос")
        assert elapsed >= 0.0
    else:
        hits = bench.query_at(ROOT, "вопрос", 8, 0.55)
    assert hits == [{"path": "a.md"}]


def test_a_non_object_query_response_exits_three_through_the_mode(monkeypatch,
                                                                  tmp_path,
                                                                  capsys):
    """The end the operator sees. `mode_recall_crosscheck`'s print pass already
    maps `QUERY_FAILURES` to 3; the point is that this failure now IS one."""
    monkeypatch.setattr(bench.subprocess, "run", lambda *_a, **_k: _Proc("[]"))
    monkeypatch.setattr(bench, "load_truth", lambda *_a, **_k: _truth())
    monkeypatch.setattr(bench, "_run_state", lambda *_a, **_k: {})
    monkeypatch.setattr(bench, "get_outputs_dir", lambda: tmp_path / "outputs")

    code = bench.mode_recall_crosscheck(_questions(), None, ROOT, TODAY, None)
    assert code == 3
    assert "запрос не выполнен" in capsys.readouterr().err
