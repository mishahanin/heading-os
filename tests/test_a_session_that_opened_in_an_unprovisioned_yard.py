"""SessionStart says so when a worktree was never provisioned.

The bootstrap does not start an agent unless it reached `status: ok`, so in the
ordinary flow this never fires. It is for the session opened BY HAND in a
worktree directory, where there is no bootstrap at all and the state it would
have refused is invisible.

MEASURED 2026-09-03 in a fresh worktree of this repository:

    .claude/settings.local.json   ABSENT   -> eleven PreToolUse walls unregistered
    .env                          ABSENT   -> data root resolves to examples/ (demo)
    .venv                         ABSENT

None of those announces itself. Every guard gated on "the data root differs
from the workspace root" stays armed in demo mode and reports clean.

Both directions are asserted. A check that warned in HELM too would put a
scary banner on every ordinary session and be deleted within the week.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "session-start.py"


def _run(cwd: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps({"cwd": str(cwd)}),
        cwd=str(cwd), capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _status(worktree: Path, payload: str) -> Path:
    path = worktree / ".claude" / ".yard-bootstrap-status"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path


def test_the_main_clone_is_never_warned_about():
    """The quiet direction, and the one that keeps this check alive."""
    assert "YARD" not in _run(ROOT)


def test_an_unprovisioned_worktree_is_named(temporary_worktree):
    output = _run(temporary_worktree)
    assert "YARD NOT PROVISIONED" in output


def test_the_warning_says_what_is_actually_at_risk(temporary_worktree):
    """A warning that does not say what it costs gets read as noise."""
    output = _run(temporary_worktree)
    assert "walls" in output
    assert "FORCE_BOOTSTRAP=1" in output


def test_a_completed_bootstrap_is_silent(temporary_worktree):
    _status(temporary_worktree,
            '{"status":"ok","step":11,"timestamp":"x","version":"5.0"}')
    assert "YARD" not in _run(temporary_worktree)


@pytest.mark.parametrize("status,step", [("failed", 7), ("in_progress", 4)])
def test_an_incomplete_bootstrap_reports_the_step(temporary_worktree,
                                                  status, step):
    _status(temporary_worktree,
            json.dumps({"status": status, "step": step,
                        "timestamp": "x", "version": "5.0"}))
    output = _run(temporary_worktree)
    assert "DID NOT COMPLETE" in output
    assert str(step) in output


def test_a_corrupt_status_file_is_not_read_as_healthy(temporary_worktree):
    """Absent, corrupt and complete are three states, not two.

    A guard that cannot tell corrupt from missing reports one as the other; one
    that cannot tell corrupt from complete is worse, because it stays quiet.
    """
    _status(temporary_worktree, "{not json at all")
    output = _run(temporary_worktree)
    assert "UNREADABLE" in output
    assert "unprovisioned" in output


def test_a_status_file_that_is_valid_json_but_not_an_object(temporary_worktree):
    """`[]` and `3` parse. `.get` on them raises, and a raising hook loses
    every other alert it was going to print."""
    _status(temporary_worktree, "[]")
    output = _run(temporary_worktree)
    assert "UNREADABLE" in output or "DID NOT COMPLETE" in output
