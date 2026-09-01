"""Unit tests for /pipeline source."""
import json
import logging
from datetime import date
from pathlib import Path

from scripts.bridge_daemon.sources.pipeline import (
    TOUCH_LOG_FILE,
    list_pipeline,
    mark_touched,
    read_touch_log,
)


def _write_pipeline(workspace_root, content):
    p = workspace_root / "context" / "pipeline.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_empty_when_file_missing(tmp_path):
    """Missing pipeline.md -> empty result."""
    result = list_pipeline(tmp_path)
    assert result["deals"] == []
    assert result["counts"] == {}
    assert result["overdue_count"] == 0
    assert result["total_value_usd"] == 0
    assert result["data_time"] is None


def test_basic_row_parsed(tmp_path):
    """A single active row is parsed correctly."""
    _write_pipeline(tmp_path,
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---------|---------|-------|------------|------------|-------|-------------|----------|\n"
        "| Acme Co | USA | Proposal | $1,000,000 | 2026-05-01 | Misha | Send NDA | 2026-05-25 |\n"
    )
    result = list_pipeline(tmp_path, today=date(2026, 5, 18))
    assert len(result["deals"]) == 1
    d = result["deals"][0]
    assert d["company"] == "Acme Co"
    assert d["country"] == "USA"
    assert d["stage"] == "Proposal"
    assert d["value_usd"] == 1_000_000
    assert d["value_display"] == "$1,000,000"
    assert d["owner"] == "Misha"
    assert d["next_action"] == "Send NDA"
    assert d["due_date"] == "2026-05-25"
    assert d["days_until_due"] == 7
    assert d["is_overdue"] is False


def test_tbd_value_handled(tmp_path):
    """TBD values yield value_usd=None and preserved display."""
    _write_pipeline(tmp_path,
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---------|---------|-------|------------|------------|-------|-------------|----------|\n"
        "| Acme Ltd | UK | Proposal | TBD | 2026-03-02 | Misha | DEPRIORITIZED. | - |\n"
        "| Vortex | DE | Demo/POC | TBD (rev share, 3yr) | 2026-04-01 | Misha | call | - |\n"
    )
    result = list_pipeline(tmp_path)
    assert result["deals"][0]["value_usd"] is None
    assert result["tbd_count"] == 2


def test_overdue_detection(tmp_path):
    """Deals with due_date < today are marked overdue."""
    _write_pipeline(tmp_path,
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---------|---------|-------|------------|------------|-------|-------------|----------|\n"
        "| Stale Co | XX | Lead | TBD | 2026-03-01 | Misha | Follow up | 2026-04-01 |\n"
    )
    result = list_pipeline(tmp_path, today=date(2026, 5, 18))
    d = result["deals"][0]
    assert d["is_overdue"] is True
    assert d["days_until_due"] == -47
    assert result["overdue_count"] == 1


def test_sort_by_stage_desc(tmp_path):
    """Won first, then Negotiation, then Proposal, then Demo/POC, etc."""
    _write_pipeline(tmp_path,
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---------|---------|-------|------------|------------|-------|-------------|----------|\n"
        "| Lead Co | A | Lead | TBD | 2026-03-01 | M | a | - |\n"
        "| Won Co | B | Won | $1,000,000 | 2026-03-01 | M | b | - |\n"
        "| Qual Co | C | Qualified | TBD | 2026-03-01 | M | c | - |\n"
        "| Neg Co | D | Negotiation | $500,000 | 2026-03-01 | M | d | - |\n"
    )
    result = list_pipeline(tmp_path)
    companies = [d["company"] for d in result["deals"]]
    assert companies == ["Won Co", "Neg Co", "Qual Co", "Lead Co"]


def test_total_value_sums_priced_deals(tmp_path):
    """total_value_usd sums only priced (non-TBD) deals."""
    _write_pipeline(tmp_path,
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---------|---------|-------|------------|------------|-------|-------------|----------|\n"
        "| A | X | Negotiation | $1,000,000 | 2026-03-01 | M | a | - |\n"
        "| B | X | Negotiation | $2,500,000 | 2026-03-01 | M | b | - |\n"
        "| C | X | Lead | TBD | 2026-03-01 | M | c | - |\n"
    )
    result = list_pipeline(tmp_path)
    assert result["total_value_usd"] == 3_500_000


# ============================================================
# The third row-loss path: a pipe inside a cell
# ============================================================
# A row carrying more than nine pipes cannot be parsed into the right columns,
# so `list_pipeline` drops it, and until 2026-08-31 it did so in silence: a
# $4,000,000 Negotiation row vanished and `total_value_usd` came back 2,000,000
# against a real 6,000,000. The drop, the bound on the line, and the headline
# total are covered in full by
# `tests/bridge/test_a_wide_pipeline_row_that_vanished_with_no_log.py`, which
# landed the same day. What is left here is the REMEDY the warning offers,
# which that file does not assert and which was wrong.


def test_the_dropped_row_warning_does_not_recommend_escaping(tmp_path, caplog):
    """The advice has to be advice that works.

    The first version of this warning read "Escape it or reword the cell".
    MEASURED 2026-08-31: `line.count("|")` counts a markdown-escaped `\\|`
    exactly like a raw one, so the escaped row is dropped by this same branch
    with this same message, and an operator who followed the sentence edited
    their pipeline into a row that still disappears. Honouring the escape would
    be worse than dropping it: `_ROW_RE`'s cells are `[^|]`, so the row parses
    with every column after the pipe shifted by one.
    """
    escaped = ("| Beta Co | UK | Negotiation | $4,000,000 | 2026-05-02 | M | "
               "Send revised SOW \\| legal review | 2026-06-01 |\n")
    good = "| Acme Co | USA | Proposal | $2,000,000 | 2026-05-01 | M | Send NDA | - |\n"
    _write_pipeline(tmp_path,
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---------|---------|-------|------------|------------|-------|-------------|----------|\n"
        + good + escaped)

    with caplog.at_level(logging.WARNING):
        result = list_pipeline(tmp_path, today=date(2026, 5, 18))

    assert [d["company"] for d in result["deals"]] == ["Acme Co"]
    dropped = [r for r in caplog.records if "dropping a row" in r.getMessage()]
    assert len(dropped) == 1, caplog.text
    message = dropped[0].getMessage()
    assert "escape it" not in message.lower(), message
    assert "reword" in message.lower(), message


def test_investor_conversations_section_not_included(tmp_path):
    """Rows from sections AFTER '## Active Deals' are NOT included."""
    _write_pipeline(tmp_path,
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---------|---------|-------|------------|------------|-------|-------------|----------|\n"
        "| Real Deal | A | Lead | TBD | 2026-03-01 | M | a | - |\n"
        "\n## Investor Conversations\n\n"
        "| Investor | X | Y | Z | W | M | a | - |\n"
    )
    result = list_pipeline(tmp_path)
    assert len(result["deals"]) == 1
    assert result["deals"][0]["company"] == "Real Deal"


# ============================================================
# Phase 1.55: touch tracking
# ============================================================
def test_mark_touched_writes_log(tmp_path):
    r = mark_touched(tmp_path, "Acme Co", note="called Bob")
    assert r["ok"] is True
    assert r["company_key"] == "acme co"
    log = tmp_path / TOUCH_LOG_FILE
    assert log.exists()
    text = log.read_text(encoding="utf-8")
    assert "acme co" in text
    assert "called Bob" in text


def test_mark_touched_rejects_empty_company(tmp_path):
    assert mark_touched(tmp_path, "")["ok"] is False
    assert mark_touched(tmp_path, "   ")["ok"] is False
    assert mark_touched(tmp_path, "X" * 250)["ok"] is False


def test_mark_touched_strips_newlines_from_note(tmp_path):
    mark_touched(tmp_path, "Acme", note="line1\nline2\rline3")
    log = tmp_path / TOUCH_LOG_FILE
    text = log.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln]
    assert len(lines) == 1  # single-line JSON entry
    # The `\r` in the fixture asserted nothing until 2026-08-30. A sanitiser
    # written `note.replace("\n", " ")` alone leaves the carriage return in the
    # stored value; `json.dumps` escapes it, so the file stays one physical
    # line and `len(lines) == 1` still passes while the log holds a raw CR.
    # Read the decoded value, not the encoded text.
    assert "\\r" not in text
    note = json.loads(lines[0])["note"]
    assert "\r" not in note and "\n" not in note, repr(note)


def test_company_key_normalises_parentheticals(tmp_path):
    """'Acme (via reseller)' and 'Acme' resolve to the SAME key.

    BOTH FORMS MARKED, since 2026-08-30. The docstring always claimed a two-way
    equivalence and the body only ever marked the parenthetical one, so the
    equality it names went untested: an implementation that strips
    parentheticals but keys the plain form differently -- failing to lowercase,
    say, and yielding "Acme" -- left this green, and
    `test_list_pipeline_joins_touch_log` could not catch it either because it
    marks with the exact string the pipeline table carries.
    """
    mark_touched(tmp_path, "Acme (via Reseller Inc)")
    parenthetical = set(read_touch_log(tmp_path))
    assert "acme" in parenthetical  # parenthetical stripped

    mark_touched(tmp_path, "Acme")
    plain = set(read_touch_log(tmp_path))
    assert "acme" in plain
    assert plain == parenthetical, (
        f"the plain form keyed differently: {sorted(plain - parenthetical)}")


def test_list_pipeline_joins_touch_log(tmp_path):
    _write_pipeline(tmp_path,
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---------|---------|-------|------------|------------|-------|-------------|----------|\n"
        "| Acme Co | UK | Negotiation | TBD | 2026-01-01 | M | Send NDA | - |\n"
        "| Beta Co | DE | Proposal | TBD | 2026-01-01 | M | Follow up | - |\n"
    )
    mark_touched(tmp_path, "Acme Co", note="called")
    result = list_pipeline(tmp_path, today=date(2026, 5, 18))
    by_company = {d["company"]: d for d in result["deals"]}
    assert by_company["Acme Co"]["touched_date"]
    assert by_company["Acme Co"]["touched_note"] == "called"
    assert isinstance(by_company["Acme Co"]["days_since_touched"], int)
    assert by_company["Beta Co"]["touched_date"] is None
    assert by_company["Beta Co"]["days_since_touched"] is None
    assert result["touched_total"] == 1


def test_corrupt_touch_log_line_is_skipped(tmp_path):
    """A garbage line in _touch-log.jsonl does not poison the read."""
    log = tmp_path / TOUCH_LOG_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        '{"company": "Acme", "company_key": "acme", "date": "2026-05-18", "ts": "x", "note": ""}\n'
        'not json at all\n'
        '{"missing_key": true}\n',
        encoding="utf-8",
    )
    out = read_touch_log(tmp_path)
    assert "acme" in out
    assert len(out) == 1
