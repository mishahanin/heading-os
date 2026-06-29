#!/usr/bin/env python3
"""Advisory audit: SKILL.md bash blocks passing bare data-class paths to scripts.

Engine/data separation (CLAUDE.md): every data artifact must resolve under the DATA
root via the get_*_dir() seam. The PreToolUse data-path-redirect hook rewrites
`@outputs/...` for Read/Write/Edit/Grep/Glob tool ops, but NOT for Bash. So a SKILL
that hands a bare `outputs/...` path to a Bash-invoked script (cwd = engine root) can
misroute a write into the engine clone -- the class flagged in auto-memory
`skill-data-paths-need-explicit-resolution`.

This is ADVISORY, not the guarantee. The authoritative, how-agnostic guarantee is
`tests/test_engine_tree_clean.py` (any data artifact landing in the engine clone fails,
regardless of how the write happened). This scanner is the earlier, narrower signal:
it surfaces SKILL bash lines that *could* misroute, so they can be reviewed.

Most current hits are illustrative template paths (YYYY-MM-DD, {sender-slug}) in
documentation examples, not live misroutes -- so the gate is a BASELINE RATCHET: it
fails only when a SKILL gains a NEW bare-data-path bash line beyond the frozen baseline,
catching regressions without forcing churn on existing illustrative examples.

Usage:
  python scripts/audit-skill-bash-paths.py            # list all candidates
  python scripts/audit-skill-bash-paths.py --check    # exit 1 if any skill exceeds baseline
  python scripts/audit-skill-bash-paths.py --json      # machine-readable
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root  # noqa: E402
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET  # noqa: E402

# Data-class top-level dirs (mirror test_engine_tree_clean.DATA_DIRS).
_DATA = re.compile(r"\b(outputs|crm|knowledge|threads|plans|datastore|auto-memory)/")
# Tokens that prove the path was resolved through the seam (not a bare literal).
_RESOLVED = re.compile(r"get_\w+_dir|get_data_root|\$OUTPUTS_DIR|\$DATA_ROOT|\$\(.*get_")
# A bash line only counts if it invokes a script or redirects output.
_COMMAND = re.compile(r"python \S|scripts/|>\s|--out\b|--output\b")

# Frozen baseline of known illustrative candidates (skill -> count), captured
# 2026-06-16. A skill exceeding its baseline (or a new skill appearing) is a
# regression and fails --check. Lowering a baseline (cleaning a SKILL) is welcome;
# update the number here in the same change.
BASELINE = {
    "calibrate": 1,
    "ceo-intel": 2,
    "corporate-letter": 1,
    "dashboard": 1,
    "official-doc": 1,
    "osint": 2,
    "osint-advanced": 2,
    "partnership-doc": 1,
    "proposal": 1,
    "workspace-deep-audit": 2,
    "xpager": 1,
}


def scan_skill(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, stripped_line) candidates inside bash fenced blocks."""
    hits: list[tuple[int, str]] = []
    in_block = False
    cur_bash = False
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("```"):
            if not in_block:
                lang = line.strip().strip("`").lower()
                cur_bash = lang in ("bash", "sh", "shell", "")
            in_block = not in_block
            continue
        if in_block and cur_bash and _COMMAND.search(line):
            if _DATA.search(line) and not _RESOLVED.search(line):
                hits.append((i, line.strip()))
    return hits


def scan_all(root: Path) -> dict[str, list[tuple[int, str]]]:
    out: dict[str, list[tuple[int, str]]] = {}
    for sk in sorted(root.glob(".claude/skills/*/SKILL.md")):
        hits = scan_skill(sk)
        if hits:
            out[sk.parent.name] = hits
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit 1 if a skill exceeds its baseline")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    root = get_workspace_root()
    found = scan_all(root)
    counts = {name: len(h) for name, h in found.items()}

    if args.json:
        print(json.dumps({"counts": counts, "baseline": BASELINE}, indent=2))
    else:
        print(f"{BOLD}{CYAN}SKILL bash data-path audit (advisory){RESET}")
        for name, hits in found.items():
            base = BASELINE.get(name, 0)
            tag = f"{GREEN}=baseline{RESET}" if len(hits) <= base else f"{RED}OVER baseline ({base}){RESET}"
            print(f"\n{BOLD}{name}{RESET} [{len(hits)}] {tag}")
            for ln, text in hits:
                print(f"  {GRAY}{ln}:{RESET} {text[:110]}")
        print(f"\n{GRAY}Authoritative guarantee: tests/test_engine_tree_clean.py. This is advisory.{RESET}")

    # Regression check: any skill over baseline, or a new skill not in baseline.
    regressions = []
    for name, n in counts.items():
        base = BASELINE.get(name)
        if base is None:
            regressions.append(f"{name}: NEW skill with {n} bare-data-path bash line(s) (not in baseline)")
        elif n > base:
            regressions.append(f"{name}: {n} > baseline {base}")

    if args.check:
        if regressions:
            print(f"\n{RED}{BOLD}FAIL{RESET} -- new SKILL bash data-path misroute candidate(s):", file=sys.stderr)
            for r in regressions:
                print(f"  {RED}{r}{RESET}", file=sys.stderr)
            print(f"{YELLOW}Resolve via get_*_dir()/$OUTPUTS_DIR, or update BASELINE if intentional.{RESET}", file=sys.stderr)
            return 1
        print(f"\n{GREEN}OK{RESET} -- no SKILL bash data-path regressions vs baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
