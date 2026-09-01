#!/usr/bin/env python3
"""Record one or more CEO false-positive flags on a /scrutinize finding.

Invoked by /scrutinize Phase 3 when the CEO replies with
`flag-as-fp <ids>` in the approval block. Each ID gets one `fp_flag` row in
outputs/operations/scrutiny/runs.jsonl, beside the verdict it disagrees with.

The CEO never opens the JSONL - this is the only writing path.

Usage:
  python scripts/scrutinize-flag-fp.py \
    --scrutiny-id 2026-05-27_execution \
    --ids B1,H2,M3 \
    --notes "B1 already addressed in PR #34; M3 is a style preference"

The script reads the saved scrutiny report at
outputs/operations/scrutiny/<scrutiny-id>.md, extracts the matching
findings by ID (severity, confidence, statement, evidence, target_type),
and appends one record per finding to the JSONL.

Exit codes:
  0 ok (records appended; FP rate by severity printed to stdout)
  2 argument error
  3 scrutiny report for --scrutiny-id not found
  4 one or more --ids did not match any finding in the report
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.utils.scrutinize_record import append_row, rows_of_kind  # noqa: E402
from scripts.utils.colors import GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import get_outputs_dir  # noqa: E402

def scrutiny_dir() -> Path:
    """Resolved at call time, never at import.

    `get_outputs_dir()` reads `HEADING_OS_DATA` on every call, so it follows
    the environment for a caller that asks after the environment moved. As a
    module-level constant it asked once, during its own import, and stored the
    answer, so a test that imported this module and then repointed the root
    still got the operator's real overlay. The `mkdir` below is not among the
    primitives `tests/conftest.py` wraps, so a stray directory in that overlay
    drew no refusal.
    """
    return get_outputs_dir() / "operations" / "scrutiny"

# Finding line pattern in saved scrutiny reports.
# Matches: [B1] (conf: 92) <statement>
# Or legacy:  [B1] <statement>
_FINDING_RE = re.compile(
    r"^\s*\[([BHMLN]\d+)\]\s*(?:\(conf:\s*(\d+)\))?\s*(.*?)$",
    re.MULTILINE,
)

SEVERITY_PREFIX = {"B": "BLOCKER", "H": "HIGH", "M": "MEDIUM", "L": "LOW", "N": "NIT"}


def parse_findings_from_report(report_path: Path) -> dict[str, dict]:
    """Extract findings by ID from a saved scrutiny report.

    Returns {finding_id: {severity, confidence, statement, evidence_snippet}}.
    """
    if not report_path.exists():
        return {}
    # A report this reader cannot decode is a report it has no findings from,
    # which is the same answer the missing-file line above gives. It used to be
    # a UnicodeDecodeError traceback out of `main`, so a CEO flagging a finding
    # against a report with one bad byte got a stack trace where the file's own
    # exit-code contract (3 for a report it cannot use) already had an answer.
    # Measured 2026-09-01.
    try:
        text = report_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"{RED}ERROR: scrutiny report unreadable: {report_path} ({exc}){RESET}",
              file=sys.stderr)
        return {}
    findings: dict[str, dict] = {}
    for match in _FINDING_RE.finditer(text):
        fid = match.group(1)
        conf_raw = match.group(2)
        statement = match.group(3).strip()
        # Extract the location/evidence block immediately after the finding line.
        # Looks for "Location:" / "Evidence:" within next 8 lines.
        start = match.end()
        snippet = text[start:start + 800]
        location = ""
        evidence = ""
        for line in snippet.splitlines()[:8]:
            line = line.strip()
            if line.lower().startswith("location:"):
                location = line.split(":", 1)[1].strip()
            elif line.lower().startswith("evidence:"):
                evidence = line.split(":", 1)[1].strip()
        findings[fid] = {
            "severity": SEVERITY_PREFIX.get(fid[0], "UNKNOWN"),
            "confidence": int(conf_raw) if conf_raw else None,
            "statement": statement,
            "location": location,
            "evidence": evidence[:300],  # cap to keep JSONL readable
        }
    return findings


def parse_target_type(scrutiny_id: str) -> str:
    """Infer target type from the scrutiny-id filename stem.

    Examples:
      2026-05-27_execution           -> execution
      2026-05-27_workspace           -> workspace
      2026-05-27-execution           -> execution
      2026-05-27_skill-foo           -> file-or-dir
    """
    stem = scrutiny_id.lower()
    if "execution" in stem:
        return "execution"
    if "workspace" in stem:
        return "workspace"
    if "plan" in stem:
        return "plan"
    return "file-or-dir"


def append_records(records: list[dict]) -> None:
    """Append one `fp_flag` row per flagged finding, to the one record.

    One channel, which is what the plan asked for and what the first pass did not
    deliver: it kept writing `_fp_log.jsonl` beside the record and called the
    second write transitional. That legacy log is the one whose permanent
    emptiness (zero records in 75 runs) justified deleting its 327-line
    aggregator in the same change, so keeping it alive as a second destination
    preserved exactly the split the change existed to close. Dropped 2026-08-09
    on the scrutiny of that run.

    A CEO disagreement is the only ground truth this system will ever get, so it
    is stored beside the verdicts it disagrees with rather than in a file nothing
    else joins.
    """
    scrutiny_dir().mkdir(parents=True, exist_ok=True)
    for rec in records:
        append_row(
            run_id=rec["scrutiny_id"],
            kind="fp_flag",
            target=rec.get("target_type") or "",
            finding_id=rec["finding_id"],
            confidence_before=rec.get("confidence"),
            degraded=None,
            writer="flag-fp",
            payload={
                "severity": rec.get("severity"),
                "statement": rec.get("statement"),
                "location": rec.get("location"),
                "evidence": rec.get("evidence"),
                "ceo_note": rec.get("ceo_note"),
                "flagged_at": rec.get("flagged_at"),
            },
        )


def flagged_pairs() -> set[tuple[str, str]]:
    """The DISTINCT (run_id, finding_id) pairs the CEO has flagged.

    The same set `scrutinize-replay.load_fp_set` builds, and deliberately the
    same shape: a finding is flagged or it is not, and flagging it twice does
    not make it twice as false.
    """
    out: set[tuple[str, str]] = set()
    for row in rows_of_kind("fp_flag"):
        sid, fid = row.get("run_id"), row.get("finding_id")
        if sid and fid:
            out.add((str(sid), str(fid)))
    return out


def print_running_tally() -> None:
    """Print FP counts by severity over DISTINCT flagged findings.

    Severity comes from the finding id's letter, the same mapping the report
    parser uses, so the row needs no severity column of its own.

    Counting ROWS was the defect: measured 2026-08-30, `--ids B1,B1` followed by
    a second `--ids B1` left three rows for one finding and printed
    `FP tally: 3 recorded - BLOCKER=3`, while `scrutinize-replay.load_fp_set`
    read the same record as a single pair. The two numbers this system computes
    from one file disagreed, and the inflated one feeds the FP-rate-by-severity
    statistic this script exists to produce. Deduping at the write path (below)
    stops new duplicates; counting pairs here also makes the tally honest over
    the duplicates already on disk.
    """
    pairs = flagged_pairs()
    if not pairs:
        print("  Tally: 0 FPs recorded.")
        return
    counts: Counter[str] = Counter(
        SEVERITY_PREFIX.get(fid[:1], "UNKNOWN") for _, fid in pairs
    )
    parts = [f"{sev}={counts.get(sev, 0)}"
             for sev in ("BLOCKER", "HIGH", "MEDIUM", "LOW", "NIT")]
    print(f"  FP tally: {len(pairs)} recorded - " + ", ".join(parts))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record FP flags on /scrutinize findings.")
    parser.add_argument("--scrutiny-id", required=True,
                        help="Stem of the scrutiny report filename "
                             "(e.g. 2026-05-27_execution).")
    parser.add_argument("--ids", required=True,
                        help="Comma-separated finding IDs to flag (e.g. B1,H2,M3).")
    parser.add_argument("--notes", default="",
                        help="Optional CEO note explaining WHY these are FPs.")
    args = parser.parse_args(argv)

    report_path = scrutiny_dir() / f"{args.scrutiny_id}.md"
    if not report_path.exists():
        print(f"{RED}ERROR: scrutiny report not found: {report_path}{RESET}",
              file=sys.stderr)
        return 3

    findings = parse_findings_from_report(report_path)
    # Order-preserving dedupe. `--ids B1,B1` wrote two identical rows and
    # reported "Flagged 2 finding(s)" - measured 2026-08-30. A flag is a boolean
    # the CEO sets on a finding, not a counter he increments.
    requested_ids: list[str] = []
    for raw in args.ids.split(","):
        fid = raw.strip().upper()
        if fid and fid not in requested_ids:
            requested_ids.append(fid)

    missing = [i for i in requested_ids if i not in findings]
    if missing:
        print(f"{RED}ERROR: IDs not found in report: {', '.join(missing)}{RESET}",
              file=sys.stderr)
        print(f"  Available IDs: {', '.join(sorted(findings.keys()))}", file=sys.stderr)
        return 4

    # And the same finding flagged again in a LATER invocation - to attach a
    # note, say - appended another row. Same measurement, same day. The record
    # is append-only, so the guard has to be a read of what is already there.
    already = flagged_pairs()
    duplicates = [i for i in requested_ids if (args.scrutiny_id, i) in already]
    requested_ids = [i for i in requested_ids if i not in duplicates]
    if duplicates:
        print(f"{YELLOW}Already flagged, not recorded again: "
              f"{', '.join(duplicates)}{RESET}", file=sys.stderr)
    if not requested_ids:
        print(f"{YELLOW}Nothing new to flag.{RESET}")
        print_running_tally()
        return 0

    target_type = parse_target_type(args.scrutiny_id)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = []
    for fid in requested_ids:
        f = findings[fid]
        records.append({
            "scrutiny_id": args.scrutiny_id,
            "finding_id": fid,
            "severity": f["severity"],
            "confidence": f["confidence"],
            "statement": f["statement"],
            "location": f["location"],
            "evidence": f["evidence"],
            "target_type": target_type,
            "ceo_note": args.notes,
            "flagged_at": now,
        })

    append_records(records)

    print(f"{GREEN}Flagged {len(records)} finding(s) as FP in runs.jsonl:{RESET}")
    for rec in records:
        conf_str = f"conf={rec['confidence']}" if rec['confidence'] is not None else "conf=?"
        print(f"  [{rec['finding_id']}] {rec['severity']} {conf_str} - "
              f"{rec['statement'][:70]}{'...' if len(rec['statement']) > 70 else ''}")
    print_running_tally()
    if args.notes:
        print(f"{YELLOW}Note attached: {args.notes}{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
