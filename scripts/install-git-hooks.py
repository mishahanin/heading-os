#!/usr/bin/env python3
"""Install machine-local git hooks for the engine (and ensure framework hooks).

.git/hooks is not shared by git, so re-run this after any fresh clone or
relocation. Installs:
  - the versioned pre-push test gate (.githooks/pre-push) into the engine repo;
  - ensures the pre-commit framework hooks are active in each repo found.

Usage:
  python scripts/install-git-hooks.py           # install
  python scripts/install-git-hooks.py --check    # verify (exit non-zero if missing/stale)

`--check` verifies BOTH gates it claims to install: the pre-push test gate and
the pre-commit framework hook, in the engine and in the data overlay.

Tests: tests/test_a_scanner_that_sat_below_an_exit.py
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
    """Where git will ACTUALLY look for hooks in `repo`.

    `.git` is a directory only in an ordinary clone. In a linked worktree
    it is a regular FILE holding `gitdir: ...`, and this workspace keeps
    six worktrees, one of them inside the engine tree itself. Spelling the
    path by hand therefore named a location under a file: MEASURED
    2026-08-29 from `.claude/worktrees/hdr`, `_hooks_dir` returned
    `<worktree>/.git/hooks`, which does not exist, while git reported the
    shared `<repo>/.git/hooks`, which does and is armed. Install died with
    NotADirectoryError and `--check` called armed security gates MISSING.

    Ask git. Fall back to the literal layout only when there is no git to
    ask, which is the shape every other caller here already handles.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"],
            capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return repo / ".git" / "hooks"
    if not out:
        return repo / ".git" / "hooks"
    return Path(out) if Path(out).is_absolute() else repo / out


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


ENGINE_ROOT_PLACEHOLDER = "@@ENGINE_ROOT@@"


def install_pre_push_data(repo: Path, src: Path, engine: Path | None = None) -> None:
    """Install the DATA overlay's pre-push gate, replacing the stock git-lfs hook.

    The replacement is deliberate and safe only because the shipped hook delegates
    to `git lfs pre-push` itself; tests/test_data_repo_test_gate.py holds it to
    that. A data overlay tracks LFS objects, so a hook that forgets the delegation
    silently stops uploading them.

    The engine root is STAMPED IN here rather than guessed at run time. A data
    overlay has no interpreter of its own and borrows the engine's `.venv`; the
    hook used to find it as "the sibling named .heading-os", which is a layout
    this workspace does not promise and which fails toward a bare `python3`
    instead of toward an error. This function already knows the real path, so it
    writes it.
    """
    engine = Path(engine) if engine is not None else get_workspace_root()
    dest = _hooks_dir(repo) / "pre-push"
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = src.read_text(encoding="utf-8").replace(
        ENGINE_ROOT_PLACEHOLDER, str(Path(engine).resolve()))
    dest.write_text(body, encoding="utf-8")
    dest.chmod(0o755)


def check_pre_push_data(repo: Path) -> bool:
    """True if the DATA overlay's pre-push gate is installed AND still resolves.

    The marker alone is not enough once the engine path is stamped in at install
    time. Relocate the workspace and the stamp goes stale while the marker stays
    put, so a check that reads only the marker would report a gate that has
    quietly fallen back to a bare `python3`. That is the shape
    `.claude/rules/scope-claims.md` names: a sentence asserting more than the
    method established. So the stamp is resolved here too.
    """
    dest = _hooks_dir(repo) / "pre-push"
    if not dest.is_file():
        return False
    body = dest.read_text(encoding="utf-8")
    if DATA_GATE_MARKER not in body:
        return False
    return _stamped_engine_exists(body)


def _stamped_engine_exists(body: str) -> bool:
    """True if the installed hook's ENGINE= line points at a directory."""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("ENGINE="):
            continue
        value = stripped[len("ENGINE="):].strip().strip('"').strip("'")
        if value == ENGINE_ROOT_PLACEHOLDER:
            return False
        return Path(value).is_dir()
    # No ENGINE= line at all: an older hook that guessed the path at run time.
    # Nothing to resolve, so nothing to call stale.
    return True


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


PRE_COMMIT_FRAMEWORK_MARKER = "File generated by pre-commit"


def check_pre_commit(repo: Path) -> "bool | None":
    """Is the pre-commit framework hook live in `repo`? None = out of scope.

    `--check` used to verify the pre-push gate and nothing else, while this
    file's docstring says the script ensures the framework hooks too. So a repo
    with the push gate installed and `pre-commit install` never run exited 0 --
    reporting healthy with every COMMIT-time gate absent, the secret scanner
    among them.

    None means the repo carries no `.pre-commit-config.yaml`, so there was
    never anything to install here; that is distinct from False, which means
    the config is there and the hook is not.
    """
    if not (repo / ".pre-commit-config.yaml").is_file():
        return None
    hook = _hooks_dir(repo) / "pre-commit"
    if not hook.is_file():
        return False
    return PRE_COMMIT_FRAMEWORK_MARKER in hook.read_text(encoding="utf-8", errors="replace")


def _report_pre_commit(repo: Path, label: str) -> bool:
    """Print `check_pre_commit`'s verdict for one repo; True when nothing is wrong."""
    state = check_pre_commit(repo)
    if state is None:
        print(f"{YELLOW}{label}: no .pre-commit-config.yaml; no commit-time gate to verify{RESET}")
        return True
    if state:
        print(f"{GREEN}{label} pre-commit framework hook present{RESET}")
        return True
    print(f"{RED}{label} pre-commit framework hook MISSING -- run `pre-commit install` "
          f"in {repo}{RESET}")
    return False


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
        ok = _report_pre_commit(engine, "engine") and ok
        if data is not None:
            data_ok = check_pre_push_data(data)
            print(f"{GREEN}data overlay pre-push hook present{RESET}" if data_ok
                  else f"{RED}data overlay pre-push hook MISSING/stale{RESET}")
            ok = _report_pre_commit(data, "data overlay") and data_ok and ok
        else:
            print(f"{YELLOW}no separate data overlay repo; nothing to gate there{RESET}")
        return 0 if ok else 1

    # The hook sources are versioned in the engine tree, and `--check` above
    # needs neither. A fresh clone that is missing one -- a partial checkout, a
    # renamed .githooks, a package that drops dotfiles -- is exactly the case
    # this script's docstring tells you to run it for, and it died there with a
    # `shutil.copyfile` traceback instead of saying what was absent.
    missing = [p for p in [src] + ([data_src] if data is not None else [])
               if not p.is_file()]
    if missing:
        for p in missing:
            print(f"{RED}missing hook source: {p}{RESET}", file=sys.stderr)
        print(f"{RED}The hooks are versioned under {engine / '.githooks'}. Restore them "
              f"(`git checkout -- .githooks`) and re-run.{RESET}", file=sys.stderr)
        return 2

    install_pre_push(engine, src)
    ensure_pre_commit(engine)
    print(f"{GREEN}installed engine pre-push test gate + ensured pre-commit hooks{RESET}")
    if data is not None:
        install_pre_push_data(data, data_src, engine)
        ensure_pre_commit(data)
        print(f"{GREEN}installed data overlay pre-push test gate at {data}{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
