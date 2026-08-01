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
import os
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
