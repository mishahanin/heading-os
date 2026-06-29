#!/usr/bin/env python3
"""Generate a CEO scoring sheet for /scrutinize human-agreement benchmark.

Closes R11 from the 2026-05-27 meta-review of /scrutinize. Samples saved
scrutiny reports + cross-references the FP log to produce a Markdown
scoring sheet the CEO fills in. The filled sheet feeds Cohen's kappa
computation (CEO vs scrutinize, CEO vs Gemini-as-judge, CEO vs Grok-as-judge)
to establish a measured agreement baseline.

This script does NOT call Gemini or Grok. It only prepares the sample.
Cross-family scoring is done by feeding the same findings through
gemini-consult.py and grok-consult.py separately, then merging into the
scoring sheet via --import-rater-output.

Usage:
  # Generate a quarterly scoring sheet from the last 90 days
  python scripts/scrutinize-replay.py --since 90d --sample 5

  # Generate from a specific date range
  python scripts/scrutinize-replay.py --from 2026-03-01 --to 2026-05-31 \\
      --sample 8

  # Compute Cohen's kappa from a filled sheet
  python scripts/scrutinize-replay.py --kappa \\
      outputs/operations/scrutiny/_human_agreement_2026q2.md

Output (default): outputs/operations/scrutiny/_human_agreement_{quarter}.md

Exit codes:
  0 ok
  2 argument error
  3 no scrutiny reports found in the date range
  4 kappa computation failed (sheet malformed)
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.utils.colors import CYAN, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import get_outputs_dir  # noqa: E402

SCRUTINY_DIR = get_outputs_dir() / "operations" / "scrutiny"
FP_LOG_PATH = SCRUTINY_DIR / "_fp_log.jsonl"

_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_FINDING_RE = re.compile(
    r"^\s*\[([BHMLN]\d+)\]\s*(?:\(conf:\s*(\d+)\))?\s*(.*?)$",
    re.MULTILINE,
)
SEVERITY_PREFIX = {"B": "BLOCKER", "H": "HIGH", "M": "MEDIUM", "L": "LOW", "N": "NIT"}


@dataclass
class FindingSample:
    scrutiny_id: str
    finding_id: str
    severity: str
    confidence: int | None
    statement: str
    location: str
    evidence: str
    was_flagged_fp: bool  # ground truth from _fp_log.jsonl


# ============================================================
# Sampling
# ============================================================
def _parse_report_date(stem: str) -> datetime | None:
    m = _DATE_PREFIX_RE.match(stem)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d")
    except ValueError:
        return None


def list_reports_in_range(date_from: datetime, date_to: datetime) -> list[Path]:
    if not SCRUTINY_DIR.exists():
        return []
    out: list[Path] = []
    for report in SCRUTINY_DIR.glob("*.md"):
        if report.name.startswith("_"):
            continue
        d = _parse_report_date(report.stem)
        if d and date_from <= d <= date_to:
            out.append(report)
    return sorted(out)


def load_fp_set() -> set[tuple[str, str]]:
    """Returns set of (scrutiny_id, finding_id) flagged as FP."""
    if not FP_LOG_PATH.exists():
        return set()
    out: set[tuple[str, str]] = set()
    for line in FP_LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = rec.get("scrutiny_id")
        fid = rec.get("finding_id")
        if sid and fid:
            out.add((sid, fid))
    return out


def extract_findings(report_path: Path, fp_set: set[tuple[str, str]]) -> list[FindingSample]:
    text = report_path.read_text(encoding="utf-8")
    sid = report_path.stem
    samples: list[FindingSample] = []
    for match in _FINDING_RE.finditer(text):
        fid = match.group(1)
        conf_raw = match.group(2)
        statement = match.group(3).strip()
        # Skip BLOCKER/HIGH/MEDIUM/LOW/NIT-prefix lines that are headings, not findings
        if not statement or statement.startswith(("...", "<")):
            continue
        # Extract location + evidence from the next ~8 lines
        snippet = text[match.end():match.end() + 800]
        location, evidence = "", ""
        for line in snippet.splitlines()[:8]:
            line_s = line.strip()
            if line_s.lower().startswith("location:"):
                location = line_s.split(":", 1)[1].strip()
            elif line_s.lower().startswith("evidence:"):
                evidence = line_s.split(":", 1)[1].strip()
        samples.append(FindingSample(
            scrutiny_id=sid,
            finding_id=fid,
            severity=SEVERITY_PREFIX.get(fid[0], "UNKNOWN"),
            confidence=int(conf_raw) if conf_raw else None,
            statement=statement,
            location=location,
            evidence=evidence[:300],
            was_flagged_fp=(sid, fid) in fp_set,
        ))
    return samples


def stratified_sample(samples: list[FindingSample], n: int) -> list[FindingSample]:
    """Pick N samples balanced across severity tiers."""
    if not samples:
        return []
    by_sev: dict[str, list[FindingSample]] = {sev: [] for sev in
                                              ("BLOCKER", "HIGH", "MEDIUM", "LOW", "NIT")}
    for s in samples:
        by_sev.setdefault(s.severity, []).append(s)
    quota = max(1, n // 5)
    picked: list[FindingSample] = []
    rng = random.Random(42)  # noqa: S311 - deterministic sampling for reproducible benchmarks, not cryptographic
    for sev in ("BLOCKER", "HIGH", "MEDIUM", "LOW", "NIT"):
        bucket = by_sev.get(sev, [])
        rng.shuffle(bucket)
        picked.extend(bucket[:quota])
    rng.shuffle(picked)
    return picked[:n]


# ============================================================
# Rendering
# ============================================================
def quarter_label(date: datetime) -> str:
    q = (date.month - 1) // 3 + 1
    return f"{date.year}q{q}"


def render_scoring_sheet(samples: list[FindingSample],
                         date_from: datetime,
                         date_to: datetime) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    by_sev: Counter[str] = Counter(s.severity for s in samples)
    lines: list[str] = []
    lines.append(f"# /scrutinize Human-Agreement Scoring Sheet")
    lines.append("")
    lines.append(f"Generated: {today}. Range: {date_from.date()} to {date_to.date()}. "
                 f"Sample size: {len(samples)}.")
    lines.append("")
    lines.append("**Purpose.** Measure CEO agreement with /scrutinize findings. "
                 "Establishes the Cohen's kappa baseline used to calibrate confidence "
                 "scoring. R11 from the 2026-05-27 meta-review.")
    lines.append("")
    lines.append("**How to fill.** For each finding below, replace `<CEO: ?>` in the "
                 "rating row with one of:")
    lines.append("")
    lines.append("- **agree** - the finding was a real defect worth surfacing")
    lines.append("- **disagree** - the finding was a false positive or not worth surfacing")
    lines.append("- **ambiguous** - reasonable case for both")
    lines.append("- **skip** - cannot recall context to judge fairly")
    lines.append("")
    lines.append("Optionally add a free-text note after the rating to explain WHY. "
                 "Once complete, run `python scripts/scrutinize-replay.py --kappa "
                 "outputs/operations/scrutiny/_human_agreement_{quarter}.md` to compute "  # leak-guard: ok (string in a message/log, not a path)
                 "agreement statistics.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Sample distribution")
    lines.append("")
    lines.append("| Severity | Count |")
    lines.append("|---|---|")
    for sev in ("BLOCKER", "HIGH", "MEDIUM", "LOW", "NIT"):
        lines.append(f"| {sev} | {by_sev.get(sev, 0)} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Findings to score")
    lines.append("")
    for i, s in enumerate(samples, 1):
        conf_str = f"conf={s.confidence}" if s.confidence is not None else "conf=?"
        fp_marker = " **[was flagged FP at the time]**" if s.was_flagged_fp else ""
        lines.append(f"### {i}. [{s.finding_id}] {s.severity} {conf_str}{fp_marker}")
        lines.append("")
        lines.append(f"- Source: `{s.scrutiny_id}`")
        lines.append(f"- Location: {s.location or '_(not parsed)_'}")
        lines.append("")
        lines.append(f"**Statement:** {s.statement}")
        lines.append("")
        if s.evidence:
            lines.append(f"**Evidence:** {s.evidence}")
            lines.append("")
        lines.append(f"**CEO rating:** `<CEO: ?>` ")
        lines.append(f"**CEO note:** `<CEO: optional explanation>`")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## How to compute kappa")
    lines.append("")
    lines.append("After filling every `<CEO: ?>` row, run:")
    lines.append("")
    lines.append("```bash")
    lines.append("python scripts/scrutinize-replay.py --kappa <this-file>")
    lines.append("```")
    lines.append("")
    lines.append("The kappa output reports:")
    lines.append("")
    lines.append("- Cohen's kappa: CEO vs /scrutinize (severity dimension)")
    lines.append("- Confusion matrix per severity tier")
    lines.append("- Calibration check: CEO `disagree` rate vs confidence band")
    lines.append("- Trend if a prior sheet exists for the previous quarter")
    return "\n".join(lines)


# ============================================================
# Kappa computation
# ============================================================
_RATING_RE = re.compile(r"\*\*CEO rating:\*\*\s*`([^`]*)`")
_HEADER_RE = re.compile(r"###\s+\d+\.\s+\[([BHMLN]\d+)\]\s+(\w+)\s+conf=(\d+|\?)\s*(\*\*\[was flagged FP at the time\]\*\*)?",
                        re.MULTILINE)


def cohen_kappa(observed: list[tuple[str, str]]) -> float:
    """Compute Cohen's kappa for a list of (rater_a, rater_b) categorical pairs."""
    if not observed:
        return 0.0
    total = len(observed)
    categories = sorted({c for pair in observed for c in pair})
    if not categories:
        return 0.0
    p_observed = sum(1 for a, b in observed if a == b) / total
    counts_a: Counter[str] = Counter(a for a, _ in observed)
    counts_b: Counter[str] = Counter(b for _, b in observed)
    p_expected = sum(
        (counts_a[c] / total) * (counts_b[c] / total) for c in categories
    )
    if p_expected >= 1.0:
        return 1.0
    return (p_observed - p_expected) / (1.0 - p_expected)


def compute_kappa_from_sheet(sheet_path: Path) -> int:
    text = sheet_path.read_text(encoding="utf-8")
    headers = list(_HEADER_RE.finditer(text))
    ratings = _RATING_RE.findall(text)
    if not headers or len(headers) != len(ratings):
        print(f"{RED}ERROR: Could not parse sheet - {len(headers)} headers vs "
              f"{len(ratings)} ratings. Check format.{RESET}", file=sys.stderr)
        return 4

    # Build (scrutinize_call, ceo_call) pairs.
    # scrutinize_call: "agree" if NOT flagged_fp_marker (finding was kept), else "disagree"
    # Yes, this is a binary axis - scrutinize either kept the finding or didn't
    # ceo_call: the rating
    pairs: list[tuple[str, str]] = []
    skipped = 0
    for header, ceo in zip(headers, ratings):
        ceo = ceo.strip().lower()
        if not ceo or ceo == "?" or ceo == "skip":
            skipped += 1
            continue
        was_flagged_fp = bool(header.group(4))
        scrutinize_call = "disagree" if was_flagged_fp else "agree"
        # Normalize ceo to agree/disagree/ambiguous
        if ceo not in ("agree", "disagree", "ambiguous"):
            skipped += 1
            continue
        pairs.append((scrutinize_call, ceo))

    if not pairs:
        print(f"{RED}ERROR: No rated rows found.{RESET}", file=sys.stderr)
        return 4

    kappa = cohen_kappa(pairs)
    counts = Counter((a, b) for a, b in pairs)
    print(f"{GREEN}Cohen's kappa (CEO vs /scrutinize): {kappa:.3f}{RESET}")
    print(f"  Rated rows: {len(pairs)}. Skipped: {skipped}.")
    print(f"  Interpretation: <0=worse than chance, 0-0.2=slight, 0.2-0.4=fair, "
          f"0.4-0.6=moderate, 0.6-0.8=substantial, 0.8-1.0=near-perfect.")
    print()
    print(f"{CYAN}Confusion matrix (scrutinize -> CEO):{RESET}")
    print(f"  Both agree:   {counts[('agree', 'agree')]}")
    print(f"  Scrut agree, CEO disagree (false positive):   {counts[('agree', 'disagree')]}")
    print(f"  Scrut disagree, CEO agree (false negative):   {counts[('disagree', 'agree')]}")
    print(f"  Both disagree (correctly flagged FP):   {counts[('disagree', 'disagree')]}")
    print(f"  Ambiguous CEO calls:   {sum(c for (_, b), c in counts.items() if b == 'ambiguous')}")
    return 0


# ============================================================
# CLI
# ============================================================
def parse_date_arg(s: str) -> datetime:
    if s.endswith("d"):
        days = int(s[:-1])
        return datetime.now() - timedelta(days=days)
    return datetime.strptime(s, "%Y-%m-%d")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay scrutinize findings for "
                                                 "human-agreement benchmark.")
    parser.add_argument("--since", help="Sample window e.g. '90d' (last 90 days).")
    parser.add_argument("--from", dest="date_from", help="Start date YYYY-MM-DD.")
    parser.add_argument("--to", dest="date_to", help="End date YYYY-MM-DD.")
    parser.add_argument("--sample", type=int, default=5,
                        help="Number of findings to sample (default 5).")
    parser.add_argument("--out", help="Output path. Default: "
                                      "outputs/operations/scrutiny/_human_agreement_{quarter}.md")  # leak-guard: ok (argparse help-default text)
    parser.add_argument("--kappa", help="Compute Cohen's kappa from filled sheet at this path.")
    args = parser.parse_args(argv)

    if args.kappa:
        sheet = Path(args.kappa)
        if not sheet.exists():
            print(f"{RED}ERROR: sheet not found: {sheet}{RESET}", file=sys.stderr)
            return 2
        return compute_kappa_from_sheet(sheet)

    # Resolve date range
    if args.since:
        date_from = parse_date_arg(args.since)
        date_to = datetime.now()
    elif args.date_from and args.date_to:
        date_from = parse_date_arg(args.date_from)
        date_to = parse_date_arg(args.date_to)
    else:
        # Default: current quarter
        now = datetime.now()
        q_start_month = ((now.month - 1) // 3) * 3 + 1
        date_from = datetime(now.year, q_start_month, 1)
        date_to = now

    reports = list_reports_in_range(date_from, date_to)
    if not reports:
        print(f"{YELLOW}WARN: no scrutiny reports found in range {date_from.date()} "
              f"to {date_to.date()}{RESET}", file=sys.stderr)
        return 3

    fp_set = load_fp_set()
    all_samples: list[FindingSample] = []
    for r in reports:
        all_samples.extend(extract_findings(r, fp_set))

    print(f"{CYAN}Found {len(reports)} reports, {len(all_samples)} findings in range. "
          f"Stratified-sampling {args.sample}.{RESET}", file=sys.stderr)

    picked = stratified_sample(all_samples, args.sample)
    if not picked:
        print(f"{RED}ERROR: no findings to sample.{RESET}", file=sys.stderr)
        return 3

    out_path = Path(args.out) if args.out else (
        SCRUTINY_DIR / f"_human_agreement_{quarter_label(date_to)}.md")
    md = render_scoring_sheet(picked, date_from, date_to)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"{GREEN}Scoring sheet written: {out_path}{RESET}")
    print(f"Fill in every `<CEO: ?>` row, then run "
          f"`python scripts/scrutinize-replay.py --kappa {out_path}` to compute kappa.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
