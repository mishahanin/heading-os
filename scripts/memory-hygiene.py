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
  - dangling `[[wikilinks]]` in auto-memory (a dead pointer often carries a dead
    premise; a target cited by several files is a shared one)

Usage:
  python scripts/memory-hygiene.py            # full run, writes report, exit 0/1
  python scripts/memory-hygiene.py --json     # structured result to stdout
  python scripts/memory-hygiene.py --quiet     # one summary line only

Exit codes: 0 clean (or skipped: no data overlay, nothing to scan), 1 objective
defect(s) present, 2 script error OR a refusal to report a pass over a corpus
this run could not read. A pass is only ever printed over memory files that were
actually opened; see `coverage()`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.memory_health import (  # noqa: E402
    compute_memory_defects,
    scan_redundancy,
    scan_volatile_hooks,
    scan_dangling_links,
)
from scripts.utils.workspace import (  # noqa: E402
    data_overlay_present,
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


def _near_dup_threshold() -> float:
    """Read audit.near_dup_threshold from config/memory-index.yaml; fall back to 0.86."""
    try:
        import yaml

        cfg_path = get_workspace_root() / "config" / "memory-index.yaml"
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        val = (cfg.get("audit") or {}).get("near_dup_threshold")
        return float(val) if val is not None else 0.86
    except Exception:
        return 0.86


# An index hook line: "- [Title](target) - trailing text". Deliberately the same
# shape scan_volatile_hooks() matches, so prose paragraphs and bare bullets in
# MEMORY.md (which carry no link target) can never be flagged.
_HOOK_LINE_RE = re.compile(r"^\s*[-*]\s+\[[^\]]+\]\(([^)]+)\)")

# The live-state tells, all three drawn from what the `## Active Threads` block
# actually wrote before 2026-08-20.
_LIVE_STATE_SIGNALS = (
    ("last-touched date", re.compile(r",\s*last\s+\d{4}-\d{2}-\d{2}\b")),
    ("inline status", re.compile(r"\bstatus:\s*\S")),
    ("quiet-until date", re.compile(r"\[quiet until\s+\d{4}-\d{2}-\d{2}\]")),
)


def scan_live_state_rows(memory_dir: Path) -> dict:
    """Advisory: flag MEMORY.md hooks that quote a live status or a live date.

    memory-discipline.md: "A MEMORY.md index line names WHAT a memory is about
    and points to the file; it does NOT quote a live value ... a live deadline, a
    current status." Until 2026-08-20 the `## Active Threads` block broke that on
    every one of its 29 rows ("- active, last 2026-08-19"), and it was the single
    largest thing in a file injected inside the cached prompt prefix at every
    SessionStart: 4,820 of 17,639 chars (27%), rewritten by 66 commits in 30 days.
    It was also measurably stale by then - 30 threads active on disk, 29 listed,
    one of the 29 already closed - which is exactly the failure the rule predicts.

    scan_volatile_hooks() could not see it: that scanner skips `threads/` link
    targets by design and only looks for money values, so the largest violation of
    the rule it enforces went unflagged for months. This check works on the
    PATTERN instead of the target, so it holds for any hook that regrows one.

    The writer is gone. `thread.py` kept re-adding rows through
    ensure_active_threads_section()/add_thread_to_index() until 2026-08-27, when
    both were removed from scripts/utils/threads_lib.py along with the rest of
    the index manager, so nothing in the workspace regrows the shape now.

    Still advisory rather than gating, for a different reason: what remains is a
    HAND-written hook, in a file only the operator edits, and blocking a commit
    over a line in the memory index would stop unrelated work to report a
    convention. `.claude/rules/memory-discipline.md` states the advisory contract.

    READS ONLY; never mutates. Returns:
        {"ok": bool, "flagged": [{"target", "line", "signals"}], "note": str}
    """
    memory_file = Path(memory_dir) / "MEMORY.md"
    if not memory_file.exists():
        return {"ok": True, "flagged": [], "note": "no MEMORY.md"}
    try:
        text = memory_file.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return {"ok": False, "flagged": [], "note": f"unreadable MEMORY.md: {exc}"}

    flagged: list[dict] = []
    for raw in text.splitlines():
        m = _HOOK_LINE_RE.match(raw)
        if not m:
            continue
        signals = [name for name, pat in _LIVE_STATE_SIGNALS if pat.search(raw)]
        if signals:
            flagged.append({"target": m.group(1), "line": raw.strip(), "signals": signals})
    return {
        "ok": True,
        "flagged": flagged,
        "note": f"{len(flagged)} hook(s) quoting live state",
    }


GATE_CATEGORIES = ("orphans", "over_budget", "temporal_errors")


def coverage(memory_dir: Path, mem: dict, *, brain_ok: bool) -> dict:
    """What this run was actually able to READ, separate from what it found.

    The two are different questions and the summary line used to answer only the
    second: "0 objective defect(s) across 0 categories", in green, exit 0. That
    sentence is printed both by a healthy 266-file corpus with nothing wrong in
    it and by a run that opened no file at all, and nothing distinguishes them.
    MEASURED 2026-09-02 with `pathlib.Path.read_text`/`open` counted per run: the
    operator's overlay gave 267 distinct files opened under the memory dir and
    exit 1 on a real orphan, while `HEADING_OS_DATA` pointed at an empty
    directory gave 0 files opened, the identical green line, and exit 0. A bare
    public clone reaches the same place by resolving `get_data_root()` to
    `<root>/examples`, which carries no `auto-memory/` at all.

    `fact_files` is counted off the directory rather than taken from
    `compute_memory_defects()["file_count"]`, which includes `MEMORY.md`: an
    index with no facts under it must read as an empty corpus, not as one file.

    A category counts as EVALUATED only where its input was read:
      - `orphans` needs the fact-file list, so it needs the directory.
      - `over_budget` needs `MEMORY.md`; with no readable index the line count is
        0 and "not over budget" is a verdict over nothing.
      - `temporal_errors` needs the Odin brain compile.
    """
    memory_dir = Path(memory_dir)
    fact_files = (
        [p.name for p in memory_dir.glob("*.md") if p.name != "MEMORY.md" and p.is_file()]
        if memory_dir.is_dir()
        else []
    )
    evaluated = []
    if mem.get("status") == "ok":
        evaluated.append("orphans")
    if mem.get("index_readable"):
        evaluated.append("over_budget")
    if brain_ok:
        evaluated.append("temporal_errors")
    return {
        "memory_dir": str(memory_dir),
        "overlay_present": data_overlay_present(),
        "corpus_status": mem.get("status", "missing"),
        "fact_files": len(fact_files),
        "categories_total": len(GATE_CATEGORIES),
        "categories_evaluated": evaluated,
        "categories_not_evaluated": [c for c in GATE_CATEGORIES if c not in evaluated],
    }


def gather() -> dict:
    """Collect both halves and split defects into gate vs advisory."""
    mem_dir = get_data_root() / "auto-memory"
    mem = compute_memory_defects(mem_dir)
    redundancy = scan_redundancy(mem_dir, threshold=_near_dup_threshold())
    volatile = scan_volatile_hooks(mem_dir)
    live_state = scan_live_state_rows(mem_dir)
    dangling = scan_dangling_links(mem_dir)
    brain = collect_brain_compile()
    bdata = brain["data"] or {}
    temporal = bdata.get("temporal_validity") or {}

    gate = {
        "temporal_errors": temporal.get("errors", []),
        "memory_orphans": mem.get("orphans", []),
        "memory_index_readable": mem.get("index_readable", True),
        "memory_index_problem": mem.get("index_problem", ""),
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
        "coverage": coverage(mem_dir, mem, brain_ok=brain["ok"]),
        "advisory": advisory,
        "redundancy": redundancy,
        "volatile_hooks": volatile,
        "live_state_rows": live_state,
        "dangling_links": dangling,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def render_report(result: dict, generated_iso: str) -> str:
    mem = result["memory"]
    gate = result["gate"]
    adv = result["advisory"]
    redundancy = result["redundancy"]
    volatile = result.get("volatile_hooks", {"flagged": []})
    lines: list[str] = []
    lines.append("# Memory Hygiene Report")
    lines.append("")
    lines.append(f"**Generated:** {generated_iso}")
    lines.append(
        f"**Auto-memory:** `{mem['memory_dir']}` "
        f"({mem['file_count']} files, {mem['memory_md_lines']}/200 lines)"
    )
    cov = result.get("coverage")
    if cov:
        lines.append(
            f"**Coverage:** {cov['fact_files']} memory file(s) read, "
            f"{len(cov['categories_evaluated'])}/{cov['categories_total']} gate "
            f"categories evaluated"
            + (f" (not evaluated: {', '.join(cov['categories_not_evaluated'])})"
               if cov["categories_not_evaluated"] else "")
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
    # Name the state of the index the count was taken against. When MEMORY.md is
    # absent or unreadable every fact file is unreferenced, and the count is
    # right for a reason the reader has to be told, or "3 orphans" reads as three
    # forgotten memories rather than a missing index.
    if not gate.get("memory_index_readable", True):
        lines.append(f"- MEMORY.md was NOT read ({gate.get('memory_index_problem')}), "
                     f"so every fact file counts as unreferenced.")
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
        # NOT "stale positions". Nothing in this pipeline evaluates a
        # `revisit_when` condition -- `odin-brain-health.find_stale_positions`
        # tests the field for truthiness, and 67 of 67 live positions carry one.
        # This report printed "Odin stale positions: 67" to a human, asserting a
        # staleness it never measured. Evaluating the condition is the /odin
        # skill's step, and this report is not that skill.
        ("stale_positions", "Odin positions carrying a revisit condition (not evaluated)"),
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

    lines.append("")
    lines.append("## Redundancy (advisory - not gated)")
    if not redundancy["ok"]:
        lines.append(f"- {redundancy['note']}")
    elif not redundancy["pairs"]:
        lines.append("- no near-duplicate pairs above threshold")
    else:
        for p in redundancy["pairs"]:
            lines.append(f"- {p['a']} <-> {p['b']} (score {p['score']}) - candidate merge; resolve via /dream")
    lines.append("")

    vh = volatile.get("flagged", [])
    vd = volatile.get("flagged_descriptions", [])
    lines.append("## Volatile pointers (advisory - not gated)")
    lines.append("")
    lines.append(
        "A MEMORY.md index hook or a memory's frontmatter `description:` must name "
        "the topic and point to the file, not quote a live money value. Move "
        "volatile figures into the record body (see memory-discipline.md)."
    )
    lines.append("")
    lines.append(f"### Volatile index hooks: {len(vh)}")
    if not vh:
        lines.append("- none")
    else:
        for f in vh:
            lines.append(f"- {f['target']} [{', '.join(f['signals'])}]: {f['line']}")
    lines.append("")
    lines.append(f"### Volatile frontmatter descriptions: {len(vd)}")
    if not vd:
        lines.append("- none")
    else:
        for f in vd:
            lines.append(f"- {f['file']} [{', '.join(f['signals'])}]: {f['description']}")
    lines.append("")

    ls = result.get("live_state_rows", {"flagged": []}).get("flagged", [])
    lines.append(f"### Hooks quoting live status / date: {len(ls)}")
    lines.append("")
    lines.append(
        "A hook that carries `active, last <date>` or an inline `status:` is a "
        "record, not a pointer, and it goes stale the moment the thing it names "
        "moves. The `## Active Threads` block was retired on 2026-08-20 for this "
        "(4,820 of 17,639 chars of a SessionStart-injected file, 29 rows, one of "
        "them already closed on disk). Read the live set with "
        "`python scripts/thread.py list`."
    )
    lines.append("")
    if not ls:
        lines.append("- none")
    else:
        for f in ls:
            lines.append(f"- {f['target']} [{', '.join(f['signals'])}]: {f['line']}")
    lines.append("")

    dangling = result.get("dangling_links", {"flagged": []}).get("flagged", [])
    lines.append("## Dangling links (advisory - not gated)")
    lines.append("")
    lines.append(
        "A `[[wikilink]]` that resolves to no memory file. One is fine - the "
        "convention allows it as a marker for a memory worth writing later. What "
        "is worth reading is the SENTENCE around it: a dead pointer often carries "
        "a dead premise, and a target cited by several files is a shared stale "
        "premise rather than a typo. Repoint at the real record (a file path, a "
        "thread) or write the memory."
    )
    lines.append("")
    lines.append(f"### Dangling targets: {len(dangling)}")
    if not dangling:
        lines.append("- none")
    else:
        for f in dangling:
            lines.append(f"- `{f['target']}` <- {', '.join(f['cited_by'])}")
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
    # `[0]`, not `[1]`. The docstring opens on the same line as its quotes, so
    # line 0 is the summary and line 1 is the blank line under it -- `--help`
    # printed an empty description.
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--json", action="store_true", help="Emit the structured result as JSON")
    parser.add_argument("--quiet", action="store_true", help="Print only the one-line summary")
    parser.add_argument("--no-report", action="store_true", help="Do not write the report file (stdout only)")
    args = parser.parse_args()

    try:
        result = gather()
    except Exception as exc:  # noqa: BLE001 - degrade clearly per console-first, never silently swallow
        print(f"{RED}ERROR{RESET}: memory-hygiene failed: {exc}", file=sys.stderr)
        return 2

    # The floor, BEFORE a report is written. A report headed "0 objective
    # defects" over a corpus nobody opened is the artifact this run exists to
    # not produce, and it outlives the terminal it was printed in.
    cov = result["coverage"]
    n_eval = len(cov["categories_evaluated"])
    if cov["fact_files"] == 0 or n_eval == 0:
        if not cov["overlay_present"]:
            # A bare public clone: `get_data_root()` resolved to the bundled
            # `examples/`, which ships no memory. Nothing is wrong and nothing
            # was checked, and the second half is the half that has to be said
            # out loud (.claude/rules/scope-claims.md). Same shape as
            # `workspace-health.py`'s absent-`templates/` branch: warn, name the
            # zero, return the non-failing code.
            note = (
                f"no private data overlay ({cov['memory_dir']} is not backed by "
                f"one) - 0 memory files read, 0 of {cov['categories_total']} gate "
                f"categories evaluated. NOTHING was checked."
            )
            if args.json:
                print(json.dumps({"status": "skipped", "reason": note,
                                  "coverage": cov}, indent=2, default=str))
            else:
                print(f"{YELLOW}SKIP{RESET}: memory-hygiene: {note}", file=sys.stderr)
            return 0
        # An overlay IS present and the scan still read nothing. That is a
        # setup defect (a moved corpus, a mis-set HEADING_OS_DATA, a wiped
        # directory), never a clean memory store, so it refuses instead of
        # passing -- the shape `validate-crm-schema.py` uses for an empty CRM.
        # Exit 2 = script/setup error, distinct from exit 1 = defects found.
        reason = (
            f"read {cov['fact_files']} memory file(s) from {cov['memory_dir']} "
            f"and evaluated {n_eval} of {cov['categories_total']} gate categories "
            f"(not evaluated: {', '.join(cov['categories_not_evaluated']) or 'none'})."
        )
        if args.json:
            print(json.dumps({"status": "refused", "reason": reason,
                              "coverage": cov}, indent=2, default=str))
        else:
            print(f"{RED}memory-hygiene REFUSES to report a pass: {reason}{RESET}",
                  file=sys.stderr)
            print(f"{RED}A private data overlay is present, so an unread corpus "
                  f"is a setup defect, not a clean one.{RESET}", file=sys.stderr)
        return 2

    now = datetime.now(get_default_tz())
    generated_iso = now.isoformat(timespec="seconds")
    # Inside a guard, like `gather()` above. `render_report` raising on an
    # unexpected shape, or `write_report` meeting a full or read-only disk, used
    # to leave as a traceback and exit 1, and 1 is the code this file's own
    # contract reserves for "objective defect(s) present". An infrastructure
    # failure was therefore indistinguishable from a dirty memory store on the
    # one line a cron reads. The scan already succeeded at this point, so its
    # result is still printed below; only the exit code changes.
    report_error = None
    report_path = None
    try:
        report_text = render_report(result, generated_iso)
        if not args.no_report:
            report_path = write_report(report_text, now)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        report_error = exc
        print(f"{RED}ERROR{RESET}: memory-hygiene scanned the corpus and could "
              f"not produce its report: {exc.__class__.__name__}: {exc}",
              file=sys.stderr)

    gate_count = result["gate_count"]

    if args.json:
        out = dict(result)
        out["report_path"] = str(report_path) if report_path else None
        out["generated"] = generated_iso
        print(json.dumps(out, indent=2, default=str))
    else:
        color = RED if gate_count else GREEN
        # Counts the categories EVALUATED, not the ones with findings. The old
        # line said "across {cats} categories" where `cats` was how many gate
        # categories came back non-empty, so a clean corpus and an unread one
        # both printed "across 0 categories" in green. Same words, opposite
        # facts.
        summary = (
            f"{color}memory-hygiene: {gate_count} objective defect(s) over "
            f"{cov['fact_files']} memory file(s); {n_eval}/{cov['categories_total']} "
            f"gate categories evaluated{RESET}"
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
        if not args.quiet and cov["categories_not_evaluated"]:
            print(f"  {YELLOW}note{RESET}: not evaluated this run: "
                  f"{', '.join(cov['categories_not_evaluated'])}")
        if not args.quiet and not result["brain_ok"]:
            print(f"  {YELLOW}note{RESET}: {result['brain_note']}")
        vol = result.get("volatile_hooks", {})
        vh, vd = vol.get("flagged", []), vol.get("flagged_descriptions", [])
        if not args.quiet and (vh or vd):
            print(
                f"  {YELLOW}advisory{RESET}: {len(vh)} volatile hook(s) + "
                f"{len(vd)} volatile description(s) "
                f"(move live values to the body - see memory-discipline.md)"
            )
        ls = result.get("live_state_rows", {}).get("flagged", [])
        if not args.quiet and ls:
            print(
                f"  {YELLOW}advisory{RESET}: {len(ls)} hook(s) quoting live "
                f"status/date (the retired ## Active Threads shape) - "
                f"read the live set with `python scripts/thread.py list`"
            )

    if report_error is not None:
        print(f"{RED}memory-hygiene exits 2 (script error): the scan finished "
              f"and the report did not. The defect count above stands and is "
              f"not what this exit code is about.{RESET}", file=sys.stderr)
        return 2
    return 1 if gate_count else 0


if __name__ == "__main__":
    sys.exit(main())
