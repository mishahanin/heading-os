r"""A priced deal row dropped from /pipeline with no log line at any level.

`scripts/bridge_daemon/sources/pipeline.py`'s `list_pipeline` refuses a row
whose raw text carries more than nine pipes, because a pipe inside a cell
shifts every column after it. The refusal is correct. Until 2026-08-31 it was
also completely silent, and it was the THIRD row-loss path in that one
function: the `"---" in line` substring skip and the
`"Company"`/`"Country"`/`"Stage"` substring skip above it had each already been
found eating real deals, and each was fixed. This one was left.

MEASURED 2026-08-31 against the live parser, logging configured at DEBUG so
nothing could be missed:

    | Company | Country | Stage | Est. Value | ... |
    |---|---|---|---|---|---|---|---|
    | Spectre Telecom | UK | Negotiation | $4,000,000 | 2026-08-01 | CEO |
      Send revised SOW \| legal review | 2026-09-15 |          <- 10 pipes
    | Universal Exports | AE | Lead | $2,000,000 | 2026-08-02 | CEO |
      Intro call | 2026-09-20 |                                <-  9 pipes

    deals returned: 1 of 2 rows
    total_value_usd: 2,000,000
       Universal Exports $2,000,000

Two rows in, one row out, a $4,000,000 Negotiation deal gone, the pipeline
total understated by $4,000,000, and zero log records emitted. The escape
`\|` is the CORRECT way to put a pipe in a markdown table cell, so the
operator did nothing wrong and had no way to learn that the row was not being
counted.

Why the existing tests could not see it. Full-suite branch coverage on
2026-08-31 (19,835 tests) reported:

    scripts/bridge_daemon/sources/pipeline.py  153  13  48  5  91%
        Missing ... 254-255, 297, 300

Line 297 was the `continue` inside that guard. Not one test in the tree had
ever driven a row through it, so there was nothing to notice its silence, and
`total_value_usd`, the number the dashboard shows as the headline pipeline KPI,
had no test that a dropped row would move.

The fix is a WARNING naming the row and the pipe count, not a rescue: a
column-shifted row parsed anyway would report the wrong country, stage and
value, which is a worse answer than a loud absence. This file pins both halves:
the row is still refused, and the refusal is now audible.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from scripts.bridge_daemon.sources.pipeline import list_pipeline

_HEADER = ("| Company | Country | Stage | Est. Value | Stage Date | Owner "
           "| Next Action | Due Date |\n|---|---|---|---|---|---|---|---|\n")

_NARROW = ("| Universal Exports | AE | Lead | $2,000,000 | 2026-08-02 | CEO "
           "| Intro call | 2026-09-20 |\n")
# The pipe is ESCAPED, which is the correct markdown for a literal pipe in a
# cell. `line.count("|")` counts it anyway, so the row reaches ten.
_WIDE = ("| Spectre Telecom | UK | Negotiation | $4,000,000 | 2026-08-01 "
         "| CEO | Send revised SOW \\| legal review | 2026-09-15 |\n")


def _write_pipeline(root: Path, rows: str) -> None:
    p = root / "context" / "pipeline.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("## Active Deals\n\n" + _HEADER + rows, encoding="utf-8")


def test_the_fixture_really_is_a_wide_row_and_a_narrow_one(tmp_path):
    """Floor. If the pipe counts drift, every assertion below stops deciding."""
    assert _WIDE.strip().count("|") == 10
    assert _NARROW.strip().count("|") == 9


def test_a_wide_row_is_dropped_but_says_so(tmp_path, caplog):
    _write_pipeline(tmp_path, _WIDE + _NARROW)
    with caplog.at_level(logging.WARNING):
        result = list_pipeline(tmp_path)

    assert result["total"] == 1, "the narrow row must still parse"
    assert [d["company"] for d in result["deals"]] == ["Universal Exports"]
    assert "Spectre Telecom" in caplog.text, (
        "a priced deal left the /pipeline view and nothing named it; this is "
        "the third silent row-loss path in this parser and the last one"
    )
    assert "10 pipes" in caplog.text, caplog.text


def test_the_dropped_row_is_missing_from_the_headline_total_too(tmp_path, caplog):
    """The number the loss actually shows up in.

    `total_value_usd` feeds the dashboard's headline pipeline KPI. With both
    rows counted it is 6,000,000; the measurement above got 2,000,000. Pinning
    the count alone would leave the money assertion to another file.
    """
    _write_pipeline(tmp_path, _WIDE + _NARROW)
    with caplog.at_level(logging.WARNING):
        result = list_pipeline(tmp_path)
    assert result["total_value_usd"] == 2_000_000
    assert "4,000,000" in caplog.text, (
        "the warning must carry the row verbatim, so the operator can see "
        "which money is not in the total"
    )


def test_a_clean_table_logs_nothing(tmp_path, caplog):
    """The negative case for the warning itself.

    Without this, `logger.warning` on every row would pass the two tests
    above and turn the daemon log into noise, which is how a real warning
    stops being read.
    """
    _write_pipeline(tmp_path, _NARROW)
    with caplog.at_level(logging.WARNING):
        result = list_pipeline(tmp_path)
    assert result["total"] == 1
    assert result["total_value_usd"] == 2_000_000
    assert [r for r in caplog.records
            if "pipeline" in r.name] == [], caplog.text


@pytest.mark.parametrize("added,pipes,kept", [(0, 9, True), (1, 10, False)])
def test_the_bound_is_on_the_line_not_near_it(tmp_path, caplog, added, pipes, kept):
    """A case ON the boundary in each direction, one pipe apart.

    Nine pipes is the widest well-formed row and must be KEPT; ten is the
    narrowest malformed one and must be reported. A guard tested only at 6 and
    at 20 would survive an off-by-one that eats every complete row, and one
    tested only on the refusal side would survive `if True: continue`.
    """
    row = ("| Bound Co | UK | Lead | $1 | 2026-08-01 | CEO | note"
           + "\\|" * added + " | 2026-09-01 |\n")
    assert row.strip().count("|") == pipes, "the fixture drifted off the bound"

    _write_pipeline(tmp_path, row)
    with caplog.at_level(logging.WARNING):
        result = list_pipeline(tmp_path)

    if kept:
        assert result["total"] == 1
        assert "Bound Co" not in caplog.text
    else:
        assert result["total"] == 0
        assert "Bound Co" in caplog.text
