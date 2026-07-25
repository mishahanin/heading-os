"""Root test configuration.

Pin the per-instance timezone for the whole test session so tests that assert
local-time behaviour (calendar, scheduling, daemon heartbeats) validate the
real Etc/GMT-4 logic rather than the engine's UTC default. The production
value lives in the gitignored .env; here we set it deterministically.
See scripts.utils.workspace.get_default_tz().
"""
import os
from pathlib import Path

import pytest

os.environ.setdefault("HEADING_OS_TZ", "Etc/GMT-4")

_TESTS_ROOT = Path(__file__).resolve().parent
_ENGINE_ROOT = _TESTS_ROOT.parent


def pytest_sessionstart(session):
    """Canopus wire 1: refuse to run the suite while the frozen contract has moved.

    The gate has to fire from the CLASS of invocations, not from one command.
    scripts/run-tests.py also calls it, but that runs once at the end of a slice
    or not at all, while bare `pytest tests/test_thing.py` is the inner loop a
    build runs dozens of times — and reaching green there with a moved contract
    is exactly the outcome the freeze exists to prevent. Raising here stops the
    run before collection.

    Silent on the ordinary day: with no .canopus/ state the gate returns 0 and
    prints nothing, which is also what a fresh CI checkout sees (.canopus/ is
    gitignored). Imported inside the hook so a broken import cannot take the
    whole suite down at collection time.
    """
    from scripts.utils.canopus_gate import freeze_gate

    if freeze_gate(_ENGINE_ROOT) != 0:
        raise pytest.UsageError(
            "canopus: the frozen test contract moved; the suite will not run. "
            "Run `python scripts/canopus.py verify` for the per-file report. A "
            "contract that is genuinely wrong reopens the approval gate; it is "
            "never edited in place."
        )


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
