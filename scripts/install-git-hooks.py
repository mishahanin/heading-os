#!/usr/bin/env python3
"""Install machine-local git hooks for the engine (and ensure framework hooks).

.git/hooks is not shared by git, so re-run this after any fresh clone or
relocation. Installs:
  - the versioned pre-push test gate (.githooks/pre-push) into the engine repo;
  - ensures the pre-commit framework hooks are active in each repo found.

Usage:
  python scripts/install-git-hooks.py           # install
  python scripts/install-git-hooks.py --check    # verify (exit non-zero if missing/stale)
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import GREEN, RED, YELLOW, RESET
from scripts.utils.paths import get_workspace_root


def _hooks_dir(repo: Path) -> Path:
    return repo / ".git" / "hooks"


def install_pre_push(repo: Path, src: Path) -> None:
    """Copy the versioned pre-push hook into repo/.git/hooks and mark executable."""
    dest = _hooks_dir(repo) / "pre-push"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    dest.chmod(0o755)


def check_pre_push(repo: Path) -> bool:
    """True if the installed pre-push hook exists and references run-tests.py."""
    dest = _hooks_dir(repo) / "pre-push"
    if not dest.is_file():
        return False
    return "run-tests.py" in dest.read_text(encoding="utf-8")


def ensure_pre_commit(repo: Path) -> None:
    """Best-effort: ensure the pre-commit framework hooks are installed."""
    if (repo / ".pre-commit-config.yaml").is_file():
        subprocess.run(["pre-commit", "install"], cwd=str(repo), check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="Install/verify HEADING OS git hooks.")
    ap.add_argument("--check", action="store_true", help="verify only; exit non-zero if missing/stale")
    args = ap.parse_args()

    engine = get_workspace_root()
    src = engine / ".githooks" / "pre-push"

    if args.check:
        ok = check_pre_push(engine)
        print(f"{GREEN}pre-push hook present{RESET}" if ok else f"{RED}pre-push hook MISSING/stale{RESET}")
        return 0 if ok else 1

    install_pre_push(engine, src)
    ensure_pre_commit(engine)
    print(f"{GREEN}installed pre-push test gate + ensured pre-commit hooks{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
