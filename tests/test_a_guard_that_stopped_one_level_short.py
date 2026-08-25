"""Shard 03-p1: four guards that validated the container and stopped, a default
that defeated the branch below it, and a sentinel that was also a real value.

* ``census.append_answer`` refuses an answers file that is valid JSON of the
  wrong SHAPE, and its comment says why: without it, a traceback and exit 1
  lost the record after a traversal that may have run for 180 seconds. The
  guard checked that ``answers`` was a list and never what the list held, so a
  list of non-dicts reached ``a.get(...)`` as an AttributeError and an
  old-schema record reached ``a["question_id"]`` as a KeyError. Same crash,
  same lost work, one level down.

* ``check-build`` read ``exec_data.get("build", 0)``. ``0`` is a valid int, so
  a BUILD.json with no ``build`` key never took the "malformed build" row that
  exists for it - the exec printed build 0 and the table reported "N builds
  behind", a drift number invented for a file that states nothing.

* ``check-contract-gate`` treated the slug ``"untitled"`` as "no decodable
  slug". It is also what a plan literally named ``untitled.md`` derives, so
  that plan was reported SKIPPED with a false reason and could never report
  FOUND. The check exits 0 either way, so it was silent.

* ``census.corpus_bytes`` counted a file scope whole with no suffix filter,
  which contradicts ``refuse_if_corpus_fits_window``'s docstring and makes its
  "0 bytes of readable content" branch unreachable for the case it describes.
  ``resolve_corpus`` also had no dedup, so one scope passed twice was summed
  twice and a corpus that fits the window could cross the refusal threshold.

* ``census-submodel-bench``'s exit-code line listed "degenerate width" as an
  exit-2 condition. Only ``--dry-run`` does that.

Run: python3 -m pytest tests/test_a_guard_that_stopped_one_level_short.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


census = _load("census_under_test", "scripts/census.py")
gate = _load("contract_gate_under_test", "scripts/check-contract-gate.py")
submodel = _load("submodel_bench_under_test", "scripts/census-submodel-bench.py")


# ============================================================
# The answers file whose ELEMENTS nobody checked
# ============================================================

def _answers_file(tmp_path: Path, payload) -> Path:
    p = tmp_path / "answers.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _record():
    return {"answer": {"kind": "count", "value": 3}, "elapsed_s": 1.0}


@pytest.mark.parametrize("answers", [
    ["junk"],                                   # the reported reproduction
    [None],
    [7],
    [["question_id"]],
    [{"answer": None}],                         # a dict with no question_id
    [{"question_id": None}],
    [{"question_id": 7}],
    [{"question_id": ""}],
    [{"question_id": "q1"}, "junk"],            # one bad among good
])
def test_a_record_the_dedup_cannot_key_is_refused(tmp_path, answers):
    """RuntimeError, which `_emit_record` catches; not a traceback and exit 1."""
    path = _answers_file(tmp_path, {"schema_version": 1, "run_state": {},
                                    "answers": answers})
    with pytest.raises(RuntimeError, match="question_id"):
        census.append_answer(path, _record(), "q1", {"corpus_content_sha256": "a"})


def test_the_refusal_names_the_position(tmp_path):
    path = _answers_file(tmp_path, {"schema_version": 1, "run_state": {},
                                    "answers": [{"question_id": "q1"}, "junk"]})
    with pytest.raises(RuntimeError, match=r"\[1\]"):
        census.append_answer(path, _record(), "q2", {"corpus_content_sha256": "a"})


def test_the_refusal_does_not_overwrite_the_file(tmp_path):
    """The whole point of refusing: the answers already recorded survive."""
    before = {"schema_version": 1, "run_state": {}, "answers": ["junk"]}
    path = _answers_file(tmp_path, before)
    with pytest.raises(RuntimeError):
        census.append_answer(path, _record(), "q1", {"corpus_content_sha256": "a"})
    assert json.loads(path.read_text(encoding="utf-8")) == before


def test_a_well_formed_file_still_appends(tmp_path):
    path = _answers_file(tmp_path, {
        "schema_version": 1, "run_state": {"corpus_content_sha256": "a"},
        "answers": [{"question_id": "q1", "run_state": {"corpus_content_sha256": "a"}}]})
    census.append_answer(path, _record(), "q2", {"corpus_content_sha256": "a"})
    got = json.loads(path.read_text(encoding="utf-8"))
    assert [a["question_id"] for a in got["answers"]] == ["q1", "q2"]


def test_a_rerun_still_replaces_its_own_answer(tmp_path):
    path = _answers_file(tmp_path, {
        "schema_version": 1, "run_state": {"corpus_content_sha256": "a"},
        "answers": [{"question_id": "q1", "run_state": {"corpus_content_sha256": "a"},
                     "answer": {"value": 1}}]})
    census.append_answer(path, {"answer": {"value": 2}}, "q1",
                         {"corpus_content_sha256": "a"})
    got = json.loads(path.read_text(encoding="utf-8"))
    assert len(got["answers"]) == 1
    assert got["answers"][0]["answer"]["value"] == 2


def test_the_outer_shape_guards_still_fire(tmp_path):
    """The element check sits below two older ones; neither may be displaced."""
    for payload in ("[]", '{"schema_version": 1}', '{"answers": "not a list"}'):
        path = tmp_path / "bad.json"
        path.write_text(payload, encoding="utf-8")
        with pytest.raises(RuntimeError, match="wrong shape|wrong "):
            census.append_answer(path, _record(), "q1", {})


def test_a_fresh_file_is_still_created(tmp_path):
    path = tmp_path / "new.json"
    census.append_answer(path, _record(), "q1", {"corpus_content_sha256": "a"})
    got = json.loads(path.read_text(encoding="utf-8"))
    assert [a["question_id"] for a in got["answers"]] == ["q1"]


# ============================================================
# The corpus measured twice, and the file measured whole
# ============================================================

def test_one_scope_passed_twice_is_measured_once(tmp_path, monkeypatch):
    """Double-counting could push a corpus past the refuse-the-run threshold."""
    d = tmp_path / "notes"
    d.mkdir()
    (d / "a.md").write_text("x" * 1000, encoding="utf-8")
    monkeypatch.setattr(census, "known_scopes", lambda: {"notes": d})

    once, _m, err = census.resolve_corpus(["notes"])
    twice, _m2, err2 = census.resolve_corpus(["notes", "notes"])
    assert err is None and err2 is None
    assert census.corpus_bytes(twice) == census.corpus_bytes(once)


def test_the_same_directory_by_name_and_by_path_collapses(tmp_path, monkeypatch):
    d = tmp_path / "notes"
    d.mkdir()
    (d / "a.md").write_text("x" * 1000, encoding="utf-8")
    monkeypatch.setattr(census, "known_scopes", lambda: {"notes": d})

    resolved, mounts, err = census.resolve_corpus(["notes", str(d)])
    assert err is None
    assert len(resolved) == 1
    assert len(mounts) == len(resolved), "the mount table and the list must agree"


def test_two_different_scopes_are_both_kept(tmp_path, monkeypatch):
    """Dedup must not swallow a second, genuinely different scope."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "x.md").write_text("x" * 100, encoding="utf-8")
    (b / "y.md").write_text("y" * 100, encoding="utf-8")
    monkeypatch.setattr(census, "known_scopes", lambda: {"a": a, "b": b})

    resolved, mounts, err = census.resolve_corpus(["a", "b"])
    assert err is None
    assert len(resolved) == 2 and len(mounts) == 2
    assert census.corpus_bytes(resolved) == 200


def test_the_first_mount_name_wins_on_a_repeat(tmp_path, monkeypatch):
    d = tmp_path / "notes"
    d.mkdir()
    monkeypatch.setattr(census, "known_scopes", lambda: {"notes": d})
    _r, mounts, _e = census.resolve_corpus(["notes", str(d)])
    assert list(mounts.values()) == [census._mount_name_for(d, "notes")]


def test_a_file_scope_outside_the_corpus_suffixes_measures_zero(tmp_path):
    """So the "0 bytes of readable content, check the scope" branch can fire."""
    doc = tmp_path / "notes.docx"
    doc.write_bytes(b"x" * 300_000)
    assert census.corpus_bytes([doc]) == 0


def test_a_file_scope_of_a_corpus_type_is_still_counted(tmp_path):
    note = tmp_path / "notes.md"
    note.write_text("x" * 500, encoding="utf-8")
    assert census.corpus_bytes([note]) == 500


def test_a_directory_scope_still_filters_by_suffix(tmp_path):
    """The filter the file branch was missing was always in the walk branch."""
    d = tmp_path / "mixed"
    d.mkdir()
    (d / "a.md").write_text("x" * 100, encoding="utf-8")
    (d / "b.docx").write_bytes(b"y" * 100_000)
    assert census.corpus_bytes([d]) == 100


# ============================================================
# The build number that was invented for a silent file
# ============================================================

@pytest.mark.parametrize("payload,needle", [
    ({}, "no 'build' key"),
    ({"version": "2.1"}, "no 'build' key"),
    ({"build": None}, "None"),
    ({"build": "7"}, "'7'"),
    ({"build": True}, "True"),
    ({"build": [7]}, "[7]"),
])
def test_a_build_json_without_a_number_is_a_malformed_row(payload, needle):
    """The row's TEXT comes from the module, not from a copy of it here.

    The first version recomputed the conditional in the test, and a mutation
    collapsing it in the source survived: the test agreed with itself.
    """
    check_build = _load("check_build_under_test", "scripts/check-build.py")
    assert check_build._build_number(payload.get("build")) is None
    assert needle in check_build._build_detail(payload)


def test_a_real_build_number_still_reads():
    check_build = _load("check_build_under_test2", "scripts/check-build.py")
    assert check_build._build_number(0) == 0
    assert check_build._build_number(42) == 42


def test_the_zero_default_is_gone_from_the_source():
    """`0` is a valid build, so the default made the malformed row unreachable."""
    src = (ROOT / "scripts" / "check-build.py").read_text(encoding="utf-8")
    assert 'exec_data.get("build", 0)' not in src
    assert 'raw_build = exec_data.get("build")' in src


# ============================================================
# The sentinel that was also a real slug
# ============================================================

@pytest.mark.parametrize("plan", [
    "plans/untitled.md",
    "plans/2026-06-28-untitled.md",
])
def test_a_plan_actually_named_untitled_is_not_skipped(plan):
    assert gate._slug_is_the_fallback(plan) is False
    assert gate.derive_slug(plan) == "untitled"


def test_a_path_with_no_stem_to_decode_is_still_the_fallback():
    """The one shape that really has nothing to decode: a bare date.

    `.md` is NOT that shape - pathlib reads a leading dot as the whole stem, so
    `Path(".md").stem` is ".md" and `derive_slug` returns it verbatim. This
    test asserted otherwise on the first pass and failed, which is the check
    doing its job on the test rather than the code.
    """
    assert gate._slug_is_the_fallback("plans/2026-06-28-.md") is True
    assert gate.derive_slug("plans/2026-06-28-.md") == "untitled"


@pytest.mark.parametrize("plan", ["", "."])
def test_a_path_with_no_name_at_all_mirrors_derive_slugs_own_fallback(plan):
    """The undated half of the predicate, which `check_gate` cannot reach.

    `check_gate` refuses a falsy plan path before this runs, so the branch is
    only reachable by a direct call - and it exists because `derive_slug` has
    the same `stem or "untitled"` fallback and the two must agree. Deleting it
    would make the predicate disagree with the function it describes, so it is
    pinned here rather than removed.
    """
    assert gate._slug_is_the_fallback(plan) is True
    assert gate.derive_slug(plan) == "untitled"


@pytest.mark.parametrize("plan", [".md", "plans/.md"])
def test_a_dotfile_name_is_its_own_stem(plan):
    assert gate._slug_is_the_fallback(plan) is False
    assert gate.derive_slug(plan) == ".md"


@pytest.mark.parametrize("plan,slug", [
    ("plans/2026-06-28-compaction-control.md", "compaction-control"),
    ("plans/no-date-here.md", "no-date-here"),
    ("plans/9999-99-99-odd-date.md", "odd-date"),
])
def test_an_ordinary_plan_is_unchanged(plan, slug):
    assert gate._slug_is_the_fallback(plan) is False
    assert gate.derive_slug(plan) == slug


def test_a_plan_path_with_nothing_to_decode_is_still_skipped(tmp_path):
    """Through `check_gate`, not through the predicate.

    A mutation making the branch unreachable survived the first pass, because
    nothing asserted the SKIPPED outcome itself.
    """
    contract_dir = tmp_path / "contract"
    contract_dir.mkdir()
    status, detail = gate.check_gate(Path("plans/2026-06-28-.md"),
                                     contract_dir=contract_dir)
    assert status == "SKIPPED"
    assert "no decodable slug" in detail


def test_a_plan_with_a_real_slug_is_never_skipped(tmp_path):
    """The other side of the same branch, so neither constant can replace it."""
    contract_dir = tmp_path / "contract"
    contract_dir.mkdir()
    status, _detail = gate.check_gate(Path("plans/2026-06-28-real-slug.md"),
                                      contract_dir=contract_dir)
    assert status == "MISSING", "a decodable slug with no contract is MISSING"


def test_the_gate_finds_the_contract_for_a_plan_named_untitled(tmp_path):
    """The consequence: that plan's contract directory can report FOUND again."""
    contract_dir = tmp_path / "contract"
    (contract_dir / "2026-01-01-untitled").mkdir(parents=True)
    (contract_dir / "2026-01-01-untitled" / "case.md").write_text("x",
                                                                 encoding="utf-8")

    status, detail = gate.check_gate(Path("plans/2026-06-28-untitled.md"),
                                     contract_dir=contract_dir)
    assert status == "FOUND", detail
    assert "2026-01-01-untitled" in detail


def test_derive_slug_stays_byte_identical_to_its_twin():
    """The parity the docstring claims, asserted rather than trusted.

    The fix deliberately went into a NEW helper for this reason: changing
    `derive_slug` here would break a parity that another test locks.
    """
    log = _load("trajectory_log_under_test", "scripts/implement-trajectory-log.py")
    for plan in ("plans/2026-06-28-untitled.md", ".md", "plans/a-b-c.md",
                 "plans/2026-01-01-x.md"):
        assert gate.derive_slug(plan) == log.derive_slug(plan)


# ============================================================
# The exit code the docstring promised and main did not make
# ============================================================

def test_the_exit_code_line_scopes_the_degenerate_case():
    doc = submodel.__doc__
    assert "--dry-run ONLY" in doc
    # The correction quotes the phrase it replaced, so pin the order rather
    # than asserting absence.
    assert doc.index('"degenerate width" used to be listed unscoped') < doc.index(
        "a real run does not exit 2")


def test_a_degenerate_cell_is_still_marked_in_the_report():
    """Scoping the docstring must not be read as excusing the tag.

    The claim being made is that a real run SURFACES a degenerate cell rather
    than exiting; if the surfacing went, the docstring would be wrong again in
    the other direction.
    """
    src = (ROOT / "scripts" / "census-submodel-bench.py").read_text(encoding="utf-8")
    assert 'result["degenerate"] = degenerate' in src
    assert 'result["degenerate"] = speed_degenerate' in src
    assert "[ВЫРОЖДЕН]" in src
