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


def test_the_suite_does_not_spend_the_operators_daily_write_allowance():
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
    """
    engine_root = Path(__file__).resolve().parent.parent
    production = engine_root / ".claude" / "state" / "dispatch-rate.json"
    before = production.read_text(encoding="utf-8") if production.is_file() else None

    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(engine_root / "outputs" / "scratch" / "rate-isolation-probe.txt"),
            "content": "a write the operator did not make",
        },
    }
    runner = (
        "import sys, runpy; sys.argv = [sys.argv[1]]; "
        "runpy.run_path(sys.argv[0], run_name='__main__')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", runner, str(engine_root / ".claude" / "hooks" / "_dispatch.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        cwd=str(engine_root), env=dict(os.environ), timeout=120,
    )
    assert proc.returncode == 0, f"the hook exited {proc.returncode}: {proc.stderr}"

    after = production.read_text(encoding="utf-8") if production.is_file() else None
    assert after == before, (
        "a hook call made by the test suite moved the operator's daily write "
        "counter; the suite is spending an allowance it did not earn and is "
        "filling the runaway-loop guard with writes nobody made"
    )

    assert os.environ.get("WS_RATE_LIMIT_STATE"), (
        "WS_RATE_LIMIT_STATE is unset during the run, so the isolation in "
        "tests/conftest.py is gone"
    )


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
