#!/usr/bin/env python3
"""Shard scripts-02-p4: census-bench's input hardening stopped two levels short.

When `--score` moved inside `main`'s try, the reason was written down: a
malformed answers file "produced a traceback and exit 1 rather than the
documented exit 2 — on the acceptance path this file exists to protect". The
per-answer records were hardened for it. The two levels ABOVE them were not.

`json.loads` accepts any JSON value, so a file that is a bare list reached
`payload.get`, an `answers` object instead of a list reached `a.get` on its own
keys, and one non-dict record among good ones reached `a.get` on a string. Each
raises AttributeError, which `main` does not catch — not in its ValueError,
KeyError, JSONDecodeError, OSError or SubprocessError arms. The result was exit
1 with a traceback, and exit 1 is a documented benchmark VERDICT, so a harness
reading exit codes scores the crash as a real outcome.

`--recall-crosscheck` had the same hole one level down. Its top level is
checked; its per-question values are not, and that file is the hand-written
one: the operator answers hours after the print pass, so `{"agg-03": "see
notes"}` is the expected mistake. `answer.get("refused")` raised the same
uncatchable AttributeError.

Two claims went with them. The exit-code table named only the two `--baseline`
outcomes as the whole meaning of 1, while `--score` returns 1 for REJECTED and
for NOT-COMPARABLE and `--recall-crosscheck` returns 1 for a contradicted
ceiling. And the latency comparison read `base.get("median_s") or
base.get("median")`: `median_s` is a key `build_report` never writes, and `or`
drops a legitimate 0.0 — so the comparison vanished with no note, in a file
whose stated principle is that a dropped measurement is named.

Found by the 2026-08-23 engine audit, shard `scripts-02-p4`. Fixed 2026-08-24.
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
    path = ROOT / "scripts" / "census-bench.py"
    spec = importlib.util.spec_from_file_location("census_bench_p02p4", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bench = _load()


# ---------------------------------------------------------------------------
# Finding 1 -- the two levels above the hardened records
# ---------------------------------------------------------------------------

def _fake_corpus(monkeypatch):
    """Everything `score_answers` reaches for after the shape guard."""
    question = {"id": "agg-01", "group": "aggregate", "question_class": "count"}
    monkeypatch.setattr(bench, "load_questions", lambda root: [question])
    monkeypatch.setattr(bench, "load_truth",
                        lambda qs, corpus, today: {"agg-01": OracleAnswer(kind="count", value=3)})
    monkeypatch.setattr(bench, "_corpus_files", lambda corpus: set())
    monkeypatch.setattr(bench, "_baseline_comparison", lambda stated: (None, False, []))
    monkeypatch.setattr(bench.CorpusPaths, "from_workspace",
                        staticmethod(lambda: None))


MALFORMED = [
    pytest.param('["not", "dicts"]', "объектом", id="top-level-list"),
    pytest.param('"a string"', "объектом", id="top-level-string"),
    pytest.param("42", "объектом", id="top-level-number"),
    pytest.param('{"answers": {"agg-01": {}}}', "списком", id="answers-is-an-object"),
    pytest.param('{"answers": "agg-01"}', "списком", id="answers-is-a-string"),
    pytest.param('{"answers": [{"question_id": "a"}, "oops"]}', "позициях",
                 id="one-bad-record-among-good"),
    pytest.param('{"answers": [], "run_state": ["today"]}', "run_state",
                 id="run-state-is-a-list"),
]


@pytest.mark.parametrize("text,expected", MALFORMED)
def test_a_malformed_answers_file_is_refused_not_crashed_into(text, expected,
                                                              tmp_path):
    """ValueError is what `main` converts to the documented exit 2.

    AttributeError is what these raised, and nothing in the call chain catches
    it, so the run died with a traceback and exit 1 — a code the docstring
    assigns to a real verdict.
    """
    answers = tmp_path / "answers.json"
    answers.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        bench.score_answers(str(answers))
    assert expected in str(caught.value)


@pytest.mark.parametrize("text,_expected", MALFORMED)
def test_the_documented_exit_code_is_what_the_cli_actually_returns(
        text, _expected, tmp_path, monkeypatch, capsys):
    """The contract a wrapper reads. 2 is instrument failure; 1 is a verdict."""
    answers = tmp_path / "answers.json"
    answers.write_text(text, encoding="utf-8")
    monkeypatch.setattr(sys, "argv",
                        ["census-bench.py", "--score", str(answers), "--no-write"])
    assert bench.main() == 2, capsys.readouterr().err


def test_a_well_formed_file_still_gets_past_the_guard(tmp_path, monkeypatch):
    """Anchor: a guard that refused everything would pass every test above.

    Driven with the corpus faked out rather than wrapped in a bare `except`,
    which would have passed whatever the guard did.
    """
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(
        {"answers": [{"question_id": "agg-01", "answer": {"kind": "count", "value": 3}}],
         "run_state": {"today": "2026-08-24"}}), encoding="utf-8")
    _fake_corpus(monkeypatch)
    report = bench.score_answers(str(answers), today=date(2026, 8, 24))
    assert report["questions"], "a valid answers file produced no report"


def test_the_parsed_answers_actually_reach_the_grade(tmp_path, monkeypatch):
    """The other half of the guard: refusing bad shapes is worthless if the
    good ones are then dropped. Nothing in the suite ran `score_answers` end to
    end — the grader was pinned through `grade_one` alone — so a scorer that
    parsed the file and graded an EMPTY set would have looked identical: every
    question "not answered", which is a legitimate report of a real run.
    """
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"answers": [
        {"question_id": "agg-01", "answer": {"kind": "count", "value": 3},
         "elapsed_s": 1.0},
    ]}), encoding="utf-8")

    _fake_corpus(monkeypatch)

    report = bench.score_answers(str(answers), today=date(2026, 8, 24))
    row = report["questions"][0]
    assert row["status"] == bench.STATUS_CORRECT, row
    assert row["elapsed_s"] == 1.0, "the record was dropped between parse and grade"


# ---------------------------------------------------------------------------
# Finding 2 -- the hand-written crosscheck answers
# ---------------------------------------------------------------------------

def _crosscheck(monkeypatch, tmp_path, answers: dict):
    """Drive the grading branch with the corpus and the handoff faked out."""
    shown_path = tmp_path / "recall-crosscheck-shown.json"
    shown_path.write_text(json.dumps({
        "schema_version": 1,
        "run_state": {},
        "shown": {qid: [{"path": "threads/business/a.md", "score": 0.4}]
                  for qid in bench.CROSSCHECK_QUESTIONS},
    }), encoding="utf-8")
    answers_path = tmp_path / "cc.json"
    answers_path.write_text(json.dumps(answers), encoding="utf-8")

    monkeypatch.setattr(bench, "_crosscheck_shown_path", lambda: shown_path)
    monkeypatch.setattr(bench, "load_truth", lambda q, c, t: {
        qid: OracleAnswer(kind="paths", paths={"threads/business/a.md"}, value=1)
        for qid in bench.CROSSCHECK_QUESTIONS})
    monkeypatch.setattr(bench, "_run_state", lambda c, r, t: {})
    monkeypatch.setattr(bench, "states_comparable", lambda a, b, pins=None: (True, []))
    questions = [{"id": qid, "group": "g", "question_ru": "?"}
                 for qid in bench.CROSSCHECK_QUESTIONS]
    return bench.mode_recall_crosscheck(questions, None, ROOT, date(2026, 8, 24),
                                        str(answers_path), write=False)


@pytest.mark.parametrize("bad", ["see notes.txt", ["threads/a.md"], 7, True])
def test_a_hand_written_answer_of_the_wrong_shape_exits_two(bad, tmp_path,
                                                            monkeypatch, capsys):
    """`answer.get("refused")` raised AttributeError here, and no handler in the
    chain catches it: traceback, exit 1, no report — for the one input in this
    script a human types by hand."""
    code = _crosscheck(monkeypatch, tmp_path,
                       {"agg-05": {"kind": "paths", "paths": []},
                        "agg-03": bad,
                        "ctl-02": {"refused": True}})
    assert code == 2
    assert "agg-03" in capsys.readouterr().err


def test_well_shaped_crosscheck_answers_are_still_graded(tmp_path, monkeypatch):
    """Anchor: refusing every file would pass the four above."""
    code = _crosscheck(monkeypatch, tmp_path,
                       {qid: {"refused": True} for qid in bench.CROSSCHECK_QUESTIONS})
    assert code in (0, 1), "a valid crosscheck file must be graded, not refused"


def test_a_question_left_out_entirely_is_still_allowed(tmp_path, monkeypatch):
    """An ABSENT answer is not a malformed one: the code reads it as `{}` and
    grades it. Refusing it would make the guard stricter than the format."""
    code = _crosscheck(monkeypatch, tmp_path, {"agg-05": {"refused": True}})
    assert code in (0, 1)


# ---------------------------------------------------------------------------
# Finding 4 -- the latency comparison that vanished
# ---------------------------------------------------------------------------

def _report(base_latency):
    return {
        "latency_median_s": 1.5,
        "baseline_latency_median_s": base_latency,
        "per_class": {}, "not_scored": [], "questions": [],
        "confidently_wrong": 0, "verdict": bench.VERDICT_ACCEPTED,
        "verdict_why": "why", "verdict_rule": "rule",
        "oracle_pins_diverged": [], "retrieval_pins_diverged": [],
        "baseline_mean_by_class": None, "comparable": True,
    }


def _printed(monkeypatch, capsys, base_latency, tmp_path):
    """The latency line is printed inline by `mode_score`, so drive that."""
    answers = tmp_path / "answers.json"
    answers.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bench, "score_answers",
                        lambda path, today=None: _report(base_latency))
    bench.mode_score(str(answers), write=False)
    return capsys.readouterr().out


def test_a_zero_baseline_median_is_still_a_measurement(monkeypatch, capsys, tmp_path):
    """`or` treated 0.0 as absent and dropped the comparison silently."""
    out = _printed(monkeypatch, capsys, {"median": 0.0, "max": 0.0}, tmp_path)
    assert "0.00 с базовой линии" in out


def test_an_ordinary_baseline_median_is_compared(monkeypatch, capsys, tmp_path):
    out = _printed(monkeypatch, capsys, {"median": 2.25, "max": 3.0}, tmp_path)
    assert "2.25 с базовой линии" in out


def test_a_missing_baseline_median_is_named_not_dropped(monkeypatch, capsys, tmp_path):
    """The file's own principle: a dropped measurement is named. `median` is
    legitimately None when the baseline run measured no latencies."""
    out = _printed(monkeypatch, capsys, {"median": None, "max": None}, tmp_path)
    assert "базовой линии" in out and "нет" in out


def test_an_unusable_baseline_median_says_what_it_found(monkeypatch, capsys, tmp_path):
    out = _printed(monkeypatch, capsys, {"median": "fast"}, tmp_path)
    assert "непригодна" in out and "fast" in out


def test_the_dead_key_is_gone(monkeypatch, capsys, tmp_path):
    """`median_s` is written by nothing. It existed only to hold the `or` open,
    and while it was read first it could shadow the real key."""
    out = _printed(monkeypatch, capsys, {"median_s": 9.99, "median": 2.0}, tmp_path)
    assert "2.00 с базовой линии" in out
    assert "9.99" not in out


# ---------------------------------------------------------------------------
# Finding 3 -- the exit-code table
# ---------------------------------------------------------------------------

def test_the_exit_table_covers_every_mode_that_returns_one():
    doc = bench.__doc__
    table = doc.split("Exit codes")[1]
    for mode in ("--baseline", "--score", "--recall-crosscheck"):
        assert mode in table, f"{mode} can return 1 and the table never names it"
    assert "NOT-COMPARABLE" in table
    assert "FIX-RECALL" in table


def test_the_modes_still_return_the_one_the_table_now_documents():
    """Guard the premise. A table describing returns the code no longer makes
    is the same defect pointed the other way."""
    src = (ROOT / "scripts" / "census-bench.py").read_text(encoding="utf-8")
    score = src.split("def mode_score")[1].split("\ndef ")[0]
    assert "return 0 if verdict_name == VERDICT_ACCEPTED else 1" in score
    cross = src.split("def mode_recall_crosscheck")[1].split("\ndef ")[0]
    assert "return 1 if contradicted else 0" in cross
