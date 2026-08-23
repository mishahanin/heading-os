"""The CEO's false-positive note must reach the record, not just the screen.

`scrutinize-flag-fp.py` calls the operator's disagreement "the only ground truth
this system will ever get". Until 2026-08-23 it built a record dict carrying the
statement, location, evidence and the note, then passed `append_row` six fields
that included none of them -- and printed "Note attached: ..." anyway. The
ground truth was discarded while the operator was told it was stored.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import scrutinize_record as rec

_spec = importlib.util.spec_from_file_location(
    "scrutinize_flag_fp", ROOT / "scripts" / "scrutinize-flag-fp.py")
flag_fp = importlib.util.module_from_spec(_spec)
sys.modules["scrutinize_flag_fp"] = flag_fp
_spec.loader.exec_module(flag_fp)

_NOTE = "the caller is validated two frames up, so this cannot be reached"


def _isolate(tmp_path: Path, monkeypatch):
    d = tmp_path / "scrutiny"
    d.mkdir()
    monkeypatch.setattr(rec, "record_path", lambda: d / "runs.jsonl")
    monkeypatch.setattr(flag_fp, "SCRUTINY_DIR", d)
    return d / "runs.jsonl"


def _rows(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_the_note_lands_in_the_record(tmp_path, monkeypatch):
    path = _isolate(tmp_path, monkeypatch)
    flag_fp.append_records([{
        "scrutiny_id": "scrutiny-2026-08-23-001",
        "finding_id": "F-1",
        "severity": "HIGH",
        "confidence": 80,
        "statement": "unreachable branch",
        "location": "scripts/x.py:41",
        "evidence": "the guard above returns first",
        "target_type": "file",
        "ceo_note": _NOTE,
        "flagged_at": "2026-08-23T00:00:00+00:00",
    }])
    rows = _rows(path)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "fp_flag"
    assert row["finding_id"] == "F-1"
    blob = json.dumps(row, ensure_ascii=False)
    assert _NOTE in blob, "the operator's note was dropped on the way to the record"


def test_the_statement_lands_too(tmp_path, monkeypatch):
    """Without it the row is an ID nobody can read six months later."""
    path = _isolate(tmp_path, monkeypatch)
    flag_fp.append_records([{
        "scrutiny_id": "scrutiny-2026-08-23-001",
        "finding_id": "F-2",
        "severity": "LOW",
        "confidence": None,
        "statement": "the sweep is quadratic",
        "location": "scripts/y.py:9",
        "evidence": "",
        "target_type": "file",
        "ceo_note": "",
        "flagged_at": "2026-08-23T00:00:00+00:00",
    }])
    blob = json.dumps(_rows(path)[0], ensure_ascii=False)
    assert "the sweep is quadratic" in blob
    assert "scripts/y.py:9" in blob
