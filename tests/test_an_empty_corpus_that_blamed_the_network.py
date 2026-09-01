#!/usr/bin/env python3
"""Shard scripts-03-p1: four exits that told the operator the wrong thing.

`census-submodel-bench.py --mode speed` on a corpus with no documents made NO
network call: `score_speed` refused every runner on its own empty-prompts guard.
`reached` stayed 0, so the run exited 3 — "every enabled runner was
unreachable". Nothing was unreachable; nothing was attempted. The docstring
assigns "no documents" to exit 2 and the accuracy path delivers it; the speed
path did not, and anyone reading exit 3 goes to look at the proxy.

The same `main` then threw away what it HAD measured: `empty_widths` returned 2
above `_write_report`, so an accuracy run where one width was empty and another
measured left no artefact at all, after paying for the calls.

`build_cases`'s fallback said it drew "the longest available" and iterated
`sorted(root.glob(...))`, which is path order, with both size filters switched
off by `min_len=0`. A slice could be failed as ВЫРОЖДЕН while the corpus held
documents long enough to measure it.

`census.py` promises in its module docstring that an exit-5 sandbox refusal is
recorded as `answer: None` "because the attempt happened and the acceptance file
is a record of attempts", carving out exactly one unrecorded exit (4). The
air-gap pre-check returned 5 before the record was ever written — a second,
unnamed unrecorded exit. And `append_answer` guarded the answers file against
being unreadable but not against being valid JSON of the wrong shape, so `[]`
raised TypeError and `{}` raised KeyError, neither caught, both losing the
record after a traversal that can run 180 seconds.

`check-build.py` checked that `build` and `version` EXIST and not what they are,
so one exec's `"build": "42"` took down the whole table on a TypeError.
`check-contract-gate.py` matched a suffix of the whole directory name, so a
contract for `bug-fix` answered FOUND for the plan `fix`.

Found by the 2026-08-23 engine audit, shard `scripts-03-p1`. Fixed 2026-08-24.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sub():
    return _load("p03p1_submodel", "census-submodel-bench.py")


@pytest.fixture(scope="module")
def gate():
    return _load("p03p1_gate", "check-contract-gate.py")


@pytest.fixture(scope="module")
def build():
    return _load("p03p1_build", "check-build.py")


@pytest.fixture(scope="module")
def census():
    return _load("p03p1_census", "census.py")


# ---------------------------------------------------------------------------
# Findings 1 and 5 -- the speed exit, and the results thrown out with it
# ---------------------------------------------------------------------------

def _run_bench(sub, monkeypatch, mode, cases_for, scores=None):
    """Drive `main` with the corpus and the transports faked out."""
    monkeypatch.setattr(sys, "argv", ["census-submodel-bench.py", mode])
    monkeypatch.setattr(sub, "_refuse_if_sensitive", lambda: None)
    monkeypatch.setattr(sub, "build_cases",
                        lambda width, marker, docs: cases_for(width))
    written = []
    monkeypatch.setattr(sub, "_write_report",
                        lambda report: written.append(report) or Path("report.json"))

    def _speed(runner, cases, marker):
        if not cases:
            return None
        return {"model": runner.model, "median_s": 1.0,
                "per_call_parallel_s": 1.0, "projected_200_s": 200.0}

    def _accuracy(runner, cases, marker):
        if not cases:
            return None
        return {"model": runner.model, "n": len(cases), "max": len(cases) * 3,
                "total": len(cases), "parsed_ok": len(cases), "wall_s": 1.0,
                "hits": {"field": 1, "checkboxes": 1, "mentions": 1}}

    monkeypatch.setattr(sub, "score_speed", scores or _speed)
    monkeypatch.setattr(sub, "score_accuracy", _accuracy)
    return sub.main(), written


def _case(sub, width):
    return sub.Case(path=Path("a.md"), text="x" * width, width=width,
                    actual_len=width, truth={"field": None, "checkboxes": 0,
                                             "mentions": False})


def test_an_empty_corpus_in_speed_mode_is_a_setup_error_not_an_outage(
        sub, monkeypatch, capsys):
    """Exit 3 says every runner was unreachable. None was called."""
    code, _ = _run_bench(sub, monkeypatch, "speed", lambda width: [])
    err = capsys.readouterr().err
    assert code == 2, err
    assert "не ответила" not in err, (
        "the run blamed the network for a corpus that held no documents"
    )
    assert "нет документов" in err


def test_an_empty_speed_corpus_never_calls_a_runner(sub, monkeypatch):
    """The reason exit 3 was wrong: there was no attempt to be unreachable."""
    called = []

    def _speed(runner, cases, marker):
        called.append(runner)
        return None

    _run_bench(sub, monkeypatch, "speed", lambda width: [], scores=_speed)
    assert called == [], "a runner was attempted on an empty corpus"


def test_a_populated_speed_corpus_still_exits_zero(sub, monkeypatch):
    """Anchor: returning 2 for every speed run would pass the two above."""
    code, written = _run_bench(sub, monkeypatch, "speed",
                               lambda width: [_case(sub, width)])
    assert code == 0
    assert written, "a successful run wrote no report"


def test_a_partial_accuracy_run_keeps_what_it_measured(sub, monkeypatch, capsys):
    """The exit stays 2 — a partial run is a setup error. What was wrong is
    that the cells which DID run were discarded with it, after paying for the
    model calls, because the return sat above `_write_report`."""
    widths = sub.SLICE_WIDTHS
    code, written = _run_bench(
        sub, monkeypatch, "accuracy",
        lambda width: [] if width == widths[-1] else [_case(sub, width)])
    assert code == 2
    assert written, "the measured widths were thrown away with the empty one"
    assert written[0]["accuracy"], "the report was written empty"
    assert "Частичный" in capsys.readouterr().err


def test_a_wholly_empty_accuracy_run_writes_nothing(sub, monkeypatch):
    """Anchor: a completed benchmark that measured nothing must leave no
    artefact reading as a result."""
    code, written = _run_bench(sub, monkeypatch, "accuracy", lambda width: [])
    assert code == 2
    assert not written


# ---------------------------------------------------------------------------
# Finding 2 -- "the longest available", by length
# ---------------------------------------------------------------------------

def test_the_fallback_takes_the_longest_not_the_alphabetically_first(
        sub, monkeypatch, tmp_path):
    """`sorted(root.glob(...))` is path order. A short `aaa.md` beat a long
    `zzz.md`, and the slice was then failed as ВЫРОЖДЕН over a corpus that
    could have filled it."""
    short = tmp_path / "aaa.md"
    short.write_text("x" * 100, encoding="utf-8")
    long = tmp_path / "zzz.md"
    long.write_text("y" * 5000, encoding="utf-8")

    def _candidates(min_len, want):
        return [] if min_len else [short, long]

    monkeypatch.setattr(sub, "_candidate_docs", _candidates)
    cases = sub.build_cases(1000, "MARKER", doc_count=1)
    assert [c.path for c in cases] == [long], (
        "the fallback filled the slice with the short document"
    )
    assert cases[0].filled, "the width was left degenerate for no reason"


def test_the_fallback_still_stops_at_doc_count(sub, monkeypatch, tmp_path):
    """Anchor: sorting must not turn the cap into a full corpus walk."""
    docs = []
    for i in range(5):
        p = tmp_path / f"{i}.md"
        p.write_text("z" * (1000 * (i + 1)), encoding="utf-8")
        docs.append(p)
    monkeypatch.setattr(sub, "_candidate_docs",
                        lambda min_len, want: [] if min_len else docs)
    cases = sub.build_cases(500, "MARKER", doc_count=2)
    assert len(cases) == 2


def test_an_unreadable_document_sorts_last_rather_than_raising(sub, tmp_path):
    assert sub._doc_length(tmp_path / "gone.md") == 0


def test_the_fallback_does_not_measure_one_document_twice(sub, monkeypatch,
                                                          tmp_path):
    """The dedupe between the primary loop and the fallback, unmeasured.

    Both loops draw from `_candidate_docs`, so a document long enough for the
    width is returned by BOTH: the primary at `min_len=width`, the fallback at
    `min_len=0`. Measured 2026-09-01, deleting `if any(c.path == path ...)` left
    the file green at 43 passed, because no fixture had a corpus the two loops
    could overlap on. A duplicated document is the same slice scored twice, and
    it also feeds `distinct_truth_fraction`, which exists to notice exactly that.
    """
    long = tmp_path / "long.md"
    long.write_text("y" * 5000, encoding="utf-8")
    short = tmp_path / "short.md"
    short.write_text("z" * 900, encoding="utf-8")

    # The real shape: the long document clears `min_len`, the short one does not.
    monkeypatch.setattr(
        sub, "_candidate_docs",
        lambda min_len, want: [p for p in (long, short)
                               if p.stat().st_size >= min_len][:want])
    cases = sub.build_cases(1000, "MARKER", doc_count=2)
    assert [c.path for c in cases] == [long, short], (
        "the fallback re-added the document the primary loop had already taken")


def test_the_fallback_survives_a_document_deleted_under_it(sub, monkeypatch,
                                                           tmp_path):
    """The `except OSError` in the fallback read, which its own comment says was
    the fix, and which nothing exercised.

    Measured 2026-09-01: changing that `except OSError` to `except
    ZeroDivisionError` left the file green at 43 passed. `_candidate_docs`
    stats the corpus and `build_cases` reads it afterwards, so a file removed in
    between is the ordinary race on a live workspace, not a contrived one.

    `doc_count=2` deliberately: the fallback breaks as soon as the slice is
    full, and `_doc_length` sorts the missing file LAST, so with `doc_count=1`
    the loop stops before it ever reaches the ghost and the mutation survives.
    That was this test's own first shape, and it measured nothing.
    """
    real = tmp_path / "real.md"
    real.write_text("y" * 5000, encoding="utf-8")
    ghost = tmp_path / "ghost.md"  # never created

    monkeypatch.setattr(sub, "_candidate_docs",
                        lambda min_len, want: [] if min_len else [ghost, real])
    cases = sub.build_cases(1000, "MARKER", doc_count=2)
    assert [c.path for c in cases] == [real], (
        "a document that vanished between the stat and the read took the whole "
        "run down instead of being skipped")


def test_the_fallback_pool_is_wider_than_the_slice_it_fills(sub, monkeypatch):
    """`doc_count * 4`, so the sort has something to choose between.

    The comment says the fallback "sorts what it already looked at" - but if the
    pool were only `doc_count` wide the sort would be sorting one element and the
    whole length ordering above would be decorative. Measured 2026-09-01,
    narrowing the pool to `doc_count` left the file green at 43 passed, because
    every fixture's fake `_candidate_docs` ignores `want`.

    This one honours `want`, so the requested width is what the assertion is
    about. The multiplier is written out by hand rather than read from the
    module.
    """
    seen = []

    def _candidates(min_len, want):
        seen.append((min_len, want))
        return []

    monkeypatch.setattr(sub, "_candidate_docs", _candidates)
    sub.build_cases(1000, "MARKER", doc_count=3)
    assert seen == [(1000, 3), (0, 12)], (
        "the fallback asked for a pool that was not four times the slice: "
        f"{seen}")


# ---------------------------------------------------------------------------
# Finding 4 -- an answers file of the wrong shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["[]", '["a"]', "{}", '{"answers": {}}',
                                  '{"answers": "x"}', '"a string"', "7"])
def test_a_valid_json_answers_file_of_the_wrong_shape_is_refused(census, text,
                                                                 tmp_path):
    """RuntimeError is what `main` catches and reports as exit 7. TypeError and
    KeyError are what these raised, and neither is caught anywhere."""
    path = tmp_path / "answers.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(RuntimeError) as caught:
        census.append_answer(path, {"answer": None}, "agg-01", {})
    assert "shape" in str(caught.value) or "cannot read" in str(caught.value)
    assert path.read_text(encoding="utf-8") == text, "it overwrote the file"


@pytest.mark.parametrize("text", ["{not json", "", "{\"answers\": [}", "\x00\x01"],
                         ids=["truncated", "empty", "unbalanced", "binary"])
def test_an_undecodable_answers_file_is_refused_rather_than_replaced(
        census, text, tmp_path):
    """The OTHER half of the guard the seven shape rows never reach.

    Every one of those rows is valid JSON, so all seven land on the SAME
    `isinstance` branch and the `json.JSONDecodeError` arm above it had no
    witness: measured 2026-09-01, narrowing `except (OSError, json.JSONDecodeError)`
    to `except (OSError,)` left this whole file green at 38 passed. The shape
    assertion below already accepts "cannot read", which is the message only
    this arm produces, so the file asserted an outcome nothing could reach.

    A half-written answers file is the realistic case, not the exotic one: this
    file is rewritten atomically after a traversal that may run 180 seconds.
    """
    path = tmp_path / "answers.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(RuntimeError) as caught:
        census.append_answer(path, {"answer": None}, "agg-01", {})
    assert "cannot read" in str(caught.value)
    assert path.read_text(encoding="utf-8") == text, "it overwrote the file"


def test_an_answers_path_that_cannot_be_opened_is_refused(census, tmp_path):
    """The OSError arm of the same `except`, which the JSON rows cannot reach.

    A directory sitting where the answers file belongs `exists()`, so the read
    is attempted and raises `IsADirectoryError`. Without this row the tuple
    could be narrowed to `json.JSONDecodeError` alone and nothing would notice.
    """
    path = tmp_path / "answers.json"
    path.mkdir()
    with pytest.raises(RuntimeError) as caught:
        census.append_answer(path, {"answer": None}, "agg-01", {})
    assert "cannot read" in str(caught.value)
    assert path.is_dir(), "it replaced the directory"


def test_a_well_shaped_answers_file_is_still_appended_to(census, tmp_path):
    """Anchor: refusing everything would pass every case above."""
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"schema_version": 1, "run_state": {},
                                "answers": []}), encoding="utf-8")
    census.append_answer(path, {"answer": 3}, "agg-01", {})
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [a["question_id"] for a in saved["answers"]] == ["agg-01"]


def test_a_missing_answers_file_is_created(census, tmp_path):
    path = tmp_path / "answers.json"
    census.append_answer(path, {"answer": 3}, "agg-01", {})
    assert json.loads(path.read_text(encoding="utf-8"))["answers"]


# ---------------------------------------------------------------------------
# Finding 3 -- the refusal that left no record
# ---------------------------------------------------------------------------

def test_an_air_gapped_scope_is_recorded_as_a_refusal(census, tmp_path,
                                                      monkeypatch, capsys):
    """The docstring promises every exit-5 refusal is recorded as
    `answer: None`, and names exactly ONE unrecorded exit: 4. This was a second
    one. A missing row reads to the scorer as "not answered", losing the
    reason the run was refused."""
    answers = tmp_path / "answers.json"
    program = tmp_path / "t.py"
    program.write_text("pass", encoding="utf-8")
    scope = tmp_path / "scope"
    scope.mkdir()

    monkeypatch.setattr(census, "resolve_corpus",
                        lambda scopes: ([scope], {}, None))
    monkeypatch.setattr(census, "air_gap_reason",
                        lambda path: "air-gapped path refused")
    monkeypatch.setattr(census, "run_state", lambda *a, **k: {})
    monkeypatch.setattr(census.CorpusPaths, "from_workspace",
                        staticmethod(lambda: type("C", (), {"root": tmp_path})()))

    code = census.main(["q", "--program", str(program), "--corpus", str(scope),
                        "--emit-answers", str(answers), "--question-id", "agg-01"])
    assert code == census.EXIT_SANDBOX_REFUSED
    assert answers.exists(), "the refusal left no row at all"
    saved = json.loads(answers.read_text(encoding="utf-8"))
    row = saved["answers"][0]
    assert row["question_id"] == "agg-01"
    assert row["answer"] is None
    assert "air-gapped" in row["error"]
    assert "air-gapped" in capsys.readouterr().err


def test_a_scope_that_is_not_air_gapped_is_not_refused(census, tmp_path,
                                                       monkeypatch):
    """Anchor: refusing every scope would pass the test above."""
    program = tmp_path / "t.py"
    program.write_text("pass", encoding="utf-8")
    scope = tmp_path / "scope"
    scope.mkdir()
    monkeypatch.setattr(census, "resolve_corpus",
                        lambda scopes: ([scope], {}, None))
    monkeypatch.setattr(census, "air_gap_reason", lambda path: None)
    monkeypatch.setattr(census, "refuse_if_corpus_fits_window",
                        lambda paths: "fits the window")
    code = census.main(["q", "--program", str(program), "--corpus", str(scope)])
    assert code == census.EXIT_CORPUS_FITS_WINDOW


# ---------------------------------------------------------------------------
# Finding 6 -- a build number that is not a number
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["42", None, [], {}, True, 1.5])
def test_a_non_integer_build_is_not_a_build(build, value):
    assert build._build_number(value) is None


def test_an_integer_build_is_taken_as_written(build):
    """Anchor: refusing everything would pass the test above, and 0 is a real
    build number that a truthiness check would drop."""
    assert build._build_number(7) == 7
    assert build._build_number(0) == 0


def test_the_comparison_still_reads_the_number(build):
    """Anchor: 0 must survive `_build_number` as a number, so the equal case is
    checked with a real reading rather than a truthiness one."""
    assert build.build_status(5, 5) == ("up to date", " ")
    assert build.build_status(build._build_number(0), 0) == ("up to date", " ")


@pytest.mark.parametrize("corp,ex,text,marker", [
    (5, 5, "up to date", " "),
    (0, 0, "up to date", " "),
    (6, 5, "1 build behind", " !"),
    (7, 5, "2 builds behind", " !"),
    (99, 5, "94 builds behind", " !"),
    (5, 6, "1 build ahead of corporate", " "),
    (5, 7, "2 builds ahead of corporate", " "),
])
def test_every_branch_of_build_status_is_pinned(build, corp, ex, text, marker):
    """All four branches, with the warning marker written out by hand.

    Only the EQUAL branch was measured. Measured 2026-09-01: dropping the `!`
    from "1 build behind" and from "N builds behind" each left the file green at
    43 passed, and neither AHEAD row existed at all - though `build_status`'s
    own docstring says the function was extracted "so the AHEAD case can be
    tested". The marker is the whole point of the comparison: it is what puts a
    row in front of the operator, and an exec ahead of corporate must not carry
    it, because being ahead is normally how the next corporate build starts.

    Expected values are written here, not derived from the function.
    """
    assert build.build_status(corp, ex) == (text, marker)


# ---------------------------------------------------------------------------
# Finding 7 -- one plan's contract satisfying another
# ---------------------------------------------------------------------------

def test_a_contract_for_another_slug_is_not_this_plans_contract(gate, tmp_path):
    """FOUND is the one signal here that means "this plan went through the
    gate". `endswith(f"-{slug}")` is a suffix of the WHOLE name, so any slug
    ending another one collected it."""
    (tmp_path / "2026-01-01-bug-fix").mkdir()
    status, detail = gate.check_gate("plans/2026-06-28-fix.md",
                                     contract_dir=tmp_path)
    assert status == "MISSING", f"{status} {detail}"


def test_the_plans_own_contract_is_still_found(gate, tmp_path):
    """Anchor: matching nothing would pass the test above."""
    (tmp_path / "2026-06-28-fix").mkdir()
    status, detail = gate.check_gate("plans/2026-06-28-fix.md",
                                     contract_dir=tmp_path)
    assert status == "FOUND"
    assert "2026-06-28-fix" in detail


def test_a_slug_carrying_a_dot_is_read_from_the_name_not_the_stem(gate, tmp_path):
    """The slug segment is taken off `name`, never off `Path.stem`.

    This test used the directory `2026-01-01-bug-fix` and could not tell the two
    apart: `-fix` is a HYPHEN, so that name has no suffix and
    `Path("2026-01-01-bug-fix").stem` is the whole string. Measured 2026-09-01,
    rewriting `_dir_slug` as `Path(name).stem[11:]` left the file green at 43
    passed - the one test named for the hazard did not bind it. The worked
    example in this docstring and in `_dir_slug`'s own said `.stem` was
    `2026-01-01-bug`, which is simply not what Python returns; both are
    corrected.

    A slug with a REAL dot separates them: `Path("2026-01-01-v1.2-fix").stem`
    is `2026-01-01-v1`, whose slug segment is `v1` rather than `v1.2-fix`. A
    version number in a slug is the ordinary way that dot arrives.
    """
    assert gate._dir_slug("2026-01-01-v1.2-fix") == "v1.2-fix", (
        "the slug was read off Path.stem, which truncates at the dot")
    (tmp_path / "2026-01-01-v1.2-fix").mkdir()
    status, _ = gate.check_gate("plans/2026-01-01-v1.2-fix.md",
                                contract_dir=tmp_path)
    assert status == "FOUND"


@pytest.mark.parametrize("name,expected", [
    ("2026-08-13-census-primitive", "census-primitive"),
    ("2026-08-05-foreign-recipe", "foreign-recipe"),
    ("2026-01-01-v1.2-fix", "v1.2-fix"),
    ("not-dated-at-all", None),
    ("2026-08-13-", None),
    ("20260813-slug", None),
    ("abcd-ef-gh-slug", None),
    ("2026-08-1x-slug", None),
    # One row ON each separator, because the three rows above refuse for the
    # WRONG reason: `20260813-slug` already fails `name[4] == "-"`, so deleting
    # the test at position 7 or at position 4 changed nothing. Measured
    # 2026-09-01, both deletions left the file green at 43 passed. Each name
    # below satisfies every part of the check except the one separator it is
    # named for, so it is that clause's sole witness.
    ("2026-08131-slug", None),   # separator at 7 missing; 4 and 10 present
    ("2026008-13-slug", None),   # separator at 4 missing; 7 and 10 present
    ("2026-08-13x-slug", None),  # separator at 10 missing; 4 and 7 present
])
def test_the_slug_segment_is_read_off_the_date_prefix(gate, name, expected):
    assert gate._dir_slug(name) == expected


def test_an_absent_contract_directory_is_never_FOUND(gate, tmp_path):
    """The branch that answers before anything is read.

    `check_gate`'s own comment says "FOUND is the one signal here that means
    'this plan went through the gate', so a wrong FOUND is the only reading
    worth preventing" - and the absent-directory exit, which is the one place
    a wrong FOUND would wave every plan through at once, had no test.
    Measured 2026-09-01: flipping that return to FOUND left the file green.
    """
    missing = tmp_path / "no-such-dir"
    status, detail = gate.check_gate("plans/2026-06-28-fix.md",
                                     contract_dir=missing)
    assert status == "MISSING", f"{status} {detail}"
    assert "absent" in detail, detail
    # A FILE where the directory belongs is not a directory either.
    as_file = tmp_path / "a-file"
    as_file.write_text("x", encoding="utf-8")
    assert gate.check_gate("plans/2026-06-28-fix.md",
                           contract_dir=as_file)[0] == "MISSING"


def test_an_undated_directory_never_matches(gate, tmp_path):
    (tmp_path / "fix").mkdir()
    status, _ = gate.check_gate("plans/2026-06-28-fix.md", contract_dir=tmp_path)
    assert status == "MISSING"
