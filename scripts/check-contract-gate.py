#!/usr/bin/env python3
"""Advisory check: did this plan go through the gate and leave a contract?

Used by /implement as a soft, non-blocking reminder. Given a plan path, it
derives the plan slug and looks under `tests/contract/` for a matching
`<date>-<slug>/` directory. It prints one line and ALWAYS exits 0 — it is
advisory and never blocks /implement.

WHAT IT LOOKED FOR BEFORE, and why that had to change. Until 2026-08-07 this
globbed the plans directory for `<date>-pre-impl-<slug>.md`, the artifact
`/pre-impl` wrote. `/pre-impl` was absorbed into `/canopus` on 2026-08-02 and
`/canopus plan` was retired with the freeze lifecycle on 2026-08-07, so NOTHING
could write that filename any more: every run took the MISSING branch and warned
about an artifact that could not exist, which is a reminder the reader learns to
ignore.

The seven-step process leaves two durable traces (see
`scripts/utils/canopus_steps.py`): the approval COMMIT carrying the plan and the
red contract at step 4, and the note under `records/slices/` at step 7. Only the
first exists at the moment /implement runs, and the part of it visible on disk
is the contract directory, so that is what this now reads. It is also the better
signal: the old artifact recorded that a gate had run, while a contract IS the
thing step 4 approves.

Statuses:
    FOUND    a matching contract directory exists (newest date reported; a
             "(stale: N days)" note is appended when older than --stale-days)
    MISSING  a plan path was given but no matching contract exists
    SKIPPED  no plan path, or the path has no decodable slug

Usage:
    python scripts/check-contract-gate.py --plan plans/2026-06-28-foo.md
    python scripts/check-contract-gate.py --plan plans/2026-06-28-foo.md --json

Tests: tests/test_a_guard_that_stopped_one_level_short.py
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_default_tz, get_workspace_root
from scripts.utils.colors import GREEN, YELLOW, GRAY, BOLD, RESET

STALE_DAYS_DEFAULT = 14


def derive_slug(plan_path: str) -> str:
    """Path(plan_path).stem with leading YYYY-MM-DD- stripped if present.

    Canonical source: scripts/implement-trajectory-log.py:derive_slug. Kept
    byte-identical here because that module's hyphenated name cannot be imported
    (`from scripts.implement-trajectory-log import ...` is a syntax error). The
    parity is locked by tests/test_check_contract_gate.py (imported via importlib).
    """
    stem = Path(plan_path).stem
    # Strip YYYY-MM-DD- (10 chars + 1 hyphen = 11 chars) if it matches the pattern
    if len(stem) >= 11 and stem[4] == "-" and stem[7] == "-" and stem[10] == "-":
        date_part = stem[:10]
        if all(c.isdigit() or c == "-" for c in date_part):
            return stem[11:] or "untitled"
    return stem or "untitled"


def _slug_is_the_fallback(plan_path: str) -> bool:
    """True when `derive_slug` returned its "untitled" FALLBACK, not a real slug.

    `derive_slug` collapses "this path has no stem to decode" and "this plan is
    named untitled.md" into the same string, and the gate then read the second
    as the first: a real `plans/2026-06-28-untitled.md` was reported SKIPPED
    with "plan path has no decodable slug", which is false, and its contract
    directory could never report FOUND. The check always exits 0, so the one
    plan named that way looked permanently like a description-based run.

    The distinction is drawn here rather than in `derive_slug`, which must stay
    byte-identical to its twin in implement-trajectory-log.py - a parity
    tests/test_check_contract_gate.py locks.
    """
    stem = Path(plan_path).stem
    dated = (len(stem) >= 11 and stem[4] == "-" and stem[7] == "-"
             and stem[10] == "-" and all(c.isdigit() or c == "-" for c in stem[:10]))
    return not stem[11:] if dated else not stem


def _artifact_date(path: Path) -> date | None:
    """Parse the leading YYYY-MM-DD from a contract directory name."""
    stem = path.stem
    if len(stem) >= 10 and stem[4] == "-" and stem[7] == "-":
        try:
            return date.fromisoformat(stem[:10])
        except ValueError:
            return None
    return None


def _dir_slug(name: str) -> str | None:
    """The slug segment of a `<YYYY-MM-DD>-<slug>` directory, else None.

    On the NAME, never on `Path.stem`: a directory called
    `2026-01-01-bug-fix` has `.stem == "2026-01-01-bug"`, because `.fix` reads
    as a file extension. `derive_slug` above is right for a plan FILE and wrong
    for this, which is why the rule is spelled out twice rather than shared.
    """
    if (len(name) >= 12 and name[4] == "-" and name[7] == "-" and name[10] == "-"
            and all(c.isdigit() or c == "-" for c in name[:10])):
        return name[11:]
    return None


def check_gate(plan_path, contract_dir=None, today=None, stale_days=STALE_DAYS_DEFAULT):
    """Return (status, detail) for the contract gate check. Never raises on
    normal inputs.

    status is one of FOUND / MISSING / SKIPPED.
    """
    if not plan_path:
        return "SKIPPED", "no plan path supplied (description-based run)"

    slug = derive_slug(str(plan_path))
    if _slug_is_the_fallback(str(plan_path)):
        return "SKIPPED", "plan path has no decodable slug"

    # The ENGINE tree, deliberately, and no longer `get_plans_dir()`. A contract is
    # committed CODE and lives in this repository; the plan it belongs to lives in the
    # operator's private overlay, which a public clone does not have. Reading the
    # overlay to answer a question about a tracked directory was a data dependency
    # this check never needed.
    contract_dir = Path(contract_dir) if contract_dir is not None else get_workspace_root() / "tests" / "contract"
    if not contract_dir.is_dir():
        return "MISSING", f"no contract for slug '{slug}' (contract dir absent)"

    # Exact match on the SLUG SEGMENT of a DIRECTORY: <date>-<slug>/, NOT a
    # substring glob and no longer a suffix of the whole name.
    #
    # `iterdir()`, not `glob(f"*-{slug}")`. A slug carrying `[`, `]`, `*` or `?`
    # -- all legal in a filename, and a slug is derived from one -- turned the
    # PATTERN into the slug: `a*b` matched `2026-01-01-aZZZb`, and a stray `[`
    # made the pattern match nothing at all.
    #
    # And the filter that replaced it was `p.name.endswith(f"-{slug}")`, which
    # is a suffix of the whole directory name rather than of the slug segment,
    # so any slug that ENDS another one collected it: `2026-01-01-bug-fix/`
    # answered FOUND for the plan `2026-06-28-fix.md`, naming a contract
    # belonging to a different plan. FOUND is the one signal here that means
    # "this plan went through the gate", so a wrong FOUND is the only reading
    # worth preventing.
    matches = [
        p for p in contract_dir.iterdir()
        if p.is_dir() and _dir_slug(p.name) == slug
    ]
    if not matches:
        return "MISSING", f"no contract for slug '{slug}'"

    # Newest by parsed date, falling back to filename sort.
    newest = max(matches, key=lambda p: (_artifact_date(p) or date.min, p.name))
    ad = _artifact_date(newest)
    detail = f"{newest.name}"
    if ad is not None:
        ref = today or datetime.now(get_default_tz()).date()
        age = (ref - ad).days
        if age > stale_days:
            detail += f" (stale: {age} days)"
    return "FOUND", detail


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Advisory contract-gate check for /implement.")
    parser.add_argument("--plan", default="", help="path to the plan being implemented")
    parser.add_argument("--contract-dir", default=None, help="override the contract directory (testing)")
    parser.add_argument("--stale-days", type=int, default=STALE_DAYS_DEFAULT,
                        help=f"age threshold for the stale note (default {STALE_DAYS_DEFAULT})")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    status, detail = check_gate(args.plan, contract_dir=args.contract_dir, stale_days=args.stale_days)

    if args.json:
        print(json.dumps({"status": status, "detail": detail, "plan": args.plan}))
    else:
        color = {"FOUND": GREEN, "MISSING": YELLOW, "SKIPPED": GRAY}.get(status, RESET)
        print(f"{color}{BOLD}{status}{RESET} {GRAY}{detail}{RESET}")

    # Advisory only — always exit 0, never block /implement.
    return 0


if __name__ == "__main__":
    sys.exit(main())
