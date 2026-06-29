#!/usr/bin/env python3
"""Commit and push BOTH HEADING OS repos to their private GitHub remotes.

The two-part topology has two git repos:
  - ENGINE: the workspace root clone (.heading-os)         -> origin/main
  - DATA  : the data overlay (get_data_root(), .heading-os-data) -> origin/main

This is the standing "always push both" routine. For each repo it:
  1. runs a pre-push secret scan and refuses to push if a tracked file looks
     like a credential (.env, .session, cookies.json, .sessions/);
  2. asserts the rebuildable index (.memory-index/) is not tracked;
  3. commits staged changes (git add -A) unless --no-commit;
  4. pushes origin main using GH_TOKEN from the engine .env;
  5. verifies the branch is level with origin/main (ahead/behind == 0 0) --
     a bare `git push` can report success yet leave the ref behind, so the
     ahead/behind check is the real gate.

Exits non-zero on the first failure so callers (and /backup) can stop.

Note: the one-time initial bulk import of the data overlay was pushed in
size-bounded batches because a single multi-GB push over a slow link is dropped
by the server at completion. Routine pushes are incremental and small, so this
script does a normal push; if you ever re-import a multi-GB history, stage it.

Usage:
  python scripts/push-all.py [-m "commit message"] [--no-commit] [--dry-run]
"""

import argparse
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Re-exec under the project venv before anything else: the test gate spawns its
# runner with sys.executable, so the whole chain must inherit the locked deps
# (the system interpreter lacks pytest-cov). See scripts/utils/venv.py.
from scripts.utils.venv import ensure_venv  # noqa: E402

ensure_venv()

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.content_denylist import build_denylist
from scripts.utils.engine_guard import scan_engine_repo
from scripts.utils.git_push import supervised_push
from scripts.utils.workspace import (
    get_data_root,
    get_exec_data_root,
    get_routing_destination,
    get_workspace_root,
    is_exec_workspace,
    load_env,
)

logger = logging.getLogger(__name__)

# Tracked-path patterns that must never reach a remote. The .gitignore already
# excludes these; this is the belt-and-braces code check.
SECRET_TRACKED = re.compile(
    r"(^|/)\.env$|(^|/)\.env\.|\.session$|(^|/)\.sessions/|(^|/)cookies\.json$"
)

# Authoritative, UNBYPASSABLE content scan. The pre-commit hook (scripts/
# install-git-hooks.py) is an early-catch layer that `git commit --no-verify`
# can skip; this scan runs here in pure code on the sanctioned push path, so a
# bypassed commit is still caught before anything leaves the machine. There is
# no flag to skip it.
SCANNER = Path(__file__).resolve().parent / "secret-scanner.py"


def _push_delta_files(repo: Path) -> set[str]:
    """Files about to be pushed: the committed-but-unpushed delta plus staged and
    unstaged tracked edits (or all tracked files when origin/main is absent)."""
    have_base = run(
        ["git", "rev-parse", "--verify", "-q", "origin/main"], repo, check=False
    ).returncode == 0
    files: set[str] = set()
    if have_base:
        for args in (
            ["git", "diff", "--name-only", "--diff-filter=ACM", "origin/main..HEAD"],
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            ["git", "diff", "--name-only", "--diff-filter=ACM"],
        ):
            files.update(run(args, repo).stdout.splitlines())
    else:
        files.update(run(["git", "ls-files"], repo).stdout.splitlines())
    return {f for f in files if f}


def content_scan(repo: Path) -> None:
    """Scan the contents of every file about to be pushed. Refuse on any hit.

    Covers the committed-but-unpushed delta plus staged and unstaged tracked
    edits, so the result is identical whether or not --no-commit was passed and
    whether or not this is a dry run.
    """
    files = _push_delta_files(repo)
    if not files:
        return
    proc = subprocess.run(
        ["python3", str(SCANNER), "--stdin"],
        cwd=str(repo), input="\n".join(sorted(files)),
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        reason = "secret-like CONTENT in a file about to be pushed" if proc.returncode == 1 \
            else "secret-scanner error"
        print(f"{RED}REFUSING TO PUSH — {reason}.{RESET}")
        sys.exit(2)


def engine_clean_scan(repo: Path) -> None:
    """Authoritative, UNBYPASSABLE engine/data leak gate. Refuse the push if the
    engine clone carries ANY file routing private/corporate.

    This is the routing-destination sibling of content_scan(): pure code on the
    sanctioned push path, no skip flag. The pre-commit `engine-tree-clean` hook and
    the pre-push test suite assert the same invariant earlier, but both are
    bypassable (`git commit --no-verify`, an un-armed/removed pre-push hook). This
    wall is not -- a data artifact in the engine tree cannot leave the machine
    regardless of how it got committed. Added 2026-06-22 after a `docs/superpowers/`
    leak survived precisely because the routing check ran only at bypassable layers.
    """
    flagged = scan_engine_repo(repo)
    if flagged:
        print(f"{RED}REFUSING TO PUSH — data-class artifact(s) in the engine clone:{RESET}")
        for f in flagged:
            print(f"  {RED}{f}{RESET}")
        print(f"{GRAY}The engine repo is code only. These route private/corporate and "
              f"belong in the DATA root (.heading-os-data) or the corporate repo.{RESET}")
        print(f"{GRAY}Move them out (git rm --cached) and add the path to .gitignore.{RESET}")
        sys.exit(2)


def engine_content_scan(repo: Path, data_root: Path) -> None:
    """UNBYPASSABLE engine CONTENT-leak gate (engine only).

    The content sibling of engine_clean_scan() (routing) and content_scan()
    (secrets): refuse the push if any engine-routed file about to be pushed carries
    a real-entity token harvested from the private DATA overlay -- real person
    slugs/names, handles, e-mails, Telegram IDs, or curated company/event tokens.
    Closes the gap the 2026-06-28 public-readiness audit exposed: the structural
    guards check WHERE a file routes, never WHAT is inside it, so real data inside a
    legitimately engine-routed file slipped past every layer. Degrades to a no-op
    when the overlay is absent (public clone / CI), where the structural layers
    still hold. Suppress a true false positive inline with `content-guard: ok`.
    """
    dl = build_denylist(data_root)
    if dl.degraded or not dl.tokens:
        return
    findings: list[tuple[str, int, str, str]] = []
    for rel in sorted(_push_delta_files(repo)):
        if get_routing_destination(rel) != "engine":
            continue
        p = repo / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, matched, category in dl.scan_text(text):
            findings.append((rel, lineno, matched, category))
    if findings:
        print(f"{RED}REFUSING TO PUSH — real-entity CONTENT in engine-routed file(s):{RESET}")
        for rel, lineno, matched, category in findings:
            print(f"  {RED}{rel}:{lineno}{RESET}  \"{matched}\"  {GRAY}[{category}]{RESET}")
        print(f"{GRAY}The engine ships no real data. Genericize to a placeholder, move the "
              f"value to the DATA overlay, or annotate the line `content-guard: ok <reason>`.{RESET}")
        sys.exit(2)


def _pre_push_gate_armed(repo: Path) -> bool:
    """True if repo's pre-push hook is installed and runs the regression gate.

    The pre-push hook (installed by scripts/install-git-hooks.py) is the single
    authoritative test gate -- it runs the suite on EVERY push to the engine, not
    just this path. push-all does NOT run the suite a second time itself (that was
    a redundant double-run, removed 2026-06-20); it only refuses to push when the
    hook is absent, so the gate can never be silently skipped on an un-provisioned
    clone. Mirrors install-git-hooks.check_pre_push (kept inline because that
    module is kebab-named and not importable)."""
    hook = repo / ".git" / "hooks" / "pre-push"
    try:
        return hook.is_file() and "run-tests.py" in hook.read_text(encoding="utf-8")
    except OSError:
        return False


def run(args, cwd, env=None, check=True, capture=True):
    """Run a git command, returning CompletedProcess. Raises on non-zero when check."""
    return subprocess.run(
        args, cwd=str(cwd), env=env, check=check,
        capture_output=capture, text=True,
    )


def gh_token() -> str | None:
    """Return GH_TOKEN (the variable gh reads), loading the engine .env if needed."""
    import os
    if "GH_TOKEN" not in os.environ:
        try:
            load_env(get_workspace_root())  # loads engine .env into os.environ
        except Exception as exc:
            logger.warning("push-all: failed to load engine .env: %s", exc)
    return os.environ.get("GH_TOKEN") or None


def push_repo(name: str, repo: Path, message: str, do_commit: bool, dry_run: bool,
              push_env: dict, is_engine: bool = False, data_root: Path | None = None) -> None:
    """Commit + push one repo to origin/main, then verify ahead/behind == 0 0."""
    print(f"\n{BOLD}{CYAN}== {name}: {repo} =={RESET}")

    # 0. engine/data leak gate (engine only): the engine clone must be code-only.
    # Unbypassable -- runs before the commit, so even a working tree staged with
    # --no-verify cannot push a data-class artifact out of the engine. The DATA
    # repo legitimately carries private files and is exempt.
    if is_engine:
        engine_clean_scan(repo)
        if data_root is not None:
            engine_content_scan(repo, data_root)

    # 1. pre-push secret scan over tracked files
    tracked = run(["git", "ls-files"], repo).stdout.splitlines()
    leaks = [
        f for f in tracked
        if SECRET_TRACKED.search(f) and not f.endswith((".example", ".sample", ".template"))
    ]
    if leaks:
        print(f"{RED}REFUSING TO PUSH — secret-like tracked files:{RESET}")
        for f in leaks:
            print(f"  {RED}{f}{RESET}")
        print(f"{GRAY}Remove from the index (git rm --cached) and add to .gitignore.{RESET}")
        sys.exit(2)

    # 2. assert the rebuildable index is not tracked
    if any(f.startswith(".memory-index/") for f in tracked):
        print(f"{RED}REFUSING TO PUSH — .memory-index/ is tracked (must be gitignored).{RESET}")
        sys.exit(2)

    # 3. commit staged changes
    status = run(["git", "status", "--short"], repo).stdout.strip()
    if status and do_commit:
        if dry_run:
            print(f"{YELLOW}[dry-run]{RESET} would commit:\n{status}")
        else:
            run(["git", "add", "-A"], repo)
            run(["git", "commit", "-m", message], repo)
            head = run(["git", "rev-parse", "--short", "HEAD"], repo).stdout.strip()
            print(f"{GREEN}committed{RESET} {head}: {message.splitlines()[0]}")
    elif status and not do_commit:
        print(f"{YELLOW}uncommitted changes left (--no-commit):{RESET}\n{status}")
    else:
        print(f"{GRAY}no local changes to commit{RESET}")

    # 3.5 content secret scan over everything about to be pushed (unbypassable)
    content_scan(repo)

    if dry_run:
        print(f"{YELLOW}[dry-run]{RESET} would push origin main")
        return

    # 4. push
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()
    if branch != "main":
        print(f"{RED}REFUSING TO PUSH — branch is '{branch}', expected 'main'.{RESET}")
        sys.exit(2)
    # 4+5. supervised push + verify ahead/behind == 0 0 in one primitive.
    # The watchdog bounds the push by inactivity (no output AND no CPU), never by
    # a wall-clock guess, so the engine's pre-push test gate is never clipped; the
    # ahead/behind == 0 0 postcondition is checked without an unbounded fetch on
    # the critical path (a bare push can silently leave the ref behind).
    v = supervised_push(repo, env=push_env, stall_window=180, label=f"push:{name}")
    if v["state"] == "ok":
        print(f"{GREEN}pushed & verified [0 0] in sync with origin/main "
              f"{GRAY}({v['elapsed_s']}s){RESET}")
    else:
        print(f"{RED}{v['state'].upper()} after push — {v['reason']}{RESET}")
        if v.get("tail"):
            print(f"{GRAY}{v['tail']}{RESET}")
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Push both HEADING OS repos to their private remotes.")
    ap.add_argument("-m", "--message", help="commit message (default: dated backup message)")
    ap.add_argument("--no-commit", action="store_true", help="push existing commits only; do not commit working-tree changes")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen; make no commits or pushes")
    args = ap.parse_args()

    message = args.message or f"chore: workspace backup {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    token = gh_token()
    if not token and not args.dry_run:
        print(f"{RED}GH_TOKEN not found in engine .env — cannot authenticate push.{RESET}")
        sys.exit(2)
    import os
    push_env = dict(os.environ)
    if token:
        push_env["GH_TOKEN"] = token

    engine = get_workspace_root()

    # Exec workspaces: the engine clone is READ-ONLY for execs (consumed via
    # `git pull`; its origin is the CEO's engine repo). Only the private data
    # overlay (heading-os-data-{slug}) is writable and gets backed up. Never push
    # the engine from an exec, and don't require its pre-push gate — we don't push
    # it. This branch short-circuits before the engine gate + dual-repo logic.
    if is_exec_workspace():
        data = get_exec_data_root()
        if data == engine:
            print(f"{RED}REFUSING TO PUSH — the exec data overlay resolves to the "
                  f"engine clone; the data root is misconfigured.{RESET}")
            print(f"{GRAY}Expected a sibling ../.heading-os-data-<slug> (or "
                  f"../.heading-os-data) clone of the exec's writable data repo.{RESET}")
            sys.exit(2)
        print(f"{YELLOW}Exec workspace — pushing the data overlay only; the engine "
              f"clone is pull-only.{RESET}")
        push_repo("DATA", data, message, not args.no_commit, args.dry_run, push_env)
        print(f"\n{GREEN}{BOLD}Data overlay pushed.{RESET}" if not args.dry_run
              else f"\n{YELLOW}dry-run complete.{RESET}")
        return

    data = get_data_root()
    # Single authoritative gate: the engine's pre-push hook runs the regression
    # suite during the actual push. We do not run it a second time here -- we only
    # refuse to push if it is not armed, so the gate cannot be silently skipped.
    if not args.dry_run and not _pre_push_gate_armed(engine):
        print(f"{RED}REFUSING TO PUSH — engine pre-push test gate is not installed.{RESET}")
        print(f"{GRAY}Arm it once with: python scripts/install-git-hooks.py{RESET}")
        sys.exit(2)
    if data == engine:
        # Pre-cutover single repo: data files are legitimately tracked here, so the
        # engine-clean gate would flag everything. Do not arm it in this mode.
        print(f"{YELLOW}Data root == engine root (pre-cutover/single repo). Pushing one repo.{RESET}")
        push_repo("repo", engine, message, not args.no_commit, args.dry_run, push_env)
    else:
        push_repo("ENGINE", engine, message, not args.no_commit, args.dry_run, push_env,
                  is_engine=True, data_root=data)
        push_repo("DATA", data, message, not args.no_commit, args.dry_run, push_env)

    print(f"\n{GREEN}{BOLD}Both repos pushed.{RESET}" if not args.dry_run else f"\n{YELLOW}dry-run complete.{RESET}")


if __name__ == "__main__":
    main()
