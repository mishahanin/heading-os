"""A session ran in a YARD with `HEADING_OS_YARD` absent, and nothing said so.

MEASURED 2026-09-06 in `.yard/.heading-os/yard-yard-deletion-guard`. The
bootstrap's last step starts the agent as

    herdr pane run "$PANE_ID" "HEADING_OS_YARD=1 exec $AGENT_CMD"

and that assignment is the whole point of the line: it is exported into the
AGENT PROCESS, inherited by its shells, their children and the git hooks those
children run, and it is what the yard-side guards read. An agent started BY
HAND, from an older instruction carrying no marker, won the race against that
line by 0.8 s. `pane run` then delivered its command to an agent that was
already alive, which read it as a PROMPT. The result, invisible from the pane:

    /proc/<agent pid>/environ    ->  HEADING_OS_YARD absent
    .claude/.yard-bootstrap-status -> {"status": "ok", "step": 11}

The status was honest. The bootstrap had done its part. Nothing in the
workspace asked the one remaining question, so the session ran with the marker
down and reported healthy.

The check has to fire on `.git` being a FILE, which is what makes this a
worktree, and NOT on the marker it is looking for: a check that read
`HEADING_OS_YARD` to decide whether `HEADING_OS_YARD` matters proves itself and
fires never. That is the property this file pins hardest.

All three directions are driven, because the wrong one being silent is what
makes this useful and the wrong one being loud is what gets it deleted:

    a worktree, marker absent    ->  warned
    a worktree, marker set       ->  silent
    the main clone, marker absent->  silent
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "session-start.py"
ALERT = "YARD MARKER MISSING"


def _run(cwd: Path, marker: str | None) -> str:
    """The hook, in `cwd`, with the marker set to `marker` or removed.

    The hook is taken from THIS checkout while `cwd` is the tree under test, so
    an uncommitted change to it is what runs. `env` is built explicitly rather
    than inherited: this suite very often runs inside a real YARD, where the
    marker is set, and a test that inherited it would assert the absent case
    against a present variable.
    """
    env = dict(os.environ)
    env.pop("HEADING_OS_YARD", None)
    if marker is not None:
        env["HEADING_OS_YARD"] = marker
    result = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps({"cwd": str(cwd)}),
        cwd=str(cwd), capture_output=True, text=True, timeout=180, env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_a_worktree_without_the_marker_is_warned_about(temporary_worktree):
    assert ALERT in _run(temporary_worktree, None)


def test_the_warning_says_where_the_marker_comes_from(temporary_worktree):
    """It cannot be repaired from inside the session, so the fix is a restart.

    A warning naming the variable without saying that is an invitation to
    `export HEADING_OS_YARD=1` in the session's own shell, which sets it for
    one child and leaves the agent process exactly as it was.
    """
    output = _run(temporary_worktree, None)
    assert "cannot be repaired from inside this session" in output
    assert "HEADING_OS_YARD=1 claude" in output


def test_a_worktree_with_the_marker_is_silent(temporary_worktree):
    assert ALERT not in _run(temporary_worktree, "1")


def test_the_main_clone_is_never_warned_about(tmp_path):
    """The quiet direction, and the one that keeps this check alive.

    HELM has no marker and needs none, so a check firing on the absent variable
    alone would print this banner on the operator's every ordinary session.
    A real main clone is cut here rather than reusing this checkout, which in a
    YARD is itself a worktree and would answer the wrong question.
    """
    clone = tmp_path / "a-main-clone"
    made = subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(ROOT), str(clone)],
        capture_output=True, text=True, timeout=300)
    assert made.returncode == 0, made.stderr
    assert (clone / ".git").is_dir(), "this fixture is only a test if it is a clone"
    assert ALERT not in _run(clone, None)
