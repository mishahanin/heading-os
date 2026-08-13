"""Grading /census answers: per class, with fabrication counted apart.

Two properties decide whether this scorer is worth anything.

**The class split is not cosmetic.** The build was scoped to the traversal
class, so a mean over all ten aggregate questions can report a pass while every
question the primitive exists for fails. The gate reads the class.

**A refusal is not a wrong answer.** A primitive that says "I could not do this"
is usable; one that says thirteen when the answer is four is not. The fabrication
column is what keeps those apart, and the acceptance rule rejects on the second
at any count.

The verdict function is pure so these can pin it without a corpus.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.census_oracles import OracleAnswer  # noqa: E402


def _has_populated_overlay() -> bool:
    """True when a private data overlay with real corpus content is present.

    Two tests here read the LIVE overlay on purpose - one asserts the real
    default scopes are not refused as too small, the other grades the real
    question set. On a bare public clone the overlay is absent, both fail, and
    the failure says nothing about the engine. The 2026-08-13 audit reproduced
    exactly that by pointing `HEADING_OS_DATA` at an empty tree.
    """
    try:
        from scripts.utils.census_oracles import CorpusPaths
        corpus = CorpusPaths.from_workspace()
    except Exception:  # noqa: BLE001 - an unresolvable overlay IS an absent one
        return False
    return any(d.is_dir() and any(d.glob("*.md"))
               for d in (corpus.threads, corpus.crm, corpus.context))


needs_overlay = pytest.mark.skipif(
    not _has_populated_overlay(),
    reason="needs a populated private data overlay (bare public clone)")


def _load():
    path = ROOT / "scripts" / "census-bench.py"
    spec = importlib.util.spec_from_file_location("census_bench_score", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bench = _load()

EXISTING = {"threads/business/a.md", "threads/business/b.md", "crm/contacts/c.md"}


def paths_truth(*paths):
    return OracleAnswer(kind="paths", paths=set(paths), value=len(paths))


def count_truth(n):
    return OracleAnswer(kind="count", value=n)


# ============================================================
# Grading one answer
# ============================================================

def test_a_matching_path_set_is_correct():
    record = {"answer": {"kind": "paths",
                         "paths": ["threads/business/a.md"],
                         "sources": ["threads/business/a.md"]}}
    status, _ = bench.grade_one(record, paths_truth("threads/business/a.md"), EXISTING)
    assert status == bench.STATUS_CORRECT


def test_a_wrong_path_set_is_wrong_not_fabricated():
    """Wrong about which real files qualify is an error, not an invention."""
    record = {"answer": {"kind": "paths",
                         "paths": ["threads/business/b.md"],
                         "sources": ["threads/business/b.md"]}}
    status, why = bench.grade_one(record, paths_truth("threads/business/a.md"), EXISTING)
    assert status == bench.STATUS_WRONG
    assert "oracle" in why


def test_citing_a_file_that_does_not_exist_is_confidently_wrong():
    record = {"answer": {"kind": "paths",
                         "paths": ["threads/business/invented.md"],
                         "sources": ["threads/business/invented.md"]}}
    status, why = bench.grade_one(record, paths_truth("threads/business/a.md"), EXISTING)
    assert status == bench.STATUS_CONFIDENTLY_WRONG
    assert "do not exist" in why


def test_a_right_answer_with_an_invented_citation_is_still_fabrication():
    """The citation is what makes an answer checkable; an uncheckable right
    answer is luck, and luck must not pass an acceptance gate."""
    record = {"answer": {"kind": "paths",
                         "paths": ["threads/business/a.md"],
                         "sources": ["threads/business/a.md", "threads/business/ghost.md"]}}
    status, _ = bench.grade_one(record, paths_truth("threads/business/a.md"), EXISTING)
    assert status == bench.STATUS_CONFIDENTLY_WRONG


def test_a_refusal_is_counted_apart_from_a_wrong_answer():
    status, why = bench.grade_one({"answer": None, "error": "sandbox refused"},
                                  paths_truth("threads/business/a.md"), EXISTING)
    assert status == bench.STATUS_REFUSED
    assert "sandbox refused" in why


def test_a_count_answers_a_which_ones_question_on_cardinality():
    record = {"answer": {"kind": "count", "value": 2,
                         "sources": ["threads/business/a.md"]}}
    status, _ = bench.grade_one(
        record, paths_truth("threads/business/a.md", "threads/business/b.md"), EXISTING)
    assert status == bench.STATUS_CORRECT


def test_a_mismatched_kind_is_wrong():
    record = {"answer": {"kind": "pairs", "pairs": [["a", "b"]],
                         "sources": ["threads/business/a.md"]}}
    status, why = bench.grade_one(record, count_truth(3), EXISTING)
    assert status == bench.STATUS_WRONG
    assert "does not answer" in why


# ============================================================
# The acceptance rule, pinned
# ============================================================

@pytest.mark.parametrize("correct,cw,expected", [
    (7, 0, bench.VERDICT_ACCEPTED),
    (6, 0, bench.VERDICT_ACCEPTED),
    (5, 0, bench.VERDICT_REJECTED),
    (7, 1, bench.VERDICT_REJECTED),
    (0, 0, bench.VERDICT_REJECTED),
])
def test_the_gate_reads_the_traversal_class(correct, cw, expected):
    verdict, _ = bench.acceptance_verdict(
        {"traversal": {"n": 7, "correct": correct},
         "cross_source": {"n": 3, "correct": 3}}, cw, True, [])
    assert verdict == expected


def test_cross_source_wins_cannot_carry_a_traversal_failure():
    """The exact failure the class split exists to prevent."""
    verdict, _ = bench.acceptance_verdict(
        {"traversal": {"n": 7, "correct": 3},
         "cross_source": {"n": 3, "correct": 3}}, 0, True, [])
    assert verdict == bench.VERDICT_REJECTED


def test_one_fabrication_rejects_even_a_perfect_traversal_score():
    verdict, why = bench.acceptance_verdict(
        {"traversal": {"n": 7, "correct": 7}}, 1, True, [])
    assert verdict == bench.VERDICT_REJECTED
    assert "honest refusal" in why


def test_a_moved_corpus_refuses_the_comparison_rather_than_reporting_one():
    verdict, why = bench.acceptance_verdict(
        {"traversal": {"n": 7, "correct": 7}}, 0, False, ["corpus_sha", "today"])
    assert verdict == bench.VERDICT_NOT_COMPARABLE
    assert "corpus_sha" in why


def test_the_thresholds_are_the_ones_written_down_before_the_run():
    """A pre-registered number that drifts is not pre-registered."""
    assert bench.ACCEPT_TRAVERSAL_AT_LEAST == 6
    assert bench.ACCEPT_TRAVERSAL_OF == 7
    assert bench.ACCEPT_CONFIDENTLY_WRONG_MAX == 0
    assert bench.GATED_CLASS == "traversal"


# ============================================================
# The two obligations step 1 left open
# ============================================================

def test_the_operating_point_is_recalls_shipped_defaults_not_saturation():
    assert bench.OPERATING_TOP_K == 8
    assert bench.OPERATING_THRESHOLD == 0.55
    assert bench.QUERY_DEPTH > bench.OPERATING_TOP_K
    assert bench.QUERY_THRESHOLD < bench.OPERATING_THRESHOLD


def test_the_crosscheck_names_a_control_among_its_questions():
    """A cross-check drawn only from aggregating questions cannot notice that
    the control group behaves differently at the operating point."""
    assert any(q.startswith("ctl-") for q in bench.CROSSCHECK_QUESTIONS)
    assert len(bench.CROSSCHECK_QUESTIONS) >= 3


# ============================================================
# Scoring a whole answers file
# ============================================================

@needs_overlay
def test_an_unanswered_question_counts_as_refused_not_as_missing(tmp_path,
                                                                 monkeypatch):
    """A question absent from the answers file must not vanish from the tally."""
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"schema_version": 1, "run_state": {},
                                   "answers": []}), encoding="utf-8")
    monkeypatch.setattr(bench, "_baseline_comparison", lambda _s: (None, True, []))
    report = bench.score_answers(str(answers))
    assert report["questions"]
    assert len(report["questions"]) == 15

    # Every question is accounted for in exactly one of two places: the graded
    # tally, or the named not-scored list. A question in neither has vanished,
    # which is the failure this test exists to catch.
    graded = [r for r in report["questions"] if r["status"] != bench.STATUS_NOT_SCORED]
    assert all(r["status"] == bench.STATUS_REFUSED for r in graded)
    total = sum(b["n"] for b in report["per_class"].values())
    assert total == len(graded)
    assert total + len(report["not_scored"]) == 15
    assert report["not_scored"], "the withheld class must be named, never silent"
    assert all(r["why"] for r in report["questions"]
               if r["status"] == bench.STATUS_NOT_SCORED)


# ============================================================
# The corpus-drift defect found on 2026-08-13
# ============================================================

def test_the_corpus_digest_moves_when_a_corpus_file_changes(tmp_path):
    """`corpus_sha` is a commit; the acceptance needs the bytes.

    A `/thread log` written between two scoring runs moved an oracle's truth from
    8 threads to 9 - the log text named a country - while `corpus_sha` stayed
    byte-identical, `states_comparable` reported True, and a correct answer was
    graded wrong. The guard whose only job is that comparison said nothing.
    """
    from scripts.utils.census_state import corpus_digest

    scope = tmp_path / "context"
    scope.mkdir()
    (scope / "a.md").write_text("one\n", encoding="utf-8")
    before = corpus_digest(tmp_path)

    (scope / "a.md").write_text("one and Russia\n", encoding="utf-8")
    assert corpus_digest(tmp_path) != before, "an edited corpus file must move the digest"

    (scope / "a.md").write_text("one\n", encoding="utf-8")
    assert corpus_digest(tmp_path) == before, "restoring the bytes must restore the digest"

    (scope / "b.md").write_text("two\n", encoding="utf-8")
    assert corpus_digest(tmp_path) != before, "an added corpus file must move the digest"


def test_the_digest_ignores_rebuilt_artefacts_not_corpus_content(tmp_path):
    """A rebuilt index is not a corpus change; `.memory-index` is excluded."""
    from scripts.utils.census_state import corpus_digest

    scope = tmp_path / "context"
    scope.mkdir()
    (scope / "a.md").write_text("one\n", encoding="utf-8")
    before = corpus_digest(tmp_path)

    idx = scope / ".memory-index"
    idx.mkdir()
    (idx / "manifest.json").write_text('{"built": "now"}', encoding="utf-8")
    assert corpus_digest(tmp_path) == before


def test_the_benchmarks_own_report_does_not_move_the_digest(tmp_path):
    """The instrument must not perturb its own measurement.

    The first digest hashed the whole data overlay, `outputs/` included - which
    is where this benchmark writes its reports. `--baseline` wrote a report, the
    digest moved, and the scoring run that followed was refused as
    NOT-COMPARABLE against the baseline it had just produced. Two runs were burnt
    on it before the cause was found.
    """
    from scripts.utils.census_state import corpus_digest

    scope = tmp_path / "context"
    scope.mkdir()
    (scope / "a.md").write_text("one\n", encoding="utf-8")
    before = corpus_digest(tmp_path)

    reports = tmp_path / "outputs" / "operations" / "census-bench"
    reports.mkdir(parents=True)
    (reports / "2026-08-13_bench_census-baseline.md").write_text(
        "# a report the benchmark just wrote\n", encoding="utf-8")
    assert corpus_digest(tmp_path) == before, (
        "a benchmark report must not count as a corpus change")


def test_the_digest_tells_an_absent_scope_from_an_empty_one(tmp_path):
    """"Gone" and "empty" are different corpora, so they hash differently."""
    from scripts.utils.census_state import corpus_digest

    absent = corpus_digest(tmp_path)
    (tmp_path / "context").mkdir()
    assert corpus_digest(tmp_path) != absent


def test_two_runs_over_a_moved_corpus_are_not_comparable():
    """The pin exists to REFUSE, so a divergence must be reported by name."""
    from scripts.utils.census_state import ORACLE_PINS, states_comparable

    a = {"corpus_sha": "abc", "corpus_content_sha256": "1111", "today": "2026-08-13"}
    b = {"corpus_sha": "abc", "corpus_content_sha256": "2222", "today": "2026-08-13"}
    ok, diverged = states_comparable(a, b, ORACLE_PINS)
    assert not ok
    assert diverged == ["corpus_content_sha256"]
