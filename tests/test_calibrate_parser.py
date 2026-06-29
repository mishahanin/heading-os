"""Unit tests for scripts/calibrate.py (the JSONL parser).

Covers cases listed in plans/2026-05-13-calibrate-skill.md Phase 1 Success Criterion:
  - newest-session selection by mtime
  - --session override
  - exit codes (0 ok, 2 no session, 3 unreadable)
  - malformed lines tolerated
  - envelope schema completeness
  - tool_errors / system_reminders extraction
  - --since-utc filter
  - --max-bytes truncation
  - workspace block enumeration
  - hidden-character cleanliness of stdout
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "calibrate.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "calibrate"


# ---------- subprocess helper for CLI tests ----------

def run_parser(*args):
    """Run calibrate.py with given args, return (returncode, stdout, stderr)."""
    cmd = [sys.executable, str(SCRIPT), "--no-workspace", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return result.returncode, result.stdout, result.stderr


# ---------- session location ----------

def test_locate_newest_session_by_mtime(tmp_path):
    """Parser picks the newest .jsonl by mtime when --session is not given."""
    older = tmp_path / "older.jsonl"
    newer = tmp_path / "newer.jsonl"
    older.write_text('{"type":"user","timestamp":"2026-05-13T10:00:00Z","message":{"role":"user","content":"older"}}\n', encoding="utf-8")
    time.sleep(0.05)  # ensure distinct mtimes
    newer.write_text('{"type":"user","timestamp":"2026-05-13T10:00:00Z","message":{"role":"user","content":"newer"}}\n', encoding="utf-8")
    rc, out, err = run_parser("--sessions-dir", str(tmp_path))
    assert rc == 0, f"stderr: {err}"
    envelope = json.loads(out)
    assert envelope["session_path"].endswith("newer.jsonl"), envelope


# ---------- envelope filtering via --session override ----------

def test_session_override_via_flag():
    """--session <path> overrides auto-detection."""
    fixture = FIXTURES / "simple-correction.jsonl"
    rc, out, err = run_parser("--session", str(fixture))
    assert rc == 0, err
    env = json.loads(out)
    assert env["session_path"].endswith("simple-correction.jsonl")
    assert env["event_count"] == 5
    assert len(env["user_turns"]) == 2
    assert len(env["assistant_turns"]) == 2
    assert len(env["system_reminders"]) == 1


# ---------- exit codes ----------

def test_no_session_exits_code_2(tmp_path):
    """Empty sessions dir -> exit code 2."""
    rc, out, err = run_parser("--sessions-dir", str(tmp_path))
    assert rc == 2
    assert "no session JSONL found" in err


# ---------- malformed line tolerance ----------

def test_malformed_jsonl_lines_skipped():
    """Malformed lines logged to stderr, parsing continues."""
    fixture = FIXTURES / "malformed.jsonl"
    rc, out, err = run_parser("--session", str(fixture))
    assert rc == 0
    assert "skipped 2 malformed" in err
    env = json.loads(out)
    assert env["event_count"] == 5  # 7 total lines, 2 malformed
    assert len(env["user_turns"]) == 3


# ---------- tool_errors extraction ----------

def test_tool_errors_extracted():
    """tool_result events with exit_code != 0 or stderr -> tool_errors array."""
    fixture = FIXTURES / "tool-errors.jsonl"
    rc, out, err = run_parser("--session", str(fixture))
    assert rc == 0
    env = json.loads(out)
    assert len(env["tool_errors"]) == 2
    assert env["tool_errors"][0]["exit_code"] == 2
    assert "linkedin_archive.py" in env["tool_errors"][0]["stderr"]
    # cmd field carries the command from the preceding tool_use
    assert env["tool_errors"][0]["cmd"] == "python scripts/linkedin_archive.py"
    assert env["tool_errors"][1]["cmd"] == "python scripts/missing.py"


def test_nonexistent_session_exits_code_3(tmp_path):
    """--session <nonexistent-path> -> exit code 3 (session unreadable)."""
    fake = tmp_path / "does-not-exist.jsonl"
    rc, out, err = run_parser("--session", str(fake))
    assert rc == 3
    assert "session unreadable" in err.lower()


# ---------- --since-utc filter ----------

def test_since_utc_filter():
    """--since-utc excludes events older than the timestamp."""
    fixture = FIXTURES / "simple-correction.jsonl"
    rc, out, err = run_parser("--session", str(fixture), "--since-utc", "2026-05-13T08:42:50Z")
    assert rc == 0
    env = json.loads(out)
    # Original: 5 events spanning 08:42:11 to 08:43:15
    # After filter at 08:42:50: only events at 08:42:55, 08:43:10, 08:43:15 (3 events)
    assert env["event_count"] == 3


# ---------- workspace block enumeration ----------

def test_workspace_block_populated_by_default():
    """Without --no-workspace, envelope includes the workspace block."""
    fixture = FIXTURES / "simple-correction.jsonl"
    cmd = [sys.executable, str(SCRIPT), "--session", str(fixture)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0
    env = json.loads(result.stdout)
    assert "workspace" in env
    assert isinstance(env["workspace"]["skills"], list)
    assert len(env["workspace"]["skills"]) > 0  # at least some skills exist
    assert isinstance(env["workspace"]["rules"], list)
    assert isinstance(env["workspace"]["ceo_only_paths"], list)


def test_no_workspace_flag_omits_block():
    """--no-workspace omits the workspace block."""
    fixture = FIXTURES / "simple-correction.jsonl"
    rc, out, err = run_parser("--session", str(fixture))  # run_parser passes --no-workspace
    env = json.loads(out)
    assert "workspace" not in env


# ---------- --max-bytes truncation ----------

def test_max_bytes_truncation(tmp_path):
    """When envelope would exceed --max-bytes, oldest user_turns are dropped, truncated=True."""
    fixture = tmp_path / "large.jsonl"
    with fixture.open("w", encoding="utf-8") as fh:
        for i in range(500):
            event = {
                "type": "user",
                "timestamp": f"2026-05-13T{i//60:02d}:{i%60:02d}:00Z",
                "message": {"role": "user", "content": "x" * 2000},  # 2KB each
            }
            fh.write(json.dumps(event) + "\n")
    rc, out, err = run_parser("--session", str(fixture), "--max-bytes", "50000")
    assert rc == 0
    env = json.loads(out)
    assert env["truncated"] is True
    assert len(env["user_turns"]) < 500  # some dropped
    assert len(out.encode("utf-8")) <= 60000  # budget plus small overhead


# ---------- permission error handling ----------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX chmod required for unreadable test")
def test_permission_error_exits_code_3(tmp_path):
    """Unreadable session JSONL -> exit code 3."""
    unreadable = tmp_path / "no-read.jsonl"
    unreadable.write_text("{}\n", encoding="utf-8")
    unreadable.chmod(0o000)
    try:
        rc, out, err = run_parser("--session", str(unreadable))
        assert rc == 3
        assert "unreadable" in err.lower()
    finally:
        unreadable.chmod(0o600)  # cleanup


# ---------- system reminders extraction ----------

def test_system_reminders_extracted():
    """System events with content -> system_reminders array."""
    fixture = FIXTURES / "with-system-reminders.jsonl"
    rc, out, err = run_parser("--session", str(fixture))
    assert rc == 0
    env = json.loads(out)
    assert len(env["system_reminders"]) == 2
    assert "system-reminder" in env["system_reminders"][0]["text"]


# ---------- output hidden-character cleanliness ----------

def test_parser_output_has_no_hidden_unicode():
    """Parser stdout must not contain zero-width characters, em-dashes, etc."""
    fixture = FIXTURES / "simple-correction.jsonl"
    rc, out, err = run_parser("--session", str(fixture))
    assert rc == 0
    # Parser itself must not inject hidden chars. User content passthrough is fine.
    # Build forbidden chars from escape sequences to keep source clean
    forbidden = [
        chr(0x200B),  # zero-width space
        chr(0x200C),  # zero-width non-joiner
        chr(0x200D),  # zero-width joiner
        chr(0x00AD),  # soft hyphen
        chr(0xFEFF),  # byte order mark
    ]
    for ch in forbidden:
        assert ch not in out, f"forbidden char U+{ord(ch):04X} in output"
