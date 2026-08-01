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
    before = len(read_denials())
    assert log_denial(mechanism="isolation-probe", action="test",
                      path="tests/test_denial_log_isolation.py",
                      reason="written by the isolation regression test") is True
    records = read_denials()
    assert len(records) == before + 1
    assert records[-1]["mechanism"] == "isolation-probe"
    assert str(denial_log_path()).startswith(str(Path(os.environ["WORKSPACE_LOG_DIR"])))
