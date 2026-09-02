#!/usr/bin/env python3
"""Rotation ledger: which artifacts have been audited, at which content.

Usage:
    python scripts/audit-rotation.py --status
    python scripts/audit-rotation.py --select 10
    python scripts/audit-rotation.py --record scripts/foo.py --verdict clean
    python scripts/audit-rotation.py --record scripts/foo.py --verdict fixed \\
        --note "two findings, both closed in abc1234"
    python scripts/audit-rotation.py --json

What this is, and what it deliberately is not
---------------------------------------------
This SELECTS and RECORDS. It never audits anything and it never calls a model.
The audit itself is `scripts/engine-audit.py`, or a person, or an agent the
operator dispatches. Keeping the two apart is the point: a ledger that could
start work would start work, and the operator asked for a rotation, not a
standing campaign.

The problem it solves
---------------------
The 10-day campaign that ended 2026-09-02 was 144 commits long because the debt
had accumulated for months before anyone looked. Nothing was wrong with the
audit; what was wrong was that it happened once, late, all at once.

A rotation replaces the campaign. Each pass takes a small slice, audits it, and
records the verdict against the artifact's CONTENT HASH. An artifact whose hash
still matches its recorded verdict is not selected again. An artifact that
changed has a new hash, so it re-enters the queue by itself. A brand-new
artifact has no record at all, so it enters at the front. Over a few months
every artifact in the tree carries a verdict against its current content, and
the number this prints is the honest answer to "how much of the system has
actually been checked."

Why the hash and not a date
---------------------------
A date-keyed ledger says an artifact was audited in July, which stops being
useful the moment the file changes: the verdict describes bytes that are gone.
Keying on the content hash makes the record a claim about what is actually on
disk right now, and makes staleness self-detecting rather than something a
human has to remember to invalidate. It also makes the coverage number
un-gameable: touching a file cannot inflate it.

Selection order
---------------
1. Never audited at all -- these are new artifacts and unknown ground.
2. Audited, but at a hash that no longer matches -- these changed since.
3. Within each group, oldest recorded verdict first, then a stable shuffle
   seeded by the day, so a run on the same day is reproducible and a run a day
   later is not the same slice.

Scope, precisely (`.claude/rules/scope-claims.md`)
--------------------------------------------------
The inventory is derived from `git ls-files` on every run, filtered by
`AUDITABLE`. It is never stored, so a file added today is in the queue today
without anyone updating a list. The coverage percentage this prints is over that
inventory and says nothing about files outside it -- `tests/`, `docs/` and the
data overlay are not in it, and are named here rather than silently absent.

A recorded verdict means a person or an agent said so. This file cannot tell a
careful audit from a careless one; it records that one happened, against which
bytes, on which day.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.repo_files import IndexUnreadable, git_index_paths  # noqa: E402
from scripts.utils.workspace import get_default_tz, get_workspace_root  # noqa: E402

ROOT = get_workspace_root()
LEDGER_PATH = ROOT / "config" / "audit-rotation-ledger.json"

# `open` is the verdict that makes this a repair queue rather than a logbook.
# An audit that found defects and did not fix them is NOT a checked artifact, so
# `open` never counts toward coverage and the artifact stays in the report until
# the findings are closed. Without it, recording a defect would mark the file
# done, which is the whole failure this rotation exists to prevent.
VERDICTS = ("clean", "fixed", "not-applicable", "open")
CLOSED_VERDICTS = ("clean", "fixed", "not-applicable")

SEVERITIES = ("low", "medium", "high")

# What is in the rotation. Prefixes and suffixes, both required to match.
AUDITABLE: tuple[tuple[str, str], ...] = (
    ("scripts/", ".py"),
    ("scripts/", ".sh"),
    (".claude/hooks/", ".py"),
    (".claude/skills/", "SKILL.md"),
    (".claude/rules/", ".md"),
    (".claude/agents/", ".md"),
)

# Below this the inventory did not come from this repository. A coverage number
# over a handful of files would read as progress while measuring nothing.
MIN_INVENTORY = 200  # measured 2026-09-02: 558 auditable artifacts


def _today() -> str:
    """Today in the workspace timezone, per `.claude/rules/voice.md`.

    `date.today()` reads the host clock with no zone, so the same run on
    two machines can record two different days and the seeded slice stops
    being reproducible across them.
    """
    return datetime.now(get_default_tz()).date().isoformat()


class Unreadable(Exception):
    """The inputs could not be read, so no verdict is possible."""


# ============================================================
# Inventory, derived every run
# ============================================================

def is_auditable(rel: str) -> bool:
    return any(rel.startswith(prefix) and rel.endswith(suffix)
               for prefix, suffix in AUDITABLE)


def inventory(root: Path) -> dict[str, str]:
    """`relative path -> sha256 of its current bytes`, for every auditable file.

    Derived, never stored. That is what makes a new artifact enter the rotation
    without anyone maintaining a list, and it is the whole reason this function
    exists rather than a `files:` key in the ledger.
    """
    try:
        # One shared reader. Losing a path here would shrink the denominator of
        # the coverage number this file prints, which is the one number the
        # rotation exists to make honest.
        listed = git_index_paths(root)
    except IndexUnreadable as exc:
        raise Unreadable(str(exc)) from exc

    paths = [rel for rel in listed if is_auditable(rel)]
    if not paths:
        raise Unreadable("git ls-files returned no auditable path")

    out: dict[str, str] = {}
    for rel in paths:
        try:
            data = (root / rel).read_bytes()
        except OSError as exc:
            # Never swallow. A tracked file that cannot be read is a fault, and
            # dropping it would quietly shrink the denominator of the coverage
            # number this file prints.
            print(f"{RED}cannot read {rel}: {exc}{RESET}", file=sys.stderr)
            continue
        out[rel] = hashlib.sha256(data).hexdigest()
    return out


# ============================================================
# The ledger
# ============================================================

def load_ledger(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Unreadable(f"ledger unreadable: {exc}") from exc
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        raise Unreadable("ledger malformed: expected {'entries': {path: {...}}}")
    return entries


def save_ledger(path: Path, entries: dict[str, dict]) -> None:
    payload = {
        "_comment": (
            "Rotation ledger for scripts/audit-rotation.py. One entry per "
            "audited artifact, keyed by path, carrying the sha256 of the bytes "
            "that were audited. An artifact whose current hash differs from the "
            "recorded one is back in the queue."
        ),
        "entries": dict(sorted(entries.items())),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def verified(entries: dict[str, dict], current: dict[str, str]) -> set[str]:
    """Paths audited against the bytes that are there now, with nothing left open.

    Two conditions, and the second is the one that makes this a repair queue. A
    hash match alone would count an artifact whose audit found three defects and
    fixed none, which is the exact reading the operator refused on 2026-09-02:
    a finding must be fixed, not filed.
    """
    return {rel for rel, digest in current.items()
            if entries.get(rel, {}).get("sha256") == digest
            and entries.get(rel, {}).get("verdict") in CLOSED_VERDICTS}


def open_findings(entries: dict[str, dict],
                  current: dict[str, str]) -> list[tuple[str, dict]]:
    """`(path, finding)` for every unfixed finding whose artifact still exists.

    A finding recorded against bytes that have since changed is dropped, not
    carried: the file it described is gone, and reporting it would send the
    operator to read a line that no longer exists.
    """
    out: list[tuple[str, dict]] = []
    for rel, entry in sorted(entries.items()):
        if entry.get("verdict") != "open":
            continue
        if current.get(rel) != entry.get("sha256"):
            continue
        for finding in entry.get("findings", []):
            if isinstance(finding, dict):
                out.append((rel, finding))
    rank = {name: index for index, name in enumerate(reversed(SEVERITIES))}
    out.sort(key=lambda pair: (rank.get(pair[1].get("severity"), 99), pair[0]))
    return out


def total_minutes(findings: list[tuple[str, dict]]) -> int:
    """Sum of the per-finding estimates. An estimate is a number a person or an
    agent wrote down; nothing here derives one, and a missing one counts zero
    rather than being guessed into the total."""
    return sum(int(f.get("estimate_minutes") or 0) for _, f in findings)


# ============================================================
# Selection
# ============================================================

def select(entries: dict[str, dict], current: dict[str, str], count: int,
           *, seed: str) -> list[tuple[str, str]]:
    """`(path, why)` for the next `count` artifacts to audit.

    Never audited first, then changed-since-audited, oldest verdict first inside
    each group. The shuffle is seeded by the caller so one day's run is
    reproducible and the next day's is a different slice.
    """
    never: list[str] = []
    changed: list[tuple[str, str]] = []
    for rel, digest in current.items():
        entry = entries.get(rel)
        if entry is None:
            never.append(rel)
        elif entry.get("sha256") != digest:
            changed.append((entry.get("date", ""), rel))

    # noqa S311 below is the point of the call, not an exception to it. The
    # shuffle must be REPRODUCIBLE from the seed so one day's slice can be
    # re-derived and reviewed; a cryptographic generator cannot do that. Nothing
    # here protects a secret: the worst a predictable order can do is let
    # somebody guess which files get audited on Thursday.
    rng = random.Random(seed)  # noqa: S311 - reproducible slice, not a secret
    rng.shuffle(never)
    changed.sort(key=lambda pair: pair[0])

    picked = [(rel, "never audited") for rel in never[:count]]
    remaining = count - len(picked)
    if remaining > 0:
        picked += [(rel, f"changed since {when or 'an undated audit'}")
                   for when, rel in changed[:remaining]]
    return picked


# ============================================================
# CLI
# ============================================================

def refuse(reason: str, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"refused": reason}, indent=2))
    else:
        print(f"{RED}{BOLD}REFUSED{RESET} {reason}", file=sys.stderr)
    return 2


def render_status(entries: dict[str, dict], current: dict[str, str]) -> None:
    done = verified(entries, current)
    total = len(current)
    pct = 100.0 * len(done) / total
    stale = sorted(set(entries) - set(current))

    bar_width = 40
    filled = round(bar_width * len(done) / total)
    bar = "#" * filled + "-" * (bar_width - filled)

    print(f"{BOLD}audit rotation{RESET}")
    print(f"  {CYAN}[{bar}]{RESET} {pct:.1f}%")
    print(f"  {len(done)} of {total} auditable artifacts carry a verdict "
          f"against their CURRENT bytes")
    print(f"  {total - len(done)} in the queue")
    if stale:
        print(f"  {GRAY}{len(stale)} ledger entries name a path that is gone; "
              f"they are ignored, not counted{RESET}")


def parse_finding(raw: str) -> dict:
    """`"summary|severity|minutes"` -> a finding dict.

    Three fields, all required. A finding with no estimate cannot be scheduled
    and a finding with no severity cannot be ordered, so both are refused at the
    point of entry rather than defaulted into something plausible.
    """
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) != 3:
        raise ValueError(f"expected 'summary|severity|minutes', got {raw!r}")
    summary, severity, minutes = parts
    if not summary:
        raise ValueError("a finding needs a summary")
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}, got {severity!r}")
    if not minutes.isdigit() or int(minutes) < 1:
        raise ValueError(f"minutes must be a positive whole number, got {minutes!r}")
    return {"summary": summary, "severity": severity,
            "estimate_minutes": int(minutes)}


def render_report(findings: list[tuple[str, dict]], queue: int, total: int) -> None:
    """The daily digest: what is broken, and roughly how long it takes to fix."""
    if not findings:
        print(f"{GREEN}Nothing open. {total - queue} of {total} artifacts carry a "
              f"verdict against their current bytes.{RESET}")
        return

    minutes = total_minutes(findings)
    hours, rest = divmod(minutes, 60)
    duration = f"{hours}h {rest}m" if hours else f"{rest}m"

    print(f"{BOLD}open findings{RESET}  {len(findings)} across "
          f"{len({rel for rel, _ in findings})} artifact(s), "
          f"about {CYAN}{duration}{RESET} of work")
    print(f"{GRAY}estimates are what the auditor wrote down, not a derived "
          f"figure{RESET}\n")

    current_file = None
    for rel, finding in findings:
        if rel != current_file:
            print(f"  {BOLD}{rel}{RESET}")
            current_file = rel
        colour = {"high": RED, "medium": YELLOW, "low": GRAY}[finding["severity"]]
        print(f"    {colour}{finding['severity']:6s}{RESET} "
              f"{finding['estimate_minutes']:>4d}m  {finding['summary']}")
    print(f"\n{YELLOW}Nothing here is fixed by recording it. Approve a night "
          f"batch to have the work done:{RESET}")
    print("  python scripts/audit-rotation.py --report   # this list")
    print("  (then say the word, and the batch runs overnight, uncommitted)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true", help="coverage of the rotation")
    ap.add_argument("--report", action="store_true",
                    help="the daily digest of open findings and their estimates")
    ap.add_argument("--notify", action="store_true",
                    help="send the digest to the operator's own sink")
    ap.add_argument("--finding", action="append", default=[],
                    metavar="SUMMARY|SEVERITY|MINUTES",
                    help="one finding; repeatable; required with --verdict open")
    ap.add_argument("--select", type=int, metavar="N",
                    help="print the next N artifacts to audit")
    ap.add_argument("--record", metavar="PATH", help="record a verdict for one artifact")
    ap.add_argument("--verdict", choices=VERDICTS, help="required with --record")
    ap.add_argument("--note", default="", help="one line about what was found")
    ap.add_argument("--seed", default=None,
                    help="selection seed; defaults to today's date")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    try:
        current = inventory(ROOT)
        entries = load_ledger(LEDGER_PATH)
    except Unreadable as exc:
        return refuse(str(exc), as_json=args.json)

    if len(current) < MIN_INVENTORY:
        return refuse(f"inventory holds {len(current)} artifacts, below the floor "
                      f"of {MIN_INVENTORY}; a coverage number over this would be "
                      f"a claim about a tree that was not read",
                      as_json=args.json)

    if args.record:
        if not args.verdict:
            print(f"{RED}--record needs --verdict{RESET}", file=sys.stderr)
            return 2
        if args.record not in current:
            print(f"{RED}{args.record} is not an auditable artifact in this tree"
                  f"{RESET}", file=sys.stderr)
            return 2
        try:
            findings = [parse_finding(raw) for raw in args.finding]
        except ValueError as exc:
            print(f"{RED}{exc}{RESET}", file=sys.stderr)
            return 2
        if args.verdict == "open" and not findings:
            print(f"{RED}--verdict open needs at least one --finding; an open "
                  f"verdict with nothing open is a file marked broken with no "
                  f"way to know what to fix{RESET}", file=sys.stderr)
            return 2
        if args.verdict != "open" and findings:
            print(f"{RED}--finding is only for --verdict open; a finding "
                  f"recorded beside a closed verdict would never be reported "
                  f"and never be fixed{RESET}", file=sys.stderr)
            return 2

        entries[args.record] = {
            "sha256": current[args.record],
            "date": _today(),
            "verdict": args.verdict,
            "note": args.note,
            "findings": findings,
        }
        save_ledger(LEDGER_PATH, entries)
        print(f"{GREEN}recorded{RESET} {args.record} -> {args.verdict}"
              + (f" ({len(findings)} open finding(s), "
                 f"{sum(f['estimate_minutes'] for f in findings)}m estimated)"
                 if findings else ""))
        return 0

    if args.notify:
        # The same dated freeze the night pass reads. A digest is not a repair,
        # but it is still the automation speaking on its own initiative, and the
        # operator said nothing runs. One file governs both.
        import importlib.util as _ilu

        _spec = _ilu.spec_from_file_location(
            "night_repair_hold", ROOT / "scripts" / "night-repair.py")
        _night = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_night)
        _held = _night.hold_reason(_night.HOLD_PATH, _today())
        if _held:
            print(f"{YELLOW}HELD{RESET} {_held}", file=sys.stderr)
            return 0

        findings = open_findings(entries, current)
        if not findings:
            print(f"{GRAY}nothing open; no notification sent{RESET}")
            return 0
        minutes = total_minutes(findings)
        hours, rest = divmod(minutes, 60)
        duration = f"{hours}h {rest}m" if hours else f"{rest}m"
        lines = [f"Rotation: {len(findings)} open finding(s), about {duration}."]
        for rel, finding in findings[:10]:
            lines.append(f"- [{finding.get('severity')}] {rel}: "
                         f"{finding.get('summary')} "
                         f"(~{finding.get('estimate_minutes')}m)")
        if len(findings) > 10:
            lines.append(f"...and {len(findings) - 10} more.")
        lines.append("Approve tonight: scripts/night-repair.py --approve")

        # The operator's OWN sink. `.claude/rules/lethal-trifecta.md` exempts a
        # message that can only reach the person who already holds the data;
        # `telegram_notify` refuses any recipient outside that allowlist, so
        # this cannot become an outbound send by editing a caller.
        from scripts.utils.telegram_notify import notify, own_targets

        targets = own_targets()
        if not targets:
            print(f"{YELLOW}no own notification sink configured; printing "
                  f"instead{RESET}")
            print("\n".join(lines))
            return 0
        sent = notify(sorted(targets)[0], "\n".join(lines))
        print(f"{GREEN if sent else RED}notification "
              f"{'sent' if sent else 'FAILED'}{RESET}")
        return 0 if sent else 1

    if args.report:
        findings = open_findings(entries, current)
        done = verified(entries, current)
        if args.json:
            print(json.dumps({
                "open": [{"path": rel, **finding} for rel, finding in findings],
                "total_minutes": total_minutes(findings),
                "verified": len(done), "total": len(current)}, indent=2))
            return 0
        render_report(findings, len(current) - len(done), len(current))
        return 0

    if args.select is not None:
        if args.select < 1:
            print(f"{RED}--select needs a positive count{RESET}", file=sys.stderr)
            return 2
        picked = select(entries, current, args.select,
                        seed=args.seed or _today())
        if args.json:
            print(json.dumps({"selected": [{"path": p, "why": w} for p, w in picked]},
                             indent=2))
            return 0
        if not picked:
            print(f"{GREEN}nothing in the queue: every artifact carries a verdict "
                  f"against its current bytes.{RESET}")
            return 0
        print(f"{BOLD}next {len(picked)} artifact(s) to audit{RESET}")
        for rel, why in picked:
            print(f"  {CYAN}{rel}{RESET}  {GRAY}({why}){RESET}")
        print(f"\n{YELLOW}Audit them, then record each one:{RESET}")
        print(f"  python scripts/audit-rotation.py --record <path> "
              f"--verdict clean|fixed --note \"...\"")
        return 0

    if args.json:
        done = verified(entries, current)
        print(json.dumps({"total": len(current), "verified": len(done),
                          "queue": len(current) - len(done),
                          "percent": round(100.0 * len(done) / len(current), 1)},
                         indent=2))
        return 0

    render_status(entries, current)
    return 0


if __name__ == "__main__":
    sys.exit(main())
