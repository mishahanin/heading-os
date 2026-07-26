"""Root test configuration.

Pin the per-instance timezone for the whole test session so tests that assert
local-time behaviour (calendar, scheduling, daemon heartbeats) validate the
real Etc/GMT-4 logic rather than the engine's UTC default. The production
value lives in the gitignored .env; here we set it deterministically.
See scripts.utils.workspace.get_default_tz().

This file also holds the re-exec guard for the whole suite; see below.
"""
import os
import sys
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


# ============================================================
# Canopus v3: attestation
# ============================================================
#
# pytest_sessionstart above proves the contract did not MOVE. It cannot prove
# the contract RAN: -k, --deselect, --ignore, --lf and a bare path argument all
# reach green with every frozen byte intact, so a builder that cannot edit a
# frozen test can simply decline to run it. These three hooks record what
# actually ran, so `canopus status` reports ATTESTED or NOT ATTESTED against the
# current root hash.
#
# They record; they do not gate, and nothing here is fatal. An earlier revision
# failed a run whose frozen test file collected nothing without an explicit
# filter, on the reasoning that removal from collection is not iteration.
# Telling the two apart needs option sniffing, and option sniffing cannot see a
# bare path argument: the branch fired on `pytest tests/x.py::test_one`, the
# inner loop it was written to spare. Whole-file removal is now caught the way
# everything else is, by producing no attestation.
#
# Naming a SUBSET by node id was the one case that still attested: it collects an
# item, reports it, and fires no deselection hook. Closed in wire 2 for any file
# carrying a freeze-time baseline, because the record compares what was collected
# against the whole-file item count and a subset reports 1 of 7. A frozen test
# file with NO baseline entry keeps the wire 1 reading, where ATTESTED means the
# frozen tests that were collected all passed, not that every frozen test ran.
#
# Module-level state is safe here; the root conftest is loaded once per session.

_CANOPUS = None


def _canopus_recorder():
    """One recorder per session, built lazily so an import error is not fatal."""
    global _CANOPUS
    if _CANOPUS is None:
        from scripts.utils.canopus_gate import AttestationRecorder

        _CANOPUS = AttestationRecorder(_ENGINE_ROOT)
    return _CANOPUS


def pytest_collection_finish(session):
    """Record which frozen tests this run will actually execute.

    Runs after every filter has been applied, so session.items is the real set.
    """
    try:
        _canopus_recorder().collect(session)
    except Exception as exc:  # noqa: BLE001 - record-keeping never breaks a run
        print(f"canopus: attestation collection failed: {exc}", file=sys.stderr)


def pytest_deselected(items):
    """Count items filtered out of frozen test files."""
    try:
        _canopus_recorder().deselected(items)
    except Exception as exc:  # noqa: BLE001 - see above
        print(f"canopus: deselection tally failed: {exc}", file=sys.stderr)


def pytest_runtest_logreport(report):
    """Tally outcomes for frozen test files only."""
    try:
        _canopus_recorder().report(report)
    except Exception as exc:  # noqa: BLE001 - see above
        print(f"canopus: outcome tally failed: {exc}", file=sys.stderr)


def pytest_sessionfinish(session, exitstatus):
    """Write the attestation, or explain why it could not be written.

    Never changes the run's own exit status: a failure to record is a reporting
    problem, and turning it into a test failure would make the record-keeping
    more dangerous than the gap it closes.
    """
    try:
        _canopus_recorder().finish(session, exitstatus)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        print(f"canopus: could not write the attestation: {exc}", file=sys.stderr)


try:  # pytest-xdist is optional: a bare clone may not have it installed.
    import xdist  # noqa: F401

    _HAS_XDIST = True
except ImportError:  # pragma: no cover - exercised only without xdist
    _HAS_XDIST = False

if _HAS_XDIST:
    def pytest_xdist_node_collection_finished(node, ids):
        """Seed the controller's tally; it never runs collection itself."""
        try:
            _canopus_recorder().seed_from_ids(node.config, ids)
        except Exception as exc:  # noqa: BLE001 - record-keeping never breaks a run
            print(f"canopus: controller seeding failed: {exc}", file=sys.stderr)

    def pytest_testnodedown(node, error):
        """Fold a finished worker's deselection counts into the controller."""
        try:
            _canopus_recorder().merge_worker(getattr(node, "workeroutput", None))
        except Exception as exc:  # noqa: BLE001 - see above
            print(f"canopus: worker merge failed: {exc}", file=sys.stderr)
