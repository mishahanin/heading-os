"""Two substring skips in /investors that deleted real rows.

`sources/investors.py` decided "is this line the table header or the separator?"
by looking for substrings in the RAW line:

    if "---" in line: continue
    if "Firm" in line and "Type" in line and "HQ" in line: continue

A Notes cell is free text the operator writes, so both tests fire on real data.
`sources/pipeline.py` carried the identical pair, measured the loss on
2026-08-31, and replaced them with checks on the PARSED CELLS. The investors
parser was not touched, and it is the surface the Series B raise is run from.

MEASURED 2026-08-31 against the pre-fix tree. Three firm rows in, one out:

    three real firm rows -> total: 1 firms: ['Alpha Capital']

"intro pending --- awaiting warm path" was read as the separator. "Firm size,
Type and HQ to confirm" was read as the header. Both firms vanished from
/investors, from `counts`, from `total`, from the Pulse raise-progress card and
from unified search, with nothing logged at any level.

The regional table's fix is a DELETION rather than a cell-based re-check, and
that is the honest form: `_REGION_ROW_RE` starts with `\\d+`, so neither
`| # | Firm | Type | HQ | ... |` nor `|---|------|...|` can match it (verified
2026-08-31 against both literal rows), so the two skips could only ever destroy
rows that were already real. The decisions table is the opposite case: every
cell of `_DECISION_ROW_RE` is free text, both header and separator DO match it,
so there the checks moved onto the cells.

Second, narrower loss, same cause, measured the same day:

    statuses: {'Beta Ventures': 'first-5', 'Alpha Capital': 'TBD'}

A decision row whose Notes read "send-ready --- intro via LP" was read as the
separator, so the firm never entered `statuses` and the dashboard sorted it as
"TBD" (not yet placed) while the operator's own document had it in "First 5
(this week)".

Why the old tests could not see either: every Notes cell in
`test_sources_investors.py` is `x`, `Notes`, `mandate`, or a short phrase with
no hyphen run and no column name. The parser was only ever fed cells that
happen not to spell its own control tokens.
"""
from pathlib import Path

from scripts.bridge_daemon.sources.investors import (
    PROGRAM_DIR,
    _parse_status_from_decisions,
    list_investors,
)

HEADER = "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
SEPARATOR = "|---|------|------|----|--------|-----|-------|\n"


def _shortlist(root: Path, body: str) -> None:
    target = root / PROGRAM_DIR / "00-master-shortlist-v1.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


def test_a_dashed_note_does_not_delete_the_firm(tmp_path):
    """The row read as the separator. It carries a real cheque size."""
    _shortlist(tmp_path,
        "## Europe (2)\n\n" + HEADER + SEPARATOR +
        "| 8 | Alpha Capital | VC | Hamburg | EUR 20-60M | HIGH | plain note |\n"
        "| 9 | Beta Ventures | VC | Zurich | EUR 30M | HIGH | intro pending --- awaiting warm path |\n")

    got = list_investors(tmp_path)

    assert got["total"] == 2
    by_num = {f["num"]: f for f in got["firms"]}
    assert by_num[9]["firm"] == "Beta Ventures"
    assert by_num[9]["cheque"] == "EUR 30M"
    assert by_num[9]["notes"] == "intro pending --- awaiting warm path"


def test_a_note_naming_the_columns_does_not_delete_the_firm(tmp_path):
    """The row read as the header, because its Notes mention three column names."""
    _shortlist(tmp_path,
        "## Europe (2)\n\n" + HEADER + SEPARATOR +
        "| 8 | Alpha Capital | VC | Hamburg | EUR 20M | HIGH | plain note |\n"
        "| 10 | Gamma Partners | VC | Paris | EUR 25M | HIGH | Firm size, Type and HQ to confirm |\n")

    got = list_investors(tmp_path)

    assert got["total"] == 2
    by_num = {f["num"]: f for f in got["firms"]}
    assert by_num[10]["firm"] == "Gamma Partners"
    assert by_num[10]["hq"] == "Paris"


def test_the_real_header_and_separator_are_still_not_firms(tmp_path):
    """The other direction. Keeping the rows must not admit the table furniture.

    Without this, deleting the two skips could have been "fixed" by admitting
    everything, and the two tests above would still pass while /investors grew
    a firm called "Firm" and one called "---".
    """
    _shortlist(tmp_path,
        "## Europe (1)\n\n" + HEADER + SEPARATOR +
        "|:---:|:----:|:----:|:--:|:------:|:---:|:-----:|\n"
        "| 8 | Alpha Capital | VC | Hamburg | EUR 20M | HIGH | plain note |\n")

    got = list_investors(tmp_path)

    assert got["total"] == 1
    assert [f["firm"] for f in got["firms"]] == ["Alpha Capital"]


def test_a_dashed_note_does_not_lose_the_wave(tmp_path):
    """The decisions-table half: a status the CEO sorts the raise by."""
    _shortlist(tmp_path,
        "## Europe (2)\n\n" + HEADER + SEPARATOR +
        "| 8 | Alpha Capital | VC | Hamburg | EUR 20M | HIGH | x |\n"
        "| 9 | Beta Ventures | VC | Zurich | EUR 30M | HIGH | x |\n\n"
        "# Decisions locked\n\n"
        "## In-scope firms\n\n"
        "| Slot | Firm | Wave | Notes |\n"
        "|------|------|------|-------|\n"
        "| First 5 (this week) | Alpha Capital | Week 1 | send-ready --- intro via LP |\n"
        "| Wave 2 (warm-intro-first) | Beta Ventures | Week 2-3 | ok |\n")

    got = list_investors(tmp_path)

    statuses = {f["firm"]: f["status"] for f in got["firms"]}
    assert statuses == {"Alpha Capital": "first-5", "Beta Ventures": "wave-2"}
    assert got["counts"] == {"first-5": 1, "wave-2": 1}


def test_the_decisions_header_and_separator_are_still_not_firms(tmp_path):
    """The other direction for the decisions table, where both DO match the row
    regex and so both have to be recognised from the cells."""
    text = (
        "# Decisions locked\n\n"
        "## In-scope firms\n\n"
        "| Slot | Firm | Wave | Notes |\n"
        "|------|------|------|-------|\n"
        "| First 5 (this week) | Alpha Capital | Week 1 | ok |\n")

    statuses = _parse_status_from_decisions(text)

    assert statuses == {"alpha capital": "first-5"}, statuses
    # Neither piece of table furniture may become a status key: `_match_status`
    # matches on substrings, so a key of "firm" or "------" would re-label a
    # real firm.
    assert "firm" not in statuses
    assert not any(set(k) <= {"-", ":"} for k in statuses), statuses
