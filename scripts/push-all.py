#!/usr/bin/env python3
"""Commit and push BOTH HEADING OS repos to their private GitHub remotes.

The two-part topology has two git repos:
  - ENGINE: the workspace root clone (.heading-os)         -> origin/main
  - DATA  : the data overlay (get_data_root(), .heading-os-data) -> origin/main

This is the standing "always push both" routine. The DATA overlay goes FIRST: the
engine's pre-push hook runs the full suite inside the push, and data is the only
half that cannot be reconstructed. For each repo it:
  1. runs a pre-push secret scan and refuses to push if a tracked file looks
     like a credential (.env, .session, cookies.json, .sessions/);
  2. asserts the rebuildable index (.memory-index/) is not tracked;
  3. commits staged changes (git add -A) unless --no-commit;
  4. checks the push PRECONDITIONS: the remote is one this repository may push
     to, the branch is main, and for an engine push the pre-push suite gate is
     armed;
  5. pushes origin main using GH_TOKEN from the engine .env AND verifies the
     branch is level with origin/main (ahead/behind == 0 0) as one step --
     a bare `git push` can report success yet leave the ref behind, so the
     ahead/behind check is the real gate.

Exit codes:
  0     everything that exists was pushed.
  3     everything that COULD be pushed was; at least one repo was skipped for a
        named reason (a branch that is not main, an unarmed engine test gate).
        Each skipped repo is still committed locally, so nothing is lost. Read the
        headline, not the code: "Partial" means some repo did push, while "NOTHING
        PUSHED" means every repo was skipped and this run produced no off-machine
        copy at all. The exec and pre-cutover modes push one repo, so exit 3 there
        is always the second shape.
  1, 2  a failure that stops the run: a security refusal, a remote a repository
        must not push to, an absent push token, a misconfigured data root, or a
        push that ran and did not verify.

A refusal about ONE repository never cancels another. See RepoNotPushable.

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
from scripts.utils.denial_log import CONTEXT_ENV, log_denial
from scripts.utils.engine_guard import scan_engine_repo
from scripts.utils.git_push import remote_objection, supervised_push
from scripts.utils.workspace import (
    get_default_tz,
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


class RepoNotPushable(Exception):
    """This repository cannot be pushed right now. Says nothing about the others.

    The distinction this type exists to make is the whole point of the change that
    introduced it. push-all.py had ten sys.exit sites and they were two different
    statements wearing one uniform:

      - STOP THE WORLD: a secret in content, a data-class artifact in the engine
        clone, a real-entity token in an engine-routed file, a secret-like tracked
        filename, an absent push token, a misconfigured data root. Something is
        wrong that the operator must see before anything leaves the machine.
      - THIS REPO CANNOT BE PUSHED: a branch that is not main, an engine whose
        pre-push gate is not armed. These say nothing whatever about the other
        repository.

    Treating the second kind as the first meant that whenever the engine clone sat
    on a feature branch, the process died at the engine and the DATA overlay was
    never pushed at all. The engine sits on a feature branch during every slice of
    work by construction, so the backup was declining to back up the only
    irreplaceable half of the workspace for the duration of every slice. Measured
    twice in one session on 2026-07-29 and hand-worked-around both times.

    Raise this for a per-repository refusal. Keep sys.exit for the other kind. A
    new gate's author has to choose, and the choice is visible at the call site
    rather than resting on a convention nobody reads.
    """


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
    # The scanner counts its own refusal; this names the caller so a push-time
    # catch is distinguishable from a commit-time one. Counting here as well
    # would record one refusal twice and corrupt the denominator.
    env = dict(os.environ, **{CONTEXT_ENV: "push"})
    proc = subprocess.run(
        ["python3", str(SCANNER), "--stdin"],
        cwd=str(repo), input="\n".join(sorted(files)),
        capture_output=True, text=True, env=env,
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
        for rel in flagged:
            log_denial(mechanism="push:engine-clean-scan", action="push",
                       path=rel, reason="routes private/corporate in the engine clone")
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
        for rel, lineno, _matched, category in findings:
            # The matched token is a real-entity name, so it is the payload the
            # log must not carry: record where and what class, never the value.
            log_denial(mechanism="push:engine-content-scan", action="push",
                       path=f"{rel}:{lineno}", reason=f"real-entity token [{category}]")
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
              push_env: dict, is_engine: bool = False, data_root: Path | None = None,
              test_gate: bool = False) -> None:
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

    # 4. per-repository push preconditions.
    #
    # Three refusals sit here, AFTER the commit above, and that position is the
    # decision rather than an accident: a repo that cannot be pushed still gets
    # its local commit, so work in progress is never lost to a refusal about
    # where it can go. Raising before step 3 would have been tidier and would
    # have thrown the work away.
    #
    # They also sit ABOVE the dry-run return, which they did not before. The
    # branch check used to be below it, so a dry run reported no skip at all:
    # it hid the one thing this whole change exists to surface. Evaluating a
    # precondition writes nothing, so a dry run can afford to be honest.
    # 4a. remote identity, and it runs FIRST inside this block on purpose.
    #
    # The two refusals below raise RepoNotPushable, which _attempt absorbs, so a
    # repository on a feature branch never reaches any check placed after them.
    # A misconfigured remote must not be maskable by a routine skip, so it is
    # evaluated before either of them and stops the run outright.
    #
    # sys.exit(2) rather than RepoNotPushable, and the classification is the
    # decision: a branch that is not main says THIS repository cannot be pushed
    # and nothing about the others. A remote pointing somewhere it must not
    # says the configuration is wrong, which makes every repository in the run
    # suspect for the same reason.
    objection = remote_objection(repo, token=push_env.get("GH_TOKEN"))
    if objection:
        print(f"{RED}REFUSING TO PUSH — {objection}{RESET}")
        # Both sides of the comparison, not just the pushing repository's remote:
        # an operator who wired a convenience remote for the data repo onto the
        # engine clone would otherwise see only a URL that is correct on its own
        # and no way to see what it collided with.
        print(f"{GRAY}Check this repo's remote with: "
              f"git -C {repo} remote get-url --push origin{RESET}")
        print(f"{GRAY}Check the engine's remotes with: "
              f"git -C {get_workspace_root()} remote -v{RESET}")
        sys.exit(2)

    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()
    if branch != "main":
        raise RepoNotPushable(
            f"branch is '{branch}', expected 'main'. Merge it and push from main."
        )
    # Keyed on test_gate, NOT on is_engine, and the difference is load-bearing.
    # The single authoritative test gate is the engine's pre-push hook, which runs
    # the suite on EVERY push to the engine and not just on this path, so an
    # un-provisioned clone must not be able to push past it. A fresh clone starts
    # unarmed, because git does not share .git/hooks.
    #
    # It moved here from main() in the same change that introduced
    # RepoNotPushable: it is a precondition of pushing TO THE ENGINE REMOTE, never
    # of running the program, and while it lived in main() an unarmed hook
    # cancelled the DATA backup too. But main() checked it ABOVE the single-repo
    # branch, so it covered the pre-cutover mode as well -- and that mode pushes
    # this same engine clone with is_engine deliberately OFF, because its data
    # files are tracked legitimately there and engine_clean_scan would flag all of
    # them. Keying this raise on is_engine would therefore have narrowed a check
    # from two modes to one while looking like a pure move. Hence two flags.
    if test_gate and not _pre_push_gate_armed(repo):
        raise RepoNotPushable(
            "the engine pre-push test gate is not installed, so a push would skip "
            "the suite. Arm it once with: python scripts/install-git-hooks.py"
        )

    if dry_run:
        print(f"{YELLOW}[dry-run]{RESET} would push origin main")
        return

    # 5. supervised push + verify ahead/behind == 0 0 in one primitive.
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


def _attempt(skipped: list[tuple[str, str]], name: str, *args, **kwargs) -> None:
    """Push one repo. Record a per-repository refusal; let everything else fly.

    ONLY RepoNotPushable is absorbed. A sys.exit from any security refusal (the
    content scan, the engine clean scan, the engine content scan, a secret-like
    tracked filename) raises SystemExit, which is NOT an Exception subclass and is
    therefore not caught here even by accident. That is the invariant this helper
    exists to hold: unbypassable walls stay unbypassable.
    """
    try:
        push_repo(name, *args, **kwargs)
    except RepoNotPushable as exc:
        print(f"{YELLOW}SKIPPED {name}{RESET} {exc}")
        skipped.append((name, str(exc)))


def _report_skips(skipped: list[tuple[str, str]], args, attempted: int) -> None:
    """Print the closing summary for a partial run and exit 3. Never returns.

    Shared by both call paths -- the two-repo/pre-cutover block and the exec
    short-circuit -- because a second copy of this text is a second place for the
    reassurance below to drift out of step with the flags it is a claim about.

    *attempted* is how many repositories this mode tries to push, and the headline
    branches on it because exit 3 has two shapes, not one. The exec and pre-cutover
    modes push exactly ONE repository, so a skip there is a backup that pushed
    NOTHING -- and the word "partial" over a run that pushed nothing is a false
    success claim about the only irreplaceable half of the workspace, which is the
    harm this command exists to prevent. Two of the three modes can produce no other
    shape.
    """
    all_skipped = len(skipped) == attempted
    if all_skipped:
        print(f"\n{RED}{BOLD}NOTHING PUSHED: all {attempted} repo(s) skipped.{RESET}")
    else:
        print(f"\n{YELLOW}{BOLD}Partial: {len(skipped)} of {attempted} repo(s) "
              f"not pushed.{RESET}")
    for name, reason in skipped:
        print(f"  {YELLOW}{name}{RESET}  {reason}")
    # "Everything that could be pushed was" is dropped when nothing was pushed: it
    # is true but reads as reassurance, and there is nothing to be reassured about.
    went = "" if all_skipped else "Everything that could be pushed was. "
    # The reassurance is CONDITIONAL, because it is a claim about the disk and it
    # is false in two of the three modes. Under --dry-run nothing was committed
    # (step 3 only printed what it would do), and under --no-commit nothing was
    # committed either. A dry run is also the newly reachable skip path -- the
    # branch check moved above the dry-run return so that it reports -- so an
    # unconditional "committed locally" would be wrong precisely where this
    # change added the message.
    if args.dry_run:
        print(f"{GRAY}Nothing was written: this was a dry run.{RESET}")
    elif args.no_commit:
        print(f"{GRAY}{went}--no-commit was passed, so any working-tree changes "
              f"above are still uncommitted.{RESET}")
    else:
        print(f"{GRAY}{went}Nothing was lost: each repo above is committed "
              f"locally.{RESET}")
    sys.exit(3)


def main() -> None:
    ap = argparse.ArgumentParser(description="Push both HEADING OS repos to their private remotes.")
    ap.add_argument("-m", "--message", help="commit message (default: dated backup message)")
    ap.add_argument("--no-commit", action="store_true", help="push existing commits only; do not commit working-tree changes")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen; make no commits or pushes")
    args = ap.parse_args()

    message = args.message or f"chore: workspace backup {datetime.now(get_default_tz()).strftime('%Y-%m-%d %H:%M')}"

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
        # No test_gate: an exec's engine clone is pull-only, so this path never
        # pushes the engine and has no suite gate to require. The success line
        # below must not print over a skip -- a backup that says "Data overlay
        # pushed." after pushing nothing is worse than one that fails loudly.
        exec_skipped: list[tuple[str, str]] = []
        _attempt(exec_skipped, "DATA", data, message, not args.no_commit, args.dry_run,
                 push_env)
        if exec_skipped:
            # attempted=1: this mode has one repository, so a skip here means the
            # backup pushed nothing at all and must not read as "partial".
            _report_skips(exec_skipped, args, 1)
        print(f"\n{GREEN}{BOLD}Data overlay pushed.{RESET}" if not args.dry_run
              else f"\n{YELLOW}dry-run complete.{RESET}")
        return

    data = get_data_root()
    skipped: list[tuple[str, str]] = []
    attempted = 1 if data == engine else 2
    if data == engine:
        # Pre-cutover single repo: data files are legitimately tracked here, so the
        # engine-clean gate would flag everything. Do not arm it in this mode.
        #
        # test_gate=True even so, and that is the point of the flag being separate
        # from is_engine. This mode pushes the ENGINE clone, to the engine remote,
        # whose pre-push hook is the single authoritative suite gate. The check used
        # to sit above this branch in main() and so covered this call; keying it on
        # is_engine would have dropped it here while looking like a pure move.
        print(f"{YELLOW}Data root == engine root (pre-cutover/single repo). Pushing one repo.{RESET}")
        _attempt(skipped, "repo", engine, message, not args.no_commit, args.dry_run,
                 push_env, test_gate=True)
    else:
        # DATA FIRST, and the reason is measured rather than aesthetic: the
        # engine's pre-push hook runs the full regression suite inside the push
        # and took 320 seconds on the machine this was written on. The data
        # overlay is the only half that cannot be reconstructed, so it does not
        # queue behind a several-minute gate that may fail, stall, or be
        # interrupted.
        #
        # This costs nothing in safety, which is worth saying because the phrase
        # "stop the world" invites the opposite conclusion. Each scan protects the
        # repository it runs on: the engine scans look for a problem in the
        # ENGINE's files, while the DATA overlay runs its own content_scan over
        # its own files and legitimately carries private content. No engine
        # refusal carries any information about whether DATA is safe to push.
        _attempt(skipped, "DATA", data, message, not args.no_commit, args.dry_run, push_env)
        _attempt(skipped, "ENGINE", engine, message, not args.no_commit, args.dry_run,
                 push_env, is_engine=True, data_root=data, test_gate=True)

    if skipped:
        _report_skips(skipped, args, attempted)

    print(f"\n{GREEN}{BOLD}Both repos pushed.{RESET}" if not args.dry_run else f"\n{YELLOW}dry-run complete.{RESET}")


if __name__ == "__main__":
    main()
