"""The test suite must never append to the operator's denial log.

Regression test for a defect measured on 2026-08-01, the day the counter landed:
one suite run wrote 13 refusals into the production
`.logs/denials/denials.jsonl`, from tests that legitimately drive leak-guard and
the push walls with fixtures. A counter whose largest contributor is its own test
suite cannot answer the question it was built for.

The isolation itself lives at module scope in `tests/conftest.py`, because it has
to be in force before any test module imports anything. This file asserts it is
still in force, so removing that line fails a test rather than silently poisoning
a month of measurements.
"""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from scripts.utils.denial_log import denial_log_path, log_denial, read_denials


def test_the_suite_writes_its_denials_somewhere_of_its_own():
    engine_root = Path(__file__).resolve().parent.parent
    production = engine_root / ".logs" / "denials" / "denials.jsonl"
    assert denial_log_path() != production, (
        "the suite is pointed at the operator's denial log; a run would count "
        "its own fixtures as real refusals"
    )
    assert os.environ.get("WORKSPACE_LOG_DIR"), (
        "WORKSPACE_LOG_DIR is unset during the run, so the isolation in "
        "tests/conftest.py is gone"
    )


def test_the_suite_does_not_spend_the_operators_daily_write_allowance(tmp_path):
    """The same defect as this file's first test, one guard along.

    `check_rate_limit` in `.claude/hooks/_dispatch.py` counts Write and Edit
    calls per day and BLOCKS past 1000. Six test modules drive that hook in a
    subprocess exactly as production does, so every fixture write they make was
    counted against the operator's real allowance. Measured 2026-08-07: the
    production counter stood at 1033 and was blocking, and its stored recent
    writes were fixtures — `threads/personal/foo.md`, a Windows path that
    cannot exist on this machine, a scratch probe file. One run of three of
    those modules added 12 more.

    Two things go wrong at once, and the second is worse. The suite goes red on
    an unrelated day's volume, which is visible. And the guard's own numerator
    fills with work nobody did, so when a runaway loop finally does fire it
    cannot be told from a week of testing, which is not visible at all.

    Asserted through the hook as production drives it rather than by reading a
    constant: the redirection has to survive the subprocess, and a test that
    only checked the environment variable would pass against a hook that reads
    it and ignores it.

    **Two hook calls, and the second one is why.** Until 2026-09-04 one call did
    both jaws, and jaw two looked for its marker in the file the WHOLE run
    shares. That marker lives in `recent`, which `check_rate_limit` truncates to
    the last `RATE_LIMIT_LOOP_WINDOW` entries — twenty. Under `-n auto` every
    worker drives this hook, so twenty foreign writes arriving between this
    test's write and its read evict the marker and the test fails over work it
    did not do. MEASURED 2026-09-04, twenty-one sequential Write payloads
    against one state file: the first marker is gone and `len(recent)` is 20.

    That is a second mechanism, on top of the one the same 2026-09-04 fix
    closed: overlapping hook processes were also LOSING each other's updates
    outright, because the load-modify-save in `check_rate_limit` was not
    serialised (`tests/test_a_runaway_loop_guard_that_lost_the_events_it_counted.py`).
    The lock fixes the losing. Nothing fixes the eviction, because the eviction
    is the guard working as designed — so the assertion moved off the shared
    file instead.

    Jaw one still drives the hook with the environment the suite actually runs
    under, which is the property being guarded. Jaw two drives it a second time
    against a private file, where no other worker can reach the answer.
    """
    engine_root = Path(__file__).resolve().parent.parent
    production = engine_root / ".claude" / "state" / "dispatch-rate.json"
    hook = engine_root / ".claude" / "hooks" / "_dispatch.py"
    runner = (
        "import sys, runpy; sys.argv = [sys.argv[1]]; "
        "runpy.run_path(sys.argv[0], run_name='__main__')"
    )

    def drive(marker: str, env: dict) -> None:
        # A marker unique to THIS call, never a whole-file comparison. Until
        # 2026-09-01 this test read the production file before and after and
        # asserted the two strings equal. That file is live shared state: its
        # `recent` list holds the last 20 tool calls and `tool_history` around
        # four hundred, and BOTH grow on every Write, Edit and Bash the
        # operator's own session makes. So any concurrent work between the two
        # reads failed the test over a change the test did not cause.
        #
        # MEASURED that day. Under five concurrent fix agents the full suite
        # came back `1 failed, 20292 passed`, this being the one, with a diff
        # whose differing region was the counter and the recent-writes list. Run
        # alone, immediately afterwards and against the same code, it passed
        # three times out of three. Nothing about the hook changed between those
        # runs.
        #
        # A flaky guard is worse than a missing one: it teaches its reader that
        # red here means "somebody was busy", and this guard's real failure
        # looks exactly the same.
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(engine_root / "outputs" / "scratch" / marker),
                "content": "a write the operator did not make",
            },
        }
        proc = subprocess.run(
            [sys.executable, "-c", runner, str(hook)],
            input=json.dumps(payload), capture_output=True, text=True,
            cwd=str(engine_root), env=env, timeout=120,
        )
        assert proc.returncode == 0, f"the hook exited {proc.returncode}: {proc.stderr}"

    # Jaw one, the negative: driven under the suite's own environment, the
    # operator's real ledger never hears of us. Absence from a whole file is
    # true no matter what any other worker does to that file.
    inherited = f"rate-isolation-probe-{uuid.uuid4().hex}.txt"
    drive(inherited, dict(os.environ))

    produced = production.read_text(encoding="utf-8") if production.is_file() else ""
    assert inherited not in produced, (
        "a hook call made by the test suite landed in the operator's real "
        "dispatch-rate ledger; the suite is spending an allowance it did not "
        "earn and is filling the runaway-loop guard with writes nobody made"
    )

    redirected = os.environ.get("WS_RATE_LIMIT_STATE")
    assert redirected, (
        "WS_RATE_LIMIT_STATE is unset during the run, so the isolation in "
        "tests/conftest.py is gone"
    )
    assert Path(redirected) != production, (
        "WS_RATE_LIMIT_STATE points at the operator's own ledger, so the "
        "redirection is a redirection to nowhere"
    )

    # Jaw two, the positive: the hook DOES count, and it counts where the
    # variable sends it. Without this the test passes against a hook that counts
    # NOWHERE at all, which would be the rate limit silently switched off rather
    # than redirected. Against a private file, so neither an eviction nor
    # another worker can decide the answer.
    private_state = tmp_path / "dispatch-rate.json"
    private = f"rate-isolation-probe-{uuid.uuid4().hex}.txt"
    drive(private, dict(os.environ, WS_RATE_LIMIT_STATE=str(private_state)))

    assert private_state.is_file(), (
        f"the redirected rate-limit state {private_state} was never written, so "
        f"the hook counted this write nowhere and the guard is off, not "
        f"isolated")
    assert private in private_state.read_text(encoding="utf-8"), (
        f"the probe write is in neither ledger. It is absent from the "
        f"operator's file, which is what this test wants, but it is also "
        f"absent from the {private_state.name} the variable named, so nothing "
        f"counted it and this test would pass against a rate limit that does "
        f"not run.")


def test_a_denial_written_during_the_suite_lands_in_the_suite_directory():
    """Assert on a marker unique to this call, never on the whole-file total.

    The isolation this file guards points EVERY xdist worker at one directory,
    so the log it reads is shared by the whole run and roughly 1300 records
    arrive in it from other tests. A count delta and a "last record" check both
    read whichever worker wrote most recently, so both fail whenever another
    worker appends between the write and the read. That is not theoretical: the
    pre-push gate runs the suite through `scripts/run-tests.py` with `-n auto`,
    and this test failed there three times in twenty-four runs, once with
    `assert 1456 == (1453 + 1)` — two foreign records inside the window.

    Asserting the record's own presence tests the same property (a denial
    written during the suite lands in the suite's log, not the operator's) and
    is true no matter what any other worker does.
    """
    marker = f"isolation-probe-{os.getpid()}-{time.time_ns()}"
    assert log_denial(mechanism=marker, action="test",
                      path="tests/test_denial_log_isolation.py",
                      reason="written by the isolation regression test") is True
    assert any(record.get("mechanism") == marker for record in read_denials()), (
        "the record this test just wrote is absent from the log the suite is "
        "pointed at"
    )
    assert str(denial_log_path()).startswith(str(Path(os.environ["WORKSPACE_LOG_DIR"])))
