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

Invoked by scripts/templates/systemd/odin-cadence.service (weekly timer). Also
runnable by hand for a dry-run:
    python3 scripts/odin-cadence-notify.py            # send only if a nudge is due
    python3 scripts/odin-cadence-notify.py --min-entries 1   # force-test delivery
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

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
        if p.is_file():
            st = p.stat()
            snap[str(p.relative_to(brain_dir))] = (st.st_size, st.st_mtime_ns)
    return snap


def _maybe_headless_propose(root: Path, line: str) -> str:
    """If ODIN_REFLECT_PROPOSE_ENABLED is truthy and a fresh in-process cadence
    compute finds a non-empty cluster_detail, run the headless
    `odin reflect --propose` call and fold its proposal-file path into `line`.

    Integrity-check backstop (pre-impl gate finding, council-confirmed): this
    does NOT trust Claude Code's own --allowedTools/--disallowedTools
    enforcement alone. It snapshots knowledge/odin-brain/ immediately before and
    after the headless call, in plain Python, independent of whether the
    vendor CLI's permission layer worked -- mirroring tiered-risk.md's "the
    ledger is data, the send-gate is code" pattern. ANY detected change is a
    CRITICAL integrity failure: logged loudly, and the proposal path is
    withheld from the Telegram line entirely (send the deterministic-only line,
    same as the up-to-date case). Never raises; always returns a line to send.
    """
    if not os.environ.get("ODIN_REFLECT_PROPOSE_ENABLED"):
        return line

    cadence_path = root / CADENCE_SCRIPT
    try:
        oc = _load_cadence_module(cadence_path)
        result = oc.compute(get_data_root(), oc.DEFAULT_MIN_ENTRIES)
    except Exception as exc:  # noqa: BLE001 - boundary; a missed propose run is non-critical
        _log(f"in-process cadence compute failed ({type(exc).__name__}: {exc}); skipping propose")
        return line

    cluster_detail = result.get("cluster_detail") or []
    if not cluster_detail:
        return line

    from scripts.heading_cli import PROPOSE_DEFAULT_BUDGET_USD  # local: only needed here

    brain_dir = get_data_root() / "knowledge" / "odin-brain"
    brain_before = _brain_snapshot(brain_dir)

    cli = root / HEADING_CLI_SCRIPT
    cmd = [
        sys.executable, str(cli), "skill",
        "--budget", str(PROPOSE_DEFAULT_BUDGET_USD),
        "odin", "reflect", "--propose",
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True,
            timeout=PROPOSE_HEADLESS_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 - boundary; a missed propose run is non-critical
        _log(f"headless propose call failed to run ({type(exc).__name__}: {exc}); skipping")
        return line

    brain_after = _brain_snapshot(brain_dir)
    if brain_before != brain_after:
        _log(
            "CRITICAL: knowledge/odin-brain/ changed during a headless "
            "odin reflect --propose run -- integrity check failed; withholding "
            "the proposal path from this week's Telegram line"
        )
        # "Logged loudly" (F-10.3 Decision 9) must reach the CEO off-machine,
        # not just journald: an unauthorized brain write in a mode that never
        # writes the brain is exactly the "must reach the CEO" class this
        # workspace routes through Telegram. Escalate on the same recipient
        # main() resolves; notify() never raises, so a send failure cannot
        # turn the integrity failure into a crash.
        recipient = os.environ.get("ODIN_CADENCE_TELEGRAM_TARGET", DEFAULT_RECIPIENT)
        telegram_notify.notify(
            recipient,
            "CRITICAL: knowledge/odin-brain/ changed during a headless "
            "odin reflect --propose run -- integrity check failed. The proposal "
            "was withheld; inspect the brain and the run immediately.",
        )
        return line

    if proc.returncode != 0:
        _log(f"headless propose call exited {proc.returncode}: {proc.stderr.strip()[:200]}")
        return line

    # The proposal filename is fully deterministic (mode-catalog.md's --propose
    # sub-flow spec) -- constructing it directly is more robust than parsing an
    # LLM's free-form stdout/JSON envelope for the path.
    today = datetime.now(get_default_tz()).date()
    proposal_path = (
        get_data_root() / "outputs" / "operations" / "odin-reflect-proposals"
        / f"{today.isoformat()}_odin-reflect-proposal.md"
    )
    if not proposal_path.exists():
        _log("headless propose call succeeded but no proposal file found at the expected path")
        return line

    return f"{line} Proposal: {proposal_path}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Push the Odin cadence nudge to Telegram on a nudge.")
    ap.add_argument("--min-entries", type=int, default=None,
                    help="override the un-harvested threshold (for dry-run testing)")
    args = ap.parse_args()

    root = get_workspace_root()
    load_env(root)  # make .env (ODIN_CADENCE_TELEGRAM_TARGET) visible under systemd too
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
