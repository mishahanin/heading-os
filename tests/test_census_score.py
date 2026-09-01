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
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import census_state as census_state_module  # noqa: E402
from scripts.utils.census_oracles import OracleAnswer  # noqa: E402


def _has_populated_overlay() -> bool:
    """True when a private data overlay with real corpus content is present.

    Two tests here read the LIVE overlay on purpose - one asserts the real
    default scopes are not refused as too small, the other grades the real
    question set. On a bare public clone the overlay is absent, both fail, and
    the failure says nothing about the engine. The 2026-08-13 audit reproduced
    exactly that by pointing `HEADING_OS_DATA` at an empty tree.

    An EMPTY tree was the wrong shape to rehearse, and testing only that shape
    is why this guard shipped broken. A bare clone does not resolve to nothing:
    `get_data_root()` falls back to the engine's own bundled `examples/`, which
    ships one demo thread. One populated directory satisfied the content check
    below, so the guard said "overlay present" and the grader then ran against
    the engine's demo files, where the oracle raised `UnreadableCorpus` on a
    demo thread that carries no frontmatter. Ask the seam that already answers
    this precisely: `data_overlay_present()` is False for a demo root AND for an
    engine clone wearing a data root, True only for a real sibling or an
    explicit `HEADING_OS_DATA`.
    """
    try:
        from scripts.utils.census_oracles import CorpusPaths
        from scripts.utils.paths import data_overlay_present
        if not data_overlay_present():
            return False
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


def test_an_invented_path_is_fabrication_even_when_every_citation_is_real():
    """`paths` is checked for invention too, and only `sources` was covered.

    Both fabrication cases above happen to cite the invented file in `sources`
    as well, so the `invented += [... _seq("paths") ...]` line never decided
    anything. Mutation-confirmed 2026-09-01: deleting that line left this file
    green at 23 passed. The shape it lets through is the realistic one - the
    answer names a file that does not exist while every citation is a file that
    does, which reads as a well-sourced answer and is an invention.
    """
    record = {"answer": {"kind": "paths",
                         "paths": ["threads/business/invented.md"],
                         "sources": ["threads/business/a.md"]}}
    status, why = bench.grade_one(record, paths_truth("threads/business/a.md"), EXISTING)
    assert status == bench.STATUS_CONFIDENTLY_WRONG, (
        f"an answer naming a file that does not exist was graded {status}"
    )
    assert "do not exist" in why
    assert "invented.md" in why


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


@pytest.mark.parametrize("n", [6, 8, 0])
def test_a_gated_class_of_the_wrong_size_refuses_the_rule(n):
    """The pre-registered DENOMINATOR, which every other case here holds at 7.

    "6 of 7" stops meaning what was written down the moment a question joins or
    leaves the gated class, and the rule refuses rather than re-scaling itself.
    Nothing stood anywhere but 7: mutation-confirmed 2026-09-01, replacing
    `if n != ACCEPT_TRAVERSAL_OF` with `if False` left this file green at 23
    passed, so a class of six answered ACCEPTED on six correct and a class of
    zero answered REJECTED as though it had been measured.
    """
    verdict, why = bench.acceptance_verdict(
        {"traversal": {"n": n, "correct": n}}, 0, True, [])
    assert verdict == bench.VERDICT_NOT_COMPARABLE, (
        f"a gated class of {n} was graded against a rule pre-registered for "
        f"{bench.ACCEPT_TRAVERSAL_OF}: {verdict}"
    )
    assert str(n) in why and str(bench.ACCEPT_TRAVERSAL_OF) in why


def test_a_gated_class_that_is_absent_entirely_refuses_the_rule():
    """No `traversal` bucket at all is n=0, and must not read as a clean sweep."""
    verdict, _ = bench.acceptance_verdict({"cross_source": {"n": 3, "correct": 3}},
                                          0, True, [])
    assert verdict == bench.VERDICT_NOT_COMPARABLE


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


def test_a_renamed_corpus_file_moves_the_digest(tmp_path):
    """The digest covers the PATH, not only the bytes, and nothing said so.

    Every case above changes content, so the line that hashes each relative path
    decided nothing. Mutation-confirmed 2026-09-01: deleting it left this file
    green at 23 passed. A rename is exactly the move this digest exists to
    catch - several oracles answer with PATHS, so renaming one thread changes
    their truth while every byte of content stays where it was, which is the
    2026-08-13 defect wearing a different hat.
    """
    from scripts.utils.census_state import corpus_digest

    scope = tmp_path / "context"
    scope.mkdir()
    (scope / "a.md").write_text("one\n", encoding="utf-8")
    before = corpus_digest(tmp_path)

    (scope / "a.md").rename(scope / "b.md")
    assert corpus_digest(tmp_path) != before, (
        "renaming a corpus file left the digest unmoved, so a run whose "
        "path-valued truths all changed would be reported as comparable"
    )


# Written out rather than read from `CORPUS_SUFFIXES`, and that is the whole
# point. Parametrising over the constant was tried first and measured USELESS on
# 2026-09-01: narrowing the tuple to `(".md",)` shrank the case list with it, so
# the mutant deleted its own coverage and the file stayed green at 31 passed.
# A guard derived from the thing it guards cannot notice the thing shrinking.
DIGESTED_SUFFIXES = (".md", ".json", ".yaml", ".yml", ".txt")


def test_the_digested_suffix_set_is_the_one_written_down():
    """A suffix leaving the tuple must break something, and here it is."""
    assert census_state_module.CORPUS_SUFFIXES == DIGESTED_SUFFIXES


@pytest.mark.parametrize("suffix", DIGESTED_SUFFIXES)
def test_every_declared_corpus_suffix_moves_the_digest(tmp_path, suffix):
    """`CORPUS_SUFFIXES` names five extensions and only `.md` was exercised.

    Mutation-confirmed 2026-09-01: narrowing the tuple to `(".md",)` left this
    file green at 23 passed, so an edited `.json`, `.yaml`, `.yml` or `.txt`
    corpus file would have been invisible to the comparison the acceptance rests
    on.
    """
    from scripts.utils.census_state import corpus_digest

    scope = tmp_path / "context"
    scope.mkdir()
    before = corpus_digest(tmp_path)
    (scope / f"record{suffix}").write_text("one\n", encoding="utf-8")
    assert corpus_digest(tmp_path) != before, (
        f"a {suffix} corpus file did not move the digest"
    )


def test_a_suffix_outside_the_declared_set_leaves_the_digest_alone(tmp_path):
    """The other direction, or the case above is satisfied by hashing everything.

    A rendered PDF or a screenshot dropped beside a note is not corpus content,
    and hashing it would refuse comparisons over a file no oracle reads.
    """
    from scripts.utils.census_state import corpus_digest

    scope = tmp_path / "context"
    scope.mkdir()
    before = corpus_digest(tmp_path)
    (scope / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\n not corpus content")
    assert corpus_digest(tmp_path) == before, (
        "a .png moved the digest, so the suffix filter is not filtering"
    )


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root reads a mode-000 file, so the trap cannot be set")
def test_a_file_the_walk_cannot_read_is_not_hashed_as_an_empty_one(tmp_path):
    """An unreadable file changes the digest, as `corpus_digest` promises.

    Its own comment says silence there "would report two different corpora as
    the same one", and nothing tested it: mutation-confirmed 2026-09-01,
    replacing the `<unreadable>` marker with `pass` left this file green at 23
    passed. The two corpora built below are exactly the pair that would then
    collide - one holding an empty readable file, the other a file with content
    nobody can read.
    """
    from scripts.utils.census_state import corpus_digest

    empty = tmp_path / "empty-corpus"
    (empty / "context").mkdir(parents=True)
    (empty / "context" / "a.md").write_text("", encoding="utf-8")

    locked = tmp_path / "locked-corpus"
    (locked / "context").mkdir(parents=True)
    secret = locked / "context" / "a.md"
    secret.write_text("one and Russia\n", encoding="utf-8")
    secret.chmod(0o000)
    try:
        assert secret.is_file(), "the fixture stopped being a file"
        with pytest.raises(OSError):
            secret.read_bytes()
        assert corpus_digest(locked) != corpus_digest(empty), (
            "a file the walk could not read hashed identically to an empty one, "
            "so two different corpora would be reported as the same corpus"
        )
    finally:
        secret.chmod(0o600)


def test_the_digest_tells_an_absent_scope_from_an_empty_one(tmp_path):
    """"Gone" and "empty" are different corpora, so they hash differently."""
    from scripts.utils.census_state import corpus_digest

    absent = corpus_digest(tmp_path)
    (tmp_path / "context").mkdir()
    assert corpus_digest(tmp_path) != absent


# ============================================================
# The question set the whole gate is loaded from
# ============================================================

@pytest.mark.parametrize("payload,label", [
    (b'{"questions": [{"id": "a", "group": "aggregate"}]}\xe9', "a byte that is not UTF-8"),
    (b'{"questions": [', "JSON that stops mid-file"),
    (b'{"other": []}', "an object with no questions key"),
])
def test_an_unreadable_question_set_refuses_instead_of_tracebacking(
    tmp_path, monkeypatch, capsys, payload, label
):
    """`main()` documents exit 2 for a question set it cannot read.

    The handler around `load_questions` enumerated `FileNotFoundError`,
    `KeyError` and `json.JSONDecodeError`, which is every corrupt-file shape
    but one: `UnicodeDecodeError` is a `ValueError` and a SIBLING of
    `JSONDecodeError`, not a subclass, and the decode fails inside
    `path.read_text` before `json.loads` is ever called. It also missed plain
    `OSError`, so an unreadable-but-present file took the same path.

    MEASURED 2026-09-01 against the unfixed code, driving `main()` with a
    question set carrying one 0xe9: `UnicodeDecodeError` propagated out of
    `main()` uncaught, so the CLI printed a traceback naming a codec, a byte
    and an offset - and no path - and exited 1. This is the acceptance gate:
    exit 1 is a real verdict here (`mode_score` returns it for REJECTED), so a
    harness reading exit codes scores the crash as a measurement.

    `load_questions` runs BEFORE the wide try below it in `main()`, which
    already catches `ValueError` and `OSError`; only this earlier call site was
    narrow.
    """
    config = tmp_path / "config"
    config.mkdir()
    (config / "census-bench-questions.json").write_bytes(payload)
    monkeypatch.setattr(bench, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["census-bench.py", "--show-truth"])

    assert bench.main() == 2, f"{label} did not produce the documented exit 2"
    assert "не читается" in capsys.readouterr().err


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root reads a mode-000 file, so the trap cannot be set")
def test_a_question_set_that_exists_but_cannot_be_opened_refuses_too(
    tmp_path, monkeypatch, capsys
):
    """`path.exists()` answers True and the read still fails. Same exit."""
    config = tmp_path / "config"
    config.mkdir()
    questions = config / "census-bench-questions.json"
    questions.write_text('{"questions": []}', encoding="utf-8")
    questions.chmod(0o000)
    monkeypatch.setattr(bench, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["census-bench.py", "--show-truth"])
    try:
        assert bench.main() == 2
        assert "не читается" in capsys.readouterr().err
    finally:
        questions.chmod(0o600)


def test_two_runs_over_a_moved_corpus_are_not_comparable():
    """The pin exists to REFUSE, so a divergence must be reported by name."""
    from scripts.utils.census_state import ORACLE_PINS, states_comparable

    a = {"corpus_sha": "abc", "corpus_content_sha256": "1111", "today": "2026-08-13"}
    b = {"corpus_sha": "abc", "corpus_content_sha256": "2222", "today": "2026-08-13"}
    ok, diverged = states_comparable(a, b, ORACLE_PINS)
    assert not ok
    assert diverged == ["corpus_content_sha256"]
