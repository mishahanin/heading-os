"""Tests for the slow-shell guard in `.claude/hooks/_dispatch.py`.

The guard exists because of a measurement, not a hunch. Across the six sessions
ending 2026-08-22 the Bash tool held the session for 4.85 hours of wall time,
and the median call was 0.4 s — the tail was the whole cost. Two shapes owned
65% of it:

  * blocking waiters (`sleep 540`, `while ps -p N; do sleep 30; done`) —
    2.07 h over 19 calls, avg 393 s, every second of it a session that could
    not do anything else. `run_in_background: true` returns immediately and
    wakes the turn when the command exits, so the wait costs nothing.
  * the full suite run serially — 1.05 h over 107 calls, the big ones
    434–601 s each. Measured 2026-08-22 on 16 cores: `pytest tests/ -q -n auto`
    finished 6156 tests in 88.88 s. `scripts/run-tests.py` has passed `-n auto`
    since the push gate was parallelized; the slow runs were bare `pytest`
    invocations that bypassed it.

Both are habits, not bugs, which is why they need a wall rather than a note:
a memory file is only as good as the next session's recall, and this guard is
the workspace's own "write the guard before the sweep" rule applied to itself.

The block is rendered as a PreToolUse permission deny, the same contract the
personal-threads guard uses, so the CLI shows an intentional policy refusal
rather than a hook error.
"""
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(".claude/hooks/_dispatch.py").resolve()


def _run_hook(payload: dict) -> tuple[int, str, str]:
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True, check=False,
    )
    return p.returncode, p.stdout, p.stderr


def _deny_reason(rc: int, stdout: str, stderr: str = "") -> str | None:
    """The deny reason when the hook refused the call, else None.

    A non-zero exit is NOT an allow. Until 2026-08-23 this returned None on any
    `rc != 0`, so a hook that crashed with a traceback read exactly like a clean
    allow decision across every "must not be blocked" assertion in this file --
    about twenty of them, all hollow at once. Raise instead: a broken hook is a
    test failure, never a pass.
    """
    if rc != 0:
        raise AssertionError(
            f"the hook exited {rc} instead of deciding; that is a crash, not an "
            f"allow.\nstdout: {stdout[:500]!r}\nstderr: {stderr[:1000]!r}")
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    hso = data.get("hookSpecificOutput", {})
    if hso.get("permissionDecision") != "deny":
        return None
    return hso.get("permissionDecisionReason", "")


def _bash(command: str, **extra) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command, **extra}}


# ----------------------------------------------------------------------
# Serial full-suite pytest — blocked, and told where the parallel runner is
# ----------------------------------------------------------------------

SERIAL_SUITE_COMMANDS = [
    ".venv/bin/python -m pytest tests/ -q",
    "pytest tests/",
    ".venv/bin/python -m pytest tests/ -q 2>&1 | tail -n 15",
    'cd /home/administrator/ai/claude-workspaces/.heading-os\n.venv/bin/python -m pytest tests/ -q -p no:randomly',
    ".venv/bin/python -m pytest -q",
]


def test_blocks_every_serial_full_suite_shape() -> None:
    for command in SERIAL_SUITE_COMMANDS:
        reason = _deny_reason(*_run_hook(_bash(command)))
        assert reason is not None, f"not blocked: {command!r}"
        assert "run-tests.py" in reason, f"no runner pointer in reason for {command!r}"


def test_serial_suite_reason_carries_the_measured_numbers() -> None:
    reason = _deny_reason(*_run_hook(_bash("pytest tests/ -q")))
    assert reason is not None
    assert "88.88" in reason or "89" in reason


def test_serial_suite_blocked_even_in_background() -> None:
    """Backgrounding hides the wait; it does not make a 450 s run a 89 s run."""
    payload = _bash(".venv/bin/python -m pytest tests/ -q", run_in_background=True)
    assert _deny_reason(*_run_hook(payload)) is not None


# ----------------------------------------------------------------------
# Blocking waiters — blocked, and pointed at run_in_background
# ----------------------------------------------------------------------

WAITER_COMMANDS = [
    "sleep 540; ps -p 922223 -o etime= --no-headers || echo GONE",
    "while ps -p 922223 >/dev/null 2>&1; do sleep 30; done; echo '=== FINISHED ==='",
    'while pgrep -f "python scripts/push-all.py" >/dev/null 2>&1; do sleep 15; done\necho done',
    "sleep 500; tail -n 8 /tmp/build.log",
    "until [ -f /tmp/done.flag ]; do sleep 20; done",
]


def test_blocks_every_blocking_waiter_shape() -> None:
    for command in WAITER_COMMANDS:
        reason = _deny_reason(*_run_hook(_bash(command)))
        assert reason is not None, f"not blocked: {command!r}"
        assert "run_in_background" in reason, f"no pointer in reason for {command!r}"


def test_waiter_allowed_when_already_backgrounded() -> None:
    """A waiter that does not hold the session is exactly the prescribed fix."""
    payload = _bash("while ps -p 4242 >/dev/null; do sleep 30; done; echo done",
                    run_in_background=True)
    assert _deny_reason(*_run_hook(payload)) is None


# ----------------------------------------------------------------------
# What must keep working — the guard earns its place only if it is quiet here
# ----------------------------------------------------------------------

ALLOWED_COMMANDS = [
    # the parallel runner itself, and an explicit -n
    ".venv/bin/python scripts/run-tests.py",
    ".venv/bin/python -m pytest tests/ -q -n auto",
    ".venv/bin/python -m pytest tests/ -n 8",
    ".venv/bin/python -m pytest tests/ --numprocesses=auto",
    # a narrow target is seconds, not minutes
    ".venv/bin/python -m pytest tests/test_slow_shell_guard.py -q",
    ".venv/bin/python -m pytest tests/test_a.py tests/test_b.py",
    ".venv/bin/python -m pytest tests/ -q -k slow_shell",
    ".venv/bin/python -m pytest tests/ -q --collect-only",
    # short sleeps: waiting for a daemon socket is not a 400 s stall
    "sleep 2 && curl -s http://127.0.0.1:8765/health",
    "sleep 5; systemctl --user status heading-bridge",
    # ordinary work
    "git status --short",
    "ls scripts/ | head",
    "grep -rn 'pytest tests/' docs/",
    # Shell metacharacters INSIDE a quoted argument are data, not operators. The
    # guard's first cut split on `|` blindly, so this alternation broke into a
    # segment whose only word was `pytest`, and the hook refused a directory
    # listing. Caught 2026-08-22 by the guard blocking its own author's command.
    'ls auto-memory/ | grep -iE "test|pytest|shell|slow|background|wait"',
    "grep -rnE 'sleep 540|while ps -p' docs/ scripts/",
    'echo "run pytest tests/ -q; sleep 540" > /tmp/note.txt',
]


def test_quoted_metacharacters_are_data_not_operators() -> None:
    """A `|` inside quotes does not start a new command; nor does `;`."""
    for command in (
        'ls | grep -iE "test|pytest|slow"',
        "grep -E 'a;pytest tests/;b' README.md",
    ):
        reason = _deny_reason(*_run_hook(_bash(command)))
        assert reason is None, f"wrongly blocked {command!r}: {reason}"


def test_newline_still_separates_commands() -> None:
    """A multi-line Bash call is several commands; the second one still counts."""
    command = "cd /home/administrator/ai/claude-workspaces/.heading-os\npytest tests/ -q"
    assert _deny_reason(*_run_hook(_bash(command))) is not None


def test_allows_the_fast_and_the_ordinary() -> None:
    for command in ALLOWED_COMMANDS:
        reason = _deny_reason(*_run_hook(_bash(command)))
        assert reason is None, f"wrongly blocked {command!r}: {reason}"


# ----------------------------------------------------------------------
# A directory below the suite root is a NARROW run
# ----------------------------------------------------------------------

# The guard's own comment has always said "a file, a directory below tests/, a
# node id" are narrow, and its deny message says "Narrow runs are untouched".
# The code accepted only `.py` and `::`, so `pytest tests/security` was denied
# by a wall promising not to touch it. Found by the 2026-08-23 audit and
# reproduced. This matters more than the inconvenience: a guard that refuses the
# shape its own text exempts teaches the operator to reach for the escape hatch
# by reflex, which is the one outcome a habit guard must never produce.

NARROW_DIRECTORY_COMMANDS = [
    "pytest tests/security",
    ".venv/bin/python -m pytest tests/utils -q",
    ".venv/bin/python -m pytest tests/security/ -q",
    "pytest ./tests/security",
]

# Still the whole suite in one process, however it is spelled.
STILL_THE_WHOLE_SUITE = [
    "pytest tests",
    "pytest tests/",
    "pytest ./tests/",
    "pytest .",
]


def test_a_directory_below_the_suite_root_is_not_blocked() -> None:
    for command in NARROW_DIRECTORY_COMMANDS:
        reason = _deny_reason(*_run_hook(_bash(command)))
        assert reason is None, f"wrongly blocked a narrow run {command!r}: {reason}"


def test_the_suite_root_itself_is_still_blocked_however_spelled() -> None:
    """The depth test must not become a hole: `./tests/` is one segment after
    normalization and stays the full suite."""
    for command in STILL_THE_WHOLE_SUITE:
        reason = _deny_reason(*_run_hook(_bash(command)))
        assert reason is not None, f"the full suite slipped through: {command!r}"


def test_a_path_bearing_flag_is_not_read_as_a_target() -> None:
    """`--rootdir=/opt/x` contains a slash and names no test."""
    reason = _deny_reason(*_run_hook(_bash("pytest --rootdir=/opt/x")))
    assert reason is not None, "a flag carrying a path disarmed the guard"


def test_the_deny_message_now_names_directories_as_narrow() -> None:
    """The message listed 'a file path, -k, or --collect-only'. It never said
    directories, which is half of why the gap survived."""
    reason = _deny_reason(*_run_hook(_bash("pytest tests/ -q")))
    assert reason is not None
    assert "directory below the suite root" in reason


def test_escape_hatch_lets_a_deliberate_serial_run_through() -> None:
    """Measuring serial-vs-parallel requires running the serial case once.

    A wall with no door gets torn down. The marker is explicit and greppable,
    so a deliberate exception stays visible instead of becoming a disarmed hook.
    """
    command = ".venv/bin/python -m pytest tests/ -q  # slow-shell-ok: baseline measurement"
    assert _deny_reason(*_run_hook(_bash(command))) is None


def test_ignores_non_bash_tools() -> None:
    """A document that quotes the bad command is not the bad command."""
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": "docs/notes.md",
            "content": "Do not run `pytest tests/ -q` or `sleep 540; echo hi`.",
        },
    }
    assert _deny_reason(*_run_hook(payload)) is None


def test_empty_command_is_not_blocked() -> None:
    assert _deny_reason(*_run_hook(_bash(""))) is None
