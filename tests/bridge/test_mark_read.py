"""Unit tests for the mark-read finalizer (Exchange read-state write-back)."""
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

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


@pytest.mark.parametrize("mark_read, flag", [
    (True, "--mark-read"),
    (False, "--mark-unread"),
])
def test_the_flag_matches_the_direction_asked_for(tmp_path, mark_read, flag):
    """The one thing this finalizer computes, and nothing asserted it.

    Every other test here patches `subprocess.run` away and then asserts the
    JSON the stub itself supplied, so the whole file measured its own setup.
    `flag = "--mark-read" if mark_read else "--mark-unread"` is the only real
    line, and `mark_read=False` was never passed: inverting the ternary left the
    bridge tests green while `POST /inbox/undo-dismiss` marked a conversation
    READ instead of unread, so the mail stayed hidden in Outlook with no undo.
    """
    _stub_script(tmp_path)
    seen = []

    def _run(argv, **kwargs):
        seen.append(argv)
        return SimpleNamespace(stdout='{"ok": true, "messages_changed": 1}',
                               returncode=0)

    with patch("subprocess.run", _run):
        mark_conversation_read(tmp_path, " c1 ", mark_read=mark_read)

    assert len(seen) == 1, f"expected one child run, got {len(seen)}"
    argv = seen[0]
    assert flag in argv, f"{flag} missing from {argv}"
    other = "--mark-unread" if flag == "--mark-read" else "--mark-read"
    assert other not in argv, f"both directions were passed: {argv}"
    assert argv[-1] == "c1", "the conversation id is not stripped and last"
    assert argv[1].endswith("email-intelligence.py"), (
        f"the finalizer ran something else: {argv}")


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
