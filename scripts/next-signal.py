#!/usr/bin/env python3
"""Report "what just happened" so /next can recommend the logical next step.

Aggregates four signal sources, newest-first and handoff-weighted, into a compact
recent-actions list:

  1. HANDOFF  -- outputs/operations/handoff-archive/.latest/summary.md (the explicit,
                 human-curated "where were we" pointer; weighted highest).
  2. OUTPUTS  -- the newest files under outputs/ (noise dirs excluded), each mapped back
                 to its producing skill via reference/skill-graph.csv (produces_in).
  3. GIT      -- the last N `git log --oneline` subjects (the feat/fix/chore prefixes name
                 the recent work area).
  4. THREADS  -- active threads/business/ files by mtime (open business loops).

Read-only. No daemon, no browser, no writes. Console-first: run it directly from the
terminal or chat. Exits non-zero with a plain message if outputs/ is unreadable.

Usage:
  python scripts/next-signal.py            # lean text
  python scripts/next-signal.py --json     # structured signal for /next
  python scripts/next-signal.py --limit 12 # widen the outputs scan

Consumed by: /next.
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import skill_graph  # noqa: E402
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import (  # noqa: E402
    get_outputs_dir,
    get_threads_dir,
    get_workspace_root,
)

# ============================================================
# Configuration
# ============================================================

# Directories under outputs/ that are noise for a "what did I just do" signal.
EXCLUDE_DIRS = {"_sync", "_tmp", "browser", "clipboard", "handoff-archive"}
# Specific noise files to skip wherever they appear.
EXCLUDE_FILES = {"_latest-fetch.json"}
# Suffix under the (data-root) outputs dir to the .latest handoff summary.
HANDOFF_LATEST_SUFFIX = "operations/handoff-archive/.latest/summary.md"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# ============================================================
# Signal sources
# ============================================================

def read_handoff() -> dict | None:
    """Parse the .latest handoff summary: source, generated, objective, next steps.

    outputs/ is DATA -> resolved under the data root via get_outputs_dir()."""
    f = get_outputs_dir() / HANDOFF_LATEST_SUFFIX
    if not f.is_file():
        return None
    text = f.read_text(encoding="utf-8")
    out = {"source": "", "generated": "", "objective": "", "next_steps": []}
    section = None
    for line in text.splitlines():
        s = line.strip()
        # Source/Generated live only in the header region (above the first `## `);
        # gating on section keeps a body line that happens to start "Source:" from
        # hijacking the field or stealing the objective's first line.
        if section is None and s.startswith("Source:"):
            out["source"] = s.split(":", 1)[1].strip()
        elif section is None and s.startswith("Generated:"):
            out["generated"] = s.split(":", 1)[1].strip()
        elif s.startswith("## "):
            section = s[3:].strip().lower()
        elif s and section == "objective" and not out["objective"]:
            out["objective"] = s
        elif s and section == "next steps" and (s[0].isdigit() or s.startswith(("-", "*"))):
            # Strip a real ordinal marker (1. / 2) / - / *) only — not every leading
            # digit/dash, which would mangle steps like "3D ..." or "2026-report due".
            out["next_steps"].append(re.sub(r"^\s*(?:\d+[.)]|[-*])\s*", "", s).strip())
    return out


def recent_outputs(limit: int, graph_rows: list[dict]) -> list[dict]:
    """Newest files under outputs/, noise excluded, each mapped to producing skill(s).

    outputs/ is DATA -> resolved under the data root via get_outputs_dir(); display
    paths are kept relative to that data root."""
    base = get_outputs_dir()
    data_root = base.parent
    if not base.is_dir():
        raise FileNotFoundError(f"outputs/ not found at {base}")  # leak-guard: ok (string in a message/log, not a path)
    found: list[tuple[float, str]] = []
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(base).parts
        if any(part in EXCLUDE_DIRS for part in rel_parts):
            continue
        if p.name in EXCLUDE_FILES or p.name.startswith("."):
            continue
        try:
            found.append((p.stat().st_mtime, str(p.relative_to(data_root))))
        except OSError:
            continue
    found.sort(key=lambda t: -t[0])
    out = []
    for mtime, relpath in found[:limit]:
        out.append({
            "path": relpath,
            "mtime": _iso(mtime),
            "skills": skill_graph.by_output_dir(graph_rows, relpath),
        })
    return out


def recent_commits(root: Path, n: int) -> list[str]:
    try:
        r = subprocess.run(
            ["git", "log", "--oneline", "-n", str(n)],
            cwd=str(root), capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def active_threads(limit: int) -> list[dict]:
    """Active (non-archive) business threads by mtime. Business only -- personal threads
    carry a strict CEO-only regime and are not skill-lifecycle signals.

    threads/ is DATA -> resolved under the data root via get_threads_dir()."""
    base = get_threads_dir() / "business"
    if not base.is_dir():
        return []
    items = []
    for p in sorted(base.glob("*.md"), key=lambda x: -x.stat().st_mtime):
        items.append({"slug": p.stem, "mtime": _iso(p.stat().st_mtime)})
        if len(items) >= limit:
            break
    return items


# ============================================================
# Aggregation + render
# ============================================================

def gather(root: Path, limit: int) -> dict:
    """`root` is the ENGINE root, used only for the git log (recent_commits). The
    data sources (handoff, outputs, threads) resolve under the DATA root via their
    own get_*_dir() helpers and ignore `root`."""
    graph_rows = skill_graph.load(skill_graph.default_file())
    return {
        "handoff": read_handoff(),
        "recent_outputs": recent_outputs(limit, graph_rows),
        "recent_commits": recent_commits(root, 8),
        "active_threads": active_threads(5),
    }


def render_text(sig: dict) -> str:
    lines = []
    h = sig.get("handoff")
    if h:
        lines.append(f"{BOLD}Handoff (strongest signal){RESET}")
        if h.get("objective"):
            lines.append(f"  {GREEN}objective:{RESET} {h['objective']}")
        for step in h.get("next_steps", [])[:3]:
            lines.append(f"  {CYAN}next:{RESET} {step}")
        lines.append("")
    if sig.get("recent_outputs"):
        lines.append(f"{BOLD}Recent outputs{RESET}")
        for o in sig["recent_outputs"]:
            who = f" {GRAY}[{'/'.join(o['skills'])}]{RESET}" if o["skills"] else ""
            lines.append(f"  {o['mtime']}  {o['path']}{who}")
        lines.append("")
    if sig.get("recent_commits"):
        lines.append(f"{BOLD}Recent commits{RESET}")
        for c in sig["recent_commits"][:5]:
            lines.append(f"  {GRAY}{c}{RESET}")
        lines.append("")
    if sig.get("active_threads"):
        lines.append(f"{BOLD}Open business threads{RESET}")
        for t in sig["active_threads"]:
            lines.append(f"  {YELLOW}{t['mtime']}{RESET}  {t['slug']}")
    return "\n".join(lines).rstrip()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--json", action="store_true", help="emit the structured signal as JSON")
    p.add_argument("--limit", type=int, default=8, help="how many recent outputs to scan (default 8)")
    p.add_argument("--root", type=Path, default=None, help="workspace root (default: auto-detect)")
    args = p.parse_args(argv)

    root = args.root or get_workspace_root()
    try:
        sig = gather(root, max(1, args.limit))
    except FileNotFoundError as e:
        print(f"next-signal: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(sig, indent=2))
    else:
        print(render_text(sig))
    return 0


if __name__ == "__main__":
    sys.exit(main())
