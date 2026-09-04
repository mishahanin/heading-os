#!/usr/bin/env python3
"""The night side of the day-mode contract, as one command.

Day mode (`scripts/day-mode.py`) is only safe because something else runs
everything it skipped. That something is this file, once a night, and the
contract it implements is not invented here: it is printed verbatim by
`python scripts/day-mode.py nightly` and quoted in that file's module docstring.
Read it before changing anything below.

The order is the whole point:

  1. Run the FULL suite through `scripts/run-tests.py`, unchanged. No selection,
     no verdict cache, no day-mode narrowing. That is the only run that covers
     what day mode cannot reach.
  2. ONLY on success, move the base day mode selects against
     (`day-mode.py mark-green <HEAD>`) and record the passing files in the
     verdict store (`test-cache.py record --base <HEAD>`).
  3. ONLY on success, warm the derived artifacts a cold morning would otherwise
     pay for. Today that is the day-mode fact cache.
  4. On FAILURE, do none of 2 or 3. Revoke nothing. Make the failure LOUD.

Step 4 is the one that matters. A nightly that fails into a log nobody reads
converts day mode from a speed-up into a hole, because the green marker stops
advancing while `day-mode select` keeps narrowing against it. The failure goes
to the operator's own Telegram sink through `scripts/utils/telegram_notify.py`,
which `.claude/rules/lethal-trifecta.md` exempts from the outbound-send gate
BECAUSE it can only reach the operator: `own_targets()` resolves an allowlist
from the environment and refuses any recipient a caller could produce. This
file never names a recipient; it asks that module which sinks are the
operator's own and uses those.

The second property of a loud night -- notifying when the night did not RUN AT
ALL -- is NOT implemented as a second watchdog here, deliberately. The contract
already names its mechanism: the green marker's age. `day-mode.py select`
prints it on every run, so a marker that stopped advancing is visible at the
point of use. `--status` below is the direct read of the same question.

Usage:
  python scripts/nightly-refresh.py              # the night's run
  python scripts/nightly-refresh.py --status     # what the last run did, and when
  python scripts/nightly-refresh.py --dry-run    # resolved paths and commands, no run

Persistence: one JSON run record under `.cache/nightly-refresh/`, written
atomically, beside the two SQLite stores this run refreshes
(`.cache/day-mode/facts.db`, `.cache/test-verdicts.db`). Nothing new listens on
a port and no daemon is added.

SCHEDULING IS NOT DONE HERE. This file installs nothing and starts nothing.
`scripts/install-nightly-refresh-timer.sh` is the installer, it runs in HELM
only, and its `--check` mode is how an operator ESTABLISHES that the timer is
armed rather than inferring it from the installer having been merged.

Tests: tests/test_a_nightly_that_marked_green_over_a_failing_suite.py
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.venv_guard import ensure_venv  # noqa: E402

# Called here, at the top, for the same reason `scripts/run-tests.py` calls it:
# pytest lives only in `.venv`. It is called BEFORE the suite rather than after,
# because this file later imports `run-tests.py` to read the one copy of the
# marker expression, and that import calls `ensure_venv()` again. A re-exec
# after the suite had run would restart this process and run the whole suite a
# second time; a re-exec here has done nothing yet and costs nothing.
ensure_venv()

from scripts.utils.colors import GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.day_mode import build_index  # noqa: E402
from scripts.utils.paths import load_env  # noqa: E402
from scripts.utils.telegram_notify import notify, own_targets  # noqa: E402

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
RECORD_REL = Path(".cache/nightly-refresh/last-run.json")


# ============================================================
# Small helpers
# ============================================================

def _log(message: str) -> None:
    """One line to stdout, flushed. Under systemd this is the journal."""
    print(message, flush=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _head_revision(root: Path) -> str | None:
    """The revision this night is about, or None when git cannot answer.

    None is never read as "no changes": every caller treats it as a refusal,
    because a night that cannot name its revision cannot record one either.
    """
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _write_record(root: Path, payload: dict) -> None:
    """Atomic write of the run record: `.tmp` then `os.replace`."""
    path = root / RECORD_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _read_record(root: Path) -> dict | None:
    path = root / RECORD_REL
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Absent and unparseable are answered the same way ON PURPOSE: both mean
        # "this run record cannot tell you the night ran", and a caller that
        # distinguished them would still have to refuse in both branches.
        return None


def alarm(root: Path, subject: str, detail: str) -> int:
    """Print the failure loudly and push it to the operator's OWN sinks.

    Returns the number of sinks that accepted the message. A zero return is
    reported by the caller rather than swallowed: an unconfigured sink means the
    night failed AND nobody was told, which is strictly worse than either alone.

    The recipient is never named here. `own_targets()` resolves the operator's
    own sinks from the environment, and `notify()` refuses anything else, which
    is what keeps this off the outbound-send gate.
    """
    _log(f"{RED}nightly-refresh: {subject}{RESET}")
    for line in detail.splitlines():
        _log(f"  {line}")
    text = f"HEADING OS nightly-refresh: {subject}\n\n{detail}"
    delivered = 0
    for target in sorted(own_targets()):
        if notify(target, text):
            delivered += 1
    if delivered:
        _log(f"nightly-refresh: notified {delivered} operator sink(s).")
    else:
        _log(f"{RED}nightly-refresh: NO NOTIFICATION SENT{RESET} -- no operator"
             " sink is configured, or the transport refused. The failure above"
             " reached this log and nothing else.")
    return delivered


# ============================================================
# The corpus the night proves green
# ============================================================

def _gate_argv(root: Path) -> list[str]:
    """The pytest argv `scripts/run-tests.py` would use for the regression gate.

    Loaded from that file rather than restated, so the marker expression has ONE
    copy. A second copy is this repository's dominant defect shape: the fix
    lands in one of them and the other keeps the old answer. The file cannot be
    imported by name (`run-tests` is not a Python identifier), hence the path
    load.
    """
    path = root / "scripts" / "run-tests.py"
    spec = importlib.util.spec_from_file_location("_nightly_gate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.build_command(False))


def collect_corpus(root: Path) -> list[str]:
    """The test files the regression gate collects, repository-relative.

    Run BEFORE the suite, so a broken collection costs seconds rather than being
    discovered after eight minutes of green tests. The claim this establishes is
    narrow and exact: these are the files pytest collects under the SAME marker
    expression the gate runs. It is not a claim that each of them passed -- the
    caller only records them after the gate has exited 0 over that same
    selection.
    """
    # `-n auto` is dropped as a PAIR, not by value: xdist buys nothing for a
    # collection and a bare `a not in ("-n", "auto")` filter would also eat an
    # unrelated literal `auto` somewhere else in the argv.
    gate = _gate_argv(root)
    argv = []
    skip_next = False
    # Named `arg`, not `token`: ruff's S105 reads `token == "<literal>"` as a
    # hardcoded credential comparison, and a `noqa` to silence a rule that is
    # right about the shape and wrong about this variable is worse than the
    # word.
    for arg in gate:
        if skip_next:
            skip_next = False
            continue
        if arg == "-n":
            skip_next = True
            continue
        argv.append(arg)
    # No second `-q`. The gate's argv already carries one, and pytest counts
    # them: `-q -q` is `-qq`, at which `--collect-only` prints no node ids at
    # all. That is how this returned an EMPTY corpus over a tree with tests in
    # it, which the empty-corpus refusal below caught rather than recording a
    # green night over nothing.
    argv += ["--collect-only", "-p", "no:randomly", "-p", "no:cacheprovider"]
    result = subprocess.run(argv, cwd=str(root), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"pytest collection exited {result.returncode}:\n{result.stderr.strip()[-2000:]}")
    files = set()
    for line in result.stdout.splitlines():
        node = line.strip()
        if "::" not in node:
            continue
        candidate = node.split("::", 1)[0]
        if candidate.endswith(".py"):
            files.add(candidate)
    return sorted(files)


# ============================================================
# The night
# ============================================================

def run(root: Path, *, dry_run: bool = False) -> int:
    load_env(root)  # .env holds HEADING_OS_TZ and the *_TELEGRAM_TARGET names
    started = _now()

    revision = _head_revision(root)
    if revision is None:
        alarm(root, "could not resolve HEAD",
              f"git rev-parse HEAD failed in {root}. Nothing was run and"
              " nothing was recorded.")
        return 1

    gate = [sys.executable, str(root / "scripts" / "run-tests.py")]
    day_mode = [sys.executable, str(root / "scripts" / "day-mode.py"),
                "mark-green", revision]
    verdicts = [sys.executable, str(root / "scripts" / "test-cache.py"),
                "record", "--base", revision, "--from", "-"]

    if dry_run:
        _log(f"root:      {root}")
        _log(f"revision:  {revision}")
        _log(f"record:    {root / RECORD_REL}")
        _log(f"sinks:     {len(own_targets())} operator sink(s) configured")
        for label, argv in (("suite", gate), ("mark-green", day_mode),
                            ("verdicts", verdicts)):
            _log(f"{label + ':':<10} {' '.join(argv)}")
        _log("warm:      scripts.utils.day_mode.build_index(use_cache=True)")
        return 0

    try:
        corpus = collect_corpus(root)
    except (RuntimeError, OSError) as exc:
        alarm(root, "test collection failed, the suite was NOT run", str(exc))
        _write_record(root, {"status": "collect_failed", "started": started,
                             "finished": _now(), "revision": revision,
                             "detail": str(exc)[:2000]})
        return 1
    if not corpus:
        alarm(root, "test collection returned NO files, the suite was NOT run",
              "An empty corpus is a collection failure, not a clean tree. Nothing"
              " was marked green and no verdict was recorded.")
        _write_record(root, {"status": "collect_empty", "started": started,
                             "finished": _now(), "revision": revision})
        return 1
    _log(f"nightly-refresh: {len(corpus)} test file(s) collected at {revision[:12]}")

    # ---- 1. the full suite, unchanged --------------------------------------
    _log(f"$ {' '.join(gate)}")
    gate_rc = subprocess.run(gate, cwd=str(root), check=False).returncode

    if gate_rc != 0:
        # 4. FAILURE. No marker, no verdicts, no warm, and nothing revoked. The
        # previous green marker stays exactly where it was: revoking it here
        # would silently widen every later day-mode selection with no record of
        # why, and leaving it is what makes the stalled marker visible.
        alarm(root, f"the full suite FAILED (pytest exit {gate_rc})",
              f"revision: {revision}\n"
              f"root:     {root}\n"
              "No known-green marker was moved and no verdict was recorded, so"
              " day mode still selects against the previous green revision."
              " Nothing was revoked.\n"
              "Reproduce: python scripts/run-tests.py")
        _write_record(root, {"status": "suite_failed", "started": started,
                             "finished": _now(), "revision": revision,
                             "gate_exit": gate_rc, "collected": len(corpus)})
        return gate_rc

    # ---- 2. ONLY on success: move the base, record the verdicts ------------
    failures: list[str] = []

    # stdin=DEVNULL, not inherited: under a systemd timer stdin is already null,
    # but under any other launcher an inherited pipe leaves a child that reads
    # stdin blocking forever, and a nightly that hangs is a nightly that never
    # reports.
    _log(f"$ {' '.join(day_mode)}")
    if subprocess.run(day_mode, cwd=str(root), check=False,
                      stdin=subprocess.DEVNULL).returncode != 0:
        failures.append("day-mode.py mark-green failed: the green marker did NOT"
                        " advance, so day mode keeps selecting against the older"
                        " revision.")

    _log(f"$ {' '.join(verdicts)} ({len(corpus)} files on stdin)")
    recorded = subprocess.run(verdicts, cwd=str(root), check=False,
                              input="\n".join(corpus), text=True)
    if recorded.returncode != 0:
        failures.append("test-cache.py record failed: no verdicts were stored"
                        " for this revision.")

    # ---- 3. ONLY on success: warm what a cold morning would pay for --------
    # The day-mode fact cache is the one derived artifact in the speed inventory
    # that is cheap to rebuild, has no automatic refresh, and whose reader
    # self-heals only by paying the full cold parse (measured 6.6s cold against
    # 0.31s warm). The memory indexes have their own timer and the CodeGraph
    # index has its own watcher; neither is rebuilt here.
    try:
        index = build_index(root, use_cache=True)
        _log(f"nightly-refresh: day-mode fact cache warm "
             f"({len(index.test_files)} test files indexed)")
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        failures.append(f"warming the day-mode fact cache failed: {exc}")

    finished = _now()
    if failures:
        # The suite passed but the night did not finish its job. Loud, because
        # the visible symptom tomorrow is a marker that did not move, which is
        # indistinguishable from a nightly that never fired.
        alarm(root, "the suite PASSED but the post-run steps did not complete",
              f"revision: {revision}\n" + "\n".join(f"- {f}" for f in failures))
        _write_record(root, {"status": "partial", "started": started,
                             "finished": finished, "revision": revision,
                             "gate_exit": 0, "collected": len(corpus),
                             "failures": failures})
        return 1

    _write_record(root, {"status": "green", "started": started,
                         "finished": finished, "revision": revision,
                         "gate_exit": 0, "collected": len(corpus),
                         "failures": []})
    _log(f"{GREEN}nightly-refresh: green at {revision[:12]}; "
         f"{len(corpus)} test files recorded, caches warm.{RESET}")
    return 0


def status(root: Path) -> int:
    """What the last run did, and when. The read half of "did the night fire?".

    Exit 0 only when the last recorded run was green. `--status` answers about
    the RECORD, not about the timer: a timer that was never installed and a
    timer that was uninstalled both leave the same absent record, and
    `scripts/install-nightly-refresh-timer.sh --check` is what tells those apart.
    """
    record = _read_record(root)
    if record is None:
        _log(f"{RED}nightly-refresh: no run record at {root / RECORD_REL}.{RESET}")
        _log("  This night has never completed here, or the record is unreadable.")
        _log("  Whether the timer is armed is a different question:")
        _log("    bash scripts/install-nightly-refresh-timer.sh --check")
        return 2
    state = record.get("status", "unknown")
    _log(f"status:    {state}")
    _log(f"revision:  {record.get('revision', '?')}")
    _log(f"started:   {record.get('started', '?')}")
    _log(f"finished:  {record.get('finished', '?')}")
    _log(f"collected: {record.get('collected', '?')} test file(s)")
    for failure in record.get("failures") or []:
        _log(f"  {YELLOW}{failure}{RESET}")
    if state == "green":
        return 0
    _log(f"{RED}The last night did not finish green.{RESET}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nightly-refresh.py",
        description="Run the full suite, then move the day-mode base and warm the caches.")
    parser.add_argument("--root", help="repository root (default: this checkout)")
    parser.add_argument("--status", action="store_true",
                        help="print what the last run did, and exit non-zero unless it was green")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the resolved paths and commands without running anything")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else DEFAULT_ROOT
    if args.status:
        return status(root)
    return run(root, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
