"""Unit tests for the mark-read finalizer (Exchange read-state write-back)."""
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from scripts.bridge_daemon.finalizers.mark_read import mark_conversation_read


def _stub_script(workspace_root):
    """Create a stub email-intelligence.py so the existence check passes."""
    d = workspace_root / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "email-intelligence.py").write_text("# stub", encoding="utf-8")


def test_mark_conversation_read_parses_result(tmp_path):
    _stub_script(tmp_path)
    with patch("subprocess.run", return_value=SimpleNamespace(
            stdout='{"ok": true, "conv_id": "c1", "messages_changed": 3}',
            returncode=0)):
        r = mark_conversation_read(tmp_path, "c1", mark_read=True)
    assert r["ok"] is True
    assert r["messages_changed"] == 3


def test_mark_conversation_read_surfaces_producer_error(tmp_path):
    _stub_script(tmp_path)
    with patch("subprocess.run", return_value=SimpleNamespace(
            stdout='{"ok": false, "error": "Exchange write failed: boom"}',
            returncode=1)):
        r = mark_conversation_read(tmp_path, "c1", mark_read=True)
    assert r["ok"] is False
    assert "boom" in r["error"]


def test_mark_conversation_read_handles_no_output(tmp_path):
    _stub_script(tmp_path)
    with patch("subprocess.run", return_value=SimpleNamespace(stdout="", returncode=1)):
        r = mark_conversation_read(tmp_path, "c1", mark_read=True)
    assert r["ok"] is False
    assert "no result" in r["error"]


def test_mark_conversation_read_timeout(tmp_path):
    _stub_script(tmp_path)
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 60)):
        r = mark_conversation_read(tmp_path, "c1", mark_read=True)
    assert r["ok"] is False
    assert "timed out" in r["error"]


def test_mark_conversation_read_missing_script(tmp_path):
    """No scripts/email-intelligence.py on disk -> graceful error."""
    r = mark_conversation_read(tmp_path, "c1", mark_read=True)
    assert r["ok"] is False
    assert "not found" in r["error"]


def test_mark_conversation_read_rejects_bad_conv_id(tmp_path):
    assert mark_conversation_read(tmp_path, "", mark_read=True)["ok"] is False
    assert mark_conversation_read(tmp_path, "   ", mark_read=True)["ok"] is False
    assert mark_conversation_read(tmp_path, "x" * 600, mark_read=True)["ok"] is False
