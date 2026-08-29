"""A sheet this file rendered must be a sheet this file can read back.

Not in any audit shard; found by driving the generate-fill-kappa round trip on
2026-08-30.

`render_scoring_sheet` prints whatever finding id the run record holds, and the
dispatcher's `--finding` is a free string. `_HEADER_RE` insisted on
`[BHMLN]\\d+`. With two verdict rows in one run, `B1` and `B-1`, the sheet
generated cleanly and `main` returned 0; then `--kappa` on the filled sheet
reported "1 headers vs 2 ratings" and returned 4.

The cost is not one lost row. `compute_kappa_from_sheet` refuses the WHOLE
sheet on a header/rating count mismatch, so one odd id destroys the quarter's
kappa - and it does so only AFTER the CEO has filled the sheet in by hand,
which is the one input this benchmark exists to collect and cannot regenerate.

Group 1 of `_HEADER_RE` is never read; only the flagged-FP marker in group 4 is.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load():
    path = ROOT / "scripts" / "scrutinize-replay.py"
    spec = importlib.util.spec_from_file_location("scrutinize_replay_roundtrip", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


replay = _load()

_FROM = "2026-08-01"
_TO = "2026-08-31"


@pytest.fixture
def scrutiny(tmp_path, monkeypatch):
    from scripts.utils import scrutinize_record as rec
    d = tmp_path / "scrutiny"
    d.mkdir()
    monkeypatch.setattr(rec, "record_path", lambda: d / "runs.jsonl")
    monkeypatch.setattr(replay, "SCRUTINY_DIR", d)
    return d


def _seed(directory: Path, finding_ids: list[str]) -> str:
    """One report plus one verdict row per id, so the record path is the source."""
    from scripts.utils import scrutinize_record as rec
    sid = "2026-08-15_execution"
    (directory / f"{sid}.md").write_text("saved report body\n", encoding="utf-8")
    for fid in finding_ids:
        rec.append_row(run_id=sid, kind="verdict", target="dir:scripts",
                       finding_id=fid, pass_="2.5a", judge_family="kimi",
                       verdict="REFUTED")
    assert rec.rows_for(sid), "the record this test reasons over must be non-empty"
    return sid


def _generate_and_fill(directory: Path, out: Path, ids: list[str]) -> None:
    _seed(directory, ids)
    assert replay.main(["--from", _FROM, "--to", _TO, "--sample", str(len(ids)),
                        "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    headings = [ln for ln in text.splitlines() if ln.startswith("### ")]
    assert len(headings) == len(ids), (
        f"the sheet did not render every finding: {headings}")
    out.write_text(text.replace("`<CEO: ?>`", "`agree`"), encoding="utf-8")


def test_an_id_outside_the_bhmln_shape_still_reads_back(scrutiny, tmp_path):
    out = tmp_path / "sheet.md"
    _generate_and_fill(scrutiny, out, ["B-1"])
    assert replay.compute_kappa_from_sheet(out) == 0


def test_one_odd_id_does_not_destroy_the_rows_beside_it(scrutiny, tmp_path):
    out = tmp_path / "sheet.md"
    _generate_and_fill(scrutiny, out, ["B1", "B-1"])
    assert replay.compute_kappa_from_sheet(out) == 0


def test_a_conventional_sheet_still_reads_back(scrutiny, tmp_path):
    """The anchor: widening the id class must not break the ordinary shape."""
    out = tmp_path / "sheet.md"
    _generate_and_fill(scrutiny, out, ["B1", "H2", "M3"])
    assert replay.compute_kappa_from_sheet(out) == 0


def test_the_flagged_fp_marker_is_still_read_from_the_header(scrutiny, tmp_path):
    """Group 4 is the only group kappa consumes, so widening group 1 must not
    shift it. A flagged finding rated `agree` is a scrutinize/CEO DISAGREEMENT,
    which is the pair the confusion matrix has to see."""
    sid = _seed(scrutiny, ["B-7"])
    from scripts.utils import scrutinize_record as rec
    rec.append_row(run_id=sid, kind="fp_flag", target="dir:scripts",
                   finding_id="B-7", writer="flag-fp")
    out = tmp_path / "sheet.md"
    assert replay.main(["--from", _FROM, "--to", _TO, "--sample", "1",
                        "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "was flagged FP at the time" in text
    header = replay._HEADER_RE.search(text)
    assert header is not None
    assert header.group(4), "the flagged-FP marker was lost to the widened id class"


def test_a_malformed_sheet_is_still_refused(scrutiny, tmp_path):
    """The negative case. The widened regex must not start matching prose."""
    sheet = tmp_path / "broken.md"
    sheet.write_text("### not a numbered finding heading\n"
                     "**CEO rating:** `agree`\n", encoding="utf-8")
    assert replay.compute_kappa_from_sheet(sheet) == 4
