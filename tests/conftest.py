"""Root test configuration.

Pin the per-instance timezone for the whole test session so tests that assert
local-time behaviour (calendar, scheduling, daemon heartbeats) validate the
real Etc/GMT-4 logic rather than the engine's UTC default. The production
value lives in the gitignored .env; here we set it deterministically.
See scripts.utils.workspace.get_default_tz().
"""
import os
import sys
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
# They record; they do not gate. Failing a filtered run would charge every
# inner-loop iteration for a hole that a passive record closes at the point of
# comparison. The single exception is below: an unfiltered run whose frozen test
# file collected nothing is removal from collection, not iteration.
#
# Module-level state is safe here; the root conftest is loaded once per session.

_CANOPUS: dict = {}

_CANOPUS_FILTERS = (
    ("keyword", "-k"),
    ("markexpr", "-m"),
    ("deselect", "--deselect"),
    ("ignore", "--ignore"),
    ("ignore_glob", "--ignore-glob"),
    ("lf", "--lf"),
    ("failedfirst", "--ff"),
    ("stepwise", "--sw"),
)


def _canopus_rel(candidate):
    """Root-relative POSIX path, or None when it lies outside the tree."""
    path = Path(str(candidate))
    if not path.is_absolute():
        path = _ENGINE_ROOT / path
    try:
        return path.resolve().relative_to(_ENGINE_ROOT).as_posix()
    except (ValueError, OSError):
        return None


def _canopus_collect(session):
    from scripts.utils import canopus_freeze as cf

    manifest = cf.read_freeze(_ENGINE_ROOT)
    if manifest is None:
        return
    config = session.config
    frozen = cf.frozen_test_files(manifest, config.getini("python_files") or ["test_*.py"])
    _CANOPUS["root"] = cf.verify_manifest(manifest, _ENGINE_ROOT)["recomputed_root"]
    _CANOPUS["reasons"] = [
        f"{flag} restricted the run"
        for attr, flag in _CANOPUS_FILTERS
        if getattr(config.option, attr, None)
    ]

    collected = []
    for item in session.items:
        rel = _canopus_rel(getattr(item, "path", ""))
        if rel is not None:
            collected.append(rel)
    counts = cf.tally_collection(frozen, collected)

    empty = [rel for rel, entry in counts.items() if entry["collected"] == 0]
    if empty and not _CANOPUS["reasons"]:
        raise pytest.UsageError(
            "canopus: frozen test files were not collected: "
            + ", ".join(empty)
            + ". No filter was given, so something removed them from the run. A "
            "contract that is genuinely wrong reopens the approval gate; it is "
            "never dropped from collection."
        )
    _CANOPUS["frozen"] = counts


def pytest_collection_finish(session):
    """Record which frozen tests this run will actually execute.

    Runs after every filter has been applied, so session.items is the real set.
    """
    try:
        _canopus_collect(session)
    except pytest.UsageError:
        raise
    except Exception as exc:  # noqa: BLE001 - record-keeping never breaks a run
        print(f"canopus: attestation collection failed: {exc}", file=sys.stderr)


def pytest_runtest_logreport(report):
    """Tally outcomes for frozen test files only."""
    frozen = _CANOPUS.get("frozen")
    if not frozen:
        return
    counts = frozen.get(_canopus_rel(report.fspath))
    if counts is None:
        return
    if report.outcome == "failed":
        counts["failed"] += 1
    elif report.outcome == "skipped" and report.when in ("setup", "call"):
        counts["skipped"] += 1
    elif report.outcome == "passed" and report.when == "call":
        counts["passed"] += 1


def pytest_sessionfinish(session, exitstatus):
    """Write the attestation, or explain why it could not be written.

    Never changes the run's own exit status: a failure to record is a reporting
    problem, and turning it into a test failure would make the record-keeping
    more dangerous than the gap it closes.
    """
    frozen = _CANOPUS.get("frozen")
    if frozen is None:
        return
    try:
        from datetime import datetime, timezone

        from scripts.utils import canopus_freeze as cf

        cf.write_attestation(_ENGINE_ROOT, cf.build_attestation(
            root_digest=_CANOPUS.get("root") or "",
            frozen_tests=frozen,
            filter_reasons=_CANOPUS.get("reasons") or [],
            exit_status=int(exitstatus),
            attested_at=datetime.now(timezone.utc).isoformat(),
        ))
    except Exception as exc:  # noqa: BLE001 - see the docstring
        print(f"canopus: could not write the attestation: {exc}", file=sys.stderr)
