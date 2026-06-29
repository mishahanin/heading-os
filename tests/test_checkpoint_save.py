"""Regression: PostCompact checkpoint-save.py must not crash when the handoff path
lives in the DATA repo (engine/data separation).

Bug: the hook computed `archive_path.relative_to(WORKSPACE)` where WORKSPACE is the
ENGINE root, but the handoff archive resolves under the DATA root (a sibling tree).
relative_to() then raised ValueError on every compact event, so no resume artifact
was saved. The fix makes the @-reference path data-root-relative
(`outputs/operations/handoff-archive/<name>`), matching the /checkpoint skill and
the inject hook.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "checkpoint-save.py"
STATE_PATH = ROOT / ".claude" / "state" / "checkpoint-state.json"


@pytest.fixture(autouse=True)
def _isolate_engine_state():
    """The hook resets the engine state file (.claude/state/checkpoint-state.json),
    which is real runtime state, not env-redirected. Back it up and restore it so the
    test suite never mutates the live session's checkpoint hysteresis."""
    backup = STATE_PATH.read_text(encoding="utf-8") if STATE_PATH.exists() else None
    try:
        yield
    finally:
        if backup is not None:
            STATE_PATH.write_text(backup, encoding="utf-8")
        elif STATE_PATH.exists():
            STATE_PATH.unlink()


def _run_hook(tmp_path, payload):
    """Run checkpoint-save.py with the DATA root redirected into tmp_path."""
    env = dict(os.environ)
    env["HEADING_OS_DATA"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc


def test_postcompact_does_not_crash_with_data_repo_path(tmp_path):
    """A PostCompact event must complete cleanly (no ValueError traceback)."""
    proc = _run_hook(tmp_path, {
        "session_id": "test-sess", "trigger": "manual",
        "compact_summary": "test summary body", "transcript_path": "",
    })
    assert proc.returncode == 0, f"hook exited non-zero:\n{proc.stderr}"
    assert "Traceback" not in proc.stderr, f"hook crashed:\n{proc.stderr}"
    assert "ValueError" not in proc.stderr, f"relative_to bug still present:\n{proc.stderr}"


def test_handoff_written_under_data_root(tmp_path):
    """The archive file must be written under the DATA root, not the engine."""
    _run_hook(tmp_path, {
        "session_id": "test-sess", "trigger": "auto",
        "compact_summary": "summary", "transcript_path": "",
    })
    archive_dir = tmp_path / "outputs" / "operations" / "handoff-archive"
    files = list(archive_dir.glob("*_handoff_compact-auto_*.md"))
    assert files, f"no handoff archive written under {archive_dir}"
    latest = archive_dir / ".latest"
    assert (latest / "summary.md").exists(), "latest/summary.md not written"
    assert (latest / "prompt.md").exists(), "latest/prompt.md not written"


def test_continuation_prompt_uses_data_root_relative_path(tmp_path):
    """The @-reference must be data-root-relative (outputs/...), the path the
    inject hook + data-path-redirect resolve — never an engine-relative or absolute path."""
    _run_hook(tmp_path, {
        "session_id": "test-sess", "trigger": "manual",
        "compact_summary": "s", "transcript_path": "",
    })
    prompt = (tmp_path / "outputs" / "operations" / "handoff-archive" / ".latest" / "prompt.md").read_text(encoding="utf-8")
    assert "@outputs/operations/handoff-archive/" in prompt, (
        f"continuation prompt does not use a data-root-relative @-reference:\n{prompt}"
    )
    # Must not leak an absolute machine path into the resume artifact.
    assert str(tmp_path) not in prompt, "absolute path leaked into continuation prompt"


def test_state_reset_records_data_root_relative_summary_path(tmp_path):
    """The checkpoint-state.json summary-path pointer must also be data-root-relative.

    The engine state file is backed up / restored by the autouse _isolate_engine_state
    fixture, so this test may freely run the hook and inspect the written state.
    """
    _run_hook(tmp_path, {
        "session_id": "test-sess", "trigger": "manual",
        "compact_summary": "s", "transcript_path": "",
    })
    cs = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    p = cs.get("last_compact_summary_path", "")
    assert p.startswith("outputs/operations/handoff-archive/"), (
        f"state summary path is not data-root-relative: {p!r}"
    )
