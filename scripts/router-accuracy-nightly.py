#!/usr/bin/env python3
"""Nightly router-accuracy trend runner (F-6.2).

Runs ``skill-trigger-test.py --all --json`` once, writes a dated raw artifact and
appends one compact record to ``trend.jsonl`` under the DATA overlay's datastore
(``get_datastore_dir()/operations/router-accuracy/``). The drop-flag is NOT sent
here - the ops-radar ``router_accuracy`` signal producer reads the trend and
surfaces a Tier-B alert on its own cadence, so the flag rides the existing
ops-radar -> Telegram path with no new channel.

Sensitivity-aware (mirrors ``eval-drift-daemon``): refuses to run when the session
is sensitive (``is_sensitive()`` - the fail-closed ``SENSITIVE_MODE`` default) since
the LLM-judge traffic traverses Anthropic. Fails closed on a demo/unmigrated data
overlay via ``require_writable_data_root()``.

Usage:
  python scripts/router-accuracy-nightly.py            # run the harness + persist
  python scripts/router-accuracy-nightly.py --dry-run  # print resolved paths, no run
  python scripts/router-accuracy-nightly.py --model haiku
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, GREEN, RED, RESET, YELLOW
from scripts.utils.content_denylist import build_denylist
from scripts.utils.egress_proof import EGRESS_CLEAR, egress_state
from scripts.utils.router_payload import dirty_sources, outbound_texts
from scripts.utils.sensitive import is_sensitive, sensitivity_is_declared
from scripts.utils.workspace import (
    get_data_root,
    get_datastore_dir,
    get_default_tz,
    get_workspace_root,
    load_env,
    require_writable_data_root,
)

SUBDIR = ("operations", "router-accuracy")


def out_dir() -> Path:
    """Resolve the DATA-overlay directory holding the dated artifacts + trend.jsonl."""
    return get_datastore_dir().joinpath(*SUBDIR)


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def compact_record(date_str: str, payload: dict) -> dict:
    """Build the one-line trend record from a skill-trigger-test --json payload.

    Per-skill rate is not emitted by the harness; we divide passed/cases here and
    skip skipped skills. Totals are kept for forensic completeness.
    """
    per_skill = {}
    for r in payload.get("skills", []):
        if r.get("skipped"):
            continue
        cases = r.get("cases", 0)
        if cases:
            per_skill[r["skill"]] = round(r.get("passed", 0) / cases, 4)
    return {
        "date": date_str,
        # Explicit, because refusals now share this file. A reader must never have
        # to infer "this one is a measurement" from which keys happen to be present.
        "status": "ok",
        "overall_rate": payload.get("overall_rate"),
        "total_passed": payload.get("total_passed"),
        "total_cases": payload.get("total_cases"),
        "per_skill": per_skill,
    }


def _record_refusal(reason: str) -> None:
    """Append a typed refusal to the trend, best-effort.

    The whole reason this slice exists is that a scheduled job which refuses in
    silence is indistinguishable, from every surface, from a night that never
    came. The sibling daemon proved it: 74 days of a WARNING in a journal nobody
    reads, while the heartbeat stayed fresh and every health check said healthy.
    A refusal that writes nothing would move that bug rather than fix it.

    Best-effort because the record is telemetry ABOUT the refusal, and losing it
    must not turn a clean refusal into a crash.
    """
    try:
        target = out_dir()
        target.mkdir(parents=True, exist_ok=True)
        record = {
            "date": datetime.now(get_default_tz()).strftime("%Y-%m-%d"),
            "status": "refused",
            "reason": reason,
        }
        with (target / "trend.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"{RED}router-accuracy: the refusal was not recorded ({exc}){RESET}",
              file=sys.stderr)


def _run_harness(model: str) -> int:
    """Run the judge harness as a subprocess and persist its result.

    Split out of `run` so the egress decision above it can be tested without an
    API key, and so a test that says "the harness did not run" is asserting on
    the real call site rather than on a flag.
    """
    # The operator's timezone lives in the gitignored .env, and `get_default_tz()`
    # reads os.environ ONLY -- it never loads that file. Without it the dated
    # artifact and the trend record are stamped UTC while the timer fires on local
    # time, so a 03:00 local run lands under YESTERDAY's date. Measured on the
    # first live run: the timer fired 2026-08-03 03:00:02 and the record read
    # 2026-08-02.
    #
    # Kept here as well as in `run` deliberately. It is idempotent (`setdefault`),
    # `run` is not the only caller a future edit may give this function, and the
    # cost of a second call is one stat of a file that is already in page cache.
    # The version that lived ONLY here is what left refusal records dated in UTC.
    load_env()

    require_writable_data_root()

    target = out_dir()
    target.mkdir(parents=True, exist_ok=True)

    root = get_workspace_root()
    cmd = [sys.executable, str(root / "scripts" / "skill-trigger-test.py"), "--all", "--json"]
    if model:
        cmd += ["--model", model]
    # No env= override: X31C_TRACE_ID and the Anthropic key are inherited.
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"{RED}skill-trigger-test failed (exit {proc.returncode}):{RESET}\n"
              f"{proc.stderr[-800:]}", file=sys.stderr)
        return 1

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        print(f"{RED}could not parse harness JSON:{RESET} {e}\n{proc.stdout[-400:]}",
              file=sys.stderr)
        return 1

    date_str = datetime.now(get_default_tz()).strftime("%Y-%m-%d")
    _atomic_write_json(target / f"{date_str}.json", payload)

    record = compact_record(date_str, payload)
    with (target / "trend.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    print(f"{GREEN}router-accuracy:{RESET} {date_str} overall={record['overall_rate']} "
          f"({record['total_passed']}/{record['total_cases']}); "
          f"{len(record['per_skill'])} skills -> {target}")
    return 0


def run(model: str) -> int:
    """Decide whether this run has earned egress, then run or refuse.

    The blanket `is_sensitive()` skip this replaces was never wrong about the
    risk; it was wrong about THIS payload. Every byte the judge receives is
    tracked engine content in a public repository, so the flag was refusing to
    leak what is already published, and it refused every night since the runner
    was written. The proof answers the narrower question per payload.

    A DECLARED sensitivity still wins outright. Unset is the machine's default and
    the proof may govern it; a person who typed the variable knows something no
    denylist can, and a machine proof must not overrule them.

    Every exit here is 0. A refusal is not a failure, and a nightly unit that
    reports failed is a nightly unit the operator learns to ignore.
    """
    # Before anything below can read the clock. The previous slice put this
    # inside `_run_harness` so its frozen contract tests stayed hermetic, and
    # that left a hole its own subject was about: a REFUSAL never reaches
    # `_run_harness`, so `_record_refusal` stamped its date with .env unloaded --
    # UTC, while the unit fires on local time. Found by the timer-timezone
    # contract's order walk, which reported the exact path
    # `run -> _record_refusal -> get_default_tz`. That contract has since been
    # retired into the ordinary suite, so the hermeticity argument no longer
    # applies; the tests that drive a refusal patch this call directly.
    load_env()

    if sensitivity_is_declared():
        reason = ("sensitivity was declared for this session, which outranks any "
                  "payload proof")
        print(f"{YELLOW}router-accuracy: not running - {reason}.{RESET}")
        _record_refusal(reason)
        return 0

    state, reason = egress_state(
        "\n".join(outbound_texts()),
        build_denylist(get_data_root()),
        dirty_sources(),
    )
    if state != EGRESS_CLEAR:
        print(f"{YELLOW}router-accuracy: not running - {reason}.{RESET}")
        _record_refusal(reason)
        return 0

    return _run_harness(model)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Nightly router-accuracy trend runner (F-6.2).")
    parser.add_argument("--model", default="sonnet",
                        help="Judge model passed to skill-trigger-test.py (default sonnet)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print resolved paths and exit without running the harness")
    args = parser.parse_args(argv)

    if args.dry_run:
        target = out_dir()
        print(f"{BOLD}router-accuracy paths:{RESET}")
        print(f"  dated artifact: {target}/<YYYY-MM-DD>.json")
        print(f"  trend:          {target}/trend.jsonl")
        print(f"  sensitive now:  {is_sensitive()}")
        return 0

    return run(args.model)


if __name__ == "__main__":
    sys.exit(main())
