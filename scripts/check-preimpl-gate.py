#!/usr/bin/env python3
"""Advisory check: does a /pre-impl gate artifact exist for a plan?

Used by /implement as a soft, non-blocking reminder. Given a plan path, it
derives the plan slug and looks in the plans directory for a matching
`<date>-pre-impl-<slug>.md` artifact (the form /pre-impl writes). It prints one
line and ALWAYS exits 0 — it is advisory and never blocks /implement.

Statuses:
    FOUND    a matching pre-impl artifact exists (newest date reported; a
             "(stale: N days)" note is appended when older than --stale-days)
    MISSING  a plan path was given but no matching artifact exists
    SKIPPED  no plan path, or the path has no decodable slug

Usage:
    python scripts/check-preimpl-gate.py --plan plans/2026-06-28-foo.md
    python scripts/check-preimpl-gate.py --plan plans/2026-06-28-foo.md --json
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_plans_dir
from scripts.utils.colors import GREEN, YELLOW, GRAY, BOLD, RESET

STALE_DAYS_DEFAULT = 14


def derive_slug(plan_path: str) -> str:
    """Path(plan_path).stem with leading YYYY-MM-DD- stripped if present.

    Canonical source: scripts/implement-trajectory-log.py:derive_slug. Kept
    byte-identical here because that module's hyphenated name cannot be imported
    (`from scripts.implement-trajectory-log import ...` is a syntax error). The
    parity is locked by tests/test_check_preimpl_gate.py (imported via importlib).
    """
    stem = Path(plan_path).stem
    # Strip YYYY-MM-DD- (10 chars + 1 hyphen = 11 chars) if it matches the pattern
    if len(stem) >= 11 and stem[4] == "-" and stem[7] == "-" and stem[10] == "-":
        date_part = stem[:10]
        if all(c.isdigit() or c == "-" for c in date_part):
            return stem[11:] or "untitled"
    return stem or "untitled"


def _artifact_date(path: Path) -> date | None:
    """Parse the leading YYYY-MM-DD from a pre-impl artifact filename."""
    stem = path.stem
    if len(stem) >= 10 and stem[4] == "-" and stem[7] == "-":
        try:
            return datetime.strptime(stem[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def check_gate(plan_path, plans_dir=None, today=None, stale_days=STALE_DAYS_DEFAULT):
    """Return (status, detail) for the pre-impl gate check. Never raises on
    normal inputs.

    status is one of FOUND / MISSING / SKIPPED.
    """
    if not plan_path:
        return "SKIPPED", "no plan path supplied (description-based run)"

    slug = derive_slug(str(plan_path))
    if not slug or slug == "untitled":
        return "SKIPPED", "plan path has no decodable slug"

    plans_dir = Path(plans_dir) if plans_dir is not None else get_plans_dir()
    if not plans_dir.is_dir():
        return "MISSING", f"no pre-impl artifact for slug '{slug}' (plans dir absent)"

    # Exact suffix match: <date>-pre-impl-<slug>.md, NOT a loose substring glob.
    matches = [
        p for p in plans_dir.glob(f"*-pre-impl-{slug}.md")
        if p.stem.endswith(f"-pre-impl-{slug}")
    ]
    if not matches:
        return "MISSING", f"no pre-impl artifact for slug '{slug}'"

    # Newest by parsed date, falling back to filename sort.
    newest = max(matches, key=lambda p: (_artifact_date(p) or date.min, p.name))
    ad = _artifact_date(newest)
    detail = f"{newest.name}"
    if ad is not None:
        ref = today or date.today()
        age = (ref - ad).days
        if age > stale_days:
            detail += f" (stale: {age} days)"
    return "FOUND", detail


def main() -> int:
    parser = argparse.ArgumentParser(description="Advisory /pre-impl gate check for /implement.")
    parser.add_argument("--plan", default="", help="path to the plan being implemented")
    parser.add_argument("--plans-dir", default=None, help="override plans directory (testing)")
    parser.add_argument("--stale-days", type=int, default=STALE_DAYS_DEFAULT,
                        help=f"age threshold for the stale note (default {STALE_DAYS_DEFAULT})")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    status, detail = check_gate(args.plan, plans_dir=args.plans_dir, stale_days=args.stale_days)

    if args.json:
        print(json.dumps({"status": status, "detail": detail, "plan": args.plan}))
    else:
        color = {"FOUND": GREEN, "MISSING": YELLOW, "SKIPPED": GRAY}.get(status, RESET)
        print(f"{color}{BOLD}{status}{RESET} {GRAY}{detail}{RESET}")

    # Advisory only — always exit 0, never block /implement.
    return 0


if __name__ == "__main__":
    sys.exit(main())
