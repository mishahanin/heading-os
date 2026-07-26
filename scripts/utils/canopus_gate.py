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
    ANCHOR_MISSING,
    ANCHOR_RECORDED,
    ANCHOR_UNBOUND,
    APPROVED,
    LOCK_HELD,
    LOSS_OF_LOCK,
    FreezeCorrupt,
    build_attestation,
    frozen_test_files,
    lock_state,
    open_release_window,
    read_freeze,
    read_ledger,
    tally_collection,
    unreleased_freeze,
    verify_manifest,
    write_attestation,
)
from scripts.utils.canopus_git import AnchorResolution, resolve_anchor
from scripts.utils.colors import GREEN, RED, RESET, YELLOW


def loss_of_lock_sentences(report: dict, resolution: AnchorResolution) -> list[str]:
    """One sentence per CAUSE of LOSS OF LOCK, said together when several hold.

    `lock_state` reaches this state from FOUR independent causes, and three of
    them arrive with `report["held"]` TRUE: the contract has not moved, so a
    blanket "the frozen contract moved" is simply false, and it sends the
    operator to `verify` for a per-file report that lists nothing.

    Fixed as a SHAPE rather than as the instance that was reported. This is the
    tenth time on this project that a guard was repaired for the case in front of
    its author and left open for its siblings, so every cause gets its own
    sentence here and the enumeration is the thing a reader checks against
    `lock_state`.

    Pure string work, and it never raises: the gate that calls it fails OPEN if
    it does. The closing fallback covers a `lock_state` that reddens for a cause
    this function does not know, which is exactly the drift the enumeration is
    otherwise vulnerable to.
    """
    sentences: list[str] = []
    if not report["held"]:
        sentences.append("The frozen contract moved; run "
                         "`python scripts/canopus.py verify` for the per-file "
                         "report.")
    if resolution.status == ANCHOR_UNBOUND:
        sentences.append(resolution.approval_reason
                         or "the anchor is not in the repository this freeze "
                            "recorded, so the approval cannot be attributed")
    if resolution.status == ANCHOR_MISSING:
        sentences.append(f"The anchor artifact {resolution.anchor} is gone, so "
                         f"the approved hash cannot be read from it.")
    if (resolution.status == ANCHOR_RECORDED
            and resolution.value != report["recomputed_root"]):
        sentences.append(f"The anchor records {resolution.value} and this tree "
                         f"computes {report['recomputed_root']}, so this freeze "
                         f"is not the one that was approved.")
    if not sentences:
        sentences.append(f"The lock is red with anchor status "
                         f"{resolution.status!r}, which this gate cannot name; "
                         f"run `python scripts/canopus.py verify`.")
    return sentences


def _entries(root: Path) -> list:
    """The ledger, or an empty list. Answers rather than raising.

    read_ledger swallows OSError and UnicodeDecodeError and skips damaged lines
    from wire 2.2 onward, so this guard is a second wall rather than the first
    one. Both walls are wanted here and the reason is not symmetry: freeze_gate
    runs at every pytest session start, and a raise here fails OPEN, crashing
    the harness that was supposed to report the state. A guard whose only cost
    is four lines is cheaper than the next input nobody predicted.
    """
    try:
        return read_ledger(root)
    except (OSError, ValueError):
        return []


def _no_manifest(root: Path) -> int:
    """What the gate says when there is no manifest, which is TWO states.

    An ordinary day is silent, and it has to stay silent: a fresh clone has no
    `.canopus/` at all, and a gate that speaks on every CI run teaches an
    operator to skim it.

    The other two states are not ordinary, and until wire 2.2 the WORSE of them
    was the quieter one. A sanctioned `release --window` printed an amber line at
    every later session start. `rm .canopus/freeze.json` under a held lock
    printed NOTHING and exited 0, so deleting the manifest was cheaper than
    releasing it and the ledger, whose whole purpose is to be evidence against
    exactly that, was never read for it.

    So the deletion answers RED, one step louder than the window rather than one
    step quieter, and the escape it names is the LOGGED one. A forced release
    clears state it never parses and writes a `force_release` line, which is
    precisely what tells a cleared lock apart from a deleted one.
    """
    entries = _entries(root)
    vanished = unreleased_freeze(entries)
    if vanished is not None:
        print(f"{RED}canopus: the ledger records a freeze taken "
              f"{vanished.get('ts') or 'at an unrecorded time'} "
              f"(label: {vanished.get('label') or 'unrecorded'}) that no release "
              f"closed, and the manifest it wrote is GONE. There is no contract "
              f"left to check, so the suite is treated as unverified. Re-freeze "
              f"it, or end the lock the way the ledger can see: `python "
              f"scripts/canopus.py release --force --window --reason "
              f"\"<why>\"`.{RESET}")
        return 1
    window = open_release_window(entries)
    if window is not None:
        print(f"{YELLOW}canopus: a release window is open{RESET}  opened "
              f"{window.get('ts') or 'at an unrecorded time'}: "
              f"{window.get('reason') or 'no reason recorded'}. No lock is "
              f"held, so a green suite proves nothing about the contract.")
    return 0


def freeze_gate(root: Path) -> int:
    """The claim, as it stands after wire 2.2, and no wider.

    A build cannot reach green while its contract is moved, AND it cannot reach
    green by making the anchor's repository invisible. The older wording here
    claimed only the first half and was falsified by the second: exporting
    GIT_DIR turned a genuine LOSS OF LOCK into LOCK HELD with exit 0, because
    resolution fell back to the anchor's WORKING copy the moment git answered
    no_repo or no_git.

    The claim scoped to what was measured, because the earlier headline here read
    wider than its evidence. Three routes to "I cannot see the anchor's
    repository" were measured red after this slice where they were green before
    it: exporting GIT_DIR at a decoy directory, renaming the anchor repository's
    `.git`, and removing `git` from PATH. For BLINDING, and only for blinding,
    the bypass now costs a builder more than doing nothing. It does not follow
    that the gate cannot be defeated. Three routes stay open at zero cost and
    exit 0: `rm -rf .canopus/`, which is gitignored so git never saw it;
    `pytest --noconftest`; and `pytest --confcutdir` pointed past this conftest.
    All three are in the "what this does not buy" material in docs/EXTENDING.md,
    so nothing is hidden — what was wrong was a headline broader than its proof.

    What it still does NOT claim, said here rather than left to be discovered.
    Nothing in this file defends against a builder who replaces the `git` binary
    on PATH: every answer this gate binds to comes from a child process, and a
    substituted binary can say whatever the builder wants. And the identity binds
    a repository's LINEAGE rather than an instance — it is a digest over the root
    commits — so a `git clone` of the anchor's repository carries the same root
    commits and reads as the same repository. That is deliberate (a relocated or
    cloned repository IS the same repository, and this workspace has been
    relocated once), and it means the binding proves which HISTORY the anchor
    belongs to, never which copy of it a command read.

    Silent when no freeze is active, no release window is open, and the ledger
    records no freeze whose manifest has vanished. That is the ordinary day. The
    other two states are `_no_manifest`'s business, and the ordering of their
    volumes is the point: a deleted manifest is louder than a released one, not
    quieter.

    NEVER RAISES, whatever the state directory looks like. That is a SHAPE, held
    by the wrapper below rather than by a handler per input, because this is the
    third repair of the same invariant in one slice: a raise here fails OPEN. The
    gate runs at every pytest session start, so an escaping exception crashes the
    harness that was supposed to report a state, and the PreToolUse dispatcher's
    catch-all logs an advisory and CONTINUES while writes to frozen paths sail
    through. Measured, not reasoned: `.canopus/` at mode 000 made `read_freeze`
    raise PermissionError out of `Path.exists()`, past a handler that named only
    FreezeCorrupt.
    """
    try:
        return _freeze_gate(root)
    except Exception as exc:  # noqa: BLE001 — totality IS the requirement
        # Named, so this is a report rather than a swallow, and RED with exit 1
        # so the unexpected fails closed. An operator seeing this line is looking
        # at a gate that could not establish a state, which is not the same claim
        # as a moved contract, and the sentence says so.
        print(f"{RED}canopus: the freeze state could not be established, so the "
              f"contract is treated as unverified: "
              f"{type(exc).__name__}: {exc}{RESET}")
        return 1


def _freeze_gate(root: Path) -> int:
    """The gate proper. Call `freeze_gate`; this one is allowed to raise."""
    try:
        manifest = read_freeze(root)
    except FreezeCorrupt as exc:
        print(f"{RED}canopus: {exc}{RESET}")
        print(f"{RED}canopus: clear it with `python scripts/canopus.py release "
              f"--force --window --reason \"<why>\"`{RESET}")
        return 1
    if manifest is None:
        return _no_manifest(root)

    # A freeze is active, so the contract cannot be checked and NOT be checked:
    # an unreadable member (permissions, a vanished mount) must fail the gate,
    # not crash run-tests.py with a traceback that reads like a tooling bug.
    try:
        report = verify_manifest(manifest, root)
        resolution = resolve_anchor(manifest)
        status, value = resolution.status, resolution.value
    except OSError as exc:
        # The handler stays the filesystem one, and git_output is what keeps
        # that true: it converts OSError, SubprocessError AND ValueError into
        # None. ValueError is not decoration. subprocess.run raises it for an
        # argument holding an embedded NUL byte, and text=True decoding raises
        # UnicodeDecodeError, a ValueError subclass, on a non-UTF-8 gate
        # artifact. Both escaped before wire 2.1, and either one raising here
        # fails OPEN: this gate crashes the pytest session instead of reporting
        # a state, which is worse than any state it could report.
        print(f"{RED}canopus: the frozen contract could not be read, so it cannot "
              f"be verified: {exc}{RESET}")
        return 1
    state = lock_state(report, status, value)

    if state == LOSS_OF_LOCK:
        # Every cause, never the first one thought of. An operator who fixes
        # only the half they were told about is back here on the next run, and
        # an operator told the contract moved when it did not goes looking
        # through a per-file report that lists nothing.
        detail = " ".join(loss_of_lock_sentences(report, resolution))
        print(f"{RED}canopus: {LOSS_OF_LOCK}. {detail}{RESET}")
        return 1
    colour = GREEN if state == LOCK_HELD else YELLOW
    print(f"{colour}canopus: {state}{RESET} (label: {manifest['label']})")
    if resolution.approval != APPROVED:
        # The fourth surface, and the one that actually fires: conftest runs it
        # at every pytest session start and run-tests.py runs it before the
        # suite, while status, verify and pack are commands an operator chooses
        # to type. The lock axis already falls to amber when the approval is
        # uncommitted, so this line adds the REASON rather than the signal,
        # which is precisely what an unexplained amber costs an operator.
        print(f"{YELLOW}canopus: {resolution.approval}{RESET}  "
              f"{resolution.approval_reason}")
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
