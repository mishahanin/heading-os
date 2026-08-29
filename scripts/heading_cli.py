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

Tests: tests/test_a_budget_that_was_declared_and_never_spent.py, tests/test_heading_cli.py

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
from pathlib import Path, PurePosixPath, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.paths import get_data_root, get_workspace_root  # noqa: E402

# Stable named shortcuts -> repo-relative script paths. Extend as bundles grow.
REGISTRY = {
    "health": "scripts/workspace-health.py",
    "classification": "scripts/classification-health.py",
}

# --- Headless skill runner (F-10.3) -----------------------------------------

DEFAULT_BUDGET_USD = 0.50

# The "propose" tier's write grant is scoped to this one proposal-output
# directory, and its deny is scoped to the sensitive brain directory --
# both resolved absolute (//-anchored) from the data root at call time, since
# the headless process's cwd is the engine workspace root, not the data root.
# leak-guard: ok (data-root-relative fragment, not a hardcoded absolute path;
# the data root comes from the get_data_root() seam in _abs_pattern below).
PROPOSE_WRITE_REL = "outputs/operations/odin-reflect-proposals"  # leak-guard: ok
ODIN_BRAIN_DENY_REL = "knowledge/odin-brain"  # leak-guard: ok
# A first-cut default (4x the runner's normal $0.50) for a real reflect-cluster-
# drafting pass -- tune after observing real costs (plan Open Question 2).
PROPOSE_DEFAULT_BUDGET_USD = 2.00

# Per-tier budget defaults, applied when the operator did not pass --budget.
# This table is why the constant above is not decoration: until 2026-08-25
# nothing referenced it, `--budget` defaulted to DEFAULT_BUDGET_USD for every
# tier, and the one propose-tier skill therefore ran at a quarter of the
# budget its own comment declared. A number that describes an intention nobody
# reads is worse than no number: the comment made the cost gate look decided.
TIER_DEFAULT_BUDGET_USD = {
    "propose": PROPOSE_DEFAULT_BUDGET_USD,
}

# Default-deny: a skill runs headless only if listed here; the value names its
# tier and, optionally, the exact leading args it is allowed to run with
# (`args_prefix`) -- a skill with no args_prefix is allowed with any args.
SKILL_ALLOWLIST = {
    "state-check": {"tier": "read-only"},
    # queue-draft is the reference draft-tier skill: it DEPOSITS a gated card and
    # can never approve/send (the draft tier grants deposit but not the send
    # transports, enforced in build_skill_command + SEND_DENY).
    "queue-draft": {"tier": "draft"},
    # odin is allowlisted for exactly ONE invocation shape: `reflect --propose`.
    # Every other Odin mode (learn, log, teach, collect, consult, compile,
    # skill-proposal, and reflect WITHOUT --propose) is refused before any
    # vendor call -- the args_prefix gate is what makes the tier narrow in
    # practice, not just at the tool-permission layer.
    "odin": {"tier": "propose", "args_prefix": ["reflect", "--propose"]},
}

# The --allowedTools set per tier. The read-only tier grants only Read; the draft
# tier adds Write and the action-queue DEPOSIT subcommand (staging a gated card),
# but NEVER approve/send. The propose tier grants only Read here -- its Write
# grant is a single path-scoped Edit(...) pattern appended dynamically in
# build_skill_command (it depends on the data root, resolved at call time).
# A tier grants only what it needs, so the send transports are unreachable by
# construction (the allowlist-first send boundary).
TIER_ALLOWED = {
    "read-only": ["Read"],
    "draft": [
        "Read",
        "Write",
        "Bash(python scripts/action-queue.py deposit:*)",
        "Bash(python3 scripts/action-queue.py deposit:*)",
    ],
    "propose": ["Read"],
}

# Defense-in-depth denylist: every outbound transport, blocked regardless of tier.
# The allowlist above is the primary boundary; this is belt-and-suspenders.
#
# Do NOT maintain this list by hand alone. The whole-script entries below are
# held against a sweep of scripts/*.py in
# tests/test_two_controls_that_measured_themselves.py, which recomputes the set
# from the scripts' own source (a transport library plus one of its send verbs,
# or a bare *.py literal naming such a script in a module that can spawn it).
# The list and the sweep must agree exactly, in both directions, so a new
# transport lands here or the suite fails. `gmail-send.py` landed 2026-08-08
# and was absent from this list until 2026-08-29, because the only tests over
# it read the constant back to itself and could not notice an omission.
SEND_DENY = [
    # Direct transports: these call an outbound mail API themselves.
    "Bash(python scripts/send-email.py:*)",
    "Bash(python3 scripts/send-email.py:*)",
    "Bash(python scripts/gmail-send.py:*)",
    "Bash(python3 scripts/gmail-send.py:*)",
    # One process hop from a direct transport: each spawns send-email.py.
    "Bash(python scripts/action-queue-execute.py:*)",
    "Bash(python3 scripts/action-queue-execute.py:*)",
    "Bash(python scripts/fireside-bot.py:*)",
    "Bash(python3 scripts/fireside-bot.py:*)",
    # Subcommand-scoped, not whole-script: `list`/`show`/`dismiss` stay usable,
    # `approve` is the synchronous send and is the one verb that must not run.
    "Bash(python scripts/action-queue.py approve:*)",
    "Bash(python3 scripts/action-queue.py approve:*)",
]


def _abs_pattern(root: Path, rel: str) -> str:
    """Build a `//`-anchored (filesystem-root-absolute) Edit(...) permission
    pattern for `root / rel`, e.g. Edit(//home/.../knowledge/odin-brain/**).

    Confirmed against code.claude.com/docs/en/permissions.md: `//` anchors an
    absolute path (Read(//Users/alice/secrets/**) matches /Users/alice/secrets/**)
    -- the resolved path's own leading `/` must be stripped first, since
    interpolating it directly after `//` produces a three-slash string
    (`Edit(///...)`) that matches nothing.
    """
    return f"Edit(//{str((root / rel).resolve()).lstrip('/')}/**)"


def build_skill_command(skill, args, *, tier, budget_usd=DEFAULT_BUDGET_USD, model=None):
    """Build the `claude -p` argv for a headless skill run.

    Pure apart from a cheap deterministic data-root resolve (for the --add-dir
    grant and the propose tier's path-scoped Edit(...) patterns). The send
    boundary lives here: every tier's --allowedTools excludes the send
    transports, and SEND_DENY names them under --disallowedTools.
    """
    prompt = "/" + skill
    if args:
        prompt = prompt + " " + " ".join(args)
    data_root = get_data_root()
    # Fresh copies -- never bind directly to TIER_ALLOWED[tier]/SEND_DENY
    # themselves, since a later .append() on an uncopied reference would
    # permanently grow the shared module-level constant across every
    # subsequent call in the same process.
    allowed = list(TIER_ALLOWED[tier])
    disallowed = list(SEND_DENY)
    if tier == "propose":
        allowed.append(_abs_pattern(data_root, PROPOSE_WRITE_REL))
        disallowed.append(_abs_pattern(data_root, ODIN_BRAIN_DENY_REL))
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
        *allowed,
        "--disallowedTools",
        *disallowed,
    ]
    if model:
        cmd += ["--model", model]
    # Grant read access to the data overlay (H1): a skill's inputs can live under
    # the data root, which the data-path-redirect hook rewrites OUTSIDE cwd. Claude
    # limits file tools to cwd + --add-dir, so without this a headless read is denied.
    if data_root != get_workspace_root():
        cmd += ["--add-dir", str(data_root)]
    return cmd


class OutsideWorkspace(ValueError):
    """The target resolved outside the workspace root."""


def _resolve(target: str, root: Path) -> Path:
    """Resolve a target to an absolute script path under the workspace root.

    A bare name (no slash) is looked up under scripts/. A relative path is taken
    as-is from the workspace root.

    CONTAINED, which the docstring already claimed and the code did not check.
    An ABSOLUTE target replaced `root` entirely under pathlib (`root / "/tmp/x"`
    is `/tmp/x`), and `../` walked out the same way. `heading` is a constrained
    command surface, so "under the workspace root" has to be enforced rather
    than described.
    """
    if PurePosixPath(target).is_absolute() or PureWindowsPath(target).is_absolute():
        raise OutsideWorkspace(f"absolute paths are not accepted: {target}")
    # Both separators, because the line above already reasons about Windows
    # path shapes. Testing only "/" made `scripts\workspace-health.py` a BARE
    # NAME, rewrote it to `scripts/scripts\workspace-health.py`, and reported
    # "script not found" for a path the docstring says is taken as-is.
    rel = target if ("/" in target or "\\" in target) else f"scripts/{target}"
    resolved = (root / rel).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise OutsideWorkspace(
            f"{target} resolves to {resolved}, outside the workspace root {root_resolved}")
    return resolved


def _dispatch(script_path: Path, args: list[str]) -> int:
    if not script_path.is_file():
        print(f"heading: script not found: {script_path}", file=sys.stderr)
        return 2
    return subprocess.run([sys.executable, str(script_path), *args]).returncode


def run_skill(skill, args, *, budget_usd=None, model=None) -> int:
    """Run an allowlisted skill headless via `claude -p`. Exit-code primary.

    Returns 2 (refused: not allowlisted, or args don't match the skill's
    required args_prefix -- either way, before any vendor call), 3 (claude
    binary absent, degrade clearly), or the `claude` process exit code otherwise.

    `budget_usd=None` means "the operator did not choose", and the tier decides
    (TIER_DEFAULT_BUDGET_USD, falling back to DEFAULT_BUDGET_USD). An explicit
    number always wins, including one that equals a default -- the caller asked
    for it. The tier is only known after the allowlist lookup below, which is
    why this cannot be resolved in the signature.
    """
    entry = SKILL_ALLOWLIST.get(skill)
    if entry is None:
        print(
            f"heading: skill not allowlisted for headless run: {skill}",
            file=sys.stderr,
        )
        return 2
    args_prefix = entry.get("args_prefix")
    if args_prefix and list(args[: len(args_prefix)]) != list(args_prefix):
        print(
            f"heading: skill '{skill}' is only allowlisted for headless run with "
            f"args {args_prefix!r} as the leading arguments; refusing {args!r}",
            file=sys.stderr,
        )
        return 2
    if shutil.which("claude") is None:
        print(
            "heading: claude binary not found on PATH (headless skill run needs it)",
            file=sys.stderr,
        )
        return 3
    tier = entry["tier"]
    if budget_usd is None:
        budget_usd = TIER_DEFAULT_BUDGET_USD.get(tier, DEFAULT_BUDGET_USD)
    cmd = build_skill_command(
        skill, args, tier=tier, budget_usd=budget_usd, model=model
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
        "--budget", type=float, default=None,
        help=f"max USD for the run (default {DEFAULT_BUDGET_USD:.2f}, or "
             f"{PROPOSE_DEFAULT_BUDGET_USD:.2f} for a propose-tier skill); "
             f"MUST precede the skill name",
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
    try:
        if args.command == "run":
            return _dispatch(_resolve(args.script, root), args.args)
        # Named shortcut.
        return _dispatch(_resolve(REGISTRY[args.command], root), args.args)
    except OutsideWorkspace as exc:
        print(f"heading: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
