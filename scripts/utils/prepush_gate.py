#!/usr/bin/env python3
"""What the pre-push gate runs: the whole suite, or the tests this push can reach.

`scripts/run-tests.py --pre-push` calls `decide()` with the ref lines git writes
to a pre-push hook's stdin, and runs whatever comes back. Nothing else in the
workspace narrows: a bare `python scripts/run-tests.py`, CI, and the night's full
run all still run everything.

WHY THE RANGE COMES FROM STDIN AND NOT FROM `origin/main..HEAD`. Git hands the
hook one line per ref, `<local ref> <local sha> <remote ref> <remote sha>`, and
that is the authoritative statement of what this push adds. `origin/main` is a
remote-tracking ref: it is whatever the last fetch left behind, it may be an hour
stale, it may not exist at all, and on a fresh branch it names a different
history. A gate computed against it narrows against a guess.

THE SAFETY ARGUMENT, in one sentence. The remote sha is already on the remote,
so `.github/workflows/ci.yml` has run the whole suite over it; the range
`<remote sha>..<local sha>` is exactly the part of this push that no full run has
seen, and day mode selects the tests that range can reach plus the mandatory
core. The night's full run (`scripts/day-mode.py nightly`) is the backstop for
what selection can miss, and `scripts/utils/day_mode.py` states those bounds
itself. Read it before trusting a narrowed run.

WHAT IS NOT NARROWED, and this is the part that must never move.
`scripts/push-all.py` runs `content_scan`, `engine_content_scan` and
`engine_clean_scan` before pytest and independently of it. Those are the leak and
secret walls. They are untouched here, they still run on every push, and they are
not tests. The 32 content-leak TESTS prove the wall's CODE is right, which is a
different question, and day mode selects them whenever that code changes.

EVERY DOUBT WIDENS. A gate that narrows when it is unsure is a hole with a
speed-up painted on it, so each condition below returns the full suite with a
reason a human can read, and `Decision.reason` is printed on every invocation
including the narrowed ones. A gate that silently fell back to everything would
be indistinguishable from one that never narrowed, and nobody would notice the
day it stopped working.

    stdin empty, or a line that is not four fields   invoked by hand, or not by git
    the remote sha is all zeros                      a branch the remote has never seen
    the local sha is all zeros                       a ref deletion, carrying no commits
    the remote sha is not an ancestor                a force push, or a diverged history
    the local sha is not HEAD                        the pushed commit is not what pytest
                                                     would run against
    the working tree is dirty                        same reason, from the other side
    a changed file no route reaches                  day mode could not decide
    day mode raises, or git does                     the selector has no answer
    anything else at all                             the bare `except Exception` below

MEASURED 2026-09-04, replaying the last twenty commits of this repository, each
against a scratch checkout of its OWN tree rather than today's. Fifteen narrowed,
to between 155 and 191 of about 1078 test files. Five widened, and every one of
them widened on the last condition in the table: a changed `.gitignore`, a
generated `docs/*.html`, a `scripts/dev/` helper, and one commit that changed six
`.claude/rules/*.md` files at once. A sixth commit selected 1076 of 1076 by
itself, because it changed a file `tests/conftest.py` reads and a conftest is a
proxy for its whole subtree; that is day mode answering honestly, not the gate
giving up.

THE ONE NUMBER THAT DECIDES WHETHER THIS IS SAFE. Across all twenty commits,
every test file the commit ITSELF carried was selected. A commit's own regression
tests are what the full run would have caught its defect with, and the narrowed
run runs all of them, every time.

Tests: tests/test_a_push_gate_that_narrowed_on_a_guess.py
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from scripts.utils.day_mode import DayModeError, build_index, select
# The NUL-separated git reader is IMPORTED, not reimplemented. It decodes bytes
# with `surrogateescape` and splits in the same function, and its docstring
# carries the measurement behind that: `text=True` on a `-z` invocation rewrites a
# CR inside a path to LF, so a file whose name carries one comes back as a name
# that is not on disk and every route keyed on it matches nothing. A second copy
# here is this repository's dominant defect shape, a fix that lands in one of N
# copies, and `tests/test_a_reader_that_lost_a_byte_on_the_way_in.py` caught this
# module doing exactly that on 2026-09-04: it split on NUL locally while the
# decode sat one function away, where the guard could not see it and where a
# later edit could have removed it. Reaching for the underscore name is
# deliberate, and preferable to owning a duplicate.
from scripts.utils.day_mode import _git_z


class _Widen(Exception):
    """Internal: abandon narrowing and run everything, carrying the reason."""


@dataclass(frozen=True)
class RefUpdate:
    """One line of a pre-push hook's stdin."""

    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str


@dataclass
class Decision:
    """What to run and why. `full` is the safe answer and the default."""

    full: bool
    reason: str
    tests: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    total_tests: int = 0
    notes: list[str] = field(default_factory=list)


def is_null_sha(sha: str) -> bool:
    """True for git's "no such object" sha: all zeros.

    Asked of the SHAPE and not the length, because that is 40 characters under
    sha1 and 64 under sha256, and this repository will not notice the day it
    migrates. An empty string is not a null sha, it is an unparseable line, and
    the caller has already refused it.
    """
    return bool(sha) and set(sha) == {"0"}


def parse_refs(text: str) -> list[RefUpdate]:
    """The ref lines git wrote, or raise `_Widen` naming what was wrong.

    Blank lines are dropped rather than refused: `$(cat)` in the hook strips the
    trailing newline and `printf` puts exactly one back, so a stray blank is a
    shell artefact and not a statement about the push. A line that is not four
    whitespace-separated fields is refused, because that means whatever wrote to
    this stdin was not git.
    """
    refs: list[RefUpdate] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4:
            raise _Widen(
                f"stdin line {line.strip()!r} is not git's "
                f"'<local ref> <local sha> <remote ref> <remote sha>'"
            )
        refs.append(RefUpdate(*fields))
    if not refs:
        raise _Widen(
            "stdin carried no ref lines, so the range this push adds is unknown "
            "(the hook was invoked by hand, or by something that is not git)"
        )
    return refs


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=False
    )


def _git_out(root: Path, *args: str) -> str:
    """One decoded line of git output. NEVER used with `-z`; see `_paths` below."""
    result = _git(root, *args)
    if result.returncode != 0:
        raise _Widen(
            f"git {' '.join(args)} failed: "
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )
    return result.stdout.decode("utf-8", "surrogateescape")


def _paths(root: Path, *args: str) -> list[str]:
    """NUL-separated git paths, read by day mode's reader and never split here.

    `DayModeError` is translated at the boundary so a git failure widens with the
    message git actually gave, rather than arriving at the generic handler as an
    exception type the reader has to decode.
    """
    try:
        return _git_z(root, *args)
    except DayModeError as exc:
        raise _Widen(str(exc)) from exc


def _changed_in_range(root: Path, ref: RefUpdate) -> set[str]:
    """The net file change between what the remote holds and what is being pushed.

    NET, deliberately. A file touched and reverted inside the range leaves the
    pushed tree byte-identical to the tree CI already ran the whole suite over,
    so there is nothing new to test in it.
    """
    return set(_paths(root, "diff", "--name-only", "-z", ref.remote_sha, ref.local_sha))


def _refuse_unless_ancestor(root: Path, ref: RefUpdate) -> None:
    result = _git(root, "merge-base", "--is-ancestor", ref.remote_sha, ref.local_sha)
    if result.returncode == 0:
        return
    if result.returncode == 1:
        raise _Widen(
            f"{ref.remote_sha[:12]} on {ref.remote_ref} is not an ancestor of "
            f"{ref.local_sha[:12]}: this is a force push or a diverged history, "
            f"so the range is not what this push adds"
        )
    raise _Widen(
        f"git could not compare {ref.remote_sha[:12]} with {ref.local_sha[:12]}: "
        f"{result.stderr.decode('utf-8', 'replace').strip()}"
    )


def _refuse_unless_tree_matches(root: Path, refs: list[RefUpdate]) -> None:
    """pytest runs against the working tree, so the tree must BE the pushed commit.

    Two ways it is not, and both are ordinary commands rather than exotica.
    `git push origin feature:main` pushes a commit that is not HEAD, and the
    selection would then be computed from one tree while the tests ran against
    another. An uncommitted edit does the same thing from the other side: the
    range says one thing and the files pytest imports say another.
    """
    head = _git_out(root, "rev-parse", "HEAD").strip()
    for ref in refs:
        if ref.local_sha != head:
            raise _Widen(
                f"{ref.local_ref} pushes {ref.local_sha[:12]}, which is not HEAD "
                f"({head[:12]}), so the tests would run against a different tree "
                f"than the one being pushed"
            )
    # WILL THIS EVER BE CLEAN? Asked because a condition that always fires is a
    # gate that never narrows, and nobody would notice the difference.
    # ESTABLISHED 2026-09-04 by reading the code path and sampling the main
    # clone, NOT by watching a real push: `scripts/push-all.py` runs
    # `git add -A && git commit` at step 3 and pushes at step 5, with nothing but
    # git reads in between, so the tree is clean at hook time by construction.
    # Every ambient writer into the engine clone is gitignored, including the one
    # live daemon that writes there (`sync-exchange-daemon`, into
    # `.sync-exchange/`), pytest's own `.pytest_cache/` and `__pycache__/`, and
    # day mode's `.cache/day-mode/`. Eleven samples of the main clone's
    # `git status --porcelain` over four minutes were all empty.
    #
    # The named exception is `push-all.py --no-commit`, which pushes a dirty tree
    # deliberately. It widens here, and that is the right answer rather than a
    # cost: what is on disk is then genuinely not what is being pushed.
    dirty = _paths(root, "status", "--porcelain", "-z")
    if dirty:
        # The entry is `XY<space>PATH`, so the path starts at 3. Sliced, not
        # split: a status prefix can contain a space, and a `split()` here would
        # lose a path that has one. Reported for the operator to read, never
        # matched against anything.
        raise _Widen(
            f"the working tree carries {len(dirty)} uncommitted change(s), so the "
            f"files pytest would import are not the commits being pushed "
            f"(first: {dirty[0][3:] or dirty[0]})"
        )


def _decide(root: Path, stdin_text: str) -> Decision:
    refs = parse_refs(stdin_text)

    changed: set[str] = set()
    for ref in refs:
        if is_null_sha(ref.local_sha):
            raise _Widen(
                f"{ref.remote_ref} is being deleted, which carries no commits to "
                f"select against"
            )
        if is_null_sha(ref.remote_sha):
            raise _Widen(
                f"the remote has never seen {ref.remote_ref}, so no full run has "
                f"covered its base"
            )
        _refuse_unless_ancestor(root, ref)
        changed |= _changed_in_range(root, ref)

    _refuse_unless_tree_matches(root, refs)

    index = build_index(root)
    selection = select(index, sorted(changed))

    # THE SHARPER RULE THAT WAS CONSIDERED AND MEASURED AWAY. An undecided file is
    # one no route reaches, and it looked like it should split in two: a file some
    # test MENTIONS, where the full suite is the only cover, against a file no test
    # names at all, where a full run exercises it no more than the mandatory core
    # already does. Widening only on the first would have narrowed more pushes.
    #
    # MEASURED 2026-09-04 with day mode's own `_mentions` predicate over all 1081
    # test files, against the thirteen undecided files the twenty-commit replay
    # actually produced: ELEVEN of the thirteen are mentioned, including
    # `.gitignore`, `docs/RULES-REFERENCE.md` and four `.claude/rules/*.md`. Only
    # `reference/skill-router-notes.md` and `reference/persistence-detail.md` were
    # not. So the sharper rule would have widened in almost the same cases, for a
    # new mechanism and a second read of every test file. It is not built, and this
    # paragraph is here so the next person does not re-derive it.
    if selection.undecided:
        raise _Widen(
            f"day mode could not decide which tests reach "
            f"{len(selection.undecided)} changed file(s), first "
            f"{selection.undecided[0]}"
        )
    if not selection.tests:
        raise _Widen(
            "day mode selected no tests at all, which means the mandatory core "
            "is empty and the index is not answering"
        )

    span = ", ".join(
        f"{r.remote_ref} {r.remote_sha[:12]}..{r.local_sha[:12]}" for r in refs
    )
    return Decision(
        full=False,
        reason=(
            f"{len(selection.tests)} of {selection.total_tests} test files for "
            f"{len(selection.changed)} changed file(s) in {span}"
        ),
        tests=selection.tests,
        changed=selection.changed,
        total_tests=selection.total_tests,
        notes=[f"{route}: {count}" for route, count in selection.by_route().items()],
    )


def decide(root: Path, stdin_text: str) -> Decision:
    """Narrow this push's test run, or return the full suite and say why.

    Never raises. Every failure path, anticipated or not, lands on the full
    suite: the bare `except Exception` is the point of this function and not an
    oversight. A selector that crashed and let the push through would be the one
    defect this whole gate exists to make impossible.
    """
    try:
        return _decide(Path(root), stdin_text)
    except _Widen as widen:
        return Decision(full=True, reason=str(widen))
    except Exception as exc:  # noqa: BLE001 - see the docstring; widening is the handler
        return Decision(
            full=True,
            reason=(
                f"the gate could not narrow safely: "
                f"{type(exc).__name__}: {exc}"
            ),
        )
