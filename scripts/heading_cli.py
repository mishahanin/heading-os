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

Invocation (the project is not pip-packaged, so there is no `heading` console
script yet; invoke the module directly):

  python scripts/heading_cli.py run scripts/utils/paths.py
  python scripts/heading_cli.py health
  python scripts/heading_cli.py list

`run` takes a path relative to the workspace root (a bare name is resolved under
scripts/). Named subcommands are shortcuts in the registry below.
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.paths import get_workspace_root  # noqa: E402

# Stable named shortcuts -> repo-relative script paths. Extend as bundles grow.
REGISTRY = {
    "health": "scripts/workspace-health.py",
    "classification": "scripts/classification-health.py",
}


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="heading",
        description="Thin dispatcher over HEADING OS scripts (F-10.1 hybrid).",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run a script by path (relative to workspace root)")
    p_run.add_argument("script", help="e.g. scripts/utils/paths.py or a bare name under scripts/")
    p_run.add_argument("args", nargs=argparse.REMAINDER, help="arguments passed through")

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
    if args.command == "run":
        return _dispatch(_resolve(args.script, root), args.args)
    # Named shortcut.
    return _dispatch(_resolve(REGISTRY[args.command], root), args.args)


if __name__ == "__main__":
    raise SystemExit(main())
