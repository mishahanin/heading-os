#!/usr/bin/env python3
"""Commit and push BOTH HEADING OS repos to their private GitHub remotes.

The two-part topology has two git repos:
  - ENGINE: the workspace root clone (.heading-os)         -> origin/main
  - DATA  : the data overlay (get_data_root(), .heading-os-data) -> origin/main

This is the standing "always push both" routine. The DATA overlay goes FIRST: the
engine's pre-push hook runs the full suite inside the push, and data is the only
half that cannot be reconstructed. For each repo it:
  0. scans the CONTENT of everything about to be pushed for secrets, and for the
     engine also runs the routing and real-entity walls. All of this happens
     BEFORE the commit, so a secret is never written into local history;
  1. refuses to push if any file this push would CARRY -- tracked or merely
     untracked-and-not-ignored -- looks like a credential by NAME (.env,
     .session, cookies.json, .sessions/);
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
        Each skipped repo is still committed locally UNLESS --no-commit or
        --dry-run was passed, in which case its working-tree changes are still
        uncommitted; `_report_skips` prints which of the three it was, and this
        line claimed the first unconditionally until 2026-08-30. Read the
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
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Re-exec under the project venv before anything else: the test gate spawns its
# runner with sys.executable, so the whole chain must inherit the locked deps
# (the system interpreter lacks pytest-cov). See scripts/utils/venv_guard.py.
from scripts.utils.venv_guard import ensure_venv  # noqa: E402

ensure_venv()

from scripts.utils.clone_guard import require_main_clone
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.content_denylist import build_denylist
from scripts.utils.denial_log import CONTEXT_ENV, log_denial
from scripts.utils.engine_guard import (
    engine_text_files,
    engine_text_rels,
    repo_carried_paths,
    scan_engine_repo,
)
from scripts.utils.git_push import remote_objection, supervised_push
from scripts.utils.paths import log_dir
from scripts.utils.push_history import (
    HistoryUnavailable,
    generations,
    read_blob,
    unpushed_blobs,
    unpushed_paths,
)
from scripts.utils.workspace import (
    get_default_tz,
    get_data_root,
    get_exec_data_root,
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
SCANNER_TIMEOUT_S = 300


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


def _z_paths(args: list[str], repo: Path) -> list[str]:
    """NUL-separated git path output as real names, decoded from BYTES.

    The shared `run` helper below is text mode, which is correct for the commit
    messages and refs it mostly carries and wrong for filenames: text mode turns
    on universal newlines and rewrites every CR byte to LF. Separate helper
    rather than a flag on `run`, so the choice is visible at the call site and a
    new path reader has one obvious thing to copy.
    """
    out = subprocess.run(
        args, cwd=str(repo), check=True, capture_output=True,
    ).stdout.decode("utf-8", "surrogateescape")
    # No `.strip()`: a filename may legally begin or end with whitespace.
    return [path for path in out.split("\0") if path]


def _push_delta_files(repo: Path) -> set[str]:
    """Files about to be pushed: the committed-but-unpushed delta, staged and
    unstaged tracked edits, and every untracked file git is not ignoring.

    The untracked leg was missing until 2026-08-23, and the gap was structural
    rather than incidental. `engine_content_scan` runs at step 0 of `push_repo`,
    deliberately BEFORE the commit, so that a tree staged with `--no-verify`
    cannot slip past. But `git diff` sees only tracked files, so a brand-new file
    was invisible at scan time and committed by `git add -A` a moment later. The
    routing wall next to it has always used `git ls-files --others`, and the two
    have to agree about what "about to be pushed" means.

    Ignored files stay out: they are not going to be pushed, and scanning them
    would refuse a push over the contents of `.sessions/` or a scratch file.

    Every command runs with `-z`. Without it git C-quotes any path holding a
    non-ASCII byte, and the quoted string matches no routing rule and opens no
    file - so a Cyrillic-named artifact walked through the content walls on a
    workspace whose operator writes in Russian. Same defect, same day, as
    `engine_guard.repo_carried_paths`.

    `_z_paths`, not the shared `run`, for the reason recorded there on
    2026-08-30: `run` is text mode, every subprocess text mode turns on universal
    newlines, and that rewrites each CR byte to LF with no `newline=` knob to
    switch it off. `-z` is no defence, because the translation happens in Python
    after git has already emitted the bytes verbatim. These paths feed
    `_run_scanner` and `engine_content_scan`, which open them by name, so a
    mistranslated name is a file about to be pushed that no scanner ever reads.
    """
    have_base = run(
        ["git", "rev-parse", "--verify", "-q", "origin/main"], repo, check=False
    ).returncode == 0
    # `origin/main..HEAD` needs BOTH ends to resolve, and `have_base` proves only
    # the left one. On an unborn HEAD git exits 128, `run` defaults to
    # check=True, and the resulting CalledProcessError is not one of the two
    # things `_attempt` absorbs -- so the whole backup died with a traceback and
    # NEITHER repo was pushed. `engine_content_scan` calls this at step 0, before
    # the commit, which is where a fresh clone with a remote but no commits of
    # its own meets it on an ordinary run.
    have_head = run(
        ["git", "rev-parse", "--verify", "-q", "HEAD"], repo, check=False
    ).returncode == 0
    files: set[str] = set()
    if have_base and have_head:
        # `--no-renames`, for the reason `push_history.unpushed_blobs`
        # already gives it. With rename detection on (the git default) a
        # `git mv` plus an edit is ONE `R` entry, and `--diff-filter=ACM`
        # drops it, so the DESTINATION path appears in no leg at all.
        # MEASURED 2026-08-29 in a scratch repo with a real bare remote: a
        # staged rename carrying a new secret returned the EMPTY set here,
        # `content_scan` skipped the scanner because `if files:` was False,
        # `engine_content_scan` opened no file, and step 3 committed and
        # step 5 pushed the token. Only the bypassable pre-commit hook
        # stood in the way, which is exactly what this wall backstops.
        # Turning renames off restores the `A` for the destination path.
        #
        # `T` (typechange) joined `ACM` on 2026-09-02, for the same reason one
        # letter further along. A tracked regular file replaced by a symlink
        # is ONE `T` entry and nothing else, so the path fell out of every leg
        # and this wall never opened it. MEASURED that day in a scratch repo:
        # `git diff --name-status` reported `T f.txt` while
        # `--diff-filter=ACM` returned the empty set and `--diff-filter=ACMT`
        # returned `f.txt`. The workspace forbids symlinks, which is why the
        # gap was rated unreachable; a content wall that relies on another
        # rule holding is a wall with a hole in it, and widening a filter can
        # only ever add files to the scan.
        for args in (
            ["git", "diff", "-z", "--no-renames", "--name-only",
             "--diff-filter=ACMT", "origin/main..HEAD"],
            ["git", "diff", "-z", "--cached", "--no-renames", "--name-only",
             "--diff-filter=ACMT"],
            ["git", "diff", "-z", "--no-renames", "--name-only",
             "--diff-filter=ACMT"],
        ):
            files.update(_z_paths(args, repo))
    else:
        # No base, or no HEAD: the index IS the whole delta. `git ls-files`
        # works against an unborn HEAD and lists everything staged, so this
        # branch loses no coverage in either case.
        files.update(_z_paths(["git", "ls-files", "-z"], repo))
    files.update(
        _z_paths(["git", "ls-files", "-z", "--others", "--exclude-standard"], repo)
    )
    return {f for f in files if f}


def _run_scanner(paths, cwd: Path, context: str, extra_env=None):
    """Run secret-scanner.py over `paths`, relative to `cwd`. Exits 2 on a stall.

    The scanner counts its own refusal; `context` names the caller so a
    push-time catch is distinguishable from a commit-time one. Counting here as
    well would record one refusal twice and corrupt the denominator.

    The repo name rides along because the scanner records a repo-RELATIVE path
    and the callers run over both clones: without it, the same relative path in
    the engine and in the data overlay produce two records nothing can tell
    apart, which is the ambiguity the context field exists to remove.

    `--stdin0` and BYTES, not `--stdin` and text. `_z_paths` goes to the
    trouble of reading git's `-z` output as bytes so a path holding a newline
    survives verbatim, and this handoff then joined that list with `"\n"` and
    handed it to a reader that splits on `"\n"`. MEASURED 2026-09-01 in a
    scratch repo: a tracked `two\nlines.env` carrying a `ghp_`-shaped token
    arrived at the scanner as two names that open nothing, both skipped in
    silence, and `content_scan` passed the push. The identical token in
    `creds.env` was refused, so the measurement was measuring something. Text
    mode also cannot encode a surrogateescape name at all, which is the other
    half of what `_z_paths` preserves.
    """
    env = dict(os.environ, **{CONTEXT_ENV: context})
    if extra_env:
        env.update(extra_env)
    payload = b"\0".join(p.encode("utf-8", "surrogateescape") for p in sorted(paths))
    try:
        proc = subprocess.run(
            [sys.executable, str(SCANNER), "--stdin0"],
            cwd=str(cwd), input=payload,
            capture_output=True, env=env, timeout=SCANNER_TIMEOUT_S,
        )
        # Decoded here rather than by `text=True`, so the INPUT stays bytes.
        # `_refuse_on_scanner` writes these straight to the caller's streams.
        return subprocess.CompletedProcess(
            proc.args, proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"),
        )
    except subprocess.TimeoutExpired:
        # Bounded for the same reason the pushes are: an indefinite stall in the
        # only irreplaceable-half backup path is worse than a named failure.
        print(f"{RED}REFUSING TO PUSH -- secret scanner exceeded "
              f"{SCANNER_TIMEOUT_S}s in {cwd}.{RESET}")
        sys.exit(2)


def _refuse_on_scanner(proc, where: str) -> None:
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        reason = f"secret-like CONTENT in {where}" if proc.returncode == 1 \
            else "secret-scanner error"
        print(f"{RED}REFUSING TO PUSH — {reason}.{RESET}")
        sys.exit(2)


def content_scan(repo: Path) -> None:
    """Scan the contents of everything about to be pushed. Refuse on any hit.

    TWO WORLDS, because a push sends both. The working tree and index are what
    the next commit will carry; the unpushed COMMITS are what the wire will
    carry regardless of what the disk says now. Scanning only the first was the
    defect, and it is the defect this docstring used to deny by claiming "a
    bypassed commit is still caught before anything leaves the machine".

    MEASURED 2026-08-29 on a real repository with a real bare remote
    (`.tmp/audit/measure61.py`), before the history pass existed:

      secret committed with `--no-verify`, then wiped from the working tree
        -> the file WAS listed, the scanner read the cleaned bytes off the disk,
           "No secrets detected.", exit 0, push ships the commit.
      commit A adds the secret, commit B removes it, both unpushed
        -> the two-endpoint diff nets to nothing, the file was not even listed,
           exit 0, push ships commit A.
      control, the same secret sitting in the working tree
        -> refused, so the measurement was measuring something.

    Both directions are now covered and neither replaces the other: history says
    nothing about an uncommitted edit, and the working tree says nothing about a
    commit already made.
    """
    files = _push_delta_files(repo)
    if files:
        _refuse_on_scanner(
            _run_scanner(files, repo, f"push:{repo.name}"),
            "a file about to be pushed")
    history_content_scan(repo)


def history_content_scan(repo: Path) -> None:
    """Scan the BYTES of every unpushed commit, not the bytes on disk.

    The blobs are laid out in a scratch tree because the scanner takes file
    paths, and they are laid out in GENERATIONS because it cannot be handed two
    versions of one path at once. See `push_history.generations`: the pass count
    is the largest number of versions any single file has in the range, not the
    commit count.

    Two environment variables, and both are load-bearing:

    `WORKSPACE_ROOT` points at the scratch tree so the scanner's own
    `SKIP_PATHS` resolve. Those three self-referencing files -- the scanner,
    `secret_patterns.py`, `.env.example` -- contain secret patterns by
    definition, and a commit that edits one of them is ordinary here. Without
    this the history pass would refuse the backup over the scanner's own source.
    Re-listing those paths locally was the alternative and it is the duplication
    this audit keeps finding: the second copy is the one that stops being fixed.

    `WORKSPACE_LOG_DIR` pins the denial log back to the REAL workspace. It is
    derived from `get_workspace_root()`, so moving the root would have written
    each refusal into the scratch tree and deleted it seconds later -- a gate
    that refuses and keeps no record of having refused.
    """
    try:
        blobs = unpushed_blobs(repo)
    except HistoryUnavailable as exc:
        # Unverified is not clean, and this branch means git could not say what
        # the push would send.
        print(f"{RED}REFUSING TO PUSH — cannot read the unpushed history of "
              f"{repo.name}: {exc}{RESET}")
        sys.exit(2)
    if not blobs:
        return

    real_log_dir = os.environ.get("WORKSPACE_LOG_DIR") or str(log_dir())
    with tempfile.TemporaryDirectory(prefix="heading-push-history-") as scratch:
        for index, group in enumerate(generations(blobs)):
            root = Path(scratch) / str(index)
            rels: list[str] = []
            for blob in group:
                dest = (root / blob.rel).resolve()
                # git tree entries cannot hold `..`, so this is belt and braces
                # rather than a live hole -- and it is cheap insurance on the
                # one path in this file that writes attacker-influenced names.
                if not dest.is_relative_to(root.resolve()):
                    print(f"{RED}REFUSING TO PUSH — a committed path escapes "
                          f"its own tree: {blob.rel}{RESET}")
                    sys.exit(2)
                dest.parent.mkdir(parents=True, exist_ok=True)
                # write_bytes, so a blob whose mode is a symlink lands as an
                # ORDINARY FILE holding the target string. The workspace creates
                # no symlinks, and materialising one here would let a committed
                # link point the scanner at something outside the scratch tree.
                try:
                    dest.write_bytes(read_blob(repo, blob.sha))
                except HistoryUnavailable as exc:
                    print(f"{RED}REFUSING TO PUSH — {exc}{RESET}")
                    sys.exit(2)
                rels.append(blob.rel)
            proc = _run_scanner(
                rels, root, f"push:{repo.name}:history",
                extra_env={"WORKSPACE_ROOT": str(root),
                           "WORKSPACE_LOG_DIR": real_log_dir})
            if proc.returncode != 0:
                print(f"{YELLOW}The paths below are the versions carried by the "
                      f"UNPUSHED COMMITS of {repo.name}, not the files on disk. "
                      f"Cleaning the working tree does not remove them: the "
                      f"history has to be rewritten.{RESET}")
            _refuse_on_scanner(proc, "a commit about to be pushed")


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

    The unpushed HISTORY is judged alongside the working tree, and the reason is
    the same 2026-06-22 leak in the shape it would take today: commit the
    private-routed file, notice, `git rm` it, commit again, push. Both commits go
    to the remote; `git ls-files` and `--others` have nothing left to report; the
    wall clears a push that ships the file. A working-tree scan cannot see a
    deletion, because a deletion is what it looks like from there.
    """
    try:
        carried_history = unpushed_paths(repo)
    except HistoryUnavailable as exc:
        print(f"{RED}REFUSING TO PUSH — cannot read the unpushed history of "
              f"{repo.name}: {exc}{RESET}")
        sys.exit(2)
    flagged = dict.fromkeys(scan_engine_repo(repo, extra_paths=carried_history))
    if flagged:
        # A path can be flagged for either reason or both, and the two need
        # different remedies, so the report says which. Saying "git rm --cached"
        # over a path that is already deleted on disk sends the operator to do
        # something that changes nothing and leaves the leak in the history.
        on_disk = {rel for rel in flagged if (repo / rel).exists()}
        for rel in flagged:
            log_denial(mechanism="push:engine-clean-scan", action="push",
                       path=rel, reason="routes private/corporate in the engine clone")
        print(f"{RED}REFUSING TO PUSH — data-class artifact(s) in the engine clone:{RESET}")
        for f in flagged:
            where = "working tree" if f in on_disk else "unpushed history"
            print(f"  {RED}{f}{RESET} {GRAY}[{where}]{RESET}")
        print(f"{GRAY}The engine repo is code only. These route private/corporate and "
              f"belong in the DATA root (.heading-os-data) or the corporate repo.{RESET}")
        if on_disk:
            print(f"{GRAY}Working tree: move them out (git rm --cached) and add the "
                  f"path to .gitignore.{RESET}")
        if len(flagged) > len(on_disk):
            print(f"{GRAY}Unpushed history: deleting the file is not enough, the "
                  f"commits still carry it. Rewrite the range before pushing.{RESET}")
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

    Reads the unpushed COMMITS as well as the disk, for the reason `content_scan`
    measures. This repository is PUBLIC, so a real name that reached any commit
    in the range is published by the push whatever the working copy says now. A
    finding from history is labelled `path@blobsha` to keep the two apart, since
    only one of them can be fixed by editing a file.
    """
    dl = build_denylist(data_root)
    if dl.degraded or not dl.tokens:
        # No overlay = a public clone or CI. Skipping is correct and quiet: the
        # structural layers still hold and there is nothing to harvest.
        if data_root is None or not Path(data_root).is_dir():
            return
        # An overlay that IS present and still produced a degraded or empty list
        # means the harvest broke. Refuse: this gate is the only layer that reads
        # WHAT is inside an engine-routed file, and a silent skip here is exactly
        # the "looks like coverage" failure the flag exists to prevent.
        print(f"{RED}REFUSING TO PUSH — the real-entity denylist could not be "
              f"built from {data_root}.{RESET}")
        print(f"{GRAY}The content gate reads no file until this is fixed. Check "
              f"config/content-denylist.yaml for a parse error, and the "
              f"crm/, admin/ and config/ trees for readability.{RESET}")
        log_denial(mechanism="push:engine-content-scan", action="push",
                   path=str(data_root), reason="denylist degraded with an overlay present")
        sys.exit(2)
    findings: list[tuple[str, int, str, str]] = []
    unscanned: list[str] = []

    # The unpushed COMMITS first, then the disk. `engine_text_rels` asks the
    # routing and suffix questions of the PATH alone, which is the only half of
    # `engine_text_files` that has an answer for a version of a file a later
    # commit deleted -- `is_file()` says no, and the push sends it anyway.
    try:
        history = [b for b in unpushed_blobs(repo)
                   if b.rel in set(engine_text_rels([b.rel]))]
    except HistoryUnavailable as exc:
        print(f"{RED}REFUSING TO PUSH — cannot read the unpushed history of "
              f"{repo.name}: {exc}{RESET}")
        sys.exit(2)
    for blob in history:
        label = f"{blob.rel}@{blob.sha[:9]}"
        try:
            text = read_blob(repo, blob.sha).decode("utf-8")
        except HistoryUnavailable as exc:
            unscanned.append(f"{label}: {exc}")
            continue
        except UnicodeDecodeError as exc:
            unscanned.append(f"{label}: {exc}")
            continue
        for lineno, matched, category in dl.scan_text(text):
            findings.append((label, lineno, matched, category))

    gone: list[str] = []
    for rel in engine_text_files(repo, sorted(_push_delta_files(repo))):
        try:
            text = (repo / rel).read_text(encoding="utf-8")
        except FileNotFoundError:
            # THE WALK-THEN-READ RACE. `_push_delta_files` lists paths and this
            # loop reads them after, and `engine_text_files` filters on
            # `is_file()` in between, so the window is narrow but open: a file
            # created and deleted inside it made the read raise, landed in
            # `unscanned`, and REFUSED a push carrying nothing wrong. A gate
            # that blocks on its own timing is how an operator learns to reach
            # for `--no-verify`.
            #
            # WHY SKIPPING IS SAFE HERE, and the reason is NOT "the file is
            # gone" -- that argument is wrong in general, because a tracked file
            # deleted from the worktree keeps its content in the INDEX, and a
            # blind skip would let a staged secret past the last wall by
            # deleting the file after staging it.
            #
            # It is safe because of what step 3 does. MEASURED 2026-09-01: this
            # script stages with `git add -A` (see the commit step below), and
            # `git add -A` STAGES THE DELETION -- a scratch repo with a file
            # added and then removed from the worktree had an index entry
            # holding the content before `add -A` and no entry after it. So a
            # path absent from the worktree at scan time is absent from the
            # commit this run makes, and there is no content for the push to
            # carry. `tests/test_a_wall_that_refused_over_a_file_it_could_not_push.py`
            # holds that dependency, so narrowing the staging command turns this
            # skip back into a hole and fails there rather than in silence.
            gone.append(rel)
            continue
        except (OSError, UnicodeDecodeError) as exc:
            # Recorded, then refused below. A bare `continue` here meant an
            # engine-routed file whose bytes are not valid UTF-8 -- a note saved
            # as UTF-16, a stray byte in a patch, a transient read error --
            # passed the LAST wall with no record at all, and the push was
            # reported clean over a file nobody had read. The sibling CLI
            # `content-guard.py` closed exactly this on 2026-08-14; the copy in
            # here kept the hole, so the bypassable layer was stronger than the
            # unbypassable one. Genuine binaries never reach this line:
            # `engine_text_files` drops them by suffix first.
            unscanned.append(f"{rel}: {exc}")
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
        if any("@" in rel for rel, _l, _m, _c in findings):
            print(f"{GRAY}A path shown as `file@blobsha` is a version carried by an "
                  f"UNPUSHED COMMIT. Editing the file now does not remove it; the "
                  f"commit range has to be rewritten before this push can go.{RESET}")
        sys.exit(2)
    if gone:
        # Not a refusal. There is no content here for this push to carry, so
        # refusing would block on the gate's own timing. It is still SAID, because
        # a corpus that shrank in silence is a narrowed check printing like a
        # complete one (`.claude/rules/scope-claims.md`).
        print(f"{GRAY}content gate: {len(gone)} path(s) listed in the delta "
              f"vanished before they could be read. `git add -A` stages that "
              f"deletion, so this push carries no content for them:{RESET}")
        for rel in gone:
            print(f"{GRAY}  {rel}{RESET}")
    if unscanned:
        for note in unscanned:
            log_denial(mechanism="push:engine-content-scan", action="push",
                       path=note.split(":", 1)[0], reason="engine-routed file could not be read")
        print(f"{RED}REFUSING TO PUSH — engine-routed file(s) the content gate "
              f"could not read:{RESET}")
        for note in unscanned:
            print(f"  {RED}{note}{RESET}")
        print(f"{GRAY}Unverified is not clean. Re-save the file as UTF-8, fix the read "
              f"error, or give it a binary suffix so the gate skips it deliberately.{RESET}")
        sys.exit(2)


ENGINE_GATE_MARKER = "run-tests.py"
DATA_GATE_MARKER = "heading-os-data-test-gate"


def _git_hooks_dir(repo: Path) -> Path | None:
    """Where git looks for `repo`'s hooks, or None when that cannot be resolved.

    `repo / ".git" / "hooks"` was read directly, and it is only right for an
    ordinary clone. In a LINKED WORKTREE (and in a submodule) `.git` is a
    gitFILE holding `gitdir: <path>`, and git resolves hooks against the COMMON
    gitdir, not the per-worktree one. MEASURED 2026-08-30 in a scratch repo with
    a worktree added: the armed hook lives at `<main>/.git/hooks/pre-push`,
    `git rev-parse --git-path hooks/pre-push` from the worktree names exactly
    that file, and the old expression pointed at a `.git/hooks` directory that
    does not exist -- so `_pre_push_gate_armed` returned False forever and every
    push from a worktree was skipped with "the pre-push test gate is not
    installed" while the gate was armed and would have run. An agent working in
    a `git worktree` is an ordinary layout here.

    Resolved in pure Python rather than by shelling out, because this feeds a
    security gate and every unresolvable shape has to fail CLOSED: an
    unreadable gitfile, a gitfile that does not start with `gitdir:`, or an
    unreadable `commondir` all return None, which the caller reads as "not
    armed". Widening it is what would be dangerous, so it never guesses.
    """
    dot_git = repo / ".git"
    if dot_git.is_dir():
        return dot_git / "hooks"
    if not dot_git.is_file():
        return None
    try:
        line = dot_git.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not line.startswith("gitdir:"):
        return None
    target = line.split(":", 1)[1].strip()
    if not target:
        # `Path("")` is `Path(".")`, which would resolve the hooks dir back onto
        # the worktree itself and answer a question this file cannot answer.
        return None
    gitdir = Path(target)
    if not gitdir.is_absolute():
        gitdir = (repo / gitdir).resolve()
    # A worktree's gitdir carries `commondir`, pointing at the shared .git that
    # actually holds `hooks/`. A submodule's gitdir has no `commondir` and holds
    # its own `hooks/`, so the absence of the file is the answer, not an error.
    commondir = gitdir / "commondir"
    if commondir.is_file():
        try:
            rel = commondir.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return None
        if not rel:
            return None
        common = Path(rel)
        gitdir = common if common.is_absolute() else (gitdir / common).resolve()
    return gitdir / "hooks"


def _pre_push_gate_armed(repo: Path, marker: str = ENGINE_GATE_MARKER) -> bool:
    """True if repo's pre-push hook is installed and runs the regression gate.

    `marker` selects which gate to look for. The engine's hook runs the engine
    suite; a DATA overlay's hook runs that overlay's own tests and then hands off
    to git-lfs. The stock git-lfs hook carries neither marker, so it correctly
    reads as "no gate".

    The pre-push hook (installed by scripts/install-git-hooks.py) is the single
    authoritative test gate -- it runs the suite on EVERY push to the engine, not
    just this path. push-all does NOT run the suite a second time itself (that was
    a redundant double-run, removed 2026-06-20); it only refuses to push when the
    hook is absent, so the gate can never be silently skipped on an un-provisioned
    clone. Mirrors install-git-hooks.check_pre_push (kept inline because that
    module is kebab-named and not importable).

    It deliberately does NOT mirror install-git-hooks.check_pre_push_data, which
    since 2026-08-21 also resolves the engine path stamped into the data hook.
    The two answer different questions and both answers are true: this one asks
    "will a gate run at all", and a data hook with a stale stamp still runs the
    overlay's tests (under a fallback interpreter, saying so on stderr), so
    refusing the push here would block a backup over a degraded gate rather than
    an absent one. `--check` asks "is it correctly installed" and is the surface
    that should go red. Do not collapse them into one predicate."""
    hooks = _git_hooks_dir(repo)
    if hooks is None:
        return False
    hook = hooks / "pre-push"
    try:
        return hook.is_file() and marker in hook.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # `UnicodeDecodeError` is a ValueError, so `except OSError` never caught
        # it. A pre-push hook is an arbitrary executable - a compiled binary, or
        # a shell script carrying one Latin-1 byte in a comment - and this
        # predicate is documented to fail CLOSED on every shape it cannot
        # resolve. MEASURED 2026-09-01: a hook holding `caf\xe9` raised out of a
        # function that answers True or False, past `_attempt` (which absorbs
        # only RepoNotPushable), and ended the backup on a traceback instead of
        # the named refusal. `_git_hooks_dir` above already catches the pair;
        # this is the half that was left behind.
        return False


def run(args, cwd, env=None, check=True, capture=True):
    """Run a git command, returning CompletedProcess. Raises on non-zero when check."""
    return subprocess.run(
        args, cwd=str(cwd), env=env, check=check,
        capture_output=capture, text=True,
    )


def gh_token() -> str | None:
    """Return GH_TOKEN (the variable gh reads), loading the engine .env if needed."""
    if "GH_TOKEN" not in os.environ:
        try:
            load_env(get_workspace_root())  # loads engine .env into os.environ
        except Exception as exc:
            logger.warning("push-all: failed to load engine .env: %s", exc)
    return os.environ.get("GH_TOKEN") or None


def push_repo(name: str, repo: Path, message: str, do_commit: bool, dry_run: bool,
              push_env: dict, is_engine: bool = False, data_root: Path | None = None,
              test_gate: bool = False,
              gate_marker: str = ENGINE_GATE_MARKER) -> None:
    """Commit + push one repo to origin/main, then verify ahead/behind == 0 0."""
    print(f"\n{BOLD}{CYAN}== {name}: {repo} =={RESET}")

    # 0. engine/data leak gate (engine only): the engine clone must be code-only.
    # Unbypassable -- runs before the commit, so even a working tree staged with
    # --no-verify cannot push a data-class artifact out of the engine. The DATA
    # repo legitimately carries private files and is exempt.
    # 0a. secret CONTENT scan, for every repo, BEFORE the commit.
    #
    # It used to run at step 3.5, after this function's own `git add -A && git
    # commit`. Nothing left the machine either way - the push was still refused -
    # but the secret was in local history by then, so the repair was a history
    # scrub instead of an edit. The two walls beside it have always run here, and
    # `_push_delta_files`' own docstring says step 0 is deliberate "so that a tree
    # staged with --no-verify cannot slip past". `scripts/publish-service.py`
    # already scans before its `git add -A` for the same reason.
    #
    # No coverage is lost by moving it: `_push_delta_files` includes untracked
    # files, so the set here is the same one the commit is about to create.
    content_scan(repo)

    if is_engine:
        engine_clean_scan(repo)
        if data_root is not None:
            engine_content_scan(repo, data_root)

    # 1. pre-push secret scan over every file this push would CARRY
    #
    # Tracked AND untracked-not-ignored, not `git ls-files` alone. Step 3 below
    # runs `git add -A`, which makes untracked files tracked, and this wall never
    # ran again - so a credential this very run was about to commit was never
    # tested by it. The gap had no backstop either: `.gitignore` carries
    # `.sessions/` and one exact `outputs/browser/cookies.json`, not a bare
    # `*.session` or `cookies.json` rule, and `scripts/secret-scanner.py` lists
    # `.session` in SKIP_EXTENSIONS - so the content scan returns clean for
    # precisely this file type. A `telegram.session` dropped at the repo root
    # walked through all three layers.
    #
    # `repo_carried_paths` is the shared resolver the routing wall at step 0
    # already uses, so the two walls now agree about what "about to be pushed"
    # means. It runs `-z` for both lists, which is what the paragraph below is
    # about.
    #
    # `-z`, for the reason `_push_delta_files` records four hundred lines up and
    # this call site was left out of. Without it git C-quotes any path holding a
    # non-ASCII byte and wraps it in double quotes, and the DATA clone carries
    # such paths today. The trailing quote defeats every `$`-anchored branch of
    # SECRET_TRACKED, so a tracked `.env`, `*.session` or `cookies.json` under a
    # Cyrillic-named directory was not refused; the leading quote defeats the
    # `.memory-index/` prefix test in step 2 outright. content_scan() is no
    # backstop -- it scans the push DELTA, and this step exists precisely for a
    # credential tracked long before the push.
    carried = repo_carried_paths(repo)
    leaks = [
        f for f in carried
        if SECRET_TRACKED.search(f) and not f.endswith((".example", ".sample", ".template"))
    ]
    if leaks:
        # Counted like the two walls above it, and it is the only refusal in this
        # file that needed adding: the three below are configuration and
        # precondition failures, which the counter deliberately leaves out, while
        # this one is a leak guard proper. content_scan() does not cover it —
        # that scans the push DELTA (_push_delta_files), so a credential file
        # tracked since long before this push is refused here and would otherwise
        # be refused with nothing recording that it happened.
        for rel in leaks:
            log_denial(mechanism="push:secret-tracked-files", action="push",
                       path=rel, reason="secret-like path tracked in the repository")
        print(f"{RED}REFUSING TO PUSH — secret-like tracked files:{RESET}")
        for f in leaks:
            print(f"  {RED}{f}{RESET}")
        print(f"{GRAY}Remove from the index (git rm --cached) and add to .gitignore.{RESET}")
        sys.exit(2)

    # 2. assert the rebuildable index is not going to be pushed
    #
    # `carried`, the same set as step 1 above. This read `git ls-files` alone,
    # so an UNTRACKED and not-ignored `.memory-index/` passed here and was made
    # tracked by `git add -A` three lines down - the same one-step-behind gap the
    # filename wall had.
    if any(f.startswith(".memory-index/") for f in carried):
        print(f"{RED}REFUSING TO PUSH — .memory-index/ would be pushed "
              f"(it is rebuildable and must be gitignored).{RESET}")
        sys.exit(2)

    # 3. commit staged changes
    status = run(["git", "status", "--short"], repo).stdout.strip()
    if status and do_commit:
        if dry_run:
            print(f"{YELLOW}[dry-run]{RESET} would commit:\n{status}")
        else:
            try:
                run(["git", "add", "-A"], repo)
                run(["git", "commit", "-m", message], repo)
            except subprocess.CalledProcessError as exc:
                # The commonest failure here is a pre-commit hook refusing the
                # commit, and `run` captures output -- so without this the
                # operator saw a bare traceback and the scanner's explanation of
                # WHAT it refused was swallowed with the CompletedProcess.
                sys.stdout.write(exc.stdout or "")
                sys.stderr.write(exc.stderr or "")
                print(f"{RED}COMMIT REFUSED in {repo.name} "
                      f"(exit {exc.returncode}) — see the hook output above.{RESET}")
                sys.exit(2)
            head = run(["git", "rev-parse", "--short", "HEAD"], repo).stdout.strip()
            print(f"{GREEN}committed{RESET} {head}: {message.splitlines()[0]}")
    elif status and not do_commit:
        print(f"{YELLOW}uncommitted changes left (--no-commit):{RESET}\n{status}")
    else:
        print(f"{GRAY}no local changes to commit{RESET}")


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
    # `env=push_env`: the precondition must ask git the same question, in the
    # same environment, that the push at the chokepoint will. Without it this
    # check reads the ambient world while the push runs `push_env`.
    objection = remote_objection(repo, token=push_env.get("GH_TOKEN"),
                                 env=push_env)
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
    if test_gate and not _pre_push_gate_armed(repo, marker=gate_marker):
        which = "data overlay" if gate_marker == DATA_GATE_MARKER else "engine"
        raise RepoNotPushable(
            f"the {which} pre-push test gate is not installed, so a push would skip "
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
    require_main_clone(__file__)
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
        _attempt(skipped, "DATA", data, message, not args.no_commit, args.dry_run, push_env,
                 test_gate=True, gate_marker=DATA_GATE_MARKER)
        _attempt(skipped, "ENGINE", engine, message, not args.no_commit, args.dry_run,
                 push_env, is_engine=True, data_root=data, test_gate=True)

    if skipped:
        _report_skips(skipped, args, attempted)

    # Branched on `attempted`, because "Both repos pushed." over the pre-cutover
    # mode -- which announces "Pushing one repo." nine lines up -- is the closing
    # headline claiming a second off-machine copy that does not exist. Every
    # other summary line in this file is careful about exactly that, and this one
    # was not until 2026-08-30.
    headline = "Both repos pushed." if attempted > 1 else "Repo pushed."
    print(f"\n{GREEN}{BOLD}{headline}{RESET}" if not args.dry_run else f"\n{YELLOW}dry-run complete.{RESET}")


if __name__ == "__main__":
    main()
