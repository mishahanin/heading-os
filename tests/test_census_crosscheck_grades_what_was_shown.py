"""The crosscheck must grade the material the model actually saw.

Found by the 2026-08-23 audit. `--recall-crosscheck` runs in two invocations
with a human in between: the first prints each question with the hits `/recall`
would compose over, the operator answers from THOSE hits and nothing else, and
the second grades the answers and computes each question's recall ceiling.

Both invocations called `query_at` afresh. Nothing carried the first call's hits
forward, so the ceiling in the report described a query run minutes or hours
after the one the answers came from. The index rebuilds on file change and on a
nightly timer, and the corpus is the live workspace, so the two need not agree.

That turns the mode's own falsification rule -- "at ceiling 0.00 the answer must
be wrong or a refusal" -- into something a rebuild can manufacture or erase. An
answer composed from three real hits, graded against a re-query that returned
none, reads as РАСХОЖДЕНИЕ: the ceiling looks refuted when nothing was refuted.
The harness that exists to falsify an assumption was itself falsifiable by an
unrelated background job.
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
        "census_bench_crosscheck", ROOT / "scripts" / "census-bench.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["census_bench_crosscheck"] = module
    spec.loader.exec_module(module)
    return module


bench = _load_bench()
FIXTURE = ROOT / "tests" / "fixtures" / "census_corpus"
TODAY = date(2026, 6, 15)

QID = bench.CROSSCHECK_QUESTIONS[0]


class _Truth:
    """Stands in for the oracle result the real `load_truth` returns."""

    def __init__(self, paths):
        self.paths = set(paths)
        self.kind = "paths"
        self.value = None


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    """A crosscheck with a fixed oracle, a scriptable index, and a tmp out dir."""
    calls = {"n": 0, "now": []}

    def _query_at(_root, _text, _top_k, _threshold):
        # `now` is what the index returns AT THIS MOMENT, for every question.
        # A test changes it between the two passes to stand in for a rebuild.
        calls["n"] += 1
        return [{"path": p, "score": 0.5} for p in calls["now"]]

    monkeypatch.setattr(bench, "query_at", _query_at)
    monkeypatch.setattr(
        bench, "load_truth",
        lambda *_a, **_k: {q: _Truth({"a.md"}) for q in bench.CROSSCHECK_QUESTIONS},
    )
    monkeypatch.setattr(bench, "get_outputs_dir", lambda: tmp_path / "outputs")
    monkeypatch.setattr(bench, "_run_state", lambda *_a, **_k: {"corpus_sha": "abc"})
    return calls


def _questions():
    return [{"id": q, "group": "g", "question_class": "c", "question_ru": f"q{q}"}
            for q in bench.CROSSCHECK_QUESTIONS]


def _answers(tmp_path, payload):
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _corpus():
    """A fixture corpus. Every oracle here is monkeypatched, so only `root` matters."""
    return bench.CorpusPaths(
        root=FIXTURE, threads=FIXTURE / "threads", crm=FIXTURE / "crm",
        context=FIXTURE / "context", auto_memory=FIXTURE / "auto-memory",
        knowledge=FIXTURE / "knowledge", outputs=FIXTURE / "outputs",
    )


def test_the_grading_pass_uses_the_hits_the_print_pass_showed(wired, tmp_path, capsys):
    """The whole defect, in one run.

    The print pass shows `a.md` — the one path the oracle wants — so the model
    can and does answer correctly. Between the two invocations the index changes
    and now returns nothing. Graded against the re-query the ceiling is 0.00 and
    a correct answer reads as РАСХОЖДЕНИЕ; graded against what was shown it is
    1.00 and predicts nothing, which is the truth.
    """
    wired["now"] = ["a.md"]
    corpus = _corpus()

    assert bench.mode_recall_crosscheck(
        _questions(), corpus, ROOT, TODAY, None) == 0

    wired["now"] = []          # the index rebuilt between the two invocations
    answers = _answers(tmp_path, {q: {"kind": "paths", "paths": ["a.md"]}
                                  for q in bench.CROSSCHECK_QUESTIONS})
    code = bench.mode_recall_crosscheck(_questions(), corpus, ROOT, TODAY, answers)
    out = capsys.readouterr().out

    assert "РАСХОЖДЕНИЕ" not in out, (
        "a correct answer was reported as refuting the ceiling, because the "
        "ceiling was recomputed against a re-query the model never saw"
    )
    assert code == 0


def test_a_real_contradiction_is_still_reported(wired, tmp_path, capsys):
    """The mutation guard. Reading the shown hits must not silence the finding.

    Here the print pass genuinely showed nothing, and the answer is still
    correct — the model cited what it could not have read. That is the outcome
    this mode exists to catch, and it must survive the fix.
    """
    wired["now"] = []
    corpus = _corpus()
    assert bench.mode_recall_crosscheck(
        _questions(), corpus, ROOT, TODAY, None) == 0

    answers = _answers(tmp_path, {q: {"kind": "paths", "paths": ["a.md"]}
                                  for q in bench.CROSSCHECK_QUESTIONS})
    code = bench.mode_recall_crosscheck(_questions(), corpus, ROOT, TODAY, answers)
    assert "РАСХОЖДЕНИЕ" in capsys.readouterr().out
    assert code == 1


def test_grading_without_a_print_pass_refuses(wired, tmp_path, capsys):
    """No shown-hits file means no honest ceiling. Refuse; do not re-query.

    Console-first: exits non-zero with a plain message naming the missing file
    and the command that writes it.
    """
    wired["now"] = ["a.md"]
    corpus = _corpus()
    answers = _answers(tmp_path, {q: {"refused": True}
                                  for q in bench.CROSSCHECK_QUESTIONS})
    code = bench.mode_recall_crosscheck(_questions(), corpus, ROOT, TODAY, answers)
    assert code == 2
    combined = capsys.readouterr()
    assert "--recall-crosscheck" in (combined.out + combined.err)
    assert wired["n"] == 0, "the grading pass queried the index anyway"


def test_a_changed_index_between_the_passes_is_reported(wired, tmp_path, capsys):
    """Grade on the shown hits, but say the corpus moved underneath."""
    wired["now"] = ["a.md"]
    corpus = _corpus()
    assert bench.mode_recall_crosscheck(
        _questions(), corpus, ROOT, TODAY, None) == 0

    monkey = {"corpus_sha": "def"}
    bench._run_state = lambda *_a, **_k: monkey
    answers = _answers(tmp_path, {q: {"kind": "paths", "paths": ["a.md"]}
                                  for q in bench.CROSSCHECK_QUESTIONS})
    bench.mode_recall_crosscheck(_questions(), corpus, ROOT, TODAY, answers)
    out = capsys.readouterr().out
    assert "corpus_sha" in out, "the state drift between the two passes was silent"
