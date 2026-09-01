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


def _interpreter() -> str:
    """The interpreter ruff is run as a module of.

    The repo's own `.venv` first, because the pre-commit hook that calls this
    script is `language: system` and pre-commit resolves `python3` from PATH.
    On a machine where that PATH interpreter is not the venv, reading
    `sys.executable` alone put the whole gate under an interpreter that had
    never heard of ruff, and the answer came back "0 findings".
    """
    root = get_workspace_root()
    for candidate in (root / ".venv" / "bin" / "python",
                      root / ".venv" / "Scripts" / "python.exe"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _current() -> Counter:
    """Counter keyed ``relpath::CODE`` of current ruff findings (pyproject select)."""
    root = get_workspace_root()
    interpreter = _interpreter()
    r = subprocess.run(  # nosec B603 - fixed args; ruff run as a module of this interpreter
        [interpreter, "-m", "ruff", "check", ".", "--output-format", "json"],
        cwd=str(root), capture_output=True, text=True,
    )
    # ruff exits 0 (clean) or 1 (findings); >=2 is a real tool error.
    if r.returncode >= 2:
        sys.stderr.write(r.stderr)
        raise SystemExit(2)
    # "ruff found nothing" and "ruff never ran" both arrive as an empty result,
    # and treating them as one answer is how a gate reports the reassuring half
    # of the truth. A run that produced findings prints a JSON array; a missing
    # module exits 1 with nothing at all. The empty-with-failure case is the one
    # that must never be read as a clean tree, because `update` would then write
    # an empty baseline and disarm the ratchet for good.
    if r.returncode != 0 and not r.stdout.strip():
        raise SystemExit(
            f"lint-ratchet: ruff did not run under {interpreter}"
            f"{': ' + r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ''}\n"
            "Refusing to report a clean tree it never measured. Install the dev "
            "toolchain (`uv sync` or `pip install -r requirements-dev.txt`) and retry."
        )
    try:
        items = json.loads(r.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"lint-ratchet: could not parse ruff JSON output: {exc}") from exc
    counts: Counter = Counter()
    for it in items:
        # Anchored to `root`, not to this process's cwd. ruff runs with
        # `cwd=root` and today emits ABSOLUTE paths, so `Path(...).resolve()`
        # happened to be right -- verified by running the gate from /tmp, which
        # is why the audit finding claiming a crash there is refuted. But the
        # correctness rested on a ruff output detail nothing here pins. Joining
        # to `root` first is a no-op for an absolute path and correct for a
        # relative one, so the gate no longer depends on which shape ruff picks.
        rel = (root / it["filename"]).resolve().relative_to(root).as_posix()
        counts[f"{rel}::{it['code']}"] += 1
    return counts


def _load_baseline() -> Counter:
    if not BASELINE.exists():
        return Counter()
    return Counter(json.loads(BASELINE.read_text(encoding="utf-8")))


def _refuse_a_vanished_corpus(cur: Counter, base: Counter) -> None:
    """Zero findings over a tree whose baseline records hundreds is not a clean tree.

    The refusal at the top of `_current()` tells "ruff found nothing" apart from
    "ruff never ran" by looking at the exit status. It cannot tell either of
    those from the third answer: ruff RAN, exited 0, printed `[]`, and inspected
    nothing: an `exclude` widened in pyproject.toml, an `extend-exclude` typo,
    a `.gitignore`-driven walk that stopped finding files. MEASURED 2026-09-01
    against the committed 143-bucket baseline, with ruff stubbed to return
    exit 0 and `[]`: `check` printed "OK - 0 findings, at or below baseline (245
    fewer; run `update` to tighten the ratchet)" and returned 0.

    The destructive half is the same one the file's header describes: `update`
    would then write `{}` and forgive every recorded finding for good. The door
    is different; the room behind it is identical, so the refusal sits where
    both commands pass through.

    Not a percentage and not a floor on the count: only the collapse to exactly
    zero, which is the shape a vanished corpus produces and which a day of real
    fixes does not reach from 245 in one step. A tree that genuinely has no
    findings left is recorded by deleting `.lint-baseline.json` deliberately.
    """
    if cur or not base:
        return
    raise SystemExit(
        f"lint-ratchet: ruff ran and reported ZERO findings over a tree whose "
        f"baseline records {sum(base.values())} across {len(base)} (file, rule) "
        f"buckets.\nThat is a corpus that vanished, not lint that was fixed: "
        f"check `exclude` / `extend-exclude` in pyproject.toml and that `ruff "
        f"check .` still walks the tree.\nIf the tree really is clean, delete "
        f".lint-baseline.json deliberately and commit that."
    )


def cmd_update() -> int:
    cur = _current()
    _refuse_a_vanished_corpus(cur, _load_baseline())
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
    _refuse_a_vanished_corpus(cur, base)
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
