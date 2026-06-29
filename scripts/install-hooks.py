#!/usr/bin/env python3
"""
install-hooks.py - SUPERSEDED legacy git-hook installer.

As of 2026-05-31 the workspace commit gate is the pre-commit framework
(`.pre-commit-config.yaml`, installed via `pre-commit install`). The standalone
`# 31C-SECRET-SCANNER` hook this script used to write is now folded into that
config as the `secret-scanner-31c` local hook. Running the install path here
would overwrite the framework's `.git/hooks/pre-commit` and re-introduce the
dual-mechanism conflict that silently bypassed all hooks in May 2026.

This script therefore refuses to install whenever `.pre-commit-config.yaml`
exists. Use `pre-commit install` instead.

Usage:
  python3 scripts/install-hooks.py          # refuses if framework config present
  python3 scripts/install-hooks.py --check  # report which mechanism manages hooks
"""

import sys
import os
import stat
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root
from scripts.utils.colors import GREEN, YELLOW, RED, BOLD, RESET

HOOK_MARKER = "# 31C-SECRET-SCANNER"

PRE_COMMIT_HOOK = f"""#!/bin/sh
{HOOK_MARKER}
# Pre-commit hook: scan staged files for secrets
# Coexists with existing Git LFS hooks

STAGED=$(git diff --cached --name-only --diff-filter=ACMR)
[ -z "$STAGED" ] && exit 0

# Run scanner - if python3 fails, warn but don't block
echo "$STAGED" | python3 scripts/secret-scanner.py --stdin
EXIT_CODE=$?

if [ $EXIT_CODE -eq 1 ]; then
    echo ""
    echo "COMMIT BLOCKED: Secrets detected in staged files."
    echo "Remove the secrets, then try again."
    echo "To bypass (DANGEROUS): git commit --no-verify"
    exit 1
fi

if [ $EXIT_CODE -gt 1 ]; then
    echo "WARNING: Secret scanner encountered an error. Commit proceeding."
fi

exit 0
"""


def install_pre_commit(hooks_dir: Path, check_only: bool = False) -> bool:
    """Install or check the pre-commit hook."""
    hook_path = hooks_dir / "pre-commit"

    if hook_path.exists():
        content = hook_path.read_text(encoding="utf-8", errors="replace")
        if HOOK_MARKER in content:
            if check_only:
                print(f"  {GREEN}pre-commit: installed{RESET}")
            else:
                print(f"  {GREEN}pre-commit: already installed (skipping){RESET}")
            return True

        if check_only:
            print(f"  {YELLOW}pre-commit: exists but missing secret scanner{RESET}")
            return False

        # Existing hook without our marker - append
        print(f"  {YELLOW}pre-commit: appending secret scanner to existing hook{RESET}")
        with open(hook_path, "a", encoding="utf-8") as f:
            f.write("\n\n" + PRE_COMMIT_HOOK.lstrip("#!/bin/sh\n"))
    else:
        if check_only:
            print(f"  {RED}pre-commit: not installed{RESET}")
            return False

        print(f"  {GREEN}pre-commit: installing{RESET}")
        hook_path.write_text(PRE_COMMIT_HOOK, encoding="utf-8")

    # Make executable
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Install git hooks for the workspace.")
    parser.add_argument("--check", action="store_true", help="Check if hooks are installed")
    args = parser.parse_args()

    root = get_workspace_root()
    hooks_dir = root / ".git" / "hooks"

    if not hooks_dir.exists():
        print(f"{RED}Error: .git/hooks not found. Is this a git repository?{RESET}")
        sys.exit(1)

    # Superseded guard: the pre-commit framework is the canonical commit gate.
    # Refuse to clobber its generated hook with the legacy standalone scanner.
    framework_config = root / ".pre-commit-config.yaml"
    if framework_config.exists():
        if args.check:
            print(f"{BOLD}Git hooks status:{RESET}")
            print(f"  {GREEN}managed by the pre-commit framework{RESET} "
                  f"(.pre-commit-config.yaml present)")
            print(f"  This installer is superseded; secret scanning runs as the "
                  f"secret-scanner-31c local hook.")
            sys.exit(0)
        print(f"{RED}Refusing to install:{RESET} this workspace's commit gate is the "
              f"pre-commit framework (.pre-commit-config.yaml).")
        print(f"Installing the legacy standalone hook would overwrite the "
              f"framework's .git/hooks/pre-commit and bypass every other check.")
        print(f"Run {BOLD}pre-commit install{RESET} instead.")
        sys.exit(1)

    print(f"{BOLD}Git hooks {'status' if args.check else 'installation'}:{RESET}")
    installed = install_pre_commit(hooks_dir, check_only=args.check)

    if args.check:
        sys.exit(0 if installed else 1)
    else:
        print(f"\n{GREEN}Done.{RESET}")


if __name__ == "__main__":
    main()
