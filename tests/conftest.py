"""Root test configuration.

Pin the per-instance timezone for the whole test session so tests that assert
local-time behaviour (calendar, scheduling, daemon heartbeats) validate the
real Etc/GMT-4 logic rather than the engine's UTC default. The production
value lives in the gitignored .env; here we set it deterministically.
See scripts.utils.workspace.get_default_tz().

This file also holds the re-exec guard for the whole suite; see below.
"""
import os
from pathlib import Path

import pytest

from scripts.utils import venv as _venv

os.environ.setdefault("HEADING_OS_TZ", "Etc/GMT-4")

# The suite's re-exec guard, set ONCE, here, because this file is collected
# before any test module.
#
# About twenty scripts call `ensure_venv()` at module scope and about twenty test
# modules load a script by path. When the running interpreter is not
# .venv/bin/python, that call `os.execv`s the WHOLE pytest process, which inherits
# pytest's capture as file descriptor 1: the relaunched run writes every byte
# into a temp file nobody reads, so `pytest tests/` prints ZERO bytes while
# exiting 0 on a passing set and 1 on a failing one. Measured on this repository,
# 2026-07-26. A run that prints nothing is indistinguishable from one that never
# happened.
#
# It lived as three per-module copies until wire 2.2, and that shape could not
# hold. The variable is process-global, so each copy satisfied the other two
# modules' guard tests: deleting the line from one module left its own test
# passing. Worse, the defect was self-erasing, because a NEW unguarded module
# re-execs at collection, `ensure_venv` sets this same sentinel before
# `os.execv`, and in the relaunched silent run every guard test passes.
#
# The constant is referenced rather than spelled out: a duplicated literal drifts
# silently the day venv.py renames it.
os.environ.setdefault(_venv._SENTINEL, "1")

_TESTS_ROOT = Path(__file__).resolve().parent
_ENGINE_ROOT = _TESTS_ROOT.parent

# Runtime logs written during a test run go to their own directory, never to the
# operator's. Measured the day the denial counter landed (2026-08-01): a single
# suite run appended 13 refusals to the production `.logs/denials/denials.jsonl`
# from tests that legitimately exercise leak-guard and the push walls with
# fixtures. Left alone, the instrument built to decide which guards earn their
# cost would have counted its own test suite as the workspace's main offender,
# which is the same defect class as an instrument with a silently wrong
# denominator — the thing this counter exists to end, reproduced inside it.
#
# Assignment, not setdefault: isolation that a stray shell variable can switch
# off is not isolation. Individual tests still redirect per-case with monkeypatch
# or an explicit subprocess env, and both continue to win over this.
_TEST_LOG_DIR = str(_ENGINE_ROOT / ".logs" / "_pytest")
os.environ["WORKSPACE_LOG_DIR"] = _TEST_LOG_DIR

# The same isolation, one guard along. `check_rate_limit` in the PreToolUse
# dispatcher counts Write and Edit calls per day and BLOCKS past 1000, and six
# test modules drive that hook in a subprocess exactly as production does.
# Measured 2026-08-07, before this line: the operator's counter stood at 1033
# and was blocking three tests, and the writes it had stored were fixtures —
# `threads/personal/foo.md`, a Windows path that cannot exist on this machine,
# a scratch probe file. One run of three of those modules added 12 more.
#
# UNLINKED at import, not merely redirected. The counter is keyed by date and
# resets itself tomorrow, but a run this hour would otherwise inherit every
# earlier run's fixtures today, and enough runs in one day would reproduce the
# same block in the new location. Starting from zero is what makes the
# redirection a fix rather than a move.
#
# Only the session that OWNS the variable resets it, and that distinction was
# measured rather than reasoned. This suite spawns pytest CHILDREN — the Canopus
# probe alone runs several per contract — and each child imports this file. With
# an unconditional unlink they wiped their parent's counter mid-run, so after a
# full run the file was simply gone and the reset the comment promised was not
# the reset the code performed. An inherited variable means a parent already did
# this; the child leaves it alone.
_TEST_RATE_STATE = _ENGINE_ROOT / ".logs" / "_pytest" / "dispatch-rate.json"
_OWNS_RATE_STATE = "WS_RATE_LIMIT_STATE" not in os.environ
os.environ["WS_RATE_LIMIT_STATE"] = str(_TEST_RATE_STATE)
if _OWNS_RATE_STATE:
    try:
        _TEST_RATE_STATE.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:  # pragma: no cover - reported, never fatal to the run
        print(f"[conftest] could not reset the test rate-limit state: {exc}")


@pytest.fixture(autouse=True)
def _isolate_runtime_logs():
    """Re-arm the redirection before every test.

    The module-scope assignment above covers import time and nothing else. A
    test that sets the variable by hand and pops it in a `finally` leaves it
    UNSET for every test that follows, and the rest of the session then writes
    to the operator's real log. That is not hypothetical: the denial counter's
    own contract does exactly that, and it is how this fixture was found.
    Restoring per test costs nothing and does not fight monkeypatch, which
    applies inside the test body, after this.
    """
    os.environ["WORKSPACE_LOG_DIR"] = _TEST_LOG_DIR
    os.environ["WS_RATE_LIMIT_STATE"] = str(_TEST_RATE_STATE)
    yield


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests by top-level directory so the per-push CI filter holds.

    The per-push unit job runs `-m "not integration and not acceptance"`. That
    filter is only honest if every test under tests/integration/ actually carries
    the `integration` marker (and likewise tests/acceptance/ -> `acceptance`).
    Marking by path removes the requirement that each file remember to declare it,
    and closes the gap where an unmarked integration suite (e.g. the LFS-fixture
    convert-to-md tests) silently ran in the unit job and failed on a fresh clone.
    Both markers are registered in [tool.pytest.ini_options]; --strict-markers is on.
    """
    for item in items:
        try:
            rel = item.path.resolve().relative_to(_TESTS_ROOT)
        except (ValueError, AttributeError):
            continue
        top = rel.parts[0] if rel.parts else ""
        if top in ("integration", "acceptance"):
            item.add_marker(top)
