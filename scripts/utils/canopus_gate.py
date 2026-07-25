#!/usr/bin/env python3
"""The Canopus freeze check, as run by every route into the test suite.

Separate from scripts/canopus.py (an operator CLI) and from run-tests.py (which
re-execs the interpreter at import time via ensure_venv, so it is not safely
importable from a test).

Two callers, deliberately: tests/conftest.py runs it at pytest session start,
and scripts/run-tests.py runs it before spawning the suite. conftest covers the
CLASS of invocations rather than one command — bare `pytest tests/test_thing.py`
is the inner-loop command a build runs dozens of times per slice, while
run-tests.py runs once at the end or not at all. The duplicate call costs one
extra read_freeze.

This is where the freeze guarantee actually fires. Everything else about the
freeze is inert without it, because a verification that is never invoked fails
100% of the time regardless of how well its expected value is protected.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from scripts.utils.canopus_freeze import (
    LOCK_HELD,
    LOSS_OF_LOCK,
    FreezeCorrupt,
    anchor_state,
    build_attestation,
    frozen_test_files,
    lock_state,
    read_freeze,
    tally_collection,
    verify_manifest,
    write_attestation,
)
from scripts.utils.colors import GREEN, RED, RESET, YELLOW


def freeze_gate(root: Path) -> int:
    """Canopus wire 1: a build cannot reach green while its contract is moved.

    Silent when no freeze is active, which is the ordinary day.
    """
    try:
        manifest = read_freeze(root)
    except FreezeCorrupt as exc:
        print(f"{RED}canopus: {exc}{RESET}")
        print(f"{RED}canopus: clear it with `python scripts/canopus.py release "
              f"--force --reason \"<why>\"`{RESET}")
        return 1
    if manifest is None:
        return 0

    # A freeze is active, so the contract cannot be checked and NOT be checked:
    # an unreadable member (permissions, a vanished mount) must fail the gate,
    # not crash run-tests.py with a traceback that reads like a tooling bug.
    try:
        report = verify_manifest(manifest, root)
        _anchor, status, value = anchor_state(manifest)
    except OSError as exc:
        print(f"{RED}canopus: the frozen contract could not be read, so it cannot "
              f"be verified: {exc}{RESET}")
        return 1
    state = lock_state(report, status, value)

    if state == LOSS_OF_LOCK:
        print(f"{RED}canopus: {LOSS_OF_LOCK}. The frozen contract moved; run "
              f"`python scripts/canopus.py verify` for the per-file report.{RESET}")
        return 1
    colour = GREEN if state == LOCK_HELD else YELLOW
    print(f"{colour}canopus: {state}{RESET} (label: {manifest['label']})")
    return 0


class AttestationRecorder:
    """One pytest session's attestation state, driven by the conftest hooks.

    A plain object rather than module-level globals in conftest, because the
    tests that exercise these hooks would otherwise have to monkeypatch the
    LIVE session's counters. Measured: they did, and the suite's own run
    recorded 20 of 31 reports because eleven tests redirected the tally into a
    throwaway dict. A record that the test suite can silently corrupt is worse
    than no record.

    Duck-typed against pytest's session, config, item, and report objects; no
    pytest import, so run-tests.py can import this module outside the venv.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root_digest: str | None = None
        self.frozen: dict | None = None
        self.patterns: list[str] = ["test_*.py"]
        # The per-file item counts taken at freeze time by `freeze --contract`.
        # Empty for a wire 1 freeze, and an absent entry keeps the wire 1
        # behaviour for that file rather than failing it.
        self.baseline: dict = {}
        # Deselections arrive BEFORE the tally exists (see deselected below), so
        # they are buffered by root-relative path and folded in on every route
        # that builds or rebuilds self.frozen.
        self.pending_deselected: dict[str, int] = {}

    def _rel(self, candidate) -> str | None:
        """Root-relative POSIX path, or None when it lies outside the tree."""
        path = Path(str(candidate))
        if not path.is_absolute():
            path = self.root / path
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except (ValueError, OSError):
            return None

    def _frozen_names(self, config) -> list[str] | None:
        """The frozen test files, or None when no freeze is active."""
        manifest = read_freeze(self.root)
        if manifest is None:
            return None
        self.patterns = config.getini("python_files") or ["test_*.py"]
        self.baseline = manifest.get("baseline") or {}
        self.root_digest = verify_manifest(manifest, self.root)["recomputed_root"]
        return frozen_test_files(manifest, self.patterns)

    def collect(self, session) -> None:
        """Record which frozen tests this run will actually execute.

        Fires in a plain run and inside each xdist worker. The xdist CONTROLLER
        never reaches it: collection happens in the workers, and the controller's
        session.items stays empty. seed_from_ids below is the controller's route.
        """
        frozen = self._frozen_names(session.config)
        if frozen is None:
            return
        collected = [
            rel for rel in (self._rel(getattr(item, "path", "")) for item in session.items)
            if rel is not None
        ]
        self.frozen = tally_collection(frozen, collected)
        self._apply_pending()

    def seed_from_ids(self, config, ids) -> None:
        """Seed the controller's tally from a worker's collected node ids.

        Measured, not assumed: under -n auto the controller sees no items at
        collection time, so without this the canonical gate records
        "collected nothing" for every frozen file and can never attest. The ids
        arrive post-deselection, which is why workers ship their deselection
        counts back separately.
        """
        if self.frozen is not None:
            return
        frozen = self._frozen_names(config)
        if frozen is None:
            return
        collected = [
            rel for rel in (self._rel(str(node_id).split("::", 1)[0]) for node_id in ids)
            if rel is not None
        ]
        self.frozen = tally_collection(frozen, collected)
        self._apply_pending()

    def merge_worker(self, worker_output) -> None:
        """Fold one worker's deselection counts into the controller's tally.

        pytest's deselection hook fires inside the worker that did the
        collecting, so the controller learns about it only through the worker's
        shipped-back output.

        Taken at face value, never summed: every xdist worker collects the FULL
        set and deselects identically, so adding across workers multiplied the
        count by the worker number. The larger of the two wins, so a worker that
        somehow filtered more is not silently under-reported.
        """
        if not self.frozen or not isinstance(worker_output, dict):
            return
        for rel, count in (worker_output.get("canopus_deselected") or {}).items():
            counts = self.frozen.get(rel)
            if counts is not None:
                counts["deselected"] = max(counts["deselected"], int(count))

    def _apply_pending(self) -> None:
        """Fold buffered deselections into the tally, once there is one.

        Never lowers a count: merge_worker may already have folded a worker's
        larger figure in.
        """
        if not self.frozen:
            return
        for rel, count in self.pending_deselected.items():
            counts = self.frozen.get(rel)
            if counts is not None:
                counts["deselected"] = max(counts["deselected"], count)

    def deselected(self, items) -> None:
        """Count items filtered out of frozen test files.

        -k, -m, --lf and --deselect all route through pytest's deselection hook,
        which is why nothing here inspects an option, or whether one was given.

        BUFFERED, not written straight into the tally. pytest fires this hook
        from inside pytest_collection_modifyitems, which runs BEFORE
        pytest_collection_finish builds self.frozen — so an earlier revision's
        `if not self.frozen: return` guard dropped every deselection on the
        floor, and collect() then seeded a fresh all-zero tally over the top.
        Measured, not theorised: `pytest -k test_a` on a 3-test frozen file
        printed "2 deselected" and still attested "none deselected", in a plain
        run and under -n 2 alike. The entire -k / -m / --lf / --deselect
        detection axis was inert while its unit tests passed, because they call
        this method after seeding the tally by hand and so invert the real hook
        order.
        """
        for item in items:
            rel = self._rel(getattr(item, "path", ""))
            if rel is not None:
                self.pending_deselected[rel] = self.pending_deselected.get(rel, 0) + 1
        self._apply_pending()

    def report(self, report) -> None:
        """Tally one outcome, for frozen test files only."""
        if not self.frozen:
            return
        counts = self.frozen.get(self._rel(report.fspath))
        if counts is None:
            return
        if report.outcome == "failed":
            counts["failed"] += 1
        elif report.outcome == "skipped" and report.when in ("setup", "call"):
            counts["skipped"] += 1
        elif report.outcome == "passed" and report.when == "call":
            counts["passed"] += 1

    def finish(self, session, exitstatus) -> bool:
        """Write the record from the controller only. True when one was written.

        Under pytest-xdist every worker reaches session finish holding a partial
        tally and its own exit status, and the last writer would win. A worker is
        the only process carrying config.workerinput.
        """
        if os.environ.get("CANOPUS_NO_ATTEST"):
            # The contract runner sets this in the child it spawns. `probe` can
            # run while a freeze is held, and a probe's partial tally must never
            # overwrite the record left by a real gate run.
            return False
        if self.frozen is None:
            return False
        if hasattr(session.config, "workerinput"):
            # A worker ships its deselection counts home instead of writing.
            output = getattr(session.config, "workeroutput", None)
            if output is not None:
                output["canopus_deselected"] = {
                    rel: counts["deselected"] for rel, counts in self.frozen.items()
                    if counts["deselected"]
                }
            return False
        write_attestation(self.root, build_attestation(
            root_digest=self.root_digest or "",
            frozen_tests=self.frozen,
            exit_status=int(exitstatus),
            attested_at=datetime.now(timezone.utc).isoformat(),
            baseline=self.baseline,
        ))
        return True
