#!/usr/bin/env python3
"""Odin cadence notify -- timer entrypoint that pushes the cadence nudge to Telegram.

Thin orchestrator (no LLM, no brain write). Runs the read-only cadence checker;
on a genuine nudge it sends the one-line, COUNTS-ONLY suggestion to the CEO's
Telegram alert channel ("Urgent Stuff for M" by default; override with
ODIN_CADENCE_TELEGRAM_TARGET) via the dedicated notifications bot
(scripts/utils/telegram_notify.py). When up to date it
sends nothing. A transient send failure is logged and SWALLOWED (exit 0) so the
oneshot systemd unit is never left in `failed` state -- the next `/prime` surfaces
the same signal as a backstop (plan Decision 9).

It runs `odin-cadence.py --quiet`, whose stdout IS the canonical suggestion line
(empty when up to date). Reusing that line verbatim -- rather than rebuilding it
here from `--json` -- guarantees the Telegram text can never drift from the line
the CEO sees at `/prime`, and keeps the counts-only contract in one place.

Two surfaces share this entrypoint:
  - Normal mode (odin-cadence.service, retired timer / manual): send the counts
    line, folding in a proposal path when the propose flow produces one.
  - `--propose-only` mode (odin-propose.service, the live weekly surface): skip
    the counts subprocess and the counts send entirely (ops-radar already
    surfaces the counts daily) and deliver ONLY a real outcome -- a standalone
    proposal-path message when a proposal is produced, or the CRITICAL
    integrity alert -- else nothing. `--min-entries` is inert in this mode
    (cluster presence is min_entries-independent).

Invoked by scripts/templates/systemd/odin-propose.service (weekly timer). Also
runnable by hand:
    python3 scripts/odin-cadence-notify.py                 # counts nudge if due
    python3 scripts/odin-cadence-notify.py --min-entries 1 # force-test counts delivery
    python3 scripts/odin-cadence-notify.py --propose-only  # propose surface (silent unless a proposal is produced)
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Workspace import bootstrap (per development-standards.md)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_data_root, get_default_tz, get_workspace_root  # noqa: E402
from scripts.utils.paths import load_env  # noqa: E402
from scripts.utils import telegram_notify  # noqa: E402

CADENCE_SCRIPT = "scripts/odin-cadence.py"
HEADING_CLI_SCRIPT = "scripts/heading_cli.py"
PROPOSE_HEADLESS_TIMEOUT = 600  # seconds; mirrors dream-shadow's nightly-path allowance

# Where the weekly nudge lands. The recipient is read from the gitignored engine
# .env (ODIN_CADENCE_TELEGRAM_TARGET) so no personal channel id lives in this
# engine-routed (eventually-public) file. The CEO routes it to his "Urgent Stuff
# for M" alert channel via that .env value. The in-code fallback is "" --
# unconfigured, never a send attempt (Telegram's own-account "me"/Saved
# Messages sentinel is not a valid fallback anywhere in this system).
DEFAULT_RECIPIENT = ""


def _log(msg: str) -> None:
    print(f"[odin-cadence-notify] {msg}", file=sys.stderr)


def _load_cadence_module(cadence_path: Path):
    """In-process import of the hyphenated odin-cadence.py module -- the same
    load pattern tests/test_odin_cadence.py already uses. A separate function
    (rather than inlining importlib calls) so tests can monkeypatch this one
    seam and hand back a fake module with a `compute` of their choosing."""
    spec = importlib.util.spec_from_file_location("odin_cadence_notify_cadence", cadence_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _brain_snapshot(brain_dir: Path) -> dict:
    """(relative_path -> (size, mtime_ns)) for every file under brain_dir.

    Cheap os.stat-based snapshot, no hashing -- this workspace's brain is a
    handful of markdown files, and mtime+size already catches any add, remove,
    or edit. Returns {} if the directory doesn't exist (never raises)."""
    snap: dict[str, tuple[int, int]] = {}
    if not brain_dir.is_dir():
        return snap
    for p in sorted(brain_dir.rglob("*")):
        try:
            if not p.is_file():
                continue
            st = p.stat()
        except OSError:
            # A file removed between rglob and stat raised FileNotFoundError out
            # of the very function annotated "(never raises)" -- and out of the
            # caller annotated "NEVER raises" -- crashing the integrity check.
            continue
        snap[str(p.relative_to(brain_dir))] = (st.st_size, st.st_mtime_ns)
    return snap


def _run_headless_propose(root: Path) -> Optional[Path]:
    """Run the headless `odin reflect --propose` flow, returning the proposal Path.

    If ODIN_REFLECT_PROPOSE_ENABLED is truthy and a fresh in-process cadence
    compute finds a non-empty cluster_detail, run the headless
    `odin reflect --propose` call and return the deterministic proposal-file
    Path. Returns None on any non-delivery outcome: flag unset, no cluster,
    compute/subprocess failure, integrity failure, non-zero exit, or a missing
    proposal file. `cluster_detail` is min_entries-independent
    (analyze_reflect_clusters does not use it), so no min_entries is threaded.

    Integrity-check backstop (council-confirmed): this does NOT trust Claude
    Code's own --allowedTools/--disallowedTools enforcement alone. It snapshots
    knowledge/odin-brain/ immediately before and after the headless call, in
    plain Python, independent of whether the vendor CLI's permission layer
    worked -- mirroring tiered-risk.md's "the ledger is data, the send-gate is
    code" pattern. ANY detected change is a CRITICAL integrity failure: logged
    loudly, escalated to the CEO over Telegram, and the proposal path is
    withheld (return None). NEVER raises.
    """
    if not os.environ.get("ODIN_REFLECT_PROPOSE_ENABLED"):
        return None

    cadence_path = root / CADENCE_SCRIPT
    try:
        oc = _load_cadence_module(cadence_path)
        result = oc.compute(get_data_root(), oc.DEFAULT_MIN_ENTRIES)
    except Exception as exc:  # noqa: BLE001 - boundary; a missed propose run is non-critical
        _log(f"in-process cadence compute failed ({type(exc).__name__}: {exc}); skipping propose")
        return None

    cluster_detail = result.get("cluster_detail") or []
    if not cluster_detail:
        return None

    try:
        from scripts.heading_cli import PROPOSE_DEFAULT_BUDGET_USD
    except ImportError as exc:
        # This function's docstring says NEVER raises, and the module is built
        # so a failure leaves the unit un-failed and exits 0. A bare import here
        # broke both, and because `_maybe_headless_propose` runs BEFORE the
        # Telegram send, it also killed the counts nudge on the way out.
        _log(f"propose skipped: {exc}")
        return None

    brain_dir = get_data_root() / "knowledge" / "odin-brain"
    brain_before = _brain_snapshot(brain_dir)

    cli = root / HEADING_CLI_SCRIPT
    cmd = [
        sys.executable, str(cli), "skill",
        "--budget", str(PROPOSE_DEFAULT_BUDGET_USD),
        "odin", "reflect", "--propose",
    ]
    started = time.time()
    proc = None
    try:
        proc = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True,
            timeout=PROPOSE_HEADLESS_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 - boundary; a missed propose run is non-critical
        # `return None` stood here, and it skipped the integrity check below on
        # the ONE path where the child ran unsupervised. TimeoutExpired fires
        # after up to PROPOSE_HEADLESS_TIMEOUT (600s) of a hung or looping
        # headless agent, which is precisely the run whose brain writes the
        # backstop exists to catch. Measured 2026-08-29 against a stub that
        # wrote knowledge/odin-brain/ and then blew the timeout: the file was
        # left reading "TAMPERED", nothing logged CRITICAL, and no Telegram
        # escalation was sent. The before-snapshot was already in hand. Fall
        # through instead, so the after-snapshot is taken whenever the before
        # one was, and let the `proc is None` guard below stop the run.
        _log(f"headless propose call failed to run ({type(exc).__name__}: {exc}); "
             f"checking brain integrity before skipping")

    brain_after = _brain_snapshot(brain_dir)
    if brain_before != brain_after:
        _log(
            "CRITICAL: knowledge/odin-brain/ changed during a headless "
            "odin reflect --propose run -- integrity check failed; withholding "
            "the proposal path"
        )
        # "Logged loudly" (F-10.3 Decision 9) must reach the CEO off-machine,
        # not just journald: an unauthorized brain write in a mode that never
        # writes the brain is exactly the "must reach the CEO" class this
        # workspace routes through Telegram. Escalate on the same recipient
        # main() resolves; notify() never raises, so a send failure cannot
        # turn the integrity failure into a crash.
        recipient = os.environ.get("ODIN_CADENCE_TELEGRAM_TARGET", DEFAULT_RECIPIENT)
        delivered = telegram_notify.notify(
            recipient,
            "CRITICAL: knowledge/odin-brain/ changed during a headless "
            "odin reflect --propose run -- integrity check failed. The proposal "
            "was withheld; inspect the brain and the run immediately.",
        )
        if not delivered:
            # The escalation's whole point is reaching the CEO OFF the machine
            # whose integrity is in question, and it can silently fail to: on an
            # unconfigured workspace `recipient` is DEFAULT_RECIPIENT (""), which
            # telegram_notify documents as never a send attempt, so notify()
            # logs under ITS logger and returns False. Nothing under this
            # script's own journald prefix said the most serious message it can
            # produce went nowhere. Say it here, where an operator reading the
            # unit's log will see it beside the CRITICAL line above.
            _log(f"CRITICAL escalation was NOT delivered to Telegram "
                 f"(target={recipient!r}); this alert exists only in this log")
        return None

    if proc is None:
        # The subprocess never completed (the exception path above). The brain
        # is verified clean by this point, which is the only thing that path
        # could still establish; there is no proposal to look for.
        return None

    if proc.returncode != 0:
        _log(f"headless propose call exited {proc.returncode}: {proc.stderr.strip()[:200]}")
        return None

    # The proposal filename is fully deterministic (mode-catalog.md's --propose
    # sub-flow spec) -- constructing it directly is more robust than parsing an
    # LLM's free-form stdout/JSON envelope for the path.
    proposals_dir = get_data_root() / "outputs" / "operations" / "odin-reflect-proposals"
    today = datetime.now(get_default_tz()).date()
    proposal_path = proposals_dir / f"{today.isoformat()}_odin-reflect-proposal.md"
    if proposal_path.exists():
        return proposal_path

    # Same-run fallback (scrutiny M1): the date that NAMES the file is the
    # headless run's session-date (libc TZ), while `today` above is a Python
    # reconstruction via get_default_tz() (HEADING_OS_TZ). Those two bases can
    # diverge -- HEADING_OS_TZ edited without re-running the installer, or the
    # up-to-600s call crossing local midnight -- and the exact-date path then
    # misses a file that was really written. Recover it directly: the newest
    # proposal file touched since THIS call started is, by construction, the one
    # this run produced (single weekly timer, no concurrent writer). The 2s
    # grace absorbs coarse mtime resolution on 9P/FAT mounts.
    #
    # Each candidate is stat'd ONCE, inside a guard. The old form called
    # `p.stat()` twice per file -- in the filter and again in the sort key --
    # both unguarded, so a proposal removed between the glob and either call
    # raised FileNotFoundError out of a function whose contract above says
    # "NEVER raises", out of `main`, and out of the oneshot unit. In normal mode
    # that also threw away the counts nudge, which had already been computed and
    # had nothing to do with proposals. The identical race was fixed in
    # `_brain_snapshot` in this same file; this block had been left behind.
    fresh = []
    for p in proposals_dir.glob("*_odin-reflect-proposal.md"):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime >= started - 2:
            fresh.append((mtime, p))
    if fresh:
        return max(fresh, key=lambda t: t[0])[1]

    _log("headless propose call succeeded but no proposal file found (exact-date or fresh)")
    return None


def _maybe_headless_propose(root: Path, line: str) -> str:
    """Normal-path wrapper: fold the propose outcome into the counts `line`.

    Behavior-preserving over the pre-refactor version -- when a proposal is
    produced, append its (absolute) path to the counts line exactly as before;
    otherwise return the line unchanged. The `--propose-only` surface calls
    `_run_headless_propose` directly instead of this wrapper.
    """
    p = _run_headless_propose(root)
    return f"{line} Proposal: {p}" if p else line


def main() -> int:
    ap = argparse.ArgumentParser(description="Push the Odin cadence nudge to Telegram on a nudge.")
    ap.add_argument("--min-entries", type=int, default=None,
                    help="override the un-harvested threshold (for dry-run testing)")
    ap.add_argument("--propose-only", action="store_true",
                    help="run ONLY the headless propose flow (odin-propose.timer): "
                         "skip the counts nudge; deliver a standalone proposal-path "
                         "message only when a proposal is produced")
    args = ap.parse_args()

    root = get_workspace_root()
    load_env(root)  # make .env (ODIN_REFLECT_PROPOSE_ENABLED / target) visible under systemd too

    if args.propose_only:
        # Propose-only surface (odin-propose.timer). Placed immediately after
        # load_env so the propose gate + recipient are read from .env under
        # systemd, and deliberately NOT behind the counts `cadence.exists()`
        # guard (_run_headless_propose loads odin-cadence itself and degrades on
        # absence). Skip the counts subprocess + counts send entirely -- ops-radar
        # already surfaces the counts daily. Deliver ONLY a real outcome: the
        # proposal path (relative, phone-readable), or nothing. Any CRITICAL
        # integrity alert is sent inside _run_headless_propose.
        p = _run_headless_propose(root)
        if p is None:
            _log("propose-only: no proposal this run")
            return 0
        recipient = os.environ.get("ODIN_CADENCE_TELEGRAM_TARGET", DEFAULT_RECIPIENT)
        try:
            rel = p.relative_to(get_data_root())
        except ValueError:
            rel = p
        if telegram_notify.notify(recipient, f"Odin reflect proposal ready: {rel}"):
            _log(f"propose-only: proposal delivered to {recipient}: {rel}")
        else:
            _log("propose-only: proposal not delivered (see telegram_notify log)")
        return 0

    cadence = root / CADENCE_SCRIPT
    if not cadence.exists():
        _log(f"cadence script absent ({cadence}); nothing to do")
        return 0

    cmd = [sys.executable, str(cadence), "--quiet"]
    if args.min_entries is not None:
        cmd += ["--min-entries", str(args.min_entries)]
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=120)
    except Exception as exc:  # noqa: BLE001 - boundary; a missed nudge is non-critical
        _log(f"cadence check failed to run ({type(exc).__name__}: {exc}); exiting 0")
        return 0

    if proc.returncode != 0:
        # A crash -- non-zero exit, traceback on stderr, empty stdout -- was
        # indistinguishable from "nothing due", and the log affirmatively said
        # "up to date". The propose path in this same file already checks the
        # return code; this one did not.
        err = (proc.stderr or "").strip().splitlines()[-1:] or ["no stderr"]
        _log(f"cadence check exited {proc.returncode} ({err[0]}); NOT a nudge verdict")
        return 0

    line = proc.stdout.strip()
    if not line:
        _log("up to date -- no nudge to send")
        return 0

    line = _maybe_headless_propose(root, line)

    # Send the counts-only line to the CEO's alert channel (override-able target).
    recipient = os.environ.get("ODIN_CADENCE_TELEGRAM_TARGET", DEFAULT_RECIPIENT)
    if telegram_notify.notify(recipient, line):
        _log(f"nudge delivered to {recipient}: {line}")
    else:
        _log("nudge not delivered (see telegram_notify log); /prime will backstop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
