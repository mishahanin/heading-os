"""Tests for scripts/eval-outcomes.py - the binary OUTCOME runner (R13).

Covers: the crm_log assertor against the real finalizer in a sandbox (right
slug/date, wrong-slug detection, missing-contact, idempotency), the
doctype_render assertor (field-presence pass + missing-field reporting), the
isolation boundary (loads ONLY evals/outcomes/, never evals/cases/ or _staged/),
the no-model-call invariant, and the benchmark sidecar write/skip.

eval-outcomes.py is kebab-case, so it is importlib-loaded by path.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def mod():
    path = ROOT / "scripts" / "eval-outcomes.py"
    spec = importlib.util.spec_from_file_location("eval_outcomes", str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---- doctype_render assertor ------------------------------------------------

def _official(**overrides) -> dict:
    data = {
        "CLASS": "Board Resolution", "REF_ID": "R-1", "DATE": "2026-06-06",
        "PLACE": "Sample City, Country", "ISSUER_NAME": "Misha Hanin",
        "ISSUER_TITLE": "CEO", "SUBJECT": "Test",
    }
    data.update(overrides)
    return data


def test_doctype_render_positive_passes(mod, tmp_path):
    case = {"outcome": {"type": "doctype_render", "doctype": "official",
                        "expect_missing": [], "data": _official()}}
    results = mod._assert_doctype_render(case, tmp_path, render=False)
    assert results and all(r["passed"] for r in results)


def test_doctype_render_reports_missing(mod, tmp_path):
    data = _official()
    del data["PLACE"]
    data["ISSUER_TITLE"] = ""  # whitespace/empty counts as missing
    case = {"outcome": {"type": "doctype_render", "doctype": "official",
                        "expect_missing": ["ISSUER_TITLE", "PLACE"], "data": data}}
    results = mod._assert_doctype_render(case, tmp_path, render=False)
    assert all(r["passed"] for r in results)


def test_doctype_render_wrong_expectation_fails(mod, tmp_path):
    data = _official()
    del data["PLACE"]
    case = {"outcome": {"type": "doctype_render", "doctype": "official",
                        "expect_missing": [], "data": data}}  # wrong: PLACE IS missing
    results = mod._assert_doctype_render(case, tmp_path, render=False)
    assert any(not r["passed"] for r in results)


# ---- crm_log assertor (real finalizer, sandbox) -----------------------------

def _crm_case(conv_id, slug, dt, ok, **extra) -> dict:
    conv = {"id": conv_id, "topic": "Topic", "latest_datetime": dt,
            "crm_context": ({"contact_slug": slug} if slug else {})}
    outcome = {"type": "crm_log", "conv_id": conv_id, "conversations": [conv],
               "create_contacts": ([slug] if (slug and ok) else []),
               "expect_ok": ok}
    outcome.update(extra)
    return {"outcome": outcome}


def test_crm_log_success(mod, tmp_path):
    case = _crm_case("c1", "acme", "2026-06-04T09:00:00", True,
                     expected_slug="acme", expected_date="2026-06-04")
    results = mod._assert_crm_log(case, tmp_path, render=False)
    assert all(r["passed"] for r in results)


def test_crm_log_wrong_slug_detected(mod, tmp_path):
    case = _crm_case("c1", "acme", "2026-06-04T09:00:00", True,
                     expected_slug="not-acme", expected_date="2026-06-04")
    results = mod._assert_crm_log(case, tmp_path, render=False)
    assert any(not r["passed"] for r in results)


def test_crm_log_no_contact(mod, tmp_path):
    case = _crm_case("c1", None, "2026-06-04T09:00:00", False,
                     expect_error="no CRM contact linked to this conversation")
    results = mod._assert_crm_log(case, tmp_path, render=False)
    assert all(r["passed"] for r in results)


def test_crm_log_idempotency_caught(mod, tmp_path):
    case = _crm_case("c1", "acme", "2026-06-04T09:00:00", True,
                     expected_slug="acme", expected_date="2026-06-04",
                     expect_idempotent=True)
    results = mod._assert_crm_log(case, tmp_path, render=False)
    assert all(r["passed"] for r in results)
    assert any("idempotent" in r["check"] for r in results)


# ---- isolation: loads ONLY evals/outcomes/ ----------------------------------

def test_loader_ignores_cases_and_staged(mod, tmp_path):
    skill = tmp_path / "someskill"
    (skill / "evals" / "cases").mkdir(parents=True)
    (skill / "evals" / "cases" / "prose.json").write_text(
        json.dumps({"id": "prose", "checks": {}}), encoding="utf-8")
    (skill / "evals" / "outcomes").mkdir(parents=True)
    (skill / "evals" / "outcomes" / "out.json").write_text(
        json.dumps({"id": "out", "outcome": {"type": "doctype_render"}}), encoding="utf-8")
    (skill / "evals" / "outcomes" / "_staged").mkdir()
    (skill / "evals" / "outcomes" / "_staged" / "draft.json").write_text(
        json.dumps({"id": "draft"}), encoding="utf-8")

    cases = mod.load_outcome_cases(skill)
    ids = {c.get("id") for c in cases}
    assert ids == {"out"}, "must load only evals/outcomes/*.json, not cases/ or _staged/"


# ---- no model call ----------------------------------------------------------

def test_no_model_import():
    src = (ROOT / "scripts" / "eval-outcomes.py").read_text(encoding="utf-8")
    assert "import anthropic" not in src
    assert "import langfuse" not in src


# ---- benchmark sidecar ------------------------------------------------------

def test_benchmark_sidecar_write_and_skip(mod, tmp_path, monkeypatch):
    skill_dir = tmp_path / ".claude" / "skills" / "official-doc"
    (skill_dir / "evals" / "outcomes").mkdir(parents=True)
    case = {"id": "c1", "outcome": {"type": "doctype_render", "doctype": "official",
                                    "expect_missing": [], "data": _official()}}
    (skill_dir / "evals" / "outcomes" / "c1.json").write_text(
        json.dumps(case), encoding="utf-8")
    monkeypatch.setattr(mod, "SKILLS_DIR", tmp_path / ".claude" / "skills")

    bench = skill_dir / "evals" / "benchmark-outcomes.json"
    mod.run_skill("official-doc", None, render=False, write_benchmark=True)
    assert bench.exists()
    payload = json.loads(bench.read_text(encoding="utf-8"))
    assert payload["last_run"]["check_total"] >= 1
    assert "baseline" in payload

    bench.unlink()
    mod.run_skill("official-doc", None, render=False, write_benchmark=False)
    assert not bench.exists()


# ---- run_one_case setup-error branches --------------------------------------

def test_run_one_case_setup_error_branches(mod):
    """All four setup-error conditions must return setup_error=True so the runner
    can floor the exit code at 2 - a malformed case must never pass silently."""
    # 1. malformed: no outcome block
    _, se = mod.run_one_case({"id": "m"}, render=False)
    assert se is True
    # 2. unknown outcome type
    _, se = mod.run_one_case({"id": "u", "outcome": {"type": "bogus"}}, render=False)
    assert se is True
    # 3. a case that failed to load
    _, se = mod.run_one_case({"id": "l", "_load_error": "bad json"}, render=False)
    assert se is True
    # 4. an assertor that raises (crm_log with no conv_id -> KeyError, caught as setup error)
    _, se = mod.run_one_case({"id": "x", "outcome": {"type": "crm_log"}}, render=False)
    assert se is True


# ---- main() exit-code contract (0 pass / 1 fail / 2 setup error) ------------

def _seed_skill(mod, tmp_path, monkeypatch, case: dict):
    skill_dir = tmp_path / ".claude" / "skills" / "official-doc"
    (skill_dir / "evals" / "outcomes").mkdir(parents=True)
    (skill_dir / "evals" / "outcomes" / "c.json").write_text(json.dumps(case), encoding="utf-8")
    monkeypatch.setattr(mod, "SKILLS_DIR", tmp_path / ".claude" / "skills")
    monkeypatch.setattr(sys, "argv", ["eval-outcomes", "--skill", "official-doc", "--no-write"])


def test_main_exit_0_all_pass(mod, tmp_path, monkeypatch):
    _seed_skill(mod, tmp_path, monkeypatch,
                {"id": "c", "outcome": {"type": "doctype_render", "doctype": "official",
                                        "expect_missing": [], "data": _official()}})
    assert mod.main() == 0


def test_main_exit_1_on_failed_check(mod, tmp_path, monkeypatch):
    data = _official()
    del data["PLACE"]  # PLACE is missing, but the case wrongly expects []
    _seed_skill(mod, tmp_path, monkeypatch,
                {"id": "c", "outcome": {"type": "doctype_render", "doctype": "official",
                                        "expect_missing": [], "data": data}})
    assert mod.main() == 1


def test_main_exit_2_on_setup_error(mod, tmp_path, monkeypatch):
    _seed_skill(mod, tmp_path, monkeypatch,
                {"id": "c", "outcome": {"type": "bogus-unknown-type"}})
    assert mod.main() == 2
