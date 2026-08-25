"""Shard 02-p4: three input errors in the census benchmark that were not
refused, on a path whose exit codes are its whole product.

`--recall-crosscheck` documents exit 1 as "the ceiling's meaning as an upper
bound was contradicted" - a real benchmark verdict. Every input error on that
path is supposed to exit 2 with a message. Three did not:

* The grading branch does `set(answer.get("paths", []))`. The default only
  applies when the key is ABSENT, so a hand-written `"paths": null` reached
  `set(None)` - a TypeError, and `main`'s except chain lists ValueError,
  KeyError, JSONDecodeError, OSError and SubprocessError but not that one. The
  run died on a traceback and exited 1, so a harness reading exit codes filed
  an operator's typo as a falsified benchmark ceiling. This is the interactive
  mode: the operator writes that file by hand, hours after the print pass.

* The shown file - the one the docstring calls THE MEASUREMENT, and the one
  that sits on disk overnight between the two passes - got no shape check at
  all, while the answers file beside it got one. Re-saved as `[]`, `record.get`
  is an AttributeError and the exit is 1 again.

* `score_answers` built `{a.get("question_id"): a for a in records}`, so two
  records for one question silently collapsed and the verdict turned on file
  order. A record with no id landed under None and matched nothing. The module
  states elsewhere that a dropped measurement is named, never silent.

Run: python3 -m pytest tests/test_a_typo_that_was_filed_as_a_falsified_benchmark.py
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


def _load_bench():
    spec = importlib.util.spec_from_file_location(
        "census_bench_refusals", ROOT / "scripts" / "census-bench.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["census_bench_refusals"] = module
    spec.loader.exec_module(module)
    return module


bench = _load_bench()
FIXTURE = ROOT / "tests" / "fixtures" / "census_corpus"
TODAY = date(2026, 6, 15)


class _Truth:
    def __init__(self, paths):
        self.paths = set(paths)
        self.kind = "paths"
        self.value = None


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    """A crosscheck with a fixed oracle, a scriptable index, and a tmp out dir."""
    state = {"now": ["a.md"]}

    monkeypatch.setattr(bench, "query_at",
                        lambda *_a, **_k: [{"path": p, "score": 0.5}
                                           for p in state["now"]])
    monkeypatch.setattr(
        bench, "load_truth",
        lambda *_a, **_k: {q: _Truth({"a.md"}) for q in bench.CROSSCHECK_QUESTIONS})
    monkeypatch.setattr(bench, "get_outputs_dir", lambda: tmp_path / "outputs")
    monkeypatch.setattr(bench, "_run_state", lambda *_a, **_k: {"corpus_sha": "abc"})
    return state


def _questions():
    return [{"id": q, "group": "g", "question_class": "c", "question_ru": f"q{q}"}
            for q in bench.CROSSCHECK_QUESTIONS]


def _corpus():
    return bench.CorpusPaths(
        root=FIXTURE, threads=FIXTURE / "threads", crm=FIXTURE / "crm",
        context=FIXTURE / "context", auto_memory=FIXTURE / "auto-memory",
        knowledge=FIXTURE / "knowledge", outputs=FIXTURE / "outputs",
    )


def _answers(tmp_path, payload) -> str:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _print_pass(wired, tmp_path):
    """Run the print pass so the shown file exists, and return its path."""
    assert bench.mode_recall_crosscheck(
        _questions(), _corpus(), ROOT, TODAY, None) == 0
    return bench._crosscheck_shown_path()


def _good_answers():
    return {q: {"kind": "paths", "paths": ["a.md"]}
            for q in bench.CROSSCHECK_QUESTIONS}


# ============================================================
# The paths value that was never a list
# ============================================================

@pytest.mark.parametrize("bad", [None, 42, "a.md", {"a": 1}, 1.5, True])
def test_a_non_list_paths_exits_two_not_one(wired, tmp_path, capsys, bad):
    """Exit 1 on this path means the benchmark was falsified. A typo is not."""
    _print_pass(wired, tmp_path)
    payload = _good_answers()
    payload[bench.CROSSCHECK_QUESTIONS[0]] = {"kind": "paths", "paths": bad}

    code = bench.mode_recall_crosscheck(_questions(), _corpus(), ROOT, TODAY,
                                        _answers(tmp_path, payload))
    assert code == 2
    assert "paths" in capsys.readouterr().err


def test_the_offending_question_is_named(wired, tmp_path, capsys):
    _print_pass(wired, tmp_path)
    qid = bench.CROSSCHECK_QUESTIONS[0]
    payload = _good_answers()
    payload[qid] = {"kind": "paths", "paths": None}

    bench.mode_recall_crosscheck(_questions(), _corpus(), ROOT, TODAY,
                                 _answers(tmp_path, payload))
    assert qid in capsys.readouterr().err


def test_a_null_paths_is_refused_not_graded_as_wrong(wired, tmp_path, capsys):
    """Coercing to [] would grade the question wrong and invent a measurement.

    That is the quieter version of the same defect: a typo turned into a
    benchmark result instead of a traceback.
    """
    _print_pass(wired, tmp_path)
    payload = _good_answers()
    payload[bench.CROSSCHECK_QUESTIONS[0]] = {"kind": "paths", "paths": None}

    bench.mode_recall_crosscheck(_questions(), _corpus(), ROOT, TODAY,
                                 _answers(tmp_path, payload))
    out = capsys.readouterr().out
    assert "исход" not in out, "no question may be graded from a refused file"


def test_an_answer_with_no_paths_key_is_still_graded(wired, tmp_path, capsys):
    """The guard fires on a PRESENT key of the wrong type, not on absence.

    An answer that carries no `paths` at all is a legitimate wrong answer to a
    paths question, and grading it is the mode's job.
    """
    _print_pass(wired, tmp_path)
    payload = _good_answers()
    payload[bench.CROSSCHECK_QUESTIONS[0]] = {"kind": "count", "value": 3}

    code = bench.mode_recall_crosscheck(_questions(), _corpus(), ROOT, TODAY,
                                        _answers(tmp_path, payload))
    assert code == 0
    assert "wrong" in capsys.readouterr().out


def test_a_refusal_is_still_accepted(wired, tmp_path, capsys):
    _print_pass(wired, tmp_path)
    payload = {q: {"refused": True} for q in bench.CROSSCHECK_QUESTIONS}
    code = bench.mode_recall_crosscheck(_questions(), _corpus(), ROOT, TODAY,
                                        _answers(tmp_path, payload))
    assert code == 0
    assert "refused" in capsys.readouterr().out


def test_a_well_formed_answers_file_still_grades(wired, tmp_path, capsys):
    _print_pass(wired, tmp_path)
    code = bench.mode_recall_crosscheck(_questions(), _corpus(), ROOT, TODAY,
                                        _answers(tmp_path, _good_answers()))
    assert code == 0
    assert "correct" in capsys.readouterr().out


def test_a_non_object_answer_is_still_refused(wired, tmp_path, capsys):
    """The container guard must survive the value guard being added beside it."""
    _print_pass(wired, tmp_path)
    payload = _good_answers()
    payload[bench.CROSSCHECK_QUESTIONS[0]] = "see notes.txt"
    code = bench.mode_recall_crosscheck(_questions(), _corpus(), ROOT, TODAY,
                                        _answers(tmp_path, payload))
    assert code == 2


# ============================================================
# The shown file nobody shape-checked
# ============================================================

@pytest.mark.parametrize("payload", ["[]", '"text"', "7", "null", "true"])
def test_a_shown_file_that_is_not_an_object_exits_two(wired, tmp_path, capsys,
                                                      payload):
    shown_path = _print_pass(wired, tmp_path)
    shown_path.write_text(payload, encoding="utf-8")

    code = bench.mode_recall_crosscheck(_questions(), _corpus(), ROOT, TODAY,
                                        _answers(tmp_path, _good_answers()))
    assert code == 2
    assert "показанная выдача" in capsys.readouterr().err


@pytest.mark.parametrize("shown", ["a string", 7, ["a.md"], True])
def test_a_shown_field_of_the_wrong_type_exits_two(wired, tmp_path, capsys, shown):
    shown_path = _print_pass(wired, tmp_path)
    shown_path.write_text(json.dumps({"schema_version": 1, "shown": shown}),
                          encoding="utf-8")

    code = bench.mode_recall_crosscheck(_questions(), _corpus(), ROOT, TODAY,
                                        _answers(tmp_path, _good_answers()))
    assert code == 2
    assert "shown" in capsys.readouterr().err


def test_an_absent_shown_field_still_names_the_pass_to_rerun(wired, tmp_path,
                                                             capsys):
    """Absent is INCOMPLETE, not corrupt, and keeps its actionable message.

    Refusing both with one message would trade a useful instruction for a
    shorter branch.
    """
    shown_path = _print_pass(wired, tmp_path)
    shown_path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    code = bench.mode_recall_crosscheck(_questions(), _corpus(), ROOT, TODAY,
                                        _answers(tmp_path, _good_answers()))
    assert code == 2
    assert "печатающий проход" in capsys.readouterr().err


def test_a_missing_shown_file_is_unchanged(wired, tmp_path, capsys):
    code = bench.mode_recall_crosscheck(_questions(), _corpus(), ROOT, TODAY,
                                        _answers(tmp_path, _good_answers()))
    assert code == 2
    assert "нет показанной выдачи" in capsys.readouterr().err


# ============================================================
# Two answers for one question
# ============================================================

def _score_payload(records):
    return {"schema_version": 1, "answers": records}


def _score_file(tmp_path, payload) -> str:
    path = tmp_path / "score.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_two_records_for_one_question_are_refused(tmp_path):
    """Last-wins meant one of two measurements vanished with the file order."""
    path = _score_file(tmp_path, _score_payload([
        {"question_id": "agg-05", "answer": {"kind": "paths", "paths": ["a.md"]}},
        {"question_id": "agg-05", "answer": {"kind": "paths", "paths": ["b.md"]}},
    ]))
    with pytest.raises(ValueError, match="agg-05"):
        bench.score_answers(path, today=TODAY)


def test_the_refusal_names_every_repeated_id(tmp_path):
    path = _score_file(tmp_path, _score_payload([
        {"question_id": "agg-01", "answer": {}},
        {"question_id": "agg-01", "answer": {}},
        {"question_id": "agg-02", "answer": {}},
        {"question_id": "agg-02", "answer": {}},
        {"question_id": "agg-03", "answer": {}},
    ]))
    with pytest.raises(ValueError) as exc:
        bench.score_answers(path, today=TODAY)
    assert "agg-01" in str(exc.value) and "agg-02" in str(exc.value)
    assert "agg-03" not in str(exc.value), "a single record is not a duplicate"


@pytest.mark.parametrize("record", [
    {"answer": {}},                     # no question_id at all
    {"question_id": None, "answer": {}},
    {"question_id": "", "answer": {}},
    {"question_id": 7, "answer": {}},
    {"question_id": ["agg-01"], "answer": {}},
])
def test_a_record_with_no_usable_id_is_refused(tmp_path, record):
    """It landed under the key None, matched no question, and scored as absent."""
    path = _score_file(tmp_path, _score_payload([record]))
    with pytest.raises(ValueError, match="question_id"):
        bench.score_answers(path, today=TODAY)


def test_the_refusal_names_the_position(tmp_path):
    path = _score_file(tmp_path, _score_payload([
        {"question_id": "agg-01", "answer": {}},
        {"answer": {}},
    ]))
    with pytest.raises(ValueError, match=r"\[1\]"):
        bench.score_answers(path, today=TODAY)


def test_the_outer_shape_guards_still_fire(tmp_path):
    """The new checks sit below three older ones; none may be displaced."""
    for payload, needle in (
        ("[]", "объектом"),
        ('{"answers": "not a list"}', "списком"),
        ('{"answers": [{"question_id": "a"}, "oops"]}', "позициях"),
    ):
        path = tmp_path / "bad.json"
        path.write_text(payload, encoding="utf-8")
        with pytest.raises(ValueError, match=needle):
            bench.score_answers(str(path), today=TODAY)
