"""Regression: scrutinize-fp-aggregate must count findings regardless of the
markdown decoration around `[<severity><n>]`.

Measured on 2026-08-01 against the real outputs/operations/scrutiny/ corpus (63
reports in the private DATA overlay, not available here): the pre-fix pattern
`^\\s*\\[([BHMLN]\\d+)\\]` matched only 7 findings across 3 of 63 reports. Report
format drifted over time - finding lines gained heading prefixes (`### [L1]
...`, `#### [M7] ...`) and bold-list wrapping (`- **[M1]** ...`, `**[M1] (conf:
78)** ...`) that the old anchor never tolerated - while a looser variant of the
same bracketed shape matched 93 findings across 24 reports. So the false-positive
rate's denominator was silently wrong.

This test never touches the real DATA overlay. It builds synthetic report
fixtures inline, covering every prefix/confidence variant found during that
audit, and asserts the fixed regex/extraction counts them correctly.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "scrutinize_fp_aggregate", str(ROOT / "scripts" / "scrutinize-fp-aggregate.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _write_report(tmp_path: Path, name: str, text: str) -> None:
    (tmp_path / name).write_text(text, encoding="utf-8")


def test_bare_bracket_form_still_matches(tmp_path, monkeypatch):
    """The one shape the old regex already handled must keep working."""
    monkeypatch.setattr(_mod, "SCRUTINY_DIR", tmp_path)
    _write_report(tmp_path, "2026-08-01-file.md", (
        "[N1] (conf: 90) Some finding text.\n"
    ))
    totals = _mod.count_total_findings()
    assert totals["total"] == 1
    assert totals["by_severity"]["NIT"] == 1
    assert totals["by_conf_band"]["75-100"] == 1


def test_heading_prefixed_findings_are_counted(tmp_path, monkeypatch):
    """`### [ID] ...` and `#### [ID] ...` were invisible to the old anchor."""
    monkeypatch.setattr(_mod, "SCRUTINY_DIR", tmp_path)
    _write_report(tmp_path, "2026-04-19-plan.md", (
        "### [L1] (conf: 90) LOW - stray warning\n"
        "\n"
        "#### [M7] RESOLVED - config drift fixed\n"
    ))
    totals = _mod.count_total_findings()
    assert totals["total"] == 2
    assert totals["by_severity"]["LOW"] == 1
    assert totals["by_severity"]["MEDIUM"] == 1
    # The RESOLVED heading carries no `(conf: N)` - no crash, just no conf band.
    assert sum(totals["by_conf_band"].values()) == 1


def test_bold_list_item_findings_are_counted(tmp_path, monkeypatch):
    """`- **[ID]** ...` and `- **[ID] (conf: N)** ...` bold-wrapped forms."""
    monkeypatch.setattr(_mod, "SCRUTINY_DIR", tmp_path)
    _write_report(tmp_path, "2026-06-27-execution.md", (
        "- **[M1]** `outputs/deliverables/report.md:12` - fix the header.\n"
        "- **[N1] (conf: 70)** Step 7 notes drift by one count.\n"
    ))
    totals = _mod.count_total_findings()
    assert totals["total"] == 2
    assert totals["by_severity"]["MEDIUM"] == 1
    assert totals["by_severity"]["NIT"] == 1
    assert totals["by_conf_band"]["50-74"] == 1


def test_bare_bold_findings_with_conf_variants(tmp_path, monkeypatch):
    """`**[ID] (conf: N)** ...` and the `emitted conf:` / no-colon spellings."""
    monkeypatch.setattr(_mod, "SCRUTINY_DIR", tmp_path)
    _write_report(tmp_path, "2026-07-08-execution.md", (
        "**[M1] (emitted conf: 72, MEDIUM) — REFUTED in 2.5a.**\n"
        "**[H2] (conf 60) no colon before the number.**\n"
        "- **[L4] (conf: 78, did not fix) trailing text after the comma.**\n"
    ))
    totals = _mod.count_total_findings()
    assert totals["total"] == 3
    assert totals["by_conf_band"]["50-74"] == 2  # 72 and 60
    assert totals["by_conf_band"]["75-100"] == 1  # 78


def test_finding_id_suffix_from_iterative_reruns(tmp_path, monkeypatch):
    """`[L1-i2]`, `[M2-carry]` etc. still resolve to their base severity."""
    monkeypatch.setattr(_mod, "SCRUTINY_DIR", tmp_path)
    _write_report(tmp_path, "2026-04-19-workspace.md", (
        "- **[L1-i2]** false positive, no fix.\n"
        "- **[M2-carry]** carried from iteration 1. Deferred.\n"
    ))
    totals = _mod.count_total_findings()
    assert totals["total"] == 2
    assert totals["by_severity"]["LOW"] == 1
    assert totals["by_severity"]["MEDIUM"] == 1


def test_inline_bracket_reference_is_not_double_counted(tmp_path, monkeypatch):
    """A finding's own prose may reference another finding ID mid-line - that
    inline mention must not be counted as a second, separate finding line."""
    monkeypatch.setattr(_mod, "SCRUTINY_DIR", tmp_path)
    _write_report(tmp_path, "2026-07-27-execution.md", (
        "### [M2] (conf: 90, iteration 2) The [L2] behaviour change shipped "
        "without a regression test\n"
    ))
    totals = _mod.count_total_findings()
    assert totals["total"] == 1
    assert totals["by_severity"]["MEDIUM"] == 1
    assert totals["by_severity"]["LOW"] == 0


def test_underscore_prefixed_reports_are_skipped(tmp_path, monkeypatch):
    """`_fp_aggregate.md` and `_fp_log.jsonl`-adjacent files are not reports."""
    monkeypatch.setattr(_mod, "SCRUTINY_DIR", tmp_path)
    _write_report(tmp_path, "_fp_aggregate.md", "[B1] (conf: 99) should not count\n")
    totals = _mod.count_total_findings()
    assert totals["total"] == 0
