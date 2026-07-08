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
from scripts.utils.sensitive import is_sensitive
from scripts.utils.workspace import (
    get_datastore_dir,
    get_default_tz,
    get_workspace_root,
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
        "overall_rate": payload.get("overall_rate"),
        "total_passed": payload.get("total_passed"),
        "total_cases": payload.get("total_cases"),
        "per_skill": per_skill,
    }


def run(model: str) -> int:
    if is_sensitive():
        print(f"{YELLOW}SENSITIVE_MODE active - skipping router-accuracy run "
              f"(judge traffic traverses Anthropic).{RESET}")
        return 0

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
