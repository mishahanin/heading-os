"""A failing OR HOLLOW night must record NOTHING, revoke nothing, and be loud.

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

THE HOLLOW DIRECTION, added 2026-09-05, and this file passing while the defect
happened is part of the finding. Everything above asks what the nightly does
with a RED suite. Nothing asked what it does with a suite that exited 0 without
running. MEASURED on the first fire of the timer, an hour after it was
installed:

    the nightly, first fire 01:30:22   24599 passed, 240 skipped   -> GREEN
    same tree, ordinary shell          24836 passed, 2 skipped, 1 failed
    collected, both                    24839

238 checks never executed, one of them was red, and the runner moved the green
marker to a0931fe and recorded 1085 green verdicts. The cause was the
launcher's PATH, not the tree: a systemd user service inherits the manager's
PATH, which carries none of the per-user tool directories, so every test gated
on gh, git-lfs, node, npx, marp, uv, pre-commit, claude or herdr skipped.
Replacing only the PATH in an ordinary shell reproduced 24599 passed, 240
skipped exactly.

The PATH is fixed in the unit template. The tests below are about the shape
rather than the cause, because an exit code of 0 says "nothing that ran failed"
and never "the checks ran", and everything that removes a test from a run
produces that same 0. So the runner reads its own pytest summary and refuses to
mark green above the ceiling in `config/nightly-skip-baseline.json`. The stub
gate prints whatever summary line the test hands it, which is how a run that
skipped 240 is driven here without a suite that skips 240.

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
"""Stub gate. Prints SUMMARY, exits with EXIT, and records that it ran."""
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
    # The real gate's own output is where the runner reads the skip count, so
    # the stub's output is the test's only lever on it.
    sys.stdout.write((HERE / "SUMMARY").read_text(encoding="utf-8"))
    sys.stdout.flush()
    sys.exit(int((HERE / "EXIT").read_text().strip()))
'''

# The ceiling the throwaway tree commits to, and a summary comfortably under it.
# 5 is the value shipped in config/nightly-skip-baseline.json; spelled again here
# because a fixture that read the real file would go red on a deliberate change
# to it for reasons that have nothing to do with the runner.
_CEILING = 5
_BASELINE_SUMMARY = "1 passed, 2 skipped in 1.23s\n"
# What the night of 2026-09-05 actually printed, minus the run's own duration.
_HOLLOW_SUMMARY = "24599 passed, 240 skipped in 913.44s\n"

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
    (root / "SUMMARY").write_text(_BASELINE_SUMMARY, encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "nightly-skip-baseline.json").write_text(
        json.dumps({"max_skips": _CEILING}), encoding="utf-8")

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
# The hollow direction: exit 0 is not "the checks ran"
# ============================================================

def _summary(tree: Path, text: str) -> None:
    (tree / "SUMMARY").write_text(text, encoding="utf-8")


def test_a_hollow_suite_moves_no_marker_and_records_no_verdict(tree):
    """The assertion the runner of 2026-09-05 cannot pass.

    The gate exits 0, as it did that night. The only difference from the green
    case above is the skip count in its summary, and the stubs are again the
    witnesses: a runner that trusted the exit code writes both files.
    """
    _summary(tree, _HOLLOW_SUMMARY)
    result = _drive(tree)

    assert result.returncode != 0, result.stdout + result.stderr
    assert (tree / ".calls" / "run-tests").exists(), (
        "the gate was never invoked, so this test proved nothing about what "
        "happens after it passes hollow")
    assert not (tree / ".calls" / "day-mode.py").exists(), (
        "mark-green was called over a suite that skipped 240 tests")
    assert not (tree / ".calls" / "test-cache.py").exists(), (
        "verdicts were recorded over a suite that skipped 240 tests")
    assert not (tree / ".cache" / "day-mode" / "known-green").exists()
    assert not (tree / ".cache" / "day-mode" / "facts.db").exists(), (
        "the fact cache was warmed on a night that recorded nothing")


def test_a_hollow_suite_revokes_nothing(tree):
    """Same contract as a failing night: the stalled marker is the signal."""
    marker = tree / ".cache" / "day-mode" / "known-green"
    marker.parent.mkdir(parents=True)
    marker.write_text("0123456789abcdef0123456789abcdef01234567\n", encoding="utf-8")
    before = marker.read_text(encoding="utf-8")

    _summary(tree, _HOLLOW_SUMMARY)
    assert _drive(tree).returncode != 0

    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == before


def test_a_hollow_suite_says_why_and_names_the_usual_cause(tree):
    """A refusal nobody can act on is a refusal that gets raised as the ceiling.

    The operator reading this at 01:31 needs the number, the ceiling it broke,
    and where to look first. PATH is named because that is what it was, and
    because nothing in the tree can tell the reader that.
    """
    _summary(tree, _HOLLOW_SUMMARY)
    result = _drive(tree)
    combined = result.stdout + result.stderr

    assert "SKIPPED 240" in combined
    assert f"ceiling of {_CEILING}" in combined
    assert "PATH" in combined
    assert "Nothing was revoked" in combined
    assert "NO NOTIFICATION SENT" in combined


def test_a_hollow_suite_records_the_counts_it_refused_on(tree):
    _summary(tree, _HOLLOW_SUMMARY)
    _drive(tree)
    record = json.loads((tree / ".cache" / "nightly-refresh" / "last-run.json")
                        .read_text(encoding="utf-8"))
    assert record["status"] == "skips_exceeded"
    assert record["gate_exit"] == 0
    assert record["outcomes"] == {"passed": 24599, "skipped": 240}
    assert record["max_skips"] == _CEILING


# ---- the boundary, both sides ------------------------------------------------
# A ceiling read with the wrong comparison is the mutation this pair exists for:
# `>=` where `>` belongs refuses the first of these, `>` where `>=` belongs
# accepts the second. One test on its own catches neither reliably.

def test_a_suite_exactly_at_the_ceiling_still_marks_green(tree):
    """The honest floor is not a violation. A guard that refuses it is broken."""
    _summary(tree, f"12 passed, {_CEILING} skipped in 4.00s\n")
    result = _drive(tree)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tree / ".calls" / "day-mode.py").exists()
    assert (tree / ".calls" / "test-cache.py").exists()


def test_one_skip_over_the_ceiling_is_refused(tree):
    _summary(tree, f"12 passed, {_CEILING + 1} skipped in 4.00s\n")
    result = _drive(tree)

    assert result.returncode != 0, result.stdout + result.stderr
    assert not (tree / ".calls" / "day-mode.py").exists()


# ---- reading the summary at all ----------------------------------------------

@pytest.mark.parametrize("line", [
    # pytest 9.1.1, -q, serial and under -n auto: the same line either way.
    "12 passed, 240 skipped in 913.44s",
    # Colour survives a pipe when something upstream forces it, and this
    # workspace's own shells set FORCE_COLOR.
    "\x1b[32m\x1b[32m\x1b[1m12 passed\x1b[0m, \x1b[33m240 skipped\x1b[0m"
    "\x1b[32m in 913.44s\x1b[0m\x1b[0m",
    # A run over a minute prints the wall clock twice.
    "12 passed, 240 skipped in 913.44s (0:15:13)",
    # Other outcomes sit in the same line and must not displace the count.
    "12 passed, 240 skipped, 3 xfailed, 1 xpassed, 7 warnings in 913.44s",
    # The banner form, in case the gate is ever run without -q.
    "=========== 12 passed, 240 skipped in 913.44s ============",
])
def test_the_skip_count_is_read_from_the_shapes_pytest_actually_prints(tree, line):
    _summary(tree, f"some progress output\n{line}\ntest gate: PASS\n")
    assert _drive(tree).returncode != 0
    assert not (tree / ".calls" / "day-mode.py").exists()


def test_a_count_shaped_line_after_the_summary_does_not_displace_it(tree):
    """The LAST line carrying counts is not necessarily pytest's counts line.

    The gate is a wrapper, so pytest's summary is not the last thing printed;
    `run-tests.py` already adds a line after it, and a plugin or a wrapper can
    add one that happens to carry a number beside an outcome word. What tells
    the real summary apart is its duration, and a reader that took the last
    count-shaped line instead would score this run at 0 skips and mark it green.

    The trailing line here is constructed rather than quoted from a tool that
    prints it today. The mechanism is what is being asserted: anything appended
    after the summary must not be able to overwrite it.
    """
    _summary(tree, _HOLLOW_SUMMARY + "test gate: PASS, 0 skipped checks retried\n")
    result = _drive(tree)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "SKIPPED 240" in (result.stdout + result.stderr)
    assert not (tree / ".calls" / "day-mode.py").exists()


def test_a_summary_that_cannot_be_read_is_refused_not_scored_zero(tree):
    """The decay case, and the direction it must decay in.

    A reader that returned "no counts found" as zero skips would mark this green,
    which is the original defect with a different cause. pytest rewording its
    summary must produce a loud night, not a quiet one.
    """
    _summary(tree, "the wording of this line is not pytest's any more\n")
    result = _drive(tree)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "summary could not be read" in (result.stdout + result.stderr)
    assert not (tree / ".calls" / "day-mode.py").exists()
    record = json.loads((tree / ".cache" / "nightly-refresh" / "last-run.json")
                        .read_text(encoding="utf-8"))
    assert record["status"] == "summary_unreadable"


# ---- the ceiling itself ------------------------------------------------------

@pytest.mark.parametrize("payload", [
    None,                       # absent
    "{not json",                # unparseable
    '{"note": "no ceiling"}',   # present, silent on the number
    '{"max_skips": "5"}',       # a string is not a count
    '{"max_skips": -1}',        # nor is a negative one
])
def test_an_unreadable_ceiling_refuses_before_the_suite_runs(tree, payload):
    """Sixteen minutes of green tests cannot rescue a verdict that cannot apply.

    Asserted by the gate stub NOT having been invoked, which is the observable
    difference between refusing early and refusing late.
    """
    baseline = tree / "config" / "nightly-skip-baseline.json"
    if payload is None:
        baseline.unlink()
    else:
        baseline.write_text(payload, encoding="utf-8")

    result = _drive(tree)

    assert result.returncode != 0, result.stdout + result.stderr
    assert not (tree / ".calls" / "run-tests").exists(), (
        "the suite ran under a ceiling the runner could not read")
    assert not (tree / ".calls" / "day-mode.py").exists()
    record = json.loads((tree / ".cache" / "nightly-refresh" / "last-run.json")
                        .read_text(encoding="utf-8"))
    assert record["status"] == "baseline_unreadable"


def test_the_committed_ceiling_is_a_small_honest_number():
    """The shipped baseline, read where the runner reads it.

    MEASURED 2026-09-05 in HELM at a0931fe: 2 skips in a developer shell, 5 under
    `env -i` with a full PATH. A ceiling that drifted up toward the 240 it exists
    to refuse would pass every test above and stop the guard doing anything, so
    the number itself is pinned rather than only its shape.
    """
    baseline = json.loads((ROOT / "config" / "nightly-skip-baseline.json")
                          .read_text(encoding="utf-8"))
    assert baseline["max_skips"] == 5
    assert baseline["measured"] == "2026-09-05"


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
