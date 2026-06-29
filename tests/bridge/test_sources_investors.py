"""Unit tests for /investors source."""
from pathlib import Path

from scripts.bridge_daemon.sources.investors import (
    PROGRAM_DIR,
    SEND_LOG_FILE,
    list_investors,
    mark_sent,
    read_dossier,
    undo_sent,
)


def _write_shortlist(workspace_root: Path, content: str) -> None:
    target = workspace_root / PROGRAM_DIR / "00-master-shortlist-v1.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _write_dossier(workspace_root: Path, filename: str, content: str = "# dossier") -> Path:
    target = workspace_root / PROGRAM_DIR / "dossiers" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _write_message(workspace_root: Path, filename: str, content: str = "# message") -> Path:
    target = workspace_root / PROGRAM_DIR / "messages" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def test_empty_when_program_missing(tmp_path):
    result = list_investors(tmp_path)
    assert result["firms"] == []
    assert result["counts"] == {}
    assert result["total"] == 0
    assert result["data_time"] is None
    assert result["raise_target"] is None


def test_parses_regional_row(tmp_path):
    _write_shortlist(tmp_path,
        "raise posture: $25-40M anchor\n\n"
        "## Europe (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 8 | DTCP | Independent growth VC | Hamburg | EUR 20-60M | HIGH | Operator DNA via DT |\n"
    )
    result = list_investors(tmp_path)
    assert result["total"] == 1
    assert result["raise_target"] == "$25-40M"
    f = result["firms"][0]
    assert f["num"] == 8
    assert f["firm"] == "DTCP"
    assert f["region"] == "Europe"
    assert f["hq"] == "Hamburg"
    assert f["fit"] == "HIGH"
    assert f["status"] == "TBD"  # no decisions section -> unassigned


def test_decisions_locked_assigns_status(tmp_path):
    _write_shortlist(tmp_path,
        "## Europe (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 8 | DTCP | VC | Hamburg | EUR 20M | HIGH | Notes |\n\n"
        "# Decisions locked\n\n"
        "## In-scope firms\n\n"
        "| Slot | Firm | Wave | Notes |\n"
        "|------|------|------|-------|\n"
        "| First 5 (this week) | DTCP (Vento) | Week 1 | Send-ready |\n"
    )
    result = list_investors(tmp_path)
    assert result["firms"][0]["status"] == "first-5"
    assert result["firms"][0]["status_label"] == "First 5"


def test_acronym_match_for_nif(tmp_path):
    """NIF in decisions table matches 'NATO Innovation Fund' in regional table."""
    _write_shortlist(tmp_path,
        "## Europe (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 11 | NATO Innovation Fund (NIF) | VC | Amsterdam | EUR 5-15M | HIGH | mandate |\n\n"
        "# Decisions locked\n\n"
        "## In-scope firms\n\n"
        "| Slot | Firm | Wave | Notes |\n"
        "|------|------|------|-------|\n"
        "| Parallel-track Week 1-2 | NIF (Schneider-Sikorsky) | Week 1-2 | Send-ready |\n"
    )
    result = list_investors(tmp_path)
    assert result["firms"][0]["status"] == "parallel-week-1-2"


def test_first_token_match_for_Northgate(tmp_path):
    """Northgate NGCI in decisions table matches 'Northgate Capital' in regional table via first-token."""
    _write_shortlist(tmp_path,
        "## US (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 15 | Northgate Capital (FPCI London) | Cyber VC | SF + London | 15-40M | HIGH | Santander LP base |\n\n"
        "# Decisions locked\n\n"
        "| Slot | Firm | Wave | Notes |\n"
        "|------|------|------|-------|\n"
        "| First 5 (this week) | Northgate NGCI (Henault) | Week 1 | Send-ready |\n"
    )
    result = list_investors(tmp_path)
    assert result["firms"][0]["status"] == "first-5"


def test_dossier_path_matched_by_number(tmp_path):
    """Numeric prefix is the primary match key for dossier files."""
    _write_shortlist(tmp_path,
        "## Europe (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 8 | DTCP | VC | Hamburg | 20M | HIGH | x |\n"
    )
    _write_dossier(tmp_path, "08-dtcp.md")
    _write_message(tmp_path, "08-dtcp-first-touch.md")
    result = list_investors(tmp_path)
    f = result["firms"][0]
    assert f["dossier_path"].endswith("/dossiers/08-dtcp.md")
    assert f["message_path"].endswith("/messages/08-dtcp-first-touch.md")


def test_out_of_scope_glilot_captured(tmp_path):
    """Free-text 'Glilot+' mention assigns out-of-scope status to 'Glilot Capital'."""
    _write_shortlist(tmp_path,
        "## UK / Israel (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 19 | Glilot Capital (Glilot+ growth platform) | Cyber VC | Tel Aviv | 10-30M | MED-HIGH | x |\n\n"
        "# Decisions locked\n\n"
        "## Out-of-scope this round\n\n"
        "- **Glilot+** -- dropped this round to avoid Rinat Remler chokepoint with JVP.\n"
    )
    result = list_investors(tmp_path)
    assert result["firms"][0]["status"] == "out-of-scope"


def test_firms_sorted_by_status_rank(tmp_path):
    _write_shortlist(tmp_path,
        "## Europe (3)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 8 | A | VC | X | 10M | HIGH | x |\n"
        "| 9 | B | VC | X | 10M | HIGH | x |\n"
        "| 10 | C | VC | X | 10M | HIGH | x |\n\n"
        "# Decisions locked\n\n"
        "| Slot | Firm | Wave | Notes |\n"
        "|------|------|------|-------|\n"
        "| First 5 (this week) | C | Week 1 | x |\n"
        "| Parallel-track Week 1-2 | B | Week 1-2 | x |\n"
        "| Wave 2 (warm-intro-first) | A | Week 2-3 | x |\n"
    )
    result = list_investors(tmp_path)
    names = [f["firm"] for f in result["firms"]]
    assert names == ["C", "B", "A"]


def test_read_dossier_happy_path(tmp_path):
    _write_dossier(tmp_path, "08-dtcp.md", content="# DTCP\n\nDeep notes here.")
    rel_path = f"{PROGRAM_DIR}/dossiers/08-dtcp.md"
    r = read_dossier(tmp_path, rel_path)
    assert r["ok"] is True
    assert "DTCP" in r["content"]
    assert r["size"] > 0


def test_read_dossier_blocks_traversal(tmp_path):
    r = read_dossier(tmp_path, "../etc/passwd")
    assert r["ok"] is False
    assert "program" in r["error"]


def test_read_dossier_blocks_dotdot_segments(tmp_path):
    rel_path = f"{PROGRAM_DIR}/dossiers/../../../etc/passwd"
    r = read_dossier(tmp_path, rel_path)
    assert r["ok"] is False


def test_read_dossier_rejects_non_md(tmp_path):
    target = tmp_path / PROGRAM_DIR / "dossiers" / "evil.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("nope", encoding="utf-8")
    rel_path = f"{PROGRAM_DIR}/dossiers/evil.exe"
    r = read_dossier(tmp_path, rel_path)
    assert r["ok"] is False
    assert "md" in r["error"]


def test_read_dossier_missing_path(tmp_path):
    assert read_dossier(tmp_path, "")["ok"] is False
    assert read_dossier(tmp_path, None)["ok"] is False  # type: ignore[arg-type]


# ============================================================
# Phase 1.36: send-log + mark_sent
# ============================================================
def test_mark_sent_writes_log_entry(tmp_path):
    r = mark_sent(tmp_path, 7, note="sent via Outlook to Kamieniecky")
    assert r["ok"] is True
    assert r["firm_num"] == 7
    log_path = tmp_path / PROGRAM_DIR / SEND_LOG_FILE
    assert log_path.exists()
    text = log_path.read_text(encoding="utf-8")
    assert "Kamieniecky" in text
    assert '"firm_num": 7' in text


def test_mark_sent_rejects_bad_firm_num(tmp_path):
    assert mark_sent(tmp_path, "seven")["ok"] is False  # type: ignore[arg-type]
    assert mark_sent(tmp_path, 0)["ok"] is False
    assert mark_sent(tmp_path, 999)["ok"] is False


def test_mark_sent_strips_newlines_from_note(tmp_path):
    r = mark_sent(tmp_path, 1, note="line1\nline2\rline3")
    assert r["ok"] is True
    text = (tmp_path / PROGRAM_DIR / SEND_LOG_FILE).read_text(encoding="utf-8")
    assert "\\n" not in text  # not escaped-encoded
    # The body line itself should be single-line JSON.
    lines = [ln for ln in text.splitlines() if ln]
    assert len(lines) == 1


def test_mark_sent_appends_multiple_entries(tmp_path):
    mark_sent(tmp_path, 1)
    mark_sent(tmp_path, 2)
    mark_sent(tmp_path, 1, note="re-mark")
    log_path = tmp_path / PROGRAM_DIR / SEND_LOG_FILE
    lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln]
    assert len(lines) == 3


def test_list_investors_joins_send_log(tmp_path):
    """Sent firms get sent_date + sent_note; sent_total counts them."""
    _write_shortlist(tmp_path,
        "## Europe (2)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 8 | DTCP | VC | Hamburg | 20M | HIGH | x |\n"
        "| 9 | Lakestar | VC | Zurich | 30M | HIGH | x |\n"
    )
    mark_sent(tmp_path, 8, note="Vento, LinkedIn")
    result = list_investors(tmp_path)
    by_num = {f["num"]: f for f in result["firms"]}
    assert by_num[8]["sent_date"]
    assert by_num[8]["sent_note"] == "Vento, LinkedIn"
    assert by_num[9]["sent_date"] is None
    assert result["sent_total"] == 1


def test_list_investors_sent_total_zero_when_no_log(tmp_path):
    _write_shortlist(tmp_path,
        "## Europe (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 8 | DTCP | VC | Hamburg | 20M | HIGH | x |\n"
    )
    result = list_investors(tmp_path)
    assert result["sent_total"] == 0


def test_undo_sent_cancels_prior_mark(tmp_path):
    """undo_sent appends a tombstone; list_investors sees the firm as unsent."""
    _write_shortlist(tmp_path,
        "## Europe (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 8 | DTCP | VC | Hamburg | 20M | HIGH | x |\n"
    )
    mark_sent(tmp_path, 8, note="oops")
    assert list_investors(tmp_path)["firms"][0]["sent_date"] is not None
    r = undo_sent(tmp_path, 8)
    assert r["ok"] is True
    assert list_investors(tmp_path)["firms"][0]["sent_date"] is None


def test_undo_sent_idempotent_on_unmarked(tmp_path):
    """Undoing a never-marked firm is a no-op and still returns ok."""
    r = undo_sent(tmp_path, 8)
    assert r["ok"] is True


def test_undo_sent_rejects_bad_firm_num(tmp_path):
    assert undo_sent(tmp_path, "x")["ok"] is False  # type: ignore[arg-type]
    assert undo_sent(tmp_path, 0)["ok"] is False
    assert undo_sent(tmp_path, 999)["ok"] is False


def test_mark_undo_remark_cycle(tmp_path):
    """A subsequent mark restores the sent state after an undo."""
    _write_shortlist(tmp_path,
        "## Europe (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 8 | DTCP | VC | Hamburg | 20M | HIGH | x |\n"
    )
    mark_sent(tmp_path, 8)
    undo_sent(tmp_path, 8)
    mark_sent(tmp_path, 8, note="for real this time")
    f = list_investors(tmp_path)["firms"][0]
    assert f["sent_date"] is not None
    assert f["sent_note"] == "for real this time"


def test_send_log_corrupt_line_is_skipped(tmp_path):
    """A garbage line in _send-log.jsonl does not poison the rest."""
    _write_shortlist(tmp_path,
        "## Europe (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 8 | DTCP | VC | Hamburg | 20M | HIGH | x |\n"
    )
    log_path = tmp_path / PROGRAM_DIR / SEND_LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        '{"firm_num": 8, "date": "2026-05-18", "ts": "x", "note": ""}\n'
        'not json at all\n'
        '{"missing_firm_num_key": true}\n',
        encoding="utf-8",
    )
    result = list_investors(tmp_path)
    by_num = {f["num"]: f for f in result["firms"]}
    assert by_num[8]["sent_date"] == "2026-05-18"
    assert result["sent_total"] == 1
