#!/usr/bin/env python3
"""wizard-simulate.py -- dev harness to replay canned wizard answers.

canned.yaml format:
    answers:
      company_short_name: "Acme"
      ceo_voice:
        value: "Short sentences."
        draft: "Expanded voice brief..."
        draft_approved: true
      core_values: ["Trust", "Speed"]
    skipped: [calendar_policy]

Refuses to run against workspaces tagged type: "ceo-master" - no override.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args(argv)

    # Safety: refuse to run against the CEO master workspace.
    # The `--force-ceo-master` flag below bypasses apply-script detection, but
    # we do NOT want this dev harness to ever touch a real ceo-master workspace
    # by accident. No override flag is offered - if you need to test against a
    # ceo-master identity, copy the identity file into a fixture tmpdir.
    identity = args.workspace / ".workspace-identity.json"
    if identity.exists():
        try:
            data = json.loads(identity.read_text(encoding="utf-8"))
            if data.get("type") == "ceo-master":
                print(
                    f"REFUSED: --workspace {args.workspace} is a CEO master workspace. "
                    f"This harness never runs against ceo-master. Copy to a tmpdir first.",
                    file=sys.stderr,
                )
                return 2
        except json.JSONDecodeError:
            print(f"ERROR: malformed .workspace-identity.json in {args.workspace}",
                  file=sys.stderr)
            return 2

    canned = yaml.safe_load(args.answers.read_text(encoding="utf-8")) or {}
    # Resolve apply-wizard-answers.py relative to this harness's location.
    # If the harness is ever moved out of scripts/dev/, fail fast with a clear error.
    apply_script = Path(__file__).resolve().parent.parent / "apply-wizard-answers.py"
    if not apply_script.exists():
        print(f"ERROR: apply script not found at {apply_script}. "
              f"This harness assumes it lives at scripts/dev/wizard-simulate.py.",
              file=sys.stderr)
        return 2

    for qid, value in (canned.get("answers") or {}).items():
        if isinstance(value, dict):
            payload = value
        else:
            payload = {"value": value}
        result = subprocess.run(
            [sys.executable, str(apply_script),
             "--question", qid, "--value-from-stdin", "--force-ceo-master"],
            cwd=args.workspace, input=json.dumps(payload),
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"FAILED on {qid}: {result.stderr}", file=sys.stderr)
            return result.returncode
        print(f"OK  {qid}: {result.stdout.strip()}")

    for qid in (canned.get("skipped") or []):
        result = subprocess.run(
            [sys.executable, str(apply_script), "--skip", qid, "--force-ceo-master"],
            cwd=args.workspace, capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"FAILED on skip {qid}: {result.stderr}", file=sys.stderr)
            return result.returncode
        print(f"SKIP {qid}")

    status = subprocess.run(
        [sys.executable, str(apply_script), "--status", "--force-ceo-master"],
        cwd=args.workspace, capture_output=True, text=True,
    )
    print(f"STATUS: {status.stdout}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
