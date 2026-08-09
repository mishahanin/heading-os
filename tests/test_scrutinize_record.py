"""Contract for the /scrutinize structured run record.

Written RED, before `scripts/utils/scrutinize_record.py` and the thin CLI
`scripts/scrutinize-record.py` exist, per Step 1 of
`plans/2026-08-09-scrutinize-record-roles-currency.md`.

The defect this record exists to close: across 75 saved scrutiny reports the
mandated `Refutation:` header appears in 8 files and the mandated
`## Judge layer` heading in 12. Both are prose mandates a model can omit
silently. The record moves authorship to code and, crucially, the validator has
to fail in BOTH directions - a pass that never called the dispatcher leaves zero
rows AND zero verdict mentions, which a mismatch-only check reports as clean.
"""
from __future__ import annotations

import json

import pytest

from scripts.utils import scrutinize_record as rec


@pytest.fixture
def runs(tmp_path, monkeypatch):
    """Redirect the record at a tmp file so nothing touches the real corpus."""
    path = tmp_path / "runs.jsonl"
    monkeypatch.setattr(rec, "record_path", lambda: path)
    return path


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _report(tmp_path, refutation_line, findings=1):
    """A saved report stub carrying the mandated header the validator reads."""
    body = [
        "## /scrutinize - file:x",
        "Grade: PASS-WITH-NOTES",
        f"Findings: 0 BLOCKER, {findings} HIGH, 0 MEDIUM",
        f"Refutation: {refutation_line}",
        "",
    ]
    p = tmp_path / "report.md"
    p.write_text("\n".join(body), encoding="utf-8")
    return p


# ============================================================
# Schema and append
# ============================================================
def test_pass_start_is_minted_with_run_and_target(runs):
    rec.append_row(run_id="r1", kind="pass_start", target="file:x")
    rows = _rows(runs)
    assert len(rows) == 1
    assert rows[0]["kind"] == "pass_start"
    assert rows[0]["run_id"] == "r1"
    assert rows[0]["target"] == "file:x"
    assert rows[0]["ts"]


def test_one_row_per_judged_finding(runs):
    rec.append_row(run_id="r1", kind="pass_start", target="file:x")
    for fid in ("H1", "H2", "M1"):
        rec.append_row(
            run_id="r1", kind="verdict", target="file:x", finding_id=fid,
            pass_="2.5a", judge_family="kimi", verdict="REFUTE_PARTIAL",
            confidence_before=80, confidence_after=68,
        )
    verdicts = [r for r in _rows(runs) if r["kind"] == "verdict"]
    assert [r["finding_id"] for r in verdicts] == ["H1", "H2", "M1"]
    assert {r["judge_family"] for r in verdicts} == {"kimi"}


def test_unknown_kind_is_refused(runs):
    with pytest.raises(ValueError):
        rec.append_row(run_id="r1", kind="not-a-kind", target="file:x")


def test_unknown_verdict_is_refused(runs):
    with pytest.raises(ValueError):
        rec.append_row(
            run_id="r1", kind="verdict", target="file:x", finding_id="H1",
            verdict="LOOKS_FINE",
        )


# ============================================================
# REPRODUCED / FALSIFIED - the two moments
# ============================================================
def test_reproduced_requires_an_observed_nonzero_exit(runs):
    with pytest.raises(ValueError):
        rec.append_row(
            run_id="r1", kind="reproduction", target="file:x", finding_id="H1",
            verdict="REPRODUCED", reproduction={"cmd": "pytest -q", "exit_before": None,
                                                "exit_after": None},
        )


def test_reproduced_lands_with_exit_before_only(runs):
    rec.append_row(
        run_id="r1", kind="reproduction", target="file:x", finding_id="H1",
        verdict="REPRODUCED",
        reproduction={"cmd": "pytest -q", "exit_before": 1, "exit_after": None},
    )
    row = _rows(runs)[0]
    assert row["verdict"] == "REPRODUCED"
    assert row["reproduction"]["exit_before"] == 1
    assert row["reproduction"]["exit_after"] is None


def test_falsified_requires_both_exit_codes(runs):
    with pytest.raises(ValueError):
        rec.append_row(
            run_id="r1", kind="reproduction", target="file:x", finding_id="H1",
            verdict="FALSIFIED",
            reproduction={"cmd": "pytest -q", "exit_before": 1, "exit_after": None},
        )


def test_falsified_lands_when_both_are_observed(runs):
    rec.append_row(
        run_id="r1", kind="reproduction", target="file:x", finding_id="H1",
        verdict="FALSIFIED",
        reproduction={"cmd": "pytest -q", "exit_before": 1, "exit_after": 0},
    )
    assert _rows(runs)[0]["verdict"] == "FALSIFIED"


def test_falsified_refuses_a_nonzero_post_fix_exit(runs):
    """A fix that did not make the command pass is not a falsification."""
    with pytest.raises(ValueError):
        rec.append_row(
            run_id="r1", kind="reproduction", target="file:x", finding_id="H1",
            verdict="FALSIFIED",
            reproduction={"cmd": "pytest -q", "exit_before": 1, "exit_after": 1},
        )


# ============================================================
# The validator - it must fail in BOTH directions
# ============================================================
def test_validate_fails_on_a_missing_pass_start(runs, tmp_path):
    """The silent-omission case: no rows at all, and a report claiming a pass."""
    report = _report(tmp_path, "2.5a + 2.5b", findings=1)
    defects = rec.validate(run_id="r1", report_path=report)
    assert any("pass_start" in d for d in defects)


def test_validate_fails_when_the_header_claims_more_than_the_rows_show(runs, tmp_path):
    rec.append_row(run_id="r1", kind="pass_start", target="file:x")
    report = _report(tmp_path, "2.5a + 2.5b", findings=3)
    defects = rec.validate(run_id="r1", report_path=report)
    assert any("verdict row" in d for d in defects)


def test_validate_fails_on_a_declared_skip_with_no_degraded_row(runs, tmp_path):
    rec.append_row(run_id="r1", kind="pass_start", target="file:x")
    report = _report(tmp_path, "skipped: SENSITIVE_MODE active", findings=2)
    defects = rec.validate(run_id="r1", report_path=report)
    assert any("degraded" in d for d in defects)


def test_validate_passes_on_a_legitimate_skip_with_its_degraded_row(runs, tmp_path):
    rec.append_row(run_id="r1", kind="pass_start", target="file:x")
    rec.append_row(
        run_id="r1", kind="degraded", target="file:x",
        degraded="SENSITIVE_MODE declared; no proxy call made",
    )
    report = _report(tmp_path, "skipped: SENSITIVE_MODE active", findings=2)
    assert rec.validate(run_id="r1", report_path=report) == []


def test_validate_passes_on_a_complete_run(runs, tmp_path):
    rec.append_row(run_id="r1", kind="pass_start", target="file:x")
    for fid in ("H1", "H2"):
        rec.append_row(
            run_id="r1", kind="verdict", target="file:x", finding_id=fid,
            pass_="2.5a", judge_family="claude", verdict="CORRECT",
        )
    report = _report(tmp_path, "2.5a", findings=2)
    assert rec.validate(run_id="r1", report_path=report) == []


def test_validate_ignores_rows_from_another_run(runs, tmp_path):
    rec.append_row(run_id="other", kind="pass_start", target="file:y")
    report = _report(tmp_path, "2.5a", findings=1)
    defects = rec.validate(run_id="r1", report_path=report)
    assert any("pass_start" in d for d in defects)


def test_validate_reports_a_missing_report_file(runs, tmp_path):
    rec.append_row(run_id="r1", kind="pass_start", target="file:x")
    defects = rec.validate(run_id="r1", report_path=tmp_path / "nope.md")
    assert any("report" in d.lower() for d in defects)


def test_validate_flags_a_report_with_no_refutation_header(runs, tmp_path):
    """The header is the non-circular compliance signal; its absence is the defect."""
    rec.append_row(run_id="r1", kind="pass_start", target="file:x")
    p = tmp_path / "report.md"
    p.write_text("## /scrutinize - file:x\nGrade: PASS\nFindings: 0 BLOCKER, 1 HIGH\n",
                 encoding="utf-8")
    defects = rec.validate(run_id="r1", report_path=p)
    assert any("Refutation:" in d for d in defects)


# ============================================================
# Cross-run reads (added 2026-08-09 for the single-channel fp tally)
# ============================================================
def test_rows_of_kind_spans_runs(runs):
    rec.append_row(run_id="r1", kind="pass_start", target="file:x")
    rec.append_row(run_id="r1", kind="fp_flag", target="file:x", finding_id="H1")
    rec.append_row(run_id="r2", kind="fp_flag", target="dir:y", finding_id="M2")
    flags = rec.rows_of_kind("fp_flag")
    assert [(r["run_id"], r["finding_id"]) for r in flags] == [("r1", "H1"), ("r2", "M2")]


def test_rows_of_kind_is_empty_without_a_record(runs):
    assert rec.rows_of_kind("fp_flag") == []


def test_iter_rows_skips_a_malformed_line(runs):
    rec.append_row(run_id="r1", kind="pass_start", target="file:x")
    with runs.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    rec.append_row(run_id="r1", kind="fp_flag", target="file:x", finding_id="H1")
    assert [r["kind"] for r in rec.iter_rows()] == ["pass_start", "fp_flag"]
