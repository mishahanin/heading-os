#!/usr/bin/env python3
"""Two ways `scrutinize_record.append_row` misreported what it had done.

Its whole purpose is not trusting unverified claims of completeness, and both
defects were of that shape.

1. `_check` says "Refuse a row the record must never carry. Raises ValueError."
   Every refusal in it is a ValueError except two, which called `.get` on a
   value the caller supplied. Measured 2026-08-30:
   `append_row(kind="currency", currency="ok")` and
   `append_row(kind="verdict", verdict="REPRODUCED", reproduction="nope")` both
   raised `AttributeError: 'str' object has no attribute 'get'`, so a caller
   catching ValueError to report a rejected row crashed instead of refusing it.

2. The return of `os.write` was discarded. Measured with `os.write` stubbed to
   take 20 bytes: `append_row` returned the row as written, the file held
   `{"run_id": "r", "ts"`, and `iter_rows` dropped that line on
   `JSONDecodeError` and answered `[]`. A verdict row vanished with nothing
   raising anywhere.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils import scrutinize_record as sr  # noqa: E402


@pytest.fixture
def record(tmp_path, monkeypatch):
    """Point the record at a temp file. Nothing here touches the real tree."""
    path = tmp_path / "runs.jsonl"
    monkeypatch.setattr(sr, "record_path", lambda: path)
    return path


def test_a_non_mapping_currency_is_refused_with_the_documented_valueerror(record):
    with pytest.raises(ValueError, match="currency must be a mapping"):
        sr.append_row(run_id="r", kind="currency", target="t", currency="ok")


def test_a_non_mapping_reproduction_is_refused_with_the_documented_valueerror(record):
    with pytest.raises(ValueError, match="reproduction must be a mapping"):
        sr.append_row(run_id="r", kind="verdict", target="t",
                      verdict="REPRODUCED", reproduction="nope")


def test_a_refused_row_is_not_written(record):
    """A refusal that half-wrote would be worse than the crash it replaced."""
    with pytest.raises(ValueError):
        sr.append_row(run_id="r", kind="currency", target="t", currency="ok")
    assert not record.exists()


def test_a_well_formed_currency_row_still_lands(record):
    """The negative case: the type guard must not refuse a legal row."""
    sr.append_row(run_id="r", kind="currency", target="t",
                  currency={"result": sorted(sr.CURRENCY_RESULTS)[0]})
    rows = sr.iter_rows()
    assert len(rows) == 1
    assert rows[0]["kind"] == "currency"


def test_a_currency_mapping_with_an_illegal_result_is_still_refused(record):
    """The pre-existing refusal must survive the type guard in front of it."""
    with pytest.raises(ValueError, match="unknown currency result"):
        sr.append_row(run_id="r", kind="currency", target="t",
                      currency={"result": "not-a-real-result"})


def test_the_measured_twenty_byte_write_no_longer_vanishes_the_row(record, monkeypatch):
    """The exact stub that produced `{"run_id": "r", "ts"` and an empty read."""
    real_write = os.write

    def truncating(fd, data):
        return real_write(fd, data[:20])

    monkeypatch.setattr(os, "write", truncating)
    sr.append_row(run_id="r", kind="pass_start", target="t")

    raw = record.read_bytes()
    assert raw.endswith(b"\n"), f"the row was truncated on disk: {raw!r}"
    rows = sr.iter_rows()
    assert len(rows) == 1, f"the row was dropped by iter_rows: {raw!r}"
    assert rows[0]["run_id"] == "r"


def test_a_write_that_makes_no_progress_raises_instead_of_looping_or_lying(
        record, monkeypatch):
    """Zero bytes accepted is not "written". It must not spin, and must not pass."""
    monkeypatch.setattr(os, "write", lambda fd, data: 0)
    with pytest.raises(OSError, match="short write"):
        sr.append_row(run_id="r", kind="pass_start", target="t")


def test_a_partial_write_that_can_be_completed_is_completed(record, monkeypatch):
    """A write that lands in pieces must still produce one whole, readable row."""
    real_write = os.write
    chunks = []

    def piecewise(fd, data):
        chunks.append(len(data))
        return real_write(fd, data[:7])

    monkeypatch.setattr(os, "write", piecewise)
    sr.append_row(run_id="r", kind="pass_start", target="t")
    # No `monkeypatch.undo()` here: it would also undo the `record_path` patch
    # in the `record` fixture and send `iter_rows` at the OPERATOR's real
    # record. `iter_rows` reads through `read_text` and never calls `os.write`,
    # so the stub above costs it nothing.

    assert len(chunks) > 1, "the loop never had to retry; the test proves nothing"
    rows = sr.iter_rows()
    assert len(rows) == 1, f"the row did not survive a piecewise write: {rows!r}"
    assert rows[0]["run_id"] == "r"


def test_an_ordinary_row_round_trips(record):
    """The control: without a non-empty corpus every assertion above is vacuous."""
    sr.append_row(run_id="r", kind="pass_start", target="t")
    sr.append_row(run_id="r", kind="role", target="t", role=sorted(sr.ROLES)[0])
    rows = sr.rows_for("r")
    assert [row["kind"] for row in rows] == ["pass_start", "role"]
