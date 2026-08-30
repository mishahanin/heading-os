"""An acceptance gate that can be wrong about its own inputs.

Shard `scripts-04-p1` of the 2026-08-23/24 engine audit. Six files, and the
worst of them sit on the /census acceptance path -- the one place in this
workspace whose entire job is to decide whether a measurement can be believed.

  - `--score answers.json --today 1999-01-01` graded against a different date
    and said nothing: the oracles are date-sensitive, so the verdict was
    computed against the wrong truth;
  - the whole scoring path ran OUTSIDE main()'s error handling, so a realistic
    LLM-emitted `"sources": null` was a traceback and exit 1 rather than the
    documented exit 2;
  - a `subprocess.TimeoutExpired` is not a `RuntimeError`, so the 600-second
    and 300-second query timeouts escaped every handler in the file;
  - the submodel benchmark scored 0/0 on an empty corpus, wrote a report, and
    exited 0.

Findings covered (numbering from `/tmp/audit_out3/scripts-04-p1.md`):

  census-bench          1  --today ignored in --score mode
                        2  TimeoutExpired escaped --baseline
                        3  query_at failures crashed --recall-crosscheck
                        4  --score ran outside main()'s try; null fields
                        5  --no-write honoured only by --baseline
                        6  a missing --crosscheck-answers file crashed
                        7  a duplicated hit recorded the WORST rank
                        8  a malformed --today crashed outside the try
                        9  a question with no text became `None` in argv
                       11  --crosscheck-answers silently ignored
  census-submodel-bench 1  score_speed IndexError on an empty corpus
                        2  the parallel batch was unguarded
                        3  an empty slice scored 0/0 and exited 0
                        4  REFUTED -- already fixed 2026-08-23, see the test
                        5  the fallback read lacked its OSError guard
                        6  the speed path never checked its slice was a slice
  census                1  append_answer failure escaped main()
                        2  drift was computed before the dedup
                        3  corpus_bytes had an unhandled stat() TOCTOU
                        4  two scopes could collide onto one mount name
  check-build           1  an exec AHEAD printed "-2 builds behind !"
                        3  load_json swallowed only two exception types
                        5  a future timestamp printed "just now"
  check-contract-gate   1  the slug was interpolated into a glob pattern
  check-path-references 1  check=True contradicted the documented fail-soft
                        2  git output split on spaces lost filenames
"""

import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.workspace import data_root_is_demo  # noqa: E402

# On a clone with no private overlay `get_data_root()` falls back to the
# engine's own bundled `examples/`, and the oracles refuse that corpus by
# design: one demo thread carries no YAML frontmatter, so no ground truth can
# be computed for any question. A grade is then not merely wrong, it does not
# exist, and any assertion about the SHAPE of a grade is measuring a world that
# is not there. The refusal itself is still measured, by
# `test_an_unparseable_corpus_file_is_exit_two_not_a_traceback` below, which
# runs in both worlds.
needs_gradable_corpus = pytest.mark.skipif(
    data_root_is_demo(),
    reason="the bundled examples/ corpus yields no oracle truth, so the grader "
           "is never reached and its exit code cannot be read")


def _code_only(path: Path) -> str:
    """Source with comment lines removed; every fix quotes what it removed."""
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def test_the_comment_stripper_keeps_the_code(tmp_path):
    f = tmp_path / "s.py"
    f.write_text("# args.limit or 25\nx = 1\n", encoding="utf-8")
    out = _code_only(f)
    assert "x = 1" in out
    assert "args.limit or 25" not in out


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cb = _load("census_bench_p4a", "scripts/census-bench.py")
csb = _load("census_submodel_bench_p4a", "scripts/census-submodel-bench.py")
cen = _load("census_p4a", "scripts/census.py")
chb = _load("check_build_p4a", "scripts/check-build.py")
ccg = _load("check_contract_gate_p4a", "scripts/check-contract-gate.py")
cpr = _load("check_path_references_p4a", "scripts/check-path-references.py")


def _bench(*argv):
    return subprocess.run(
        [sys.executable, "scripts/census-bench.py", *argv],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300,
    )


# ============================================================
# census-bench 4, 8, 11 - the scoring path answers cleanly
# ============================================================

@needs_gradable_corpus
def test_a_null_sources_field_is_graded_not_a_traceback(tmp_path):
    """`answer.get("sources", [])` returns the DEFAULT only when the key is
    absent. `"sources": null` returned None, and `set()` refused it -- on the
    acceptance path, with a traceback and exit 1."""
    answers = tmp_path / "a.json"
    answers.write_text(json.dumps({"answers": [
        {"question_id": "agg-01",
         "answer": {"kind": "paths", "paths": None, "sources": None}},
    ]}), encoding="utf-8")

    proc = _bench("--score", str(answers), "--no-write")
    assert proc.returncode in (0, 1), proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr


@needs_gradable_corpus
def test_an_answer_that_is_not_an_object_is_graded_not_a_traceback(tmp_path):
    """"Graded" is the claim, so the exit code has to say grading happened.

    Without the marker and the returncode assertion this passed on any clone
    with no private data overlay: the grader refuses at exit 2 before it grades
    anything, prints no traceback, and the test called that a pass. The sibling
    directly above already carries both.
    """
    answers = tmp_path / "a.json"
    answers.write_text(json.dumps({"answers": [
        {"question_id": "agg-01", "answer": "not an object"},
    ]}), encoding="utf-8")

    proc = _bench("--score", str(answers), "--no-write")
    assert proc.returncode in (0, 1), proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr


def test_an_unparseable_corpus_file_is_exit_two_not_a_traceback(monkeypatch, tmp_path, capsys):
    """`UnreadableCorpus` subclasses RuntimeError, and RuntimeError was in no
    branch of main()'s except chain. The oracles raise it deliberately, so the
    one refusal they are built to report arrived as a traceback and exit 1, on
    the acceptance path, in every mode: they all call `load_truth`."""
    answers = tmp_path / "a.json"
    answers.write_text(json.dumps({"answers": []}), encoding="utf-8")

    def refuse(questions, corpus, today):
        raise cb.UnreadableCorpus("EXAMPLE-thread.md: missing YAML frontmatter")

    monkeypatch.setattr(cb, "load_truth", refuse)
    monkeypatch.setattr(sys, "argv",
                        ["census-bench.py", "--score", str(answers), "--no-write"])
    assert cb.main() == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "missing YAML frontmatter" in err, "the refusal was reported without its cause"


def test_a_malformed_answers_file_is_exit_two_not_a_traceback(tmp_path):
    answers = tmp_path / "a.json"
    answers.write_text("{not json", encoding="utf-8")
    proc = _bench("--score", str(answers), "--no-write")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr


def test_a_malformed_today_is_exit_two_not_a_traceback():
    proc = _bench("--baseline", "--today", "not-a-date")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr


def test_crosscheck_answers_without_its_mode_is_refused():
    """It parsed happily beside --baseline and was then never read."""
    # A path that is never opened: the point is that argparse refuses the
    # combination before anything reads it.
    proc = _bench("--baseline", "--crosscheck-answers", "no/such/file.json")
    assert proc.returncode == 2
    assert "--recall-crosscheck" in proc.stderr


# ============================================================
# census-bench 1 - --today reaches the grader
# ============================================================

def test_score_answers_honours_the_today_it_is_given(monkeypatch, tmp_path):
    """The oracles take `today`; `mode_score` never passed it on."""
    answers = tmp_path / "a.json"
    answers.write_text(json.dumps({"answers": []}), encoding="utf-8")

    seen = {}

    def spy_load_truth(questions, corpus, today):
        seen["today"] = today
        return {}

    monkeypatch.setattr(cb, "load_truth", spy_load_truth)
    cb.score_answers(str(answers), date(1999, 1, 1))
    assert seen["today"] == date(1999, 1, 1)


def test_mode_score_passes_today_through(monkeypatch, tmp_path):
    answers = tmp_path / "a.json"
    answers.write_text(json.dumps({"answers": []}), encoding="utf-8")
    seen = {}

    def spy_score(path, today=None):
        seen["today"] = today
        return {"questions": [], "per_class": {}, "latency_median_s": None,
                "verdict": "NOT-COMPARABLE", "verdict_why": "", "verdict_rule": "",
                "not_scored": []}

    monkeypatch.setattr(cb, "score_answers", spy_score)
    cb.mode_score(str(answers), date(1999, 1, 1), write=False)
    assert seen["today"] == date(1999, 1, 1)


# ============================================================
# census-bench 2, 3 - a timeout is a query failure
# ============================================================

def test_a_subprocess_timeout_counts_as_a_query_failure():
    """`TimeoutExpired` is a `SubprocessError`, not a `RuntimeError`. Every
    handler in the file caught the wrong branch of the hierarchy, so the 600s
    and 300s timeouts escaped as tracebacks."""
    assert issubclass(subprocess.TimeoutExpired, cb.QUERY_FAILURES)
    assert issubclass(RuntimeError, cb.QUERY_FAILURES)
    assert issubclass(json.JSONDecodeError, cb.QUERY_FAILURES)
    assert issubclass(OSError, cb.QUERY_FAILURES)


def test_every_query_call_site_uses_the_shared_tuple():
    """Three sites, one tuple. They had three different answers, and the one
    with no try at all was the crosscheck print pass."""
    body = _code_only(ROOT / "scripts" / "census-bench.py")
    assert body.count("except QUERY_FAILURES") == 3
    assert "except RuntimeError as exc:" not in body


# ============================================================
# census-bench 5 - --no-write means no write
# ============================================================

@needs_gradable_corpus
def test_no_write_suppresses_the_score_report(tmp_path):
    """`--no-write` reached only `--baseline`; three other modes wrote regardless.

    Fixed 2026-08-30 on two counts.

    It was missing the `@needs_gradable_corpus` marker its two `--score`
    siblings carry, and it is the ONLY guard for that finding. On a clone with
    no private overlay - a clean CI checkout, i.e. the one environment where
    this always executes - `load_truth` refuses the bundled `examples/` corpus
    and exits 2 before any write path runs, so `"Отчёт:"` is absent whether or
    not `--no-write` is honoured, and the assertion passes over a world where
    the grader was never reached.

    The marker alone is not enough: an absence assertion needs proof the thing
    that would have printed actually ran. `mode_score` prints its results table
    BEFORE it decides whether to write, so the header below is the evidence the
    grader executed. Without it, "no report" and "no run" are the same string.
    """
    answers = tmp_path / "a.json"
    answers.write_text(json.dumps({"answers": []}), encoding="utf-8")

    proc = _bench("--score", str(answers), "--no-write")

    assert proc.returncode != 2, (
        f"the grader refused the corpus instead of grading it:\n{proc.stderr}")
    assert "причина" in proc.stdout, (
        "mode_score never printed its results table, so this run reached no "
        f"write path and proves nothing about --no-write:\n{proc.stdout}")
    assert "Отчёт:" not in proc.stdout, "--no-write still wrote a report"


def test_every_mode_receives_the_no_write_flag():
    """It reached only --baseline; three other modes wrote regardless."""
    body = _code_only(ROOT / "scripts" / "census-bench.py")
    assert body.count("write=not args.no_write") == 4


# ============================================================
# census-bench 7 - the best rank, not the worst
# ============================================================

def test_a_duplicated_hit_records_its_first_position(monkeypatch):
    """A dict comprehension keeps the LAST index, so a truth path returned
    twice was recorded at its worse rank -- understating every ceiling and
    k_min_full derived from it."""
    hits = [{"path": "a.md"}, {"path": "b.md"}, {"path": "a.md"}]
    monkeypatch.setattr(cb, "query_index", lambda root, text: (hits, 0.01))

    answer = cb.OracleAnswer(kind="paths", paths={"a.md"}, value=None)
    result = cb.measure_question(
        ROOT, {"id": "q1", "group": "aggregate", "question_ru": "q",
               "question_class": "traversal"}, answer)
    assert result.ranks["a.md"] == 0, "the duplicate overwrote the best rank"


# ============================================================
# census-bench 9 - a question with no text
# ============================================================

def test_a_question_with_no_text_is_refused_not_passed_to_argv(monkeypatch):
    """`None` went straight into a subprocess argv and raised TypeError.
    `measure_question` already guarded this; the crosscheck path did not."""
    called = []
    monkeypatch.setattr(cb, "query_at", lambda *a, **kw: called.append(a) or [])
    monkeypatch.setattr(cb, "load_truth", lambda q, c, t: {
        qid: cb.OracleAnswer(kind="paths", paths=set(), value=None)
        for qid in cb.CROSSCHECK_QUESTIONS})

    questions = [{"id": qid, "group": "aggregate"} for qid in cb.CROSSCHECK_QUESTIONS]
    with pytest.raises(ValueError, match="no question text"):
        cb.mode_recall_crosscheck(questions, cb.CorpusPaths.from_workspace(),
                                  ROOT, date(2026, 8, 24), None, write=False)
    assert called == [], "a None question text reached the query"


# ============================================================
# census-submodel-bench 1, 2, 3, 5, 6
# ============================================================

def test_the_speed_path_refuses_an_empty_corpus(capsys):
    """`prompts[0]` on an empty case list raised IndexError, uncaught, where
    the docstring promises exit 2 for "no documents"."""
    runner = csb.RUNNERS[0]
    assert csb.score_speed(runner, [], "marker") is None
    assert "нет документов" in capsys.readouterr().out


def test_the_parallel_batch_is_guarded():
    """The warmup and the serial loop both caught the transport tuple; the
    parallel batch sat outside any try and lost every collected result."""
    body = _code_only(ROOT / "scripts" / "census-submodel-bench.py")
    assert body.count("except TRANSPORT_FAILURES") >= 4


def test_the_transport_tuple_covers_what_a_model_call_raises():
    for exc in (TimeoutError, OSError, ValueError, KeyError):
        assert issubclass(exc, csb.TRANSPORT_FAILURES)


def test_the_fallback_document_read_is_guarded():
    """The primary loop guarded OSError; the under-sampling fallback did not."""
    body = _code_only(ROOT / "scripts" / "census-submodel-bench.py")
    build = body.split("def build_cases", 1)[1].split("\ndef ", 1)[0]
    assert build.count("except OSError") == 2


def test_the_planted_marker_does_not_inflate_actual_len(tmp_path):
    """FINDING 4, REFUTED -- and pinned so it stays that way.

    The audit reported that `_plant` appends ~33 characters before `len(text)`
    is recorded, so a slice short of the width reported `filled`. It does not:
    `_case` measures `len(sliced)`, the pre-plant slice, and has done since
    2026-08-23. That is the exact signal `--dry-run` reads to fail a degenerate
    width, so it is worth a test rather than a reading.
    """
    width = 100
    sliced = "x" * (width - 10)
    case = csb._case(tmp_path / "d.md", sliced, width, "zzq-probe", index=0)
    assert case.text != sliced, "index 0 should have been planted"
    assert case.actual_len == len(sliced)
    assert case.filled is False, "the planted marker counted toward the width"


def test_a_slice_that_does_reach_the_width_is_filled(tmp_path):
    case = csb._case(tmp_path / "d.md", "x" * 100, 100, "zzq-probe", index=0)
    assert case.filled is True


# ============================================================
# census 1, 2, 3, 4
# ============================================================

def test_corpus_bytes_survives_a_file_that_vanishes(tmp_path, monkeypatch, capsys):
    """`stat()` between `is_file()` and the read is a TOCTOU that killed the
    CLI before the window check could answer."""
    doc = tmp_path / "a.md"
    doc.write_text("hello", encoding="utf-8")
    real_stat = Path.stat

    def flaky(self, *a, **kw):
        if self.name == "a.md":
            raise OSError("vanished")
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", flaky)
    assert cen.corpus_bytes([tmp_path]) == 0
    assert "LOWER bound" in capsys.readouterr().err


def test_corpus_bytes_survives_a_named_file_that_vanishes(tmp_path, monkeypatch, capsys):
    """The same TOCTOU on a path passed DIRECTLY, not found by rglob. There are
    two `stat()` calls in this function and each needed its own guard."""
    doc = tmp_path / "a.md"
    doc.write_text("hello", encoding="utf-8")
    real_stat = Path.stat

    def flaky(self, *a, **kw):
        if self.name == "a.md":
            raise OSError("vanished")
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", flaky)
    assert cen.corpus_bytes([doc]) == 0
    assert "LOWER bound" in capsys.readouterr().err


def test_corpus_bytes_still_counts_what_is_there(tmp_path):
    """The guard must not have turned the sizer into a zero."""
    (tmp_path / "a.md").write_text("hello", encoding="utf-8")
    assert cen.corpus_bytes([tmp_path]) == 5


def test_two_scopes_that_sanitise_alike_get_different_mount_names(tmp_path):
    """`requested.replace("/", "-").strip("-.")` mapped `../foo` and `foo` both
    to `foo`, and `mounts` is keyed by PATH -- so one silently shadowed the
    other inside the sandbox."""
    a = tmp_path / "one"
    b = tmp_path / "two"
    a.mkdir()
    b.mkdir()
    name_a = cen._mount_name_for(a, "foo")
    name_b = cen._mount_name_for(b, "../foo")
    assert name_a != name_b


def test_the_mount_name_is_stable_for_one_path(tmp_path):
    """Uniqueness must not have become randomness: the traversal program is
    written against the name."""
    d = tmp_path / "one"
    d.mkdir()
    assert cen._mount_name_for(d, "foo") == cen._mount_name_for(d, "foo")


def test_drift_names_only_answers_that_are_in_the_file(tmp_path):
    """Drift was computed BEFORE the same-question dedup, so a re-run against a
    consistent corpus kept the replaced answer's id in `run_state_drift`."""
    path = tmp_path / "answers.json"
    good = {"corpus_content_sha256": "aaa"}
    drifted = {"corpus_content_sha256": "bbb"}

    cen.append_answer(path, {"answer": {"kind": "count", "value": 1}}, "agg-01", good)
    cen.append_answer(path, {"answer": {"kind": "count", "value": 2}}, "agg-01", drifted)
    cen.append_answer(path, {"answer": {"kind": "count", "value": 3}}, "agg-01", good)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_state_drift"] == []
    assert len(payload["answers"]) == 1


def test_a_genuinely_drifted_answer_is_still_named(tmp_path):
    """The reorder must not have disabled the flag."""
    path = tmp_path / "answers.json"
    cen.append_answer(path, {"answer": None}, "agg-01", {"corpus_content_sha256": "aaa"})
    cen.append_answer(path, {"answer": None}, "agg-02", {"corpus_content_sha256": "bbb"})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_state_drift"] == ["agg-02"]


def test_census_defines_an_exit_code_for_a_failed_answers_write():
    """`append_answer` raising escaped main() entirely: a traversal that had
    already run for up to 180 seconds printed its answer, then died on a
    traceback with exit 1, a code the docstring does not define."""
    assert cen.EXIT_ANSWERS_WRITE_FAILED == 7
    body = _code_only(ROOT / "scripts" / "census.py")
    assert "except (RuntimeError, OSError) as exc:" in body
    assert "return EXIT_ANSWERS_WRITE_FAILED" in body


def test_the_census_docstring_lists_exit_seven():
    header = (ROOT / "scripts" / "census.py").read_text(encoding="utf-8").split('"""', 2)[1]
    assert "7  the run completed but its record could not be written" in header


# ============================================================
# check-build 1, 3, 5
# ============================================================

@pytest.mark.parametrize("corp,ex,expect_text,expect_warn", [
    (10, 10, "up to date", False),
    (10, 9, "1 build behind", True),
    (10, 8, "2 builds behind", True),
    (10, 12, "2 builds ahead of corporate", False),
    (10, 11, "1 build ahead of corporate", False),
])
def test_the_build_status_reads_the_right_direction(corp, ex, expect_text, expect_warn):
    """`-2 builds behind !` labelled the source of the next corporate build as
    a drift defect. There was no negative branch at all."""
    status, marker = chb.build_status(corp, ex)
    assert status == expect_text
    assert (marker.strip() == "!") is expect_warn
    assert not status.startswith("-")


@pytest.mark.parametrize("exc", [PermissionError, IsADirectoryError, UnicodeDecodeError])
def test_load_json_returns_none_for_any_unreadable_file(monkeypatch, tmp_path, exc):
    """Only FileNotFoundError and JSONDecodeError were caught, so a
    permission-denied BUILD.json crashed instead of reporting "cannot read"."""
    def boom(*a, **kw):
        if exc is UnicodeDecodeError:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "bad")
        raise exc("nope")

    monkeypatch.setattr(Path, "read_text", boom)
    assert chb.load_json(tmp_path / "BUILD.json") is None


def test_a_future_timestamp_says_so_instead_of_just_now():
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    out = chb.format_age(future)
    assert "just now" not in out
    assert "FUTURE" in out or "future" in out


def test_a_recent_timestamp_still_reads_as_recent():
    from datetime import datetime, timedelta, timezone
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    assert chb.format_age(recent).endswith("m ago")


# ============================================================
# check-contract-gate 1 - the slug is not a glob
# ============================================================

def test_a_slug_with_glob_metacharacters_matches_only_itself(tmp_path):
    """`glob(f"*-{slug}")` made the slug a PATTERN: `a*b` matched
    `2026-01-01-aZZZb`, and a stray `[` matched nothing at all."""
    # A widened match: the pattern `*-a*b` finds `2026-01-01-aZZZb`.
    (tmp_path / "2026-01-01-aZZZb").mkdir()
    status, _ = ccg.check_gate("plans/2026-01-01-a*b.md", contract_dir=tmp_path)
    assert status == "MISSING", "a glob metacharacter widened the match"


def test_a_slug_with_a_character_class_finds_its_own_contract(tmp_path):
    """The false NEGATIVE, which is the worse half. Under `glob`, the pattern
    `*-a[bc]d` matches `abd` and `acd` and NOT the directory literally named
    `2026-01-01-a[bc]d` -- so a real contract went unseen and the gate reported
    MISSING for a plan that has one."""
    d = tmp_path / "2026-01-01-a[bc]d"
    d.mkdir()
    (d / "test_contract.py").write_text("", encoding="utf-8")
    status, detail = ccg.check_gate("plans/2026-01-01-a[bc]d.md", contract_dir=tmp_path)
    assert status != "MISSING", detail


def test_a_normal_slug_still_finds_its_contract(tmp_path):
    d = tmp_path / "2026-01-01-real-slug"
    d.mkdir()
    (d / "test_contract.py").write_text("", encoding="utf-8")
    status, _ = ccg.check_gate("plans/2026-01-01-real-slug.md", contract_dir=tmp_path)
    assert status != "MISSING"


# ============================================================
# check-path-references 1, 2
# ============================================================

def test_outside_a_git_repo_the_tool_degrades_instead_of_crashing(tmp_path, capsys):
    """`check=True` made this raise, and both callers hit it FIRST -- so the
    process died before the documented fail-soft path could ever run."""
    assert cpr.tracked_markdown(tmp_path) == []
    assert "no Markdown was scanned" in capsys.readouterr().err


def test_git_output_is_split_on_nul_not_on_spaces():
    """`.stdout.split()` turned a tracked `my notes.md` into two names that
    both fail to open, so that file's prose was never scanned and no dangling
    path inside it could be caught."""
    body = _code_only(ROOT / "scripts" / "check-path-references.py")
    assert '"-z"' in body
    assert ".stdout.split()" not in body


def test_the_tool_still_finds_the_repository_markdown():
    """Degrading gracefully must not have degraded always."""
    files = cpr.tracked_markdown(ROOT)
    assert len(files) > 10
    assert all(f.endswith(".md") for f in files)
