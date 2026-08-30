"""One corrupt file took down two whole eval tools, and a completed grading run
was discarded at its final write.

Three defects, one root cause repeated and one shape check never applied.

D1 `eval-flag.py:cmd_list` caught `(json.JSONDecodeError, OSError)`.
   `UnicodeDecodeError` subclasses `ValueError` and is disjoint from BOTH, so a
   single draft holding invalid UTF-8 escaped the per-file UNREADABLE handler,
   reached `main`'s `except ValueError`, printed a raw codec message and
   returned 1 having listed NOTHING. The handler's own comment says it exists
   because "the one file that needs attention looked like the ones that do
   not"; the bug inverted that, and the one bad file hid every good one.

D2 `eval-outcomes.py:load_outcome_cases` carried the same tuple. It is called
   bare from `run_skill`, which `main` calls bare, and the `except Exception`
   in `run_one_case` is DOWNSTREAM so it never sees this. One unreadable byte
   produced a traceback and zero grading results, contradicting the module
   docstring's exit-2 setup-error contract.

D3 `eval-outcomes.py:_write_benchmark` caught the same two exceptions with no
   shape check. A sidecar holding `[]`, `null` or a bare string PARSES, so the
   handler never fired and `existing["last_run"] = last_run` raised TypeError.
   That runs AFTER the entire grading loop, so a completed run was thrown away
   and the process died on a traceback instead of a documented exit code.

The load-bearing assertion for D1 and D2 is NOT "it did not crash". A tool that
silently listed nothing would pass that. Every case here asserts the OTHER,
good files were still processed, by name and by count.

Fixtures are real bytes under `tmp_path`. Nothing reads or writes the live
`.claude/skills/` tree, and the network is blocked at the socket layer.
"""
from __future__ import annotations

import importlib.util
import json
import re
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """Both scripts colour their output, so `PASS` and the case id are separated
    by a reset sequence. Strip SGR codes before matching on the human text."""
    return _ANSI.sub("", text)

# 0xe9 is a lone Latin-1 'e-acute'. Valid JSON structure, invalid UTF-8 bytes,
# so it survives to `read_text` and dies there rather than in `json.loads`.
INVALID_UTF8 = b'{"id": "corrupt", "description": "caf\xe9 latte"}'

# The three values that PARSE but are not objects. D3 exists because nothing
# distinguished these from a real sidecar.
PARSES_BUT_NOT_AN_OBJECT = {
    "empty list": "[]",
    "null": "null",
    "bare string": '"a bare string"',
}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No case here may reach a model or the network. Blocked at the socket
    layer, so an accidental import or a future code path fails loudly."""
    def _refuse(*a, **k):
        raise AssertionError("this test must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


def _load(name: str, rel: str):
    """Both scripts are kebab-case, so they are importlib-loaded by path."""
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def ef(tmp_path, monkeypatch):
    """eval-flag.py, rerooted at a throwaway workspace."""
    m = _load("eval_flag_unreadable", "scripts/eval-flag.py")
    monkeypatch.setattr(m, "ROOT", tmp_path)
    monkeypatch.setattr(m, "SKILLS_DIR", tmp_path / ".claude" / "skills")
    return m


@pytest.fixture
def eo(tmp_path, monkeypatch):
    """eval-outcomes.py, rerooted at a throwaway workspace."""
    m = _load("eval_outcomes_unreadable", "scripts/eval-outcomes.py")
    monkeypatch.setattr(m, "ROOT", tmp_path)
    monkeypatch.setattr(m, "SKILLS_DIR", tmp_path / ".claude" / "skills")
    return m


def _stage_drafts(ef, skill: str = "alpha-skill") -> Path:
    """Two readable drafts with the corrupt one sorted BETWEEN them, so a fix
    that merely stops the crash after the last good file cannot pass. `glob` is
    sorted, so 'b-corrupt' is visited before 'c-good-two'."""
    staged = ef.SKILLS_DIR / skill / "evals" / "outcomes" / "_staged"
    staged.mkdir(parents=True)
    (staged / "a-good-one.json").write_text(
        json.dumps({"id": "a-good-one", "description": "GOOD DRAFT ONE",
                    "trace_id": "trace-one"}), encoding="utf-8")
    (staged / "b-corrupt.json").write_bytes(INVALID_UTF8)
    (staged / "c-good-two.json").write_text(
        json.dumps({"id": "c-good-two", "description": "GOOD DRAFT TWO",
                    "trace_id": "trace-two"}), encoding="utf-8")
    return staged


# ============================================================
# D1 - eval-flag.py --list
# ============================================================

def test_d1_an_unreadable_draft_does_not_hide_the_readable_ones(ef, capsys):
    """The load-bearing assertion: BOTH good drafts are still listed, by id and
    by description. Asserting only "no exception" would pass on a --list that
    printed an empty array."""
    _stage_drafts(ef)

    rc = ef.cmd_list(as_json=True)

    assert rc == 0
    listed = json.loads(capsys.readouterr().out)
    by_id = {d["id"]: d for d in listed}

    # The good files were PROCESSED, not merely survived.
    assert set(by_id) == {"a-good-one", "b-corrupt", "c-good-two"}
    assert by_id["a-good-one"]["description"] == "GOOD DRAFT ONE"
    assert by_id["a-good-one"]["trace_id"] == "trace-one"
    assert by_id["c-good-two"]["description"] == "GOOD DRAFT TWO"
    assert by_id["c-good-two"]["trace_id"] == "trace-two"
    assert by_id["a-good-one"]["unreadable"] is False
    assert by_id["c-good-two"]["unreadable"] is False

    # And the bad one is reported as an unreadable FILE, naming the real cause,
    # not silently rendered as an untitled draft.
    bad = by_id["b-corrupt"]
    assert bad["unreadable"] is True
    assert "UNREADABLE" in bad["description"]
    assert "UnicodeDecodeError" in bad["description"]


def test_d1_main_list_exits_zero_and_prints_both_good_drafts(ef, capsys, monkeypatch):
    """End to end through `main`, which is where the escaped ValueError landed:
    it returned 1 and printed a raw codec message. The listing is complete, so
    it exits 0 and the corrupt draft is flagged in band."""
    _stage_drafts(ef)
    monkeypatch.setattr(sys, "argv", ["eval-flag.py", "--list"])

    rc = ef.main()

    out = _plain(capsys.readouterr().out)
    assert rc == 0
    assert "GOOD DRAFT ONE" in out
    assert "GOOD DRAFT TWO" in out
    assert "3 staged eval draft(s)" in out
    assert "UnicodeDecodeError" in out


# ============================================================
# D2 - eval-outcomes.py grading run
# ============================================================

def _good_case(cid: str) -> dict:
    """A doctype_render case that grades in-process: no subprocess, no browser,
    no model. `render=False` keeps it to a field-presence assertion."""
    return {
        "id": cid,
        "outcome": {
            "type": "doctype_render", "doctype": "official", "expect_missing": [],
            "data": {
                "CLASS": "Board Resolution", "REF_ID": "R-1",
                "DATE": "2026-06-06", "PLACE": "Sample City, Country",
                "ISSUER_NAME": "Misha Hanin", "ISSUER_TITLE": "CEO",
                "SUBJECT": "Fixture resolution",
            },
        },
    }


def _write_cases(eo, skill: str = "beta-skill") -> Path:
    """Two gradeable cases with the corrupt one sorted BETWEEN them."""
    out_dir = eo.SKILLS_DIR / skill / "evals" / "outcomes"
    out_dir.mkdir(parents=True)
    (out_dir / "a-good-one.json").write_text(
        json.dumps(_good_case("a-good-one")), encoding="utf-8")
    (out_dir / "b-corrupt.json").write_bytes(INVALID_UTF8)
    (out_dir / "c-good-two.json").write_text(
        json.dumps(_good_case("c-good-two")), encoding="utf-8")
    return out_dir


def test_d2_an_unreadable_case_does_not_take_down_the_grading_run(eo, capsys):
    """The load-bearing assertion: both good cases were GRADED and PASSED. A
    runner that loaded nothing and reported 0/0 would pass a "did not crash"
    test, and 0/0 is the false-green this suite already guards elsewhere."""
    _write_cases(eo)

    passed, total, setup_error, matched = eo.run_skill(
        "beta-skill", None, render=False, write_benchmark=False)

    out = _plain(capsys.readouterr().out)
    # All three files were visited, and the two good ones produced real checks.
    assert matched == 3
    assert passed == 2, f"both good cases should have passed, got {passed}"
    assert total == 3, "two passing checks plus the corrupt file's failed check"
    assert "PASS a-good-one" in out
    assert "PASS c-good-two" in out
    # The corrupt file is one FAILED case, and a setup error, not a crash.
    assert setup_error is True
    assert "FAIL b-corrupt" in out
    assert "UnicodeDecodeError" in out


def test_d2_main_honours_its_exit_2_setup_error_contract(eo, capsys, monkeypatch):
    """The module docstring promises "2 setup error (a malformed case ...)".
    `load_outcome_cases` is called bare from `run_skill`, itself called bare
    from `main`, and `run_one_case`'s `except Exception` is downstream, so the
    codec error escaped all three and produced a traceback instead."""
    _write_cases(eo)
    monkeypatch.setattr(
        sys, "argv", ["eval-outcomes.py", "--skill", "beta-skill", "--no-write"])

    rc = eo.main()

    out = _plain(capsys.readouterr().out)
    assert rc == 2, "a malformed case is a documented setup error, not a traceback"
    # The verdict still reports the work that WAS done.
    assert "PASS a-good-one" in out
    assert "PASS c-good-two" in out
    assert "Total: 2/3 checks passed" in out


# ============================================================
# D3 - eval-outcomes.py sidecar write, AFTER the grading loop
# ============================================================

def _sidecar(eo, blob: str | bytes, skill: str = "gamma-skill") -> tuple[Path, Path]:
    """Seed evals/benchmark-outcomes.json with a corrupt payload."""
    skill_dir = eo.SKILLS_DIR / skill
    (skill_dir / "evals").mkdir(parents=True)
    path = skill_dir / "evals" / "benchmark-outcomes.json"
    if isinstance(blob, bytes):
        path.write_bytes(blob)
    else:
        path.write_text(blob, encoding="utf-8")
    return skill_dir, path


@pytest.mark.parametrize("label,blob", sorted(PARSES_BUT_NOT_AN_OBJECT.items()))
def test_d3_a_sidecar_that_parses_but_is_not_an_object(eo, capsys, label, blob):
    """`[]`, `null` and a bare string all decode cleanly, so the exception
    handler never fired and `existing["last_run"] = last_run` raised TypeError.
    The sibling `isinstance(case, dict)` check in `load_outcome_cases` existed
    already; it was simply never applied here."""
    skill_dir, path = _sidecar(eo, blob)

    eo._write_benchmark(skill_dir, 9, 9, [{"id": "c1", "passed": 9, "total": 9,
                                           "failures": []}])

    written = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(written, dict), f"{label}: sidecar must be rewritten as an object"
    # The completed run's numbers actually reached disk.
    assert written["last_run"]["passed_total"] == 9
    assert written["last_run"]["check_total"] == 9
    assert [c["id"] for c in written["last_run"]["cases"]] == ["c1"]
    assert written["baseline"]["passed_total"] == 9
    # Discarding the previous baseline is reported, never swallowed.
    out = _plain(capsys.readouterr().out)
    assert "not a JSON object" in out
    assert "previous baseline lost" in out


def test_d3_a_sidecar_of_invalid_utf8_does_not_discard_the_run(eo, capsys):
    """The same codec gap as D1/D2, in the one function that runs after every
    case has been graded."""
    skill_dir, path = _sidecar(eo, INVALID_UTF8)

    eo._write_benchmark(skill_dir, 4, 5, [{"id": "c1", "passed": 4, "total": 5,
                                           "failures": [{"check": "x"}]}])

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["last_run"]["passed_total"] == 4
    assert written["last_run"]["check_total"] == 5
    out = _plain(capsys.readouterr().out)
    assert "UnicodeDecodeError" in out
    assert "previous baseline lost" in out


def test_d3_a_valid_sidecar_keeps_its_baseline(eo, capsys):
    """The discriminator. Without this, `existing = {}` unconditionally would
    satisfy every case above while silently destroying the baseline of every
    healthy sidecar on every run."""
    skill_dir, path = _sidecar(eo, json.dumps({
        "baseline": {"passed_total": 1, "check_total": 3, "cases": []},
        "last_run": {"passed_total": 2, "check_total": 3, "cases": []},
    }))

    eo._write_benchmark(skill_dir, 7, 7, [{"id": "c9", "passed": 7, "total": 7,
                                           "failures": []}])

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["baseline"] == {"passed_total": 1, "check_total": 3, "cases": []}
    assert written["last_run"]["passed_total"] == 7
    out = _plain(capsys.readouterr().out)
    assert "previous baseline lost" not in out


def test_d3_the_whole_run_survives_a_corrupt_sidecar(eo, capsys, monkeypatch):
    """End to end, and the point of the defect: `_write_benchmark` runs after
    the entire grading loop, so a TypeError there threw away work that had
    already been done. Both good cases graded AND the sidecar landed."""
    out_dir = eo.SKILLS_DIR / "delta-skill" / "evals" / "outcomes"
    out_dir.mkdir(parents=True)
    (out_dir / "a-good-one.json").write_text(
        json.dumps(_good_case("a-good-one")), encoding="utf-8")
    (out_dir / "b-good-two.json").write_text(
        json.dumps(_good_case("b-good-two")), encoding="utf-8")
    sidecar = eo.SKILLS_DIR / "delta-skill" / "evals" / "benchmark-outcomes.json"
    sidecar.write_text("[]", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["eval-outcomes.py", "--skill", "delta-skill"])
    rc = eo.main()

    out = _plain(capsys.readouterr().out)
    assert rc == 0, "two passing cases and a corrupt sidecar is a clean run"
    assert "Total: 2/2 checks passed" in out
    written = json.loads(sidecar.read_text(encoding="utf-8"))
    assert written["last_run"]["passed_total"] == 2
    assert sorted(c["id"] for c in written["last_run"]["cases"]) == [
        "a-good-one", "b-good-two"]
