#!/usr/bin/env python3
"""Advisory audit: engine prose naming repo paths that do not exist.

Documentation rot is silent. A rule, skill or doc page names `scripts/foo.py`,
the script is later renamed or deleted, and the prose keeps pointing at nothing.
Nobody notices until someone pastes the command and it fails. On 2026-08-21 a
sweep found eight such sites that had accumulated over months, including a
`/odin` source-ingest command that could not run and a `docs/SECURITY-MODEL.md`
paragraph describing two hook files deleted in ba1affd.

Scope, precisely (`.claude/rules/scope-claims.md`): this checks paths that the
routing map classifies as **engine**, named in **git-tracked Markdown**, against
the **engine working tree**. It says nothing about the private data overlay -- a
path routing `private` or `corporate` is skipped, because the overlay is absent
on a public clone and its absence is not evidence. It also says nothing about
paths named in Python, YAML or JSON; those have their own callers and tests.

Extraction is a regex over prose, so it is heuristic in one direction only: it
can MISS a path (an unusual spelling), and it can capture a fragment that was
never a path (`action_queue.append(` truncates to `action_queue.appen`). Both
classes live in BASELINE with a stated reason rather than being silently
filtered, so the list of what this tool ignores is readable.

BASELINE is a frozen ratchet, the same shape as `audit-skill-bash-paths.py`: a
dangling path already listed is tolerated, a NEW one fails `--check`. Removing
an entry (cleaning the prose) is welcome -- delete the line in the same change.

Usage:
  python scripts/check-path-references.py           # list every dangling reference
  python scripts/check-path-references.py --check   # exit 1 on any NEW one
  python scripts/check-path-references.py --json    # machine-readable
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root, get_routing_destination  # noqa: E402
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET  # noqa: E402

# Repo-relative paths, anchored on the top-level directories the engine owns.
# The trailing class refuses a bare trailing dot so `foo.py.` yields `foo.py`.
_PATH = re.compile(
    r"(?<![\w/.-])"
    r"((?:scripts|config|docs|reference|tests|examples|templates|\.claude|\.github)"
    r"/[A-Za-z0-9_./-]*[A-Za-z0-9_-]\.[a-z]{2,5})"
)

# History by design: a changelog names what a release removed. Never rot.
_SKIP_FILES = {"CHANGELOG.md"}

# Frozen 2026-08-21, after the eight real rot sites were fixed. Every entry is a
# path that does not exist and SHOULD NOT: a placeholder, a regex fragment, or
# correct prose about something deleted or not yet built. Reason is mandatory --
# an entry without one is indistinguishable from rot somebody gave up on.
BASELINE = {
    # Regex fragments: a sentence continued past what looked like a path.
    "scripts/bridge_daemon/sources/action_queue.appen": "fragment of `.append(`",
    "scripts/scrutinize-dispatch.assig": "fragment of `.assign`",
    "scripts/scrutinize-dispatch.swap": "prose, not a filename",
    "scripts/utils/tool_risk.tier": "fragment of `tool_risk.tier_for()`",
    ".claude/settings.local": "fragment of `.claude/settings.local.json`",
    "scripts/install-...-timer.sh": "ellipsis naming a family of installers",
    # Deliberate placeholders in templates and naming-convention examples.
    "scripts/models/product.py": "example file in the plan template",
    "scripts/models/user.py": "example file in the plan template",
    "tests/test_integration.py": "example file in the plan template",
    "scripts/rule-regression-runner.py": "named as to-be-built when the first rule artefact lands",
    "scripts/name.py": "stands for `scripts/<name>.py` in the naming rule",
    "scripts/dashboard.py": "hypothetical finding in the scrutinize eval template",
    "reference/playbook-gcc.md": "example of the per-region playbook naming",
    "config/skill-custom/deep-think.user.toml": "file the operator creates, not shipped",
    ".claude/skills/pptx-generator/.tmp/gen.py": "temp file the skill writes at runtime",
    "scripts/linkedin_archive.py": "quoted error message demonstrating a snake_case failure",
    # Correct prose about things deleted, retired, or not yet built.
    "scripts/export-sync.py": "named as archived",
    "scripts/workspace-sync.py": "named as now-deleted",
    ".claude/rules/secure-projects.md": "named as gone, superseded by SENSITIVE_MODE",
    ".claude/rules/vpn-preflight.md": "named as the path this file moved from",
    "config/classification.json": "named as replaced by routing-map.yaml",
    "scripts/telegram_client.py": "named as the WRONG path, to warn against it",
}


def tracked_markdown(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.split()
    return [f for f in out if f not in _SKIP_FILES]


def scan(root: Path) -> dict[str, list[tuple[str, int]]]:
    """Return {dangling engine-routed path: [(file, lineno), ...]}."""
    hits: dict[str, list[tuple[str, int]]] = {}
    for rel in tracked_markdown(root):
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"{YELLOW}skipped {rel}: {exc}{RESET}", file=sys.stderr)
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for path in _PATH.findall(line):
                if (root / path).exists():
                    continue
                if get_routing_destination(path) != "engine":
                    continue  # lives in an overlay this tool cannot see
                hits.setdefault(path, []).append((rel, lineno))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit 1 on a dangling path not in BASELINE")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    root = get_workspace_root()
    found = scan(root)
    new = {p: sites for p, sites in found.items() if p not in BASELINE}

    if args.json:
        print(json.dumps(
            {"dangling": dict(sorted(found.items())),
             "new": sorted(new),
             "baseline": BASELINE},
            indent=2,
        ))
    else:
        print(f"{BOLD}{CYAN}Engine prose path references (advisory){RESET}")
        print(f"{GRAY}Engine-routed paths in tracked Markdown, checked against the engine tree.")
        print(f"Overlay-routed paths are not checked -- the overlay may be absent.{RESET}\n")
        for path, sites in sorted(found.items()):
            reason = BASELINE.get(path)
            tag = f"{GREEN}baseline: {reason}{RESET}" if reason else f"{RED}NEW{RESET}"
            print(f"{BOLD}{path}{RESET} [{len(sites)}] {tag}")
            for rel, lineno in sites[:5]:
                print(f"  {GRAY}{rel}:{lineno}{RESET}")
        stale = sorted(p for p in BASELINE if p not in found)
        if stale:
            print(f"\n{YELLOW}Baseline entries no longer found (drop them):{RESET}")
            for p in stale:
                print(f"  {p}")

    if args.check:
        if new:
            print(f"\n{RED}{BOLD}FAIL{RESET} -- prose names {len(new)} path(s) that do not exist:",
                  file=sys.stderr)
            for path, sites in sorted(new.items()):
                where = ", ".join(f"{r}:{n}" for r, n in sites[:3])
                print(f"  {RED}{path}{RESET}  ({where})", file=sys.stderr)
            print(f"{YELLOW}Fix the path, or add it to BASELINE with the reason it should not exist.{RESET}",
                  file=sys.stderr)
            return 1
        print(f"\n{GREEN}OK{RESET} -- no new dangling path references.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
