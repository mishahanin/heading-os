"""A CEO false-positive flag is a boolean, not a counter.

`scrutinize-flag-fp.py` calls the operator's disagreement "the only ground truth
this system will ever get", and the FP-rate-by-severity statistic this script
exists to print is computed off the rows it writes.

Measured 2026-08-30 against the live script. `--ids B1,B1` appended two
identical rows and reported "Flagged 2 finding(s)"; a SECOND invocation naming
B1 again (to attach a note, say) appended a third. The tally then printed
`FP tally: 3 recorded - BLOCKER=3` for one finding, while
`scrutinize-replay.load_fp_set` read the very same file as ONE
`(run_id, finding_id)` pair. Two numbers computed from one record disagreed, and
the inflated one was the one the operator saw.

The record is append-only, so the two halves of the fix are separate: dedupe the
requested ids, and read what is already flagged before writing. The tally counts
distinct pairs, which also makes it honest over duplicates already on disk.
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


flag_fp = _load("scrutinize_flag_fp_dedupe", "scrutinize-flag-fp.py")
replay = _load("scrutinize_replay_fpset", "scrutinize-replay.py")

_SID = "2026-08-30_execution"
_REPORT = (
    "[B1] (conf: 92) the resolver reads a path it never normalised\n"
    "Location: scripts/example.py:41\n"
    "Evidence: the prefix check runs on the raw string\n"
    "\n"
    "[H2] (conf: 70) the retry loop has no ceiling\n"
    "Location: scripts/example.py:88\n"
)


@pytest.fixture
def record(tmp_path, monkeypatch):
    from scripts.utils import scrutinize_record as rec
    d = tmp_path / "scrutiny"
    d.mkdir()
    path = d / "runs.jsonl"
    monkeypatch.setattr(rec, "record_path", lambda: path)
    monkeypatch.setattr(flag_fp, "scrutiny_dir", lambda p=d: p)
    (d / f"{_SID}.md").write_text(_REPORT, encoding="utf-8")
    assert flag_fp.parse_findings_from_report(d / f"{_SID}.md"), \
        "the report this test flags against must parse to a non-empty finding set"
    return path


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def _fp_rows(path: Path) -> list[dict]:
    return [r for r in _rows(path) if r.get("kind") == "fp_flag"]


def test_a_repeated_id_in_one_invocation_writes_one_row(record):
    assert flag_fp.main(["--scrutiny-id", _SID, "--ids", "B1,B1,b1"]) == 0
    rows = _fp_rows(record)
    assert len(rows) == 1
    assert rows[0]["finding_id"] == "B1"


def test_re_flagging_in_a_later_invocation_writes_no_second_row(record):
    assert flag_fp.main(["--scrutiny-id", _SID, "--ids", "B1"]) == 0
    assert flag_fp.main(["--scrutiny-id", _SID, "--ids", "B1",
                         "--notes", "adding the reason I left out"]) == 0
    assert len(_fp_rows(record)) == 1


def test_a_new_id_still_lands_beside_an_already_flagged_one(record):
    """The anchor. The duplicate guard must not swallow the un-flagged sibling."""
    assert flag_fp.main(["--scrutiny-id", _SID, "--ids", "B1"]) == 0
    assert flag_fp.main(["--scrutiny-id", _SID, "--ids", "B1,H2"]) == 0
    flagged = sorted(r["finding_id"] for r in _fp_rows(record))
    assert flagged == ["B1", "H2"]


def test_the_same_finding_id_under_a_different_run_is_not_a_duplicate(record, tmp_path):
    """The dedupe key is the PAIR. B1 of one scrutiny is not B1 of another."""
    other = "2026-08-31_workspace"
    (record.parent / f"{other}.md").write_text(_REPORT, encoding="utf-8")
    assert flag_fp.main(["--scrutiny-id", _SID, "--ids", "B1"]) == 0
    assert flag_fp.main(["--scrutiny-id", other, "--ids", "B1"]) == 0
    assert len(_fp_rows(record)) == 2


def test_the_printed_tally_agrees_with_the_set_the_benchmark_consumes(record, capsys):
    """The disagreement the audit named: this script's tally versus replay's set.

    Duplicates are written directly here, bypassing the CLI guard, because the
    record already on disk carries some and the tally must be honest over them.
    """
    from scripts.utils import scrutinize_record as rec
    for _ in range(3):
        rec.append_row(run_id=_SID, kind="fp_flag", target="execution",
                       finding_id="B1", writer="flag-fp")
    assert len(_fp_rows(record)) == 3, "the duplicated corpus was not built"

    flag_fp.print_running_tally()
    printed = capsys.readouterr().out
    assert "BLOCKER=1" in printed
    assert "FP tally: 1 recorded" in printed
    assert len(replay.load_fp_set()) == 1


def test_a_missing_id_is_still_reported_after_the_dedupe(record):
    """Deduping must happen before the membership check, not instead of it."""
    assert flag_fp.main(["--scrutiny-id", _SID, "--ids", "B1,B1,Z9"]) == 4
    assert _fp_rows(record) == []


def test_two_distinct_ids_still_write_two_rows(record):
    assert flag_fp.main(["--scrutiny-id", _SID, "--ids", "B1,H2"]) == 0
    assert len(_fp_rows(record)) == 2
