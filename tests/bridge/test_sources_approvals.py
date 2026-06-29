"""Unit tests for /approvals source."""
from pathlib import Path

from scripts.bridge_daemon.sources.approvals import (
    EMAIL_DRAFTS_DIR,
    list_approvals,
    read_draft,
)


def _write_draft(tmp_path, filename, headers, body, h1=None):
    """Create a draft .md in EMAIL_DRAFTS_DIR with the given headers + body."""
    target = tmp_path / EMAIL_DRAFTS_DIR / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if h1:
        lines.append(f"# {h1}")
        lines.append("")
    for k, v in headers.items():
        lines.append(f"**{k}:** {v}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(body)
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def test_empty_when_dir_missing(tmp_path):
    r = list_approvals(tmp_path)
    assert r["items"] == []
    assert r["total"] == 0
    assert r["data_time"] is None


def test_parses_to_and_subject(tmp_path):
    _write_draft(tmp_path, "2026-05-18_mashreq.md", {
        "To": "compliance@example.com",
        "Subject": "Re: KYC update",
    }, "Body line 1\nBody line 2", h1="Mashreq KYC reply")
    r = list_approvals(tmp_path)
    assert r["total"] == 1
    it = r["items"][0]
    assert it["to"] == "compliance@example.com"
    assert it["subject"] == "Re: KYC update"
    assert it["title"] == "Mashreq KYC reply"
    assert it["kind"] == "email-draft"


def test_title_falls_back_to_filename(tmp_path):
    _write_draft(tmp_path, "2026-05-18_no-h1.md", {
        "To": "x@y.com",
        "Subject": "X",
    }, "body")
    r = list_approvals(tmp_path)
    assert "no h1" in r["items"][0]["title"]


def test_sorted_by_mtime_desc(tmp_path):
    """Newest draft first."""
    import time
    p1 = _write_draft(tmp_path, "old.md", {"To": "a@x.com", "Subject": "old"}, "x")
    time.sleep(0.05)
    p2 = _write_draft(tmp_path, "new.md", {"To": "b@x.com", "Subject": "new"}, "x")
    r = list_approvals(tmp_path)
    assert r["items"][0]["filename"] == "new.md"
    assert r["items"][1]["filename"] == "old.md"


def test_read_draft_happy_path(tmp_path):
    _write_draft(tmp_path, "x.md", {"To": "a@x.com", "Subject": "S"}, "the body text", h1="Title")
    r = read_draft(tmp_path, f"{EMAIL_DRAFTS_DIR}/x.md")
    assert r["ok"] is True
    assert "the body text" in r["content"]


def test_read_draft_blocks_traversal(tmp_path):
    assert read_draft(tmp_path, "../etc/passwd")["ok"] is False
    assert read_draft(tmp_path, "")["ok"] is False
    assert read_draft(tmp_path, f"{EMAIL_DRAFTS_DIR}/../../etc/passwd")["ok"] is False


def test_read_draft_rejects_non_md(tmp_path):
    target = tmp_path / EMAIL_DRAFTS_DIR / "evil.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("nope", encoding="utf-8")
    r = read_draft(tmp_path, f"{EMAIL_DRAFTS_DIR}/evil.exe")
    assert r["ok"] is False
    assert "md" in r["error"]


# ============================================================
# Phase 1.71: mark-sent workflow + filter
# ============================================================
def test_mark_sent_rejects_path_outside_drafts_dir(tmp_path):
    from scripts.bridge_daemon.sources.approvals import mark_sent
    r = mark_sent(tmp_path, "outputs/somewhere-else/x.md")
    assert r["ok"] is False
    assert "drafts" in r["error"].lower()


def test_mark_sent_requires_non_empty_path(tmp_path):
    from scripts.bridge_daemon.sources.approvals import mark_sent
    assert mark_sent(tmp_path, "")["ok"] is False
    assert mark_sent(tmp_path, "   ")["ok"] is False


def test_mark_sent_writes_log_entry(tmp_path):
    from scripts.bridge_daemon.sources.approvals import mark_sent, read_sent_log, SENT_LOG_FILE
    _write_draft(tmp_path, "a.md", {"To": "x@y.com", "Subject": "S"}, "body")
    rel = f"{EMAIL_DRAFTS_DIR}/a.md"
    r = mark_sent(tmp_path, rel, note="via Outlook")
    assert r["ok"] is True
    assert r["path"] == rel
    assert r["ts"]
    assert r["date"]
    # Log file exists with one record.
    log = tmp_path / SENT_LOG_FILE
    assert log.exists()
    assert rel in read_sent_log(tmp_path)


def test_mark_sent_normalizes_windows_paths(tmp_path):
    from scripts.bridge_daemon.sources.approvals import mark_sent, read_sent_log
    _write_draft(tmp_path, "a.md", {"To": "x@y.com", "Subject": "S"}, "body")
    rel_win = f"{EMAIL_DRAFTS_DIR}\\a.md".replace("/", "\\")
    r = mark_sent(tmp_path, rel_win)
    assert r["ok"] is True
    # Stored with forward-slash form.
    assert r["path"] == f"{EMAIL_DRAFTS_DIR}/a.md"
    assert f"{EMAIL_DRAFTS_DIR}/a.md" in read_sent_log(tmp_path)


def test_list_approvals_filters_sent_drafts(tmp_path):
    from scripts.bridge_daemon.sources.approvals import mark_sent
    _write_draft(tmp_path, "a.md", {"To": "a@x.com", "Subject": "A"}, "body")
    _write_draft(tmp_path, "b.md", {"To": "b@x.com", "Subject": "B"}, "body")
    mark_sent(tmp_path, f"{EMAIL_DRAFTS_DIR}/a.md")
    r = list_approvals(tmp_path)
    assert r["total"] == 1
    assert r["sent_count"] == 1
    assert r["items"][0]["filename"] == "b.md"


def test_undo_sent_restores_draft_in_queue(tmp_path):
    from scripts.bridge_daemon.sources.approvals import mark_sent, undo_sent, read_sent_log
    _write_draft(tmp_path, "a.md", {"To": "a@x.com", "Subject": "A"}, "body")
    rel = f"{EMAIL_DRAFTS_DIR}/a.md"
    mark_sent(tmp_path, rel)
    assert rel in read_sent_log(tmp_path)
    undo_sent(tmp_path, rel)
    assert rel not in read_sent_log(tmp_path)
    # And it shows up again in list_approvals.
    r = list_approvals(tmp_path)
    assert r["total"] == 1
    assert r["sent_count"] == 0


# ============================================================
# Phase 1.72: sent_log_recent() readout for /approvals footer
# ============================================================
def test_sent_log_recent_empty_when_no_log(tmp_path):
    from scripts.bridge_daemon.sources.approvals import sent_log_recent
    assert sent_log_recent(tmp_path) == []


def test_sent_log_recent_returns_active_entries(tmp_path):
    from scripts.bridge_daemon.sources.approvals import mark_sent, sent_log_recent
    mark_sent(tmp_path, f"{EMAIL_DRAFTS_DIR}/a.md", note="via Outlook")
    mark_sent(tmp_path, f"{EMAIL_DRAFTS_DIR}/b.md")
    rows = sent_log_recent(tmp_path)
    paths = [r["path"] for r in rows]
    assert f"{EMAIL_DRAFTS_DIR}/a.md" in paths
    assert f"{EMAIL_DRAFTS_DIR}/b.md" in paths
    # Each row carries filename + ts + date + note for the UI.
    a = next(r for r in rows if r["path"].endswith("/a.md"))
    assert a["filename"] == "a.md"
    assert a["note"] == "via Outlook"
    assert a["ts"]
    assert a["date"]


def test_sent_log_recent_excludes_tombstones(tmp_path):
    from scripts.bridge_daemon.sources.approvals import mark_sent, undo_sent, sent_log_recent
    rel = f"{EMAIL_DRAFTS_DIR}/x.md"
    mark_sent(tmp_path, rel)
    undo_sent(tmp_path, rel)
    rows = sent_log_recent(tmp_path)
    assert all(r["path"] != rel for r in rows)


def test_sent_log_recent_orders_ts_desc(tmp_path):
    import time
    from scripts.bridge_daemon.sources.approvals import mark_sent, sent_log_recent
    mark_sent(tmp_path, f"{EMAIL_DRAFTS_DIR}/first.md")
    time.sleep(0.01)
    mark_sent(tmp_path, f"{EMAIL_DRAFTS_DIR}/second.md")
    rows = sent_log_recent(tmp_path)
    # Newest is first.
    assert rows[0]["filename"] == "second.md"
    assert rows[1]["filename"] == "first.md"


def test_sent_log_recent_respects_limit(tmp_path):
    from scripts.bridge_daemon.sources.approvals import mark_sent, sent_log_recent
    for i in range(5):
        mark_sent(tmp_path, f"{EMAIL_DRAFTS_DIR}/d-{i}.md")
    rows = sent_log_recent(tmp_path, limit=3)
    assert len(rows) == 3
