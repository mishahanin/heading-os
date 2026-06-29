import time
from scripts.bridge_daemon.refreshers.inflight import scan_inflight

def test_scan_finds_recent_drafts(workspace_root):
    draft = workspace_root / "outputs/content/linkedin/2026-05-17-draft.md"
    draft.write_text("---\ntitle: Test\nsession_id: abc123\n---\nbody")
    rows = scan_inflight(workspace_root, retention_hours=24)
    assert len(rows) == 1
    assert rows[0]["category"] == "linkedin"
    assert rows[0]["session_id"] == "abc123"

def test_scan_ignores_old_files(workspace_root):
    draft = workspace_root / "outputs/content/linkedin/old.md"
    draft.write_text("body")
    # set mtime to 2 days ago
    old = time.time() - 2 * 86400
    import os
    os.utime(draft, (old, old))
    assert scan_inflight(workspace_root, retention_hours=24) == []
