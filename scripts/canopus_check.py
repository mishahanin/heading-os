#!/usr/bin/env python3
"""The four clauses: is a slice's committed record still telling the truth?

One note per slice says what was approved, where its contract lived, and — once
the slice ships — which commit retired that contract and where its coverage
went. This module is what holds the note to it, over the repository the note is
committed to.

    python scripts/canopus_check.py
    python scripts/canopus_check.py --range origin/main..HEAD --json

**No clause reads a timestamp.** GIT_COMMITTER_DATE and GIT_AUTHOR_DATE are
environment variables the writing session sets, and on 2026-08-06 two of them
put an implementation commit nine hours BEFORE the approval commit it descends
from: a timestamp comparison got the order wrong and `git merge-base
--is-ancestor` got it right. Ancestry and content, never clocks.

    C1  the contract did not move between its approval and the end of its life
    C2  HEAD descends from the approval commit
    C3  the contract was RED at the approval sha, run in a worktree checked out
        THERE — with the implementation present the same contract goes green, so
        a clause reading the working tree reports clean forever
    C4  the target is green at HEAD *and the junit report shows it RAN*, because
        collected is not run and an all-skipped file exits 0

**Cost bound, stated so the first slow run is not a surprise.** C1 and C2 are
two git commands each and run over EVERY note, always. C3 and C4 spawn a
worktree and a test run, so they run ONLY for notes whose `approval_sha` or
`retired_sha` falls inside `--range`, which CI passes as the push range. The CI
job carries `timeout-minutes: 10`, and this keeps the per-push cost proportional
to what the push changed rather than to the number of slices ever recorded.
Without `--range` every clause runs over every note, which is the local
whole-history reading and is deliberately the slow one.
"""
from __future__ import annotations

import argparse
import functools
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))
from scripts.utils.canopus_contract import (  # noqa: E402
    RED_OUTCOMES,
    ContractError,
    contract_interpreter,
    parse_junit,
    pytest_child_env,
)
# The GIT_ scrub, imported rather than spelled a second time. Its docstring
# carries the measurement: this runs inside the engine's git hooks, git exports
# GIT_DIR and GIT_INDEX_FILE to a hook, and a second copy of that rule is one
# rename away from silently resolving the wrong repository.
from scripts.utils.canopus_git import _child_env as git_child_env  # noqa: E402
from scripts.utils.canopus_note import NoteError, note_paths, read_note  # noqa: E402

GIT_TIMEOUT = 60
PYTEST_TIMEOUT = 900
# An endpoint that names no commit: empty, or the all-zero sha git uses for
# "there was nothing before this". See `_range_shas`.
_NULL_END = re.compile(r"0*")


class CheckError(RuntimeError):
    """A clause that could not be MEASURED, as distinct from one that failed.

    Raised inward and never outward: every clause answers `(ok, message)`, so a
    measurement that could not be taken is reported in the message rather than
    crashing the run that was meant to report it.
    """


def _git(root: Path, *argv: str) -> subprocess.CompletedProcess:
    """Run git in *root* and hand back the whole result, returncode included.

    Not `canopus_git.git_output`, which collapses every non-zero exit to None:
    C1 and C2 are exit-code clauses, and 1 (the answer is no) has to be told
    apart from 128 (the question could not be asked).
    """
    try:
        return subprocess.run(
            ["git", "-C", str(root), *argv], capture_output=True, text=True,
            timeout=GIT_TIMEOUT, check=False, env=git_child_env(),
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise CheckError(f"git {' '.join(argv)} could not run: {exc}") from exc


def _pytest(cwd: Path, target: str) -> tuple[int, str]:
    """Run pytest over *target* inside *cwd*; return (exit code, junit XML).

    PYTHONPATH is dropped from the child: C3 runs the contract in a worktree
    checked out at the approval sha, and an inherited PYTHONPATH pointing at the
    CURRENT tree would import the implementation the clause is asserting was
    absent. The PYTEST_ scrub and the flag set are `run_pytest_report`'s, and
    each flag is load-bearing for the reason written there — `junit_family=xunit1`
    above all, because the default family drops the `file` attribute and
    `parse_junit` then matches nothing.
    """
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / "report.xml"
        command = [
            str(contract_interpreter()), "-m", "pytest", str(target),
            "--junit-xml", str(report),
            "-o", "addopts=",
            "-o", "junit_family=xunit1",
            "--import-mode=importlib",
            "--continue-on-collection-errors",
            "-p", "no:cacheprovider",
            "-q",
        ]
        env = pytest_child_env(CANOPUS_NO_ATTEST="1", PYTHONDONTWRITEBYTECODE="1")
        env.pop("PYTHONPATH", None)
        try:
            proc = subprocess.run(
                command, cwd=str(cwd), capture_output=True, text=True,
                timeout=PYTEST_TIMEOUT, check=False, env=env,
            )
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise CheckError(f"the test run could not be started: {exc}") from exc
        if not report.is_file():
            # The child's own last words. A target that no longer exists is a
            # pytest usage error, which writes no report at all, and "no report"
            # alone would hide the one diagnosis the operator needs.
            said = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = "; ".join(said[-2:]) if said else "no output"
            raise CheckError(
                f"pytest wrote no report (exit {proc.returncode}): {tail}"
            )
        try:
            return proc.returncode, report.read_text(encoding="utf-8")
        except OSError as exc:
            raise CheckError(f"the report is unreadable: {exc}") from exc


def _ran(xml_text: str, target: str) -> tuple[int, bool]:
    """Per-file counts for *target*: (tests that RAN, any of them red).

    A skipped test is collected, reported, and never executed, so it is not
    counted here. That is the whole distinction C4 rests on.
    """
    try:
        _counts, outcomes = parse_junit(xml_text)
    except ContractError as exc:
        raise CheckError(str(exc)) from exc
    prefix = target.rstrip("/") + "/"
    mine = [outcome for rel, _name, outcome in outcomes
            if rel == target or rel.startswith(prefix)]
    return sum(1 for outcome in mine if outcome != "skipped"), \
        any(outcome in RED_OUTCOMES for outcome in mine)


def _drop_worktree(root: Path, tree: Path) -> None:
    """Remove the worktree, and prune if it will not go: no stale metadata."""
    try:
        removed = _git(root, "worktree", "remove", "--force", str(tree))
        if removed.returncode != 0:
            print(f"canopus-check: {tree} would not be removed "
                  f"({removed.stderr.strip()}); pruning", file=sys.stderr)
            _git(root, "worktree", "prune")
    except CheckError as exc:
        print(f"canopus-check: the worktree {tree} may be stale: {exc}",
              file=sys.stderr)


def _window_end(root: Path, note: dict) -> str:
    """The commit where the contract's life ends: HEAD, or its retirement.

    A retired note names the commit that REMOVED the contract, so the window
    that must be unmoved ends where the contract last EXISTED. Diffing through
    the removal itself reports a moved contract against every shipped slice,
    from the first one onward — the reading this clause exists to remove. Asking
    whether the path is still there at the recorded sha also covers the other
    authoring convention (a sha naming the last commit at which the contract
    stood) without having to choose between the two.
    """
    retired = note.get("retired_sha")
    if not retired:
        return "HEAD"
    present = _git(root, "cat-file", "-e", f"{retired}:{note['contract']}")
    return retired if present.returncode == 0 else f"{retired}^"


def _reports(clause):
    """A clause ANSWERS. An unmeasurable clause reports; it does not raise."""
    @functools.wraps(clause)
    def wrapper(root, note):
        try:
            return clause(Path(root), note)
        except CheckError as exc:
            return False, (f"{note.get('slug', '?')}: {clause.__name__} could not "
                           f"be measured: {exc}")
    return wrapper


@_reports
def C1(root: Path, note: dict) -> tuple[bool, str]:
    """The contract did not move between its approval and the end of its life."""
    slug, contract, sha = note["slug"], note["contract"], note["approval_sha"]
    end = _window_end(root, note)
    diff = _git(root, "diff", "--quiet", sha, end, "--", contract)
    if diff.returncode == 0:
        return True, f"{slug}: the contract {contract} is unmoved over {sha}..{end}"
    if diff.returncode == 1:
        return False, (f"{slug}: the contract {contract} moved after it was approved "
                       f"({sha}..{end}), so the record describes a target that shifted")
    return False, (f"{slug}: the contract {contract} could not be diffed over "
                   f"{sha}..{end}: {diff.stderr.strip()}")


@_reports
def C2(root: Path, note: dict) -> tuple[bool, str]:
    """HEAD descends from the approval commit. Ancestry, never a clock."""
    slug, sha = note["slug"], note["approval_sha"]
    ancestry = _git(root, "merge-base", "--is-ancestor", sha, "HEAD")
    if ancestry.returncode == 0:
        return True, f"{slug}: HEAD descends from the approval {sha}"
    if ancestry.returncode == 1:
        return False, (f"{slug}: HEAD does not descend from the approval commit "
                       f"{sha}, so the work and the record are on separate histories")
    return False, (f"{slug}: ancestry from {sha} could not be read: "
                   f"{ancestry.stderr.strip()}")


@_reports
def C3(root: Path, note: dict) -> tuple[bool, str]:
    """The contract was RED at the approval sha, in a worktree checked out there.

    The detached worktree is the whole mechanism. Run against the current tree
    the same contract passes — the implementation is present — so a clause that
    reads the checked-out files certifies every slice forever and proves
    nothing.
    """
    slug, contract, sha = note["slug"], note["contract"], note["approval_sha"]
    with tempfile.TemporaryDirectory() as scratch:
        tree = Path(scratch) / "tree"
        added = _git(root, "worktree", "add", "--detach", "-q", str(tree), sha)
        if added.returncode != 0:
            return False, (f"{slug}: the approval {sha} could not be checked out: "
                           f"{added.stderr.strip()}")
        try:
            code, _xml = _pytest(tree, contract)
        except CheckError as exc:
            return False, (f"{slug}: the contract {contract} could not be run at "
                           f"the approval {sha}: {exc}")
        finally:
            _drop_worktree(root, tree)
    if code == 0:
        return False, (f"{slug}: the contract {contract} was already GREEN at its "
                       f"approval {sha}, so freezing it defined nothing")
    return True, (f"{slug}: the contract {contract} was red at its approval {sha} "
                  f"(pytest exited {code})")


@_reports
def C4(root: Path, note: dict) -> tuple[bool, str]:
    """The target is green at HEAD, with evidence from the report that it RAN.

    The target is the promotion for a retired slice and the contract otherwise:
    the contract directory is DELETED on ship, so holding a shipped slice to it
    would only ever measure an absence.
    """
    slug = note["slug"]
    target = note["promoted_to"] if note.get("retired_sha") else note["contract"]
    try:
        code, xml_text = _pytest(root, target)
        ran, red = _ran(xml_text, target)
    except CheckError as exc:
        return False, f"{slug}: {target} did not run at HEAD: {exc}"
    if ran == 0:
        return False, (f"{slug}: {target} ran no tests at HEAD (pytest exited "
                       f"{code}); collected is not run, and a target that runs "
                       "nothing certifies nothing")
    if red or code != 0:
        return False, (f"{slug}: {target} is not green at HEAD (pytest exited "
                       f"{code}, over {ran} test(s) that ran)")
    return True, f"{slug}: {target} ran {ran} test(s) green at HEAD"


def _unreadable(note: dict) -> str:
    """Why this note cannot be checked at all, or an empty string.

    `read_note` parses; it does not validate, so a hand-edited note can reach
    here without the fields the clauses index. Naming the gap is a report; a
    KeyError traceback is a run that stops before the other notes are checked.
    """
    missing = [name for name in ("slug", "approval_sha", "contract")
               if not str(note.get(name, "")).strip()]
    if note.get("retired_sha") and not str(note.get("promoted_to", "")).strip():
        missing.append("promoted_to")
    return f"the note is missing {', '.join(missing)}" if missing else ""


def _row(slug: str, clause: str, ok: bool, message: str) -> dict:
    """One finding, in the shape `--json` prints and the text listing reads."""
    return {"slug": slug, "clause": clause, "ok": ok, "message": message}


def _range_shas(root: Path, commit_range: str | None) -> set | None:
    """Every commit in `A..B`, or None when no range was given (check them all).

    A range that is PRESENT but names no push scopes to NOTHING, which is not
    the same answer as no range at all. CI passes
    `${{ github.event.before }}..${{ github.sha }}`, and `before` is empty on a
    pull_request and on workflow_dispatch, and forty zeros on the first push to
    a new branch. `git rev-list` exits 128 on the zero shape, and that reached
    the operator as a report against a slice rather than as what it is. Widening
    to the whole history instead would be the other wrong answer: the flag being
    present says "scope me to this push", so an unknowable push scopes the
    expensive clauses to nothing, while the flag's ABSENCE keeps the deliberate
    whole-history local reading that runs them over every note.
    """
    if commit_range is None:
        return None
    if any(_NULL_END.fullmatch(end) for end in commit_range.split("..")):
        print(f"canopus-check: {commit_range!r} names no push range, so C3 and C4 "
              "run over nothing; C1 and C2 still run over every note", file=sys.stderr)
        return set()
    listed = _git(root, "rev-list", commit_range)
    if listed.returncode != 0:
        raise CheckError(f"the range {commit_range} could not be read: "
                         f"{listed.stderr.strip()}")
    return set(listed.stdout.split())


def _in_range(root: Path, note: dict, scope: set | None) -> bool:
    """Did this push touch the slice — its approval or its retirement?"""
    if scope is None:
        return True
    for name in ("approval_sha", "retired_sha"):
        sha = note.get(name)
        if not sha:
            continue
        resolved = _git(root, "rev-parse", "--verify", f"{sha}^{{commit}}")
        if resolved.returncode == 0 and resolved.stdout.strip() in scope:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Hold every Canopus slice note to the four clauses.")
    parser.add_argument("--root", default=str(ENGINE_ROOT),
                        help="repository to check (default: the engine)")
    parser.add_argument("--range", dest="commit_range", metavar="A..B",
                        help="run C3 and C4 only for notes whose approval_sha or "
                             "retired_sha falls inside this range; an endpoint "
                             "naming no commit (empty, or the all-zero sha) scopes "
                             "them to nothing rather than to everything")
    parser.add_argument("--json", action="store_true",
                        help="one JSON row per clause on stdout, nothing else")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    rows: list[dict] = []
    try:
        scope = _range_shas(root, args.commit_range)
    except CheckError as exc:
        print(f"canopus-check: {exc}", file=sys.stderr)
        return 1
    for path in note_paths(root):
        slug, clauses = path.stem, []
        try:
            note = read_note(root, slug)
            note.setdefault("slug", slug)
            complaint = _unreadable(note)
            clauses = [] if complaint else [C1, C2]
            if clauses and _in_range(root, note, scope):
                clauses += [C3, C4]
        except (NoteError, CheckError) as exc:
            # `clauses` is emptied too: the raise can land AFTER it was filled
            # (a range that will not resolve), and running clauses over a note
            # this loop has just said it cannot read would print a verdict
            # underneath its own complaint.
            complaint, clauses = str(exc), []
        if complaint:
            rows.append(_row(slug, "note", False, f"{slug}: {complaint}"))
        for clause in clauses:
            rows.append(_row(slug, clause.__name__, *clause(root, note)))
    reported = [row for row in rows if not row["ok"]]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print(f"{'ok    ' if row['ok'] else 'REPORT'} {row['clause']}  "
                  f"{row['message']}")
        print(f"{len(rows)} clause(s) over {len({row['slug'] for row in rows})} "
              f"note(s); {len(reported)} report(s)")
    return 1 if reported else 0


if __name__ == "__main__":
    sys.exit(main())
