"""A test fixture registered its worktrees beside the operator's live ones.

Two defects, one root, and the second is the one that matters.

THE SMALL ONE. `armed_main_clone`'s teardown was `shutil.rmtree(target)`. That
is complete for what the fixture normally builds, a `git clone`, which registers
nothing; it is not complete for a linked worktree, whose entry lives in the
shared git directory and outlives the checkout, the session and the branch.
MEASURED 2026-09-03: a mutation replacing that fixture's `git clone` with
`git worktree add` left 8 prunable registrations named `main-clone-under-test`,
`...1` ... `...7`, each `gitdir` pointing at a `/tmp/pytest-of-...` path that no
longer existed.

THE REAL ONE. `temporary_worktree` ran `git worktree add` with `cwd` set to THIS
checkout, and this checkout's `--git-common-dir` is HELM's `.git`. So the suite's
throwaway worktrees were registered in the very directory holding the operator's
live YARDs, and any mistake in the cleanup code reached them.

It reached them the same day. A mutation planted against the cleanup helper
replaced `shutil.rmtree(registration)` with

    for entry in registration.parent.iterdir():
        shutil.rmtree(entry, ignore_errors=True)

`registration.parent` IS `<helm>/.git/worktrees`, so `iterdir()` listed every
worktree of the repository. Nothing about the target was widened: the target was
REPLACED by the parent directory's full listing, and the loop emptied it. The
directory was left with nothing in it, and the live YARD the suite was running
in lost its own registration -- `git rev-parse --git-common-dir` exited 128, and
every further command in that session was refused until the operator rebuilt the
entry by hand from HELM. The mutation was caught, as designed. Catching it did
not undo it.

Hence the repair is ISOLATION, not more careful deletion: the code being careful
is the code under test, so its care cannot be relied on. `temporary_worktree`
now clones first (shared object database, so it stays cheap) and adds its
worktree to that clone. `test_the_fixture_registers_nowhere_near_the_operators_
live_worktrees` is the assertion that holds it there, and it was written the
wrong way round at first -- it demanded `registration.parent == <helm shared
dir>`, encoding the defect as the expectation.

Three facts about reading a registration, each measured rather than assumed:

  * a LINKED worktree's `.git` is a FILE reading `gitdir: <shared>/worktrees/<n>`
    and a MAIN clone's is a DIRECTORY, so one read answers both cases;
  * the entry's NAME cannot be predicted -- git appends a collision suffix, and
    four concurrent xdist workers produced four different names for one basename;
  * so it must be read while the checkout still exists, because the teardown
    that needs it destroys the file that names it.

AND NEVER `git worktree prune`. Prune reaches every worktree of this repository,
including ones other processes hold open right now.

A FOURTH fact, learned the day after and by this file failing. `.git/worktrees`
does not always exist: git creates it with the first linked worktree and deletes
it with the last, so a repository with none has no such directory, and that is
its normal state rather than a broken one. Two tests here read it with a bare
`.iterdir()`, which was green only because the author's own YARD was registered
in it. MEASURED 2026-09-04: with the last YARD deleted, `2 failed, 7 passed`,
both `FileNotFoundError: <helm>/.git/worktrees`. The verdict was being decided by
the state of the machine instead of by the code -- in a file about worktree
registrations. `_registered_names` answers for both states, and
`test_the_two_states_of_the_shared_directory_are_both_read` builds both in a
clone of its own so neither depends on what the operator's worktrees do today.

Run: python3 -m pytest tests/test_a_teardown_that_deleted_the_directory_and_kept_the_registration.py
"""
from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.conftest import (  # noqa: E402
    drop_worktree_registration,
    own_worktree_registration,
)

CONFTEST = ROOT / "tests" / "conftest.py"


def _shared_worktrees(checkout: Path = ROOT) -> Path:
    common = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                            cwd=str(checkout), capture_output=True, text=True,
                            check=True).stdout.strip()
    return (Path(checkout) / common).resolve() / "worktrees"


def _registered_names(shared: Path) -> frozenset[str] | None:
    """The entries under `shared`, or None when the directory does not exist.

    ABSENT IS A LAWFUL STATE, not a broken environment. git creates
    `.git/worktrees` with the first linked worktree and REMOVES it with the
    last, so a repository that has never had one -- or has just lost the only
    one it had -- does not have the directory at all. MEASURED 2026-09-04: with
    the last YARD deleted, the two tests below raised

        FileNotFoundError: .../.heading-os/.git/worktrees

    from a bare `.iterdir()`, so their verdict was decided by whether a worktree
    happened to exist on the machine rather than by the code they test. That is
    the same defect this whole file is about, one level up.

    None rather than an empty set, deliberately: "no directory" and "an empty
    directory" are different states, and a caller asserting that an action
    changed nothing must be able to catch an action that CREATED the directory.
    """
    try:
        return frozenset(path.name for path in shared.iterdir())
    except FileNotFoundError:
        return None


# ============================================================
# Reading the registration: both shapes of `.git`
# ============================================================

def test_a_linked_worktree_reports_the_entry_it_is_registered_under(
        temporary_worktree):
    registration = own_worktree_registration(temporary_worktree)

    assert registration is not None, (
        "a real linked worktree reported no registration, so nothing would be "
        "cleaned up and the leak is unchanged")
    assert registration.is_dir(), registration


def test_the_fixture_registers_nowhere_near_the_operators_live_worktrees(
        temporary_worktree):
    """The structural repair, and the one that makes the rest safe.

    This assertion read `registration.parent == _shared_worktrees()` when it was
    written, which encoded the defect as the expectation. The fixture used to
    run `git worktree add` with `cwd` set to this checkout, whose common git
    directory is HELM's, so its worktrees were registered in the same directory
    as the operator's live YARDs. It now clones first and registers in that
    clone.
    """
    registration = own_worktree_registration(temporary_worktree)

    assert registration is not None
    assert registration.parent != _shared_worktrees(), (
        f"the test fixture registered a worktree in {_shared_worktrees()}, "
        f"beside the operator's live YARDs. Any mistake in the cleanup code "
        f"then reaches them, which is exactly what happened on 2026-09-03.")
    assert _shared_worktrees() not in registration.parents, registration


def test_using_the_fixture_leaves_the_shared_directory_untouched(
        temporary_worktree):
    """Behaviour, not source inspection. The count is read while a worktree of
    the fixture is alive, which is the only moment an entry could appear.

    Read through `_registered_names`, so an absent shared directory is one of
    the two states this asserts over rather than a crash: if this repository has
    no linked worktree, the fixture must not have CREATED the directory either,
    and `None == None` says exactly that.
    """
    entries = _registered_names(_shared_worktrees())
    assert temporary_worktree.is_dir()
    assert _registered_names(_shared_worktrees()) == entries


def test_a_main_clone_reports_none_because_it_has_no_registration(
        armed_main_clone):
    """The other direction, and the one that keeps this safe.

    A helper that returned a path here would hand the teardown something to
    delete inside a repository the fixture does not own.
    """
    assert own_worktree_registration(armed_main_clone) is None, (
        "a main clone was reported as having a worktree registration")


def test_a_path_that_is_not_a_checkout_reports_none(tmp_path):
    assert own_worktree_registration(tmp_path / "nothing-here") is None
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / ".git").write_text("not a gitdir line", encoding="utf-8")
    assert own_worktree_registration(plain) is None


# ============================================================
# The leak, reproduced, then cleaned
# ============================================================

def test_deleting_the_directory_alone_leaves_the_registration(tmp_path):
    """The defect itself, made to happen before it is repaired.

    This is the mutated fixture's exact behaviour: `git worktree add`, then a
    teardown that removes only the directory.
    """
    # Its OWN clone, for the reason this file is about: a `git worktree add`
    # run in this checkout registers in the operator's HELM.
    origin = tmp_path / "origin"
    cloned = subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(ROOT), str(origin)],
        capture_output=True, text=True)
    if cloned.returncode != 0:
        pytest.skip(f"could not clone: {cloned.stderr.strip()}")

    target = tmp_path / "main-clone-under-test"
    created = subprocess.run(
        ["git", "worktree", "add", "--detach", str(target), "HEAD"],
        cwd=str(origin), capture_output=True, text=True)
    if created.returncode != 0:
        pytest.skip(f"git worktree add failed: {created.stderr.strip()}")

    registration = own_worktree_registration(target)
    try:
        assert registration is not None and registration.is_dir()
        assert registration.parent != _shared_worktrees(), (
            "even this reproduction must not register beside the operator's "
            "live YARDs")

        # The old teardown, in full.
        shutil.rmtree(target, ignore_errors=True)
        assert registration.is_dir(), (
            "the registration vanished with the directory, so there was never "
            "a leak to fix and this whole file measures nothing")

        # The repair, and it must remove exactly one entry.
        before = set(registration.parent.iterdir())
        drop_worktree_registration(registration)
        after = set(registration.parent.iterdir())

        assert not registration.exists(), "our own registration survived"
        assert before - after == {registration}, (
            f"the cleanup removed more than its own entry: "
            f"{(before - after) - {registration}}")
    finally:
        shutil.rmtree(target, ignore_errors=True)
        drop_worktree_registration(registration)


def test_dropping_nothing_is_a_no_op():
    """A teardown runs on the path where the fixture skipped, too."""
    before = _registered_names(_shared_worktrees())
    drop_worktree_registration(None)
    drop_worktree_registration(_shared_worktrees() / "does-not-exist")
    assert _registered_names(_shared_worktrees()) == before


def test_the_two_states_of_the_shared_directory_are_both_read(tmp_path):
    """Both machine states, modelled, because the machine picks one for you.

    The two tests above read THIS repository's shared directory, so on any given
    day they exercise whichever state the operator's worktrees happen to leave
    it in. This one builds both in a clone of its own and asserts the reader and
    the teardown are total over them.

    The failing half is the absent one: against the version of this file that
    called `.iterdir()` directly, the first assertion below raises
    FileNotFoundError. That is not hypothetical -- it is the 2026-09-04 push
    failure that produced this test, and it appeared the moment the last YARD
    was deleted, in a file about worktree registrations.
    """
    absent = tmp_path / "no-worktrees"
    cloned = subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(ROOT), str(absent)],
        capture_output=True, text=True)
    if cloned.returncode != 0:
        pytest.skip(f"could not clone: {cloned.stderr.strip()}")

    shared = _shared_worktrees(absent)
    assert not shared.exists(), (
        "a fresh clone already had a worktrees directory, so the absent state "
        "was never modelled and this test measures nothing")
    assert _registered_names(shared) is None
    drop_worktree_registration(shared / "never-existed")
    assert not shared.exists(), "a no-op teardown created the directory"

    # The other state, in the same clone: one linked worktree, so git creates
    # the directory. Registered in the clone, never in this checkout.
    linked = tmp_path / "linked"
    created = subprocess.run(
        ["git", "worktree", "add", "--detach", str(linked), "HEAD"],
        cwd=str(absent), capture_output=True, text=True)
    if created.returncode != 0:
        pytest.skip(f"git worktree add failed: {created.stderr.strip()}")

    registration = own_worktree_registration(linked)
    try:
        assert registration is not None
        present = _registered_names(shared)
        assert present is not None and registration.name in present
        drop_worktree_registration(shared / "never-existed")
        assert _registered_names(shared) == present, (
            "a no-op teardown changed a directory that does exist")
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(linked)],
                       cwd=str(absent), capture_output=True, text=True)
        drop_worktree_registration(registration)


# ============================================================
# The prohibition, asked of the syntax tree
# ============================================================

def test_the_test_harness_never_prunes_the_shared_git_directory():
    """Asked of the AST, not of the text.

    A substring scan for "prune" goes red the moment a comment explains why
    pruning is forbidden, which teaches the next author to delete the
    explanation. This looks for the argument in a real call instead.
    """
    tree = ast.parse(CONFTEST.read_text(encoding="utf-8"))

    literals = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for argument in node.args:
            if not isinstance(argument, (ast.List, ast.Tuple)):
                continue
            words = [e.value for e in argument.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if not words:
                continue
            literals += 1
            assert not ("worktree" in words and "prune" in words), (
                f"tests/conftest.py runs `git worktree prune`: {words}. That "
                f"reaches every worktree of this repository, including ones "
                f"other sessions are using.")

    # A floor. With no argv literals found the loop above asserted nothing, and
    # this test would pass over a conftest that had been rewritten past it.
    assert literals >= 5, (
        f"only {literals} argv list literal(s) found in tests/conftest.py; the "
        f"scan is looking at the wrong thing")


def test_both_fixtures_go_through_the_one_helper():
    """The shared-root obligation, checked rather than promised.

    This technique existed once, in `temporary_worktree`, and the fix for
    `armed_main_clone` was a second copy of it until it was extracted. A second
    copy is the one that stops being fixed.
    """
    source = CONFTEST.read_text(encoding="utf-8")
    tree = ast.parse(source)

    callers = {node.name for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef)
               and "own_worktree_registration" in ast.dump(node)
               and node.name != "own_worktree_registration"}

    assert {"armed_main_clone", "temporary_worktree"} <= callers, (
        f"a fixture stopped using the shared helper: {sorted(callers)}")
