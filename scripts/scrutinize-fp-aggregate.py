#!/usr/bin/env python3
"""Aggregate /scrutinize FP flags into a calibration report.

Reads outputs/operations/scrutiny/_fp_log.jsonl and renders
outputs/operations/scrutiny/_fp_aggregate.md with:

- FP rate per severity tier (BLOCKER/HIGH/MEDIUM/LOW/NIT)
- FP rate per target type (plan/execution/file/dir/workspace)
- FP rate per confidence band (0-25, 25-50, 50-75, 75-100)
- Calibration table (confidence vs actual FP rate) - lets the CEO check
  whether the confidence scorer is well-calibrated. A well-calibrated
  scorer produces findings at conf=80 that are wrong ~20% of the time.

The "total findings emitted" denominator is approximated by scanning
every saved scrutiny report under outputs/operations/scrutiny/*.md
for finding lines `[B1] (conf: 92) ...` and counting them. This is
exact when the FP-flagging discipline is followed: every false-positive
finding flagged, every other finding implicitly true-positive.

Usage:
  python scripts/scrutinize-fp-aggregate.py            # rebuild aggregate
  python scripts/scrutinize-fp-aggregate.py --json     # JSON output

Output: outputs/operations/scrutiny/_fp_aggregate.md (CEO-only by
directory default classification).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.utils.colors import CYAN, GREEN, RESET  # noqa: E402
from scripts.utils.workspace import get_outputs_dir  # noqa: E402

SCRUTINY_DIR = get_outputs_dir() / "operations" / "scrutiny"
FP_LOG_PATH = SCRUTINY_DIR / "_fp_log.jsonl"
AGGREGATE_PATH = SCRUTINY_DIR / "_fp_aggregate.md"

_FINDING_RE = re.compile(
    r"^\s*\[([BHMLN]\d+)\]\s*(?:\(conf:\s*(\d+)\))?",
    re.MULTILINE,
)

SEVERITY_PREFIX = {"B": "BLOCKER", "H": "HIGH", "M": "MEDIUM", "L": "LOW", "N": "NIT"}
SEVERITIES = ["BLOCKER", "HIGH", "MEDIUM", "LOW", "NIT"]
TARGET_TYPES = ["plan", "execution", "file-or-dir", "workspace"]
CONF_BANDS = [(0, 25, "0-24"), (25, 50, "25-49"),
              (50, 75, "50-74"), (75, 101, "75-100")]


def load_fp_records() -> list[dict]:
    """Returns all FP records from _fp_log.jsonl."""
    if not FP_LOG_PATH.exists():
        return []
    out: list[dict] = []
    for line in FP_LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def count_total_findings() -> dict[str, dict]:
    """Scan saved scrutiny reports + count findings by severity + confidence.

    Returns {
      'by_severity': Counter,
      'by_target_type': Counter,
      'by_conf_band': Counter,
      'total': int,
    }
    """
    by_severity: Counter[str] = Counter()
    by_target_type: Counter[str] = Counter()
    by_conf_band: Counter[str] = Counter()
    total = 0
    if not SCRUTINY_DIR.exists():
        return {"by_severity": by_severity, "by_target_type": by_target_type,
                "by_conf_band": by_conf_band, "total": 0}
    for report in SCRUTINY_DIR.glob("*.md"):
        if report.name.startswith("_"):
            continue
        text = report.read_text(encoding="utf-8")
        target = _infer_target_type(report.stem)
        for match in _FINDING_RE.finditer(text):
            fid = match.group(1)
            conf_raw = match.group(2)
            severity = SEVERITY_PREFIX.get(fid[0], "UNKNOWN")
            by_severity[severity] += 1
            by_target_type[target] += 1
            if conf_raw is not None:
                conf = int(conf_raw)
                for lo, hi, label in CONF_BANDS:
                    if lo <= conf < hi:
                        by_conf_band[label] += 1
                        break
            total += 1
    return {"by_severity": by_severity, "by_target_type": by_target_type,
            "by_conf_band": by_conf_band, "total": total}


def _infer_target_type(stem: str) -> str:
    s = stem.lower()
    if "execution" in s:
        return "execution"
    if "workspace" in s:
        return "workspace"
    if "plan" in s:
        return "plan"
    return "file-or-dir"


def _conf_band(conf: int | None) -> str | None:
    if conf is None:
        return None
    for lo, hi, label in CONF_BANDS:
        if lo <= conf < hi:
            return label
    return None


def fp_rate(fp_count: int, total: int) -> str:
    """Format a rate as 'N/D (X%)' or 'no data'."""
    if total == 0:
        return "no data"
    pct = (fp_count / total) * 100
    return f"{fp_count}/{total} ({pct:.1f}%)"


def render(fp_records: list[dict], totals: dict) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    fp_by_severity: Counter[str] = Counter()
    fp_by_target: Counter[str] = Counter()
    fp_by_conf_band: Counter[str] = Counter()
    for rec in fp_records:
        fp_by_severity[rec.get("severity", "UNKNOWN")] += 1
        fp_by_target[rec.get("target_type", "UNKNOWN")] += 1
        band = _conf_band(rec.get("confidence"))
        if band:
            fp_by_conf_band[band] += 1

    lines: list[str] = []
    lines.append("# /scrutinize - False-Positive Calibration Report")
    lines.append("")
    lines.append(f"Last rebuilt: {today}. FPs recorded: {len(fp_records)}. "
                 f"Total findings emitted (across all saved scrutiny reports): "
                 f"{totals['total']}.")
    lines.append("")
    lines.append("**What this is.** Calibration view onto `/scrutinize` false-positive "
                 "flags. Every time the CEO replies `flag-as-fp <ids>` in the approval "
                 "block, `scripts/scrutinize-flag-fp.py` appends one record per finding "
                 "to `_fp_log.jsonl`. This aggregate rolls those records up into FP "
                 "rates by severity, target type, and confidence band. The "
                 "denominator is the count of findings scrutinize has ever emitted (parsed "
                 "from saved reports under `outputs/operations/scrutiny/`).")
    lines.append("")
    lines.append("**Goal.** A well-calibrated scorer produces:")
    lines.append("")
    lines.append("- conf=95 findings wrong ~5% of the time")
    lines.append("- conf=80 findings wrong ~20% of the time")
    lines.append("- conf=60 findings wrong ~40% of the time")
    lines.append("")
    lines.append("If actual FP rates drift far from these expectations, the confidence "
                 "scorer needs recalibration (adjust the rubric in "
                 "`references/severity-grid.md` and `references/refutation-protocol.md`).")
    lines.append("")
    lines.append("---")
    lines.append("")

    if not fp_records:
        lines.append("## No FPs recorded yet")
        lines.append("")
        lines.append("This is the empty state - either scrutinize is producing zero FPs "
                     "(great), or the CEO has not yet flagged any (data not yet started). "
                     "Once `flag-as-fp` is used inside an approval block at least once, "
                     "this aggregate populates.")
        lines.append("")
        return "\n".join(lines)

    # By severity
    lines.append("## FP rate by severity")
    lines.append("")
    lines.append("| Severity | FPs | Total findings | FP rate |")
    lines.append("|---|---|---|---|")
    for sev in SEVERITIES:
        total = totals["by_severity"].get(sev, 0)
        fps = fp_by_severity.get(sev, 0)
        lines.append(f"| {sev} | {fps} | {total} | {fp_rate(fps, total)} |")
    lines.append("")

    # By target type
    lines.append("## FP rate by target type")
    lines.append("")
    lines.append("| Target type | FPs | Total findings | FP rate |")
    lines.append("|---|---|---|---|")
    for tt in TARGET_TYPES:
        total = totals["by_target_type"].get(tt, 0)
        fps = fp_by_target.get(tt, 0)
        lines.append(f"| {tt} | {fps} | {total} | {fp_rate(fps, total)} |")
    lines.append("")

    # By confidence band (calibration)
    lines.append("## Calibration - confidence band vs actual FP rate")
    lines.append("")
    lines.append("| Confidence band | FPs | Total findings | Actual FP rate | Expected FP rate |")
    lines.append("|---|---|---|---|---|")
    expected = {"0-24": "~80%", "25-49": "~55%",
                "50-74": "~35%", "75-100": "~15%"}
    for _, _, label in CONF_BANDS:
        total = totals["by_conf_band"].get(label, 0)
        fps = fp_by_conf_band.get(label, 0)
        lines.append(f"| {label} | {fps} | {total} | {fp_rate(fps, total)} | {expected[label]} |")
    lines.append("")

    # Recent FPs (last 10)
    lines.append("## Recent FPs (last 10)")
    lines.append("")
    recent = sorted(fp_records, key=lambda r: r.get("flagged_at", ""), reverse=True)[:10]
    for rec in recent:
        sev = rec.get("severity", "?")
        conf = rec.get("confidence")
        conf_str = f"conf={conf}" if conf is not None else "conf=?"
        fid = rec.get("finding_id", "?")
        stmt = (rec.get("statement", "") or "")[:120]
        loc = rec.get("location", "")
        target = rec.get("target_type", "")
        sid = rec.get("scrutiny_id", "")
        when = rec.get("flagged_at", "")[:10]
        lines.append(f"- **[{fid}]** {sev} {conf_str} ({target}, {when})  ")
        lines.append(f"  `{sid}` - {loc}")
        lines.append(f"  > {stmt}")
        note = rec.get("ceo_note", "").strip()
        if note:
            lines.append(f"  - CEO note: _{note}_")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_Generated by `scripts/scrutinize-fp-aggregate.py` from `_fp_log.jsonl`. "
                 "To record an FP, use `scripts/scrutinize-flag-fp.py` (the /scrutinize "
                 "skill calls it automatically when the CEO types `flag-as-fp <ids>` "
                 "in the approval block)._")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate /scrutinize FP flags.")
    parser.add_argument("--json", action="store_true",
                        help="Print aggregate as JSON instead of writing the .md.")
    args = parser.parse_args(argv)

    fp_records = load_fp_records()
    totals = count_total_findings()
    print(f"{CYAN}Parsed {len(fp_records)} FP records, "
          f"{totals['total']} total findings across all scrutiny reports.{RESET}",
          file=sys.stderr)

    if args.json:
        out = {
            "fp_records": fp_records,
            "totals": {
                "by_severity": dict(totals["by_severity"]),
                "by_target_type": dict(totals["by_target_type"]),
                "by_conf_band": dict(totals["by_conf_band"]),
                "total": totals["total"],
            },
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    md = render(fp_records, totals)
    AGGREGATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGGREGATE_PATH.write_text(md, encoding="utf-8")
    print(f"{GREEN}Aggregate written: {AGGREGATE_PATH}{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
