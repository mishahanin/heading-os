#!/usr/bin/env python3
"""`heading` - a thin, vendor-independent dispatcher over the HEADING OS scripts.

Part of the F-10.1 Option C hybrid: plugin-packaged skills call bundled scripts
via ${CLAUDE_PLUGIN_ROOT} (the native happy path), and the SAME scripts are also
reachable through this CLI, which is the stable invocation surface for the
monorepo, the devcontainer, the headless runner (F-10.3), and the fallback if a
Claude Code change ever breaks the plugin path variable.

This is a DISPATCHER, never a fork of script logic. It resolves the workspace
root via scripts/utils/paths.py and shells the target with the current
interpreter.

Invocation. The engine is an installed uv package, so `heading` is a console
script (`[project.scripts]` in pyproject.toml):

  uv run heading run scripts/utils/paths.py
  uv run heading health
  uv run heading list
  uv run heading skill state-check

The module form still works and is what the in-tree tests drive:

  python scripts/heading_cli.py list

`run` takes a path relative to the workspace root (a bare name is resolved under
scripts/). Named subcommands are shortcuts in the registry below. `skill` runs an
allowlisted skill headless via `claude -p`; runner flags (`--budget`, `--model`)
MUST precede the skill name, since everything after the name is passed through to
the skill as its own args.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.paths import get_data_root, get_workspace_root  # noqa: E402

# Stable named shortcuts -> repo-relative script paths. Extend as bundles grow.
REGISTRY = {
    "health": "scripts/workspace-health.py",
    "classification": "scripts/classification-health.py",
}

# --- Headless skill runner (F-10.3) -----------------------------------------

DEFAULT_BUDGET_USD = 0.50

# Default-deny: a skill runs headless only if listed here; the value is its tier.
SKILL_ALLOWLIST = {
    "state-check": "read-only",
    # queue-draft is the reference draft-tier skill: it DEPOSITS a gated card and
    # can never approve/send (the draft tier grants deposit but not the send
    # transports, enforced in build_skill_command + SEND_DENY).
    "queue-draft": "draft",
}

# The --allowedTools set per tier. The read-only tier grants only Read; the draft
# tier adds Write and the action-queue DEPOSIT subcommand (staging a gated card),
# but NEVER approve/send. A tier grants only what it needs, so the send transports
# are unreachable by construction (the allowlist-first send boundary).
TIER_ALLOWED = {
    "read-only": ["Read"],
    "draft": [
        "Read",
        "Write",
        "Bash(python scripts/action-queue.py deposit:*)",
        "Bash(python3 scripts/action-queue.py deposit:*)",
    ],
}

# Defense-in-depth denylist: every outbound transport, blocked regardless of tier.
# The allowlist above is the primary boundary; this is belt-and-suspenders.
# ANY new outbound send transport MUST be added here.
SEND_DENY = [
    "Bash(python scripts/send-email.py:*)",
    "Bash(python3 scripts/send-email.py:*)",
    "Bash(python scripts/action-queue.py approve:*)",
    "Bash(python3 scripts/action-queue.py approve:*)",
]


def build_skill_command(skill, args, *, tier, budget_usd=DEFAULT_BUDGET_USD, model=None):
    """Build the `claude -p` argv for a headless skill run.

    Pure apart from a cheap deterministic data-root resolve (for the --add-dir
    grant). The send boundary lives here: every tier's --allowedTools excludes
    the send transports, and SEND_DENY names them under --disallowedTools.
    """
    prompt = "/" + skill
    if args:
        prompt = prompt + " " + " ".join(args)
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--permission-mode",
        "dontAsk",
        "--max-budget-usd",
        str(budget_usd),
        "--allowedTools",
        *TIER_ALLOWED[tier],
        "--disallowedTools",
        *SEND_DENY,
    ]
    if model:
        cmd += ["--model", model]
    # Grant read access to the data overlay (H1): a skill's inputs can live under
    # the data root, which the data-path-redirect hook rewrites OUTSIDE cwd. Claude
    # limits file tools to cwd + --add-dir, so without this a headless read is denied.
    data_root = get_data_root()
    if data_root != get_workspace_root():
        cmd += ["--add-dir", str(data_root)]
    return cmd


def _resolve(target: str, root: Path) -> Path:
    """Resolve a target to an absolute script path under the workspace root.

    A bare name (no slash) is looked up under scripts/. A relative path is taken
    as-is from the workspace root.
    """
    rel = target if "/" in target else f"scripts/{target}"
    return (root / rel).resolve()


def _dispatch(script_path: Path, args: list[str]) -> int:
    if not script_path.is_file():
        print(f"heading: script not found: {script_path}", file=sys.stderr)
        return 2
    return subprocess.run([sys.executable, str(script_path), *args]).returncode


def run_skill(skill, args, *, budget_usd=DEFAULT_BUDGET_USD, model=None) -> int:
    """Run an allowlisted skill headless via `claude -p`. Exit-code primary.

    Returns 2 (refused: not allowlisted, before any vendor call), 3 (claude
    binary absent, degrade clearly), or the `claude` process exit code otherwise.
    """
    if skill not in SKILL_ALLOWLIST:
        print(
            f"heading: skill not allowlisted for headless run: {skill}",
            file=sys.stderr,
        )
        return 2
    if shutil.which("claude") is None:
        print(
            "heading: claude binary not found on PATH (headless skill run needs it)",
            file=sys.stderr,
        )
        return 3
    cmd = build_skill_command(
        skill, args, tier=SKILL_ALLOWLIST[skill], budget_usd=budget_usd, model=model
    )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # Exit code is authoritative; the JSON envelope is best-effort enrichment.
    out = proc.stdout
    try:
        payload = json.loads(out)
        if isinstance(payload, dict) and payload.get("result"):
            out = payload["result"]
    except (json.JSONDecodeError, ValueError):
        pass  # undocumented / non-JSON output: fall back to raw stdout
    if out:
        print(out)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    return proc.returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="heading",
        description="Thin dispatcher over HEADING OS scripts (F-10.1 hybrid).",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run a script by path (relative to workspace root)")
    p_run.add_argument("script", help="e.g. scripts/utils/paths.py or a bare name under scripts/")
    p_run.add_argument("args", nargs=argparse.REMAINDER, help="arguments passed through")

    p_skill = sub.add_parser(
        "skill",
        help="run an allowlisted skill headless via `claude -p` (runner flags precede the skill name)",
    )
    # Runner flags are declared before the positional `name` and MUST be passed
    # before it: argparse.REMAINDER greedily captures every token after `name`
    # (option-looking tokens included) as the skill's own passthrough args.
    p_skill.add_argument(
        "--budget", type=float, default=DEFAULT_BUDGET_USD,
        help="max USD for the run (default 0.50); MUST precede the skill name",
    )
    p_skill.add_argument(
        "--model", default=None, help="model override; MUST precede the skill name",
    )
    p_skill.add_argument("name", help="the allowlisted skill to run, e.g. state-check")
    p_skill.add_argument(
        "args", nargs=argparse.REMAINDER,
        help="args passed through to the skill (everything after the name)",
    )

    sub.add_parser("list", help="list the named shortcuts")
    for name in REGISTRY:
        sp = sub.add_parser(name, help=f"shortcut for {REGISTRY[name]}")
        sp.add_argument("args", nargs=argparse.REMAINDER, help="arguments passed through")

    args = ap.parse_args(argv)
    root = get_workspace_root()

    if args.command == "list":
        for name, path in REGISTRY.items():
            print(f"{name}\t{path}")
        return 0
    if args.command == "skill":
        return run_skill(args.name, args.args, budget_usd=args.budget, model=args.model)
    if args.command == "run":
        return _dispatch(_resolve(args.script, root), args.args)
    # Named shortcut.
    return _dispatch(_resolve(REGISTRY[args.command], root), args.args)


if __name__ == "__main__":
    raise SystemExit(main())
