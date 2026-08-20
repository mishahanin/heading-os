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


DATA_GATE_MARKER = "heading-os-data-test-gate"


def install_pre_push_data(repo: Path, src: Path) -> None:
    """Install the DATA overlay's pre-push gate, replacing the stock git-lfs hook.

    The replacement is deliberate and safe only because the shipped hook delegates
    to `git lfs pre-push` itself; tests/test_data_repo_test_gate.py holds it to
    that. A data overlay tracks LFS objects, so a hook that forgets the delegation
    silently stops uploading them.
    """
    dest = _hooks_dir(repo) / "pre-push"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    dest.chmod(0o755)


def check_pre_push_data(repo: Path) -> bool:
    """True if the DATA overlay's pre-push gate is installed."""
    dest = _hooks_dir(repo) / "pre-push"
    if not dest.is_file():
        return False
    return DATA_GATE_MARKER in dest.read_text(encoding="utf-8")


def data_repo_to_gate(data_root: Path, engine: Path) -> Path | None:
    """The data overlay that should carry its own gate, or None.

    Returns None in the three cases where gating it would be wrong rather than
    merely unnecessary: the pre-cutover single-repo mode, where data_root IS the
    engine and the engine gate already covers it; demo mode, where the data root
    resolves to the bundled examples/ tree and is not a repository at all; and a
    data root that is simply absent.
    """
    if data_root is None:
        return None
    data_root = Path(data_root)
    if not data_root.is_dir():
        return None
    if data_root.resolve() == Path(engine).resolve():
        return None
    if not (data_root / ".git").exists():
        return None
    return data_root


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
    data_src = engine / ".githooks" / "pre-push-data"

    from scripts.utils.paths import get_data_root
    data = data_repo_to_gate(Path(get_data_root()), engine)

    if args.check:
        ok = check_pre_push(engine)
        print(f"{GREEN}engine pre-push hook present{RESET}" if ok
              else f"{RED}engine pre-push hook MISSING/stale{RESET}")
        if data is not None:
            data_ok = check_pre_push_data(data)
            print(f"{GREEN}data overlay pre-push hook present{RESET}" if data_ok
                  else f"{RED}data overlay pre-push hook MISSING/stale{RESET}")
            ok = ok and data_ok
        else:
            print(f"{YELLOW}no separate data overlay repo; nothing to gate there{RESET}")
        return 0 if ok else 1

    install_pre_push(engine, src)
    ensure_pre_commit(engine)
    print(f"{GREEN}installed engine pre-push test gate + ensured pre-commit hooks{RESET}")
    if data is not None:
        install_pre_push_data(data, data_src)
        ensure_pre_commit(data)
        print(f"{GREEN}installed data overlay pre-push test gate at {data}{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
