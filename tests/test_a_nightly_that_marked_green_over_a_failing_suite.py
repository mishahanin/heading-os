"""A failing night must record NOTHING, revoke nothing, and be loud about it.

`scripts/nightly-refresh.py` exists because day mode is only safe while something
else runs everything day mode skipped. Its whole value sits in one conditional:
the green marker and the verdict store move ONLY after the full suite has exited
0. A version that moved them unconditionally would look identical in every green
run, in every log, and in every review, and would be discovered months later as a
day-mode selection narrowing against a base that nothing ever re-verified.

So the direction that matters here is the failing one, and it is asserted by
OBSERVABLE CONSEQUENCE rather than by reading the runner's own condition back:
the sibling commands are stubs that write a file when they are invoked, so
"mark-green was not called" is the absence of a file the stub would have created.
A runner that recorded unconditionally fails
`test_a_failing_suite_moves_no_marker_and_records_no_verdict` on that file's
existence, not on an exit code it could also produce by accident.

The quiet direction is asserted too. A guard that refuses everything satisfies
every refusal test and breaks every honest caller, so a passing suite must be
seen to call both siblings, with the revision on the command line and the
collected corpus on stdin.

Three further properties, each of which has its own test below because each
failed independently in the design:

  - a failing night REVOKES nothing. The previous green marker is left exactly
    where it was: moving it backwards would silently widen every later selection
    with no record of why, and leaving it stalled is what makes the failure
    visible at the point of use.
  - a failing night reaches the alarm path even when no Telegram sink is
    configured, and SAYS that nothing was sent. "The suite failed and nobody was
    told" is a worse state than either half alone and must not be silent.
  - a green night warms the day-mode fact cache, which is the artifact a cold
    morning would otherwise pay for.

Every subprocess here runs against a throwaway git repository with stub siblings.
The real `scripts/run-tests.py` is never invoked and the real `.cache/` of this
checkout is never touched.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "scripts" / "nightly-refresh.py"

# The names `telegram_notify.own_targets()` reads. Blanked in every child so a
# test run can never reach the operator's real sink, and so the "nothing was
# sent" branch is the one under test.
_TELEGRAM_VARS = (
    "HEADING_OS_SELF_TELEGRAM_TARGET",
    "SENTINEL_TELEGRAM_TARGET",
    "COUNCIL_MODELS_TELEGRAM_TARGET",
    "OPS_RADAR_TELEGRAM_TARGET",
    "REMINDERS_TELEGRAM_TARGET",
    "CHECKPOINT_TELEGRAM_TARGET",
    "ODIN_CADENCE_TELEGRAM_TARGET",
    "TELEGRAM_NOTIFY_BOT_TOKEN",
)

_RUN_TESTS_STUB = '''\
#!/usr/bin/env python3
"""Stub gate. Exits with the code in EXIT, and records that it ran."""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent


def build_command(acceptance):
    """The one copy of the marker expression the runner reads."""
    return [sys.executable, "-m", "pytest", "-q", "-n", "auto", "-m", "not acceptance"]


if __name__ == "__main__":
    calls = HERE / ".calls"
    calls.mkdir(exist_ok=True)
    (calls / "run-tests").write_text(" ".join(sys.argv[1:]), encoding="utf-8")
    sys.exit(int((HERE / "EXIT").read_text().strip()))
'''

_SIBLING_STUB = '''\
#!/usr/bin/env python3
"""Stub {name}. Records its argv and stdin, then exits 0."""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
calls = HERE / ".calls"
calls.mkdir(exist_ok=True)
(calls / "{name}").write_text(" ".join(sys.argv[1:]), encoding="utf-8")
if not sys.stdin.isatty():
    (calls / "{name}.stdin").write_text(sys.stdin.read(), encoding="utf-8")
sys.exit(0)
'''


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A throwaway git repository shaped like this one, with stub siblings."""
    root = tmp_path / "clone"
    (root / "scripts").mkdir(parents=True)
    (root / "tests").mkdir()

    (root / "scripts" / "run-tests.py").write_text(_RUN_TESTS_STUB, encoding="utf-8")
    for name in ("day-mode.py", "test-cache.py"):
        (root / "scripts" / name).write_text(
            _SIBLING_STUB.format(name=name), encoding="utf-8")
    # One real, trivially collectable test file, so the runner's collection pass
    # returns a non-empty corpus without running anything of consequence.
    (root / "tests" / "test_collectable.py").write_text(
        "def test_one():\n    assert True\n", encoding="utf-8")
    (root / "EXIT").write_text("0\n", encoding="utf-8")

    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "tree")
    return root


def _drive(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for name in _TELEGRAM_VARS:
        env[name] = ""
    env["PYTHONUNBUFFERED"] = "1"
    return subprocess.run(
        [sys.executable, str(RUNNER), "--root", str(root), *args],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT))


def _head(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


# ============================================================
# The direction that matters
# ============================================================

def test_a_failing_suite_moves_no_marker_and_records_no_verdict(tree):
    """The assertion a runner that recorded unconditionally cannot pass.

    Both stubs write a file the moment they are invoked, so this is the absence
    of an effect, not a restatement of the runner's own `if`.
    """
    (tree / "EXIT").write_text("1\n", encoding="utf-8")
    result = _drive(tree)

    assert result.returncode != 0, result.stdout + result.stderr
    assert (tree / ".calls" / "run-tests").exists(), (
        "the gate was never invoked, so this test proved nothing about what "
        "happens after it fails")
    assert not (tree / ".calls" / "day-mode.py").exists(), (
        "mark-green was called over a FAILING suite")
    assert not (tree / ".calls" / "test-cache.py").exists(), (
        "verdicts were recorded over a FAILING suite")
    assert not (tree / ".cache" / "day-mode" / "known-green").exists()


def test_a_failing_suite_revokes_nothing(tree):
    """The previous green marker is left exactly where it was.

    Revoking on failure would widen every later day-mode selection with no
    record of why. Leaving the marker stalled is the design: `day-mode select`
    prints its age, so the stall is visible at the point of use.
    """
    marker = tree / ".cache" / "day-mode" / "known-green"
    marker.parent.mkdir(parents=True)
    marker.write_text("0123456789abcdef0123456789abcdef01234567\n", encoding="utf-8")
    before = marker.read_text(encoding="utf-8")

    (tree / "EXIT").write_text("1\n", encoding="utf-8")
    assert _drive(tree).returncode != 0

    assert marker.exists(), "a failing night deleted the previous green marker"
    assert marker.read_text(encoding="utf-8") == before


def test_a_failing_suite_is_loud_and_says_when_nothing_was_sent(tree):
    """A nightly that fails into a log nobody reads converts day mode into a hole.

    With no sink configured the alarm cannot deliver, and that is exactly the
    state that must not be silent: the run says so in words rather than logging
    a failure and returning as though someone had been told.
    """
    (tree / "EXIT").write_text("3\n", encoding="utf-8")
    result = _drive(tree)
    combined = result.stdout + result.stderr

    assert "the full suite FAILED" in combined
    assert "pytest exit 3" in combined
    assert "NO NOTIFICATION SENT" in combined
    assert "Nothing was revoked" in combined


def test_a_failing_suite_records_the_failure_in_the_run_record(tree):
    (tree / "EXIT").write_text("1\n", encoding="utf-8")
    _drive(tree)
    record = json.loads((tree / ".cache" / "nightly-refresh" / "last-run.json")
                        .read_text(encoding="utf-8"))
    assert record["status"] == "suite_failed"
    assert record["gate_exit"] == 1
    assert record["revision"] == _head(tree)


# ============================================================
# The quiet direction: a guard that refuses everything is not a guard
# ============================================================

def test_a_passing_suite_moves_the_marker_and_records_the_corpus(tree):
    result = _drive(tree)
    assert result.returncode == 0, result.stdout + result.stderr

    revision = _head(tree)
    marked = (tree / ".calls" / "day-mode.py").read_text(encoding="utf-8")
    assert marked.split() == ["mark-green", revision]

    recorded = (tree / ".calls" / "test-cache.py").read_text(encoding="utf-8")
    assert recorded.split() == ["record", "--base", revision, "--from", "-"]

    corpus = (tree / ".calls" / "test-cache.py.stdin").read_text(encoding="utf-8")
    assert "tests/test_collectable.py" in corpus


def test_a_passing_suite_warms_the_day_mode_fact_cache(tree):
    """Step 3: the artifact a cold morning would otherwise pay for."""
    assert _drive(tree).returncode == 0
    assert (tree / ".cache" / "day-mode" / "facts.db").exists()


def test_a_passing_suite_records_green(tree):
    assert _drive(tree).returncode == 0
    record = json.loads((tree / ".cache" / "nightly-refresh" / "last-run.json")
                        .read_text(encoding="utf-8"))
    assert record["status"] == "green"
    assert record["collected"] >= 1


# ============================================================
# --status, the read half of "did the night fire?"
# ============================================================

def test_status_refuses_when_no_night_has_ever_completed(tree):
    result = _drive(tree, "--status")
    assert result.returncode == 2
    assert "no run record" in (result.stdout + result.stderr)


def test_status_is_green_after_a_green_night_and_red_after_a_failing_one(tree):
    assert _drive(tree).returncode == 0
    assert _drive(tree, "--status").returncode == 0

    (tree / "EXIT").write_text("1\n", encoding="utf-8")
    _drive(tree)
    after = _drive(tree, "--status")
    assert after.returncode == 1
    assert "suite_failed" in after.stdout


def test_dry_run_runs_nothing_at_all(tree):
    """--dry-run must not be a run. The stubs are the witnesses."""
    result = _drive(tree, "--dry-run")
    assert result.returncode == 0
    assert not (tree / ".calls").exists()
    assert not (tree / ".cache").exists()
