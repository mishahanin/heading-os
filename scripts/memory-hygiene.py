#!/usr/bin/env python3
"""memory-hygiene.py - objective-defect detector for the memory ecosystem.

Console-first hygiene loop. Aggregates the mechanically-verifiable defects across
auto-memory + the Odin brain into one dated report, exits non-zero when any
objective defect is present, and NEVER mutates memory. Consolidation (merge,
delete, reword) is judgement and stays with `/dream`; this tool only detects and
reports.

It is a detector, not an iterator: it surfaces objective rot and a human resolves
it. Objective gate (drives the exit code):
  - Odin temporal-validity ERRORS  (dangling / circular `superseded_by`)
  - orphan memory files            (a *.md not referenced from MEMORY.md)
  - MEMORY.md over budget          (> 200 lines)

Advisory (reported, never gates):
  - stale memory files (>45 days)
  - Odin stale seeds, stale positions, orphan principles

Usage:
  python scripts/memory-hygiene.py            # full run, writes report, exit 0/1
  python scripts/memory-hygiene.py --json     # structured result to stdout
  python scripts/memory-hygiene.py --quiet     # one summary line only

Exit codes: 0 clean, 1 objective defect(s) present, 2 script error.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.memory_health import compute_memory_defects  # noqa: E402
from scripts.utils.workspace import (  # noqa: E402
    get_data_root,
    get_default_tz,
    get_outputs_dir,
    get_workspace_root,
    load_env,
)

ROOT = get_workspace_root()
BRAIN_HEALTH = ROOT / "scripts" / "odin-brain-health.py"
COMPILE_TIMEOUT = 120


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def collect_brain_compile() -> dict:
    """Run `odin-brain-health.py --compile`, return its JSON or a degraded note.

    Console-first: never raises on a missing/failing brain. Returns
    {"ok": bool, "data": dict|None, "note": str}.
    """
    if not BRAIN_HEALTH.exists():
        return {"ok": False, "data": None, "note": f"brain-health script not found at {BRAIN_HEALTH}"}
    try:
        proc = subprocess.run(
            [sys.executable, str(BRAIN_HEALTH), "--compile"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=COMPILE_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "data": None, "note": f"compile call failed: {exc}"}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        first = (proc.stderr or proc.stdout or "").strip().splitlines()
        reason = first[0] if first else f"exit {proc.returncode}, no JSON"
        return {"ok": False, "data": None, "note": f"brain unavailable ({reason})"}
    return {"ok": True, "data": data, "note": ""}


def gather() -> dict:
    """Collect both halves and split defects into gate vs advisory."""
    mem_dir = get_data_root() / "auto-memory"
    mem = compute_memory_defects(mem_dir)
    brain = collect_brain_compile()
    bdata = brain["data"] or {}
    temporal = bdata.get("temporal_validity") or {}

    gate = {
        "temporal_errors": temporal.get("errors", []),
        "memory_orphans": mem.get("orphans", []),
        "over_budget": bool(mem.get("over_budget")),
        "memory_md_lines": mem.get("memory_md_lines", 0),
    }
    gate_count = (
        len(gate["temporal_errors"])
        + len(gate["memory_orphans"])
        + (1 if gate["over_budget"] else 0)
    )
    advisory = {
        "stale_memory": mem.get("stale", []),
        "temporal_warnings": temporal.get("warnings", []),
        "stale_seeds": bdata.get("stale_seeds", []),
        "stale_positions": bdata.get("stale_positions", []),
        "orphan_principles": bdata.get("orphan_principles", []),
    }
    return {
        "memory": mem,
        "brain_ok": brain["ok"],
        "brain_note": brain["note"],
        "gate": gate,
        "gate_count": gate_count,
        "advisory": advisory,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def render_report(result: dict, generated_iso: str) -> str:
    mem = result["memory"]
    gate = result["gate"]
    adv = result["advisory"]
    lines: list[str] = []
    lines.append("# Memory Hygiene Report")
    lines.append("")
    lines.append(f"**Generated:** {generated_iso}")
    lines.append(
        f"**Auto-memory:** `{mem['memory_dir']}` "
        f"({mem['file_count']} files, {mem['memory_md_lines']}/200 lines)"
    )
    if result["brain_ok"]:
        lines.append("**Odin brain:** compiled")
    else:
        lines.append(f"**Odin brain:** {result['brain_note']} (brain defects not evaluated this run)")
    lines.append(f"**Objective defects (gate):** {result['gate_count']}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Objective defects (gate)")
    lines.append("")
    lines.append("These are deterministically verifiable. Any present makes this run exit non-zero.")
    lines.append("")

    te = gate["temporal_errors"]
    lines.append(f"### Dangling / circular `superseded_by` (Odin temporal errors): {len(te)}")
    if te:
        for i in te:
            msg = i.get("message") or i.get("detail") or json.dumps(i, default=str)
            where = i.get("file") or i.get("path") or ""
            lines.append(f"- {msg}" + (f" ({where})" if where else ""))
    else:
        lines.append("- none")
    lines.append("")

    mo = gate["memory_orphans"]
    lines.append(f"### Orphan memory files (not linked from MEMORY.md): {len(mo)}")
    if mo:
        for name in mo:
            lines.append(f"- {name}")
    else:
        lines.append("- none")
    lines.append("")

    lines.append(f"### MEMORY.md over budget (>200 lines): {'yes' if gate['over_budget'] else 'no'} ({gate['memory_md_lines']}/200)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Advisory (non-gating)")
    lines.append("")
    lines.append("Age and judgement signals. Reviewed by a human; they never fail the gate.")
    lines.append("")

    sm = adv["stale_memory"]
    lines.append(f"### Stale memory files (>45 days): {len(sm)}")
    for name, age in sm[:30]:
        lines.append(f"- {name} ({age}d)")
    if len(sm) > 30:
        lines.append(f"- ...and {len(sm) - 30} more")
    lines.append("")

    for key, label in (
        ("temporal_warnings", "Odin temporal-validity warnings"),
        ("stale_seeds", "Odin stale seeds"),
        ("stale_positions", "Odin stale positions"),
        ("orphan_principles", "Odin orphan principles"),
    ):
        items = adv[key]
        lines.append(f"### {label}: {len(items)}")
        for it in items[:20]:
            if isinstance(it, dict):
                label_txt = it.get("title") or it.get("file") or it.get("message") or json.dumps(it, default=str)
            else:
                label_txt = str(it)
            lines.append(f"- {label_txt}")
        if len(items) > 20:
            lines.append(f"- ...and {len(items) - 20} more")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "Objective defects are flagged for human resolution. Resolve via `/dream` - "
        "consolidation (merge, delete, reword) is judgement, and this tool never "
        "mutates memory."
    )
    lines.append("")
    return "\n".join(lines)


def write_report(text: str, generated_dt: datetime) -> Path:
    report_dir = get_outputs_dir() / "operations" / "memory-hygiene"
    report_dir.mkdir(parents=True, exist_ok=True)
    today = generated_dt.strftime("%Y-%m-%d")
    path = report_dir / f"{today}_memory-hygiene_report.md"
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else "")
    parser.add_argument("--json", action="store_true", help="Emit the structured result as JSON")
    parser.add_argument("--quiet", action="store_true", help="Print only the one-line summary")
    parser.add_argument("--no-report", action="store_true", help="Do not write the report file (stdout only)")
    args = parser.parse_args()

    try:
        result = gather()
    except Exception as exc:  # noqa: BLE001 - degrade clearly per console-first, never silently swallow
        print(f"{RED}ERROR{RESET}: memory-hygiene failed: {exc}", file=sys.stderr)
        return 2

    now = datetime.now(get_default_tz())
    generated_iso = now.isoformat(timespec="seconds")
    report_text = render_report(result, generated_iso)

    report_path = None
    if not args.no_report:
        report_path = write_report(report_text, now)

    gate_count = result["gate_count"]
    cats = sum(
        1
        for present in (
            result["gate"]["temporal_errors"],
            result["gate"]["memory_orphans"],
            [1] if result["gate"]["over_budget"] else [],
        )
        if present
    )

    if args.json:
        out = dict(result)
        out["report_path"] = str(report_path) if report_path else None
        out["generated"] = generated_iso
        print(json.dumps(out, indent=2, default=str))
    else:
        color = RED if gate_count else GREEN
        summary = (
            f"{color}memory-hygiene: {gate_count} objective defect(s) "
            f"across {cats} categor{'y' if cats == 1 else 'ies'}{RESET}"
        )
        if report_path:
            summary += f" {GRAY}- report: {report_path}{RESET}"
        print(summary)
        if not args.quiet and gate_count:
            g = result["gate"]
            if g["temporal_errors"]:
                print(f"  {RED}-{RESET} {len(g['temporal_errors'])} dangling/circular superseded_by (Odin)")
            if g["memory_orphans"]:
                print(f"  {RED}-{RESET} {len(g['memory_orphans'])} orphan memory file(s) not in MEMORY.md")
            if g["over_budget"]:
                print(f"  {RED}-{RESET} MEMORY.md over budget ({g['memory_md_lines']}/200)")
            print(f"  {GRAY}resolve via /dream (this tool never mutates memory){RESET}")
        if not args.quiet and not result["brain_ok"]:
            print(f"  {YELLOW}note{RESET}: {result['brain_note']}")

    return 1 if gate_count else 0


if __name__ == "__main__":
    sys.exit(main())
