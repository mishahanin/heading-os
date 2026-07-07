#!/usr/bin/env python3
"""Full-ruleset lint ratchet (F-3.2): freeze existing lint debt, fail on regressions.

The pre-commit / CI security gate hard-fails on the ruff `S` subset. This ratchet
covers the FULL pyproject ruleset (S, B, A, C4, DTZ, T10, PIE, SIM, ERA) - the
several hundred pre-existing style/correctness findings that cannot all be fixed
at once. It records a per-`(file, rule)` baseline and fails only when a NEW
violation appears or an existing `(file, rule)` count increases. Fixing lint only
ever lowers the numbers, so the debt is a one-way ratchet toward zero.

Workflow:
    python scripts/lint-ratchet.py check     # CI/pre-commit: fail on regression
    python scripts/lint-ratchet.py update     # regenerate baseline AFTER intentional fixes

`check` never rewrites the baseline; after you fix findings, run `update` and
commit the smaller `.lint-baseline.json` so the ratchet tightens.
"""
from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - runs the pinned `ruff` CLI with a fixed arg list
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_workspace_root  # noqa: E402

BASELINE = get_workspace_root() / ".lint-baseline.json"


def _current() -> Counter:
    """Counter keyed ``relpath::CODE`` of current ruff findings (pyproject select)."""
    root = get_workspace_root()
    r = subprocess.run(  # nosec B603 - fixed args; ruff run as a module of this interpreter
        [sys.executable, "-m", "ruff", "check", ".", "--output-format", "json"],
        cwd=str(root), capture_output=True, text=True,
    )
    # ruff exits 0 (clean) or 1 (findings); >=2 is a real tool error.
    if r.returncode >= 2:
        sys.stderr.write(r.stderr)
        raise SystemExit(2)
    try:
        items = json.loads(r.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"lint-ratchet: could not parse ruff JSON output: {exc}") from exc
    counts: Counter = Counter()
    for it in items:
        rel = Path(it["filename"]).resolve().relative_to(root).as_posix()
        counts[f"{rel}::{it['code']}"] += 1
    return counts


def _load_baseline() -> Counter:
    if not BASELINE.exists():
        return Counter()
    return Counter(json.loads(BASELINE.read_text(encoding="utf-8")))


def cmd_update() -> int:
    cur = _current()
    BASELINE.write_text(
        json.dumps(dict(sorted(cur.items())), indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"lint-ratchet: baseline written - {sum(cur.values())} findings "
        f"across {len(cur)} (file, rule) buckets"
    )
    return 0


def cmd_check() -> int:
    cur = _current()
    base = _load_baseline()
    regressions = [
        (key, base.get(key, 0), n) for key, n in cur.items() if n > base.get(key, 0)
    ]
    if regressions:
        sys.stderr.write("lint-ratchet: NEW lint debt blocks the merge:\n")
        for key, was, now in sorted(regressions):
            f, code = key.rsplit("::", 1)
            sys.stderr.write(f"  {f}: {code} {was} -> {now}\n")
        sys.stderr.write(
            "\nFix them (see `ruff check .`). Only after intentional fixes: "
            "`python scripts/lint-ratchet.py update` and commit the baseline.\n"
        )
        return 1
    total = sum(cur.values())
    improved = sum(base.values()) - total
    msg = f"lint-ratchet: OK - {total} findings, at or below baseline"
    if improved > 0:
        msg += f" ({improved} fewer; run `update` to tighten the ratchet)"
    print(msg)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Full-ruleset lint ratchet (F-3.2)")
    ap.add_argument("command", choices=["check", "update"])
    args = ap.parse_args()
    return cmd_update() if args.command == "update" else cmd_check()


if __name__ == "__main__":
    raise SystemExit(main())
