"""The frozen test contract for Canopus wire 2.2.

Authored at the /pre-impl gate, before any of the implementation existed, and
frozen on approval. This file is the claim the slice is making:

  blinding the gate is no longer profitable. Before this slice, one environment
  variable converted a genuine LOSS OF LOCK (exit 1) into LOCK HELD (exit 0) at
  zero cost to the builder. After it, every route to "I cannot see the anchor's
  repository" resolves RED, which is strictly worse for the builder than doing
  nothing at all.

Six tests. The FIRST one is the one an adversarial reading of an earlier draft
put here, and it is the most important: without a test that reaches GREEN through
the gate on a BOUND manifest, an implementation that simply answers red whenever
a binding exists passes every other test in this file. So does every wrong
definition of repository identity, because every wrong definition reads red.

WHY THIS FILE CARRIES ITS OWN FIXTURES. A frozen contract that imports its
fixtures from an unfrozen module can be steered without moving a frozen byte:
change `tree` in tests/test_canopus_gate.py and every test here measures
something else.

WHY IT COMPUTES THE REPOSITORY IDENTITY ITSELF. `_repo_identity` does not call
the implementation's `repo_identity`. A contract that imports the definition it
measures agrees with any wrong definition. Note what this does and does not buy:
it catches a wrong definition that reads GREEN, and it is blind to one that reads
RED, which is why the first test below is load-bearing.

WHY THE FIXTURES SCRUB GIT_*. This suite runs inside this repository's pre-commit
and pre-push hooks, and git exports GIT_DIR and GIT_INDEX_FILE to a hook. Without
the scrub the contract's own git calls would resolve the OUTER repository while
the implementation resolves the inner one, producing a false red in a file that
cannot be edited in place, and GIT_INDEX_FILE would write a fixture commit into
the outer repository's index.

Imports of code that does not exist yet live inside test bodies, so the file
collects and can be frozen.
"""
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.utils.canopus_freeze import (
    APPROVED,
    build_manifest,
    verify_manifest,
    write_freeze,
)
from scripts.utils.canopus_gate import freeze_gate
from scripts.utils.canopus_git import resolve_anchor

STAMP = "2026-01-01T00:00:00+00:00"

# Every variable git reads to redirect repository discovery, plus the two a hook
# exports. Parametrised over in the poisoning test rather than reduced to the one
# that happens to be famous: a fix that names GIT_DIR alone is the defect this
# project has hit seven times, a guard covering the case in front of its author.
POISONS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_INDEX_FILE",
    "GIT_CEILING_DIRECTORIES",
)


def _git(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    """git, with every GIT_* variable removed from the child environment."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(directory), *argv],
                          capture_output=True, text=True, check=False, env=env)


def _git_init(directory: Path) -> None:
    """Make *directory* a repository, so the gate artifact has a HEAD to read."""
    for argv in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "builder@example.invalid"],
        ["config", "user.name", "Builder"],
    ):
        assert _git(directory, *argv).returncode == 0


def _git_commit(directory: Path, message: str) -> None:
    assert _git(directory, "add", "-A").returncode == 0
    assert _git(directory, "commit", "-q", "-m", message).returncode == 0


def _repo_identity(directory: Path):
    """(is a repository, identity) computed WITHOUT the implementation.

    The identity is sha256 over the sorted root commits, newline-joined: the
    property that survives relocation, which a toplevel path does not.
    """
    top = _git(directory, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return (False, "")
    roots = _git(Path(top.stdout.strip()), "rev-list", "--max-parents=0", "HEAD")
    lines = sorted(line.strip() for line in roots.stdout.splitlines() if line.strip())
    if not lines:
        return (True, "")
    return (True, hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest())


def _bound_manifest(tree: Path, anchor: Path) -> dict:
    """A manifest frozen against the repository the anchor really lives in."""
    in_repo, identity = _repo_identity(anchor.parent)
    return build_manifest([tree / "tests"], tree, label="demo", frozen_at=STAMP,
                          anchor=anchor,
                          anchor_repo={"in_repo": in_repo, "identity": identity})


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_alpha.py").write_text("def test_a():\n    assert True\n")
    return root


@pytest.fixture
def anchor(tmp_path: Path) -> Path:
    path = tmp_path / "outside" / "gate-artifact.md"
    path.parent.mkdir(parents=True)
    path.write_text("# gate artifact\n")
    return path


def test_a_committed_approval_in_the_bound_repository_reaches_lock_held(
    tree, anchor, capsys
):
    """Row one of the resolution matrix, and the test that makes the rest mean
    something.

    Every other test in this file expects a red, or exercises the unbound path.
    An implementation that answers red whenever a binding exists therefore passes
    all of them, and so does every wrong definition of repository identity: a
    toplevel path, an unsorted join, a different separator, a trailing newline.
    Each of those turns EVERY bound freeze permanently LOSS OF LOCK, which is the
    worst outcome available to this slice, and nothing else here can see it.

    So this one asserts a GREEN through the gate on a bound manifest with a
    committed approval, and asserts the approval axis directly rather than
    through printed text, because the gate prints the approval line only when it
    is NOT approved.
    """
    _git_init(anchor.parent)
    _git_commit(anchor.parent, "the gate artifact, before approval")
    manifest = _bound_manifest(tree, anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n", encoding="utf-8")
    _git_commit(anchor.parent, "the approval")

    assert freeze_gate(tree) == 0
    out = capsys.readouterr().out
    assert "LOCK HELD" in out
    assert "APPROVAL UNVERIFIED" not in out
    assert resolve_anchor(manifest).approval == APPROVED


@pytest.mark.parametrize("variable", POISONS)
def test_a_poisoned_git_environment_can_never_produce_green(
    tree, anchor, variable, monkeypatch, capsys
):
    """SC-1. The Critical wire 2.1 shipped open, at the surface that fires.

    Measured before this slice: the control run printed LOCK UNCONFIRMED and
    exited 0, and the run with GIT_DIR set to an unrelated path printed LOCK
    HELD and exited 0, because the anchor's repository went invisible and
    resolution fell back to a working copy carrying a line no human committed.

    The assertion is directional, not an equality. Poisoning the environment may
    legitimately make the answer WORSE, which is exactly what the fix does; what
    it may never do is make it better. An equality assertion would have failed
    the finished slice and passed the broken one.

    Parametrised over the whole family. A fix that names GIT_DIR alone leaves
    the bypass at the same price through the next variable, and the spec's
    central argument is that a prefix beats a denylist. Some of these may not
    blind git at all; those runs simply behave like the control and still may
    not be green.
    """
    _git_init(anchor.parent)
    _git_commit(anchor.parent, "the gate artifact, with no approval in it")
    manifest = _bound_manifest(tree, anchor)
    write_freeze(tree, manifest)
    with anchor.open("a", encoding="utf-8") as handle:
        handle.write(f"\ncanopus-anchor: {manifest['root']}\n")

    control = freeze_gate(tree)
    control_out = capsys.readouterr().out
    # Pinned so a gate silenced everywhere cannot satisfy this test by printing
    # nothing in both runs.
    assert control == 0
    assert "LOCK UNCONFIRMED" in control_out

    monkeypatch.setenv(variable, str(tree / "nowhere"))
    poisoned = freeze_gate(tree)
    poisoned_out = capsys.readouterr().out

    assert "LOCK HELD" not in poisoned_out


def test_hiding_the_repository_reddens_the_lock(tree, anchor, capsys):
    """SC-2. The second door on the same seam.

    Renaming .git costs a builder exactly as little as exporting a variable, so
    closing only the variable would leave the bypass at the same price. The lock
    is otherwise genuinely held here: the anchor carries the approved hash and
    nothing in the contract moved, so a green answer would be about the
    repository going missing and nothing else.
    """
    _git_init(anchor.parent)
    _git_commit(anchor.parent, "the gate artifact")
    manifest = _bound_manifest(tree, anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n", encoding="utf-8")
    (anchor.parent / ".git").rename(anchor.parent / ".git-hidden")

    assert freeze_gate(tree) == 1
    assert "LOSS OF LOCK" in capsys.readouterr().out


def test_a_substituted_repository_reddens_the_lock(tree, anchor, capsys):
    """SC-3. Identity, not mere presence.

    The substitute repository is real, and it carries a COMMITTED anchor line
    holding exactly the hash the gate is looking for. A binding that recorded
    only "the anchor was inside some repository" reads this as approved. Only an
    identity that survives the substitution tells the two apart.
    """
    _git_init(anchor.parent)
    _git_commit(anchor.parent, "the gate artifact")
    manifest = _bound_manifest(tree, anchor)
    write_freeze(tree, manifest)

    shutil.rmtree(anchor.parent / ".git")
    _git_init(anchor.parent)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n", encoding="utf-8")
    _git_commit(anchor.parent, "a fresh repository carrying a forged approval")

    assert freeze_gate(tree) == 1
    assert "LOSS OF LOCK" in capsys.readouterr().out


def test_a_plain_folder_anchor_still_reaches_lock_held(tree, anchor, capsys):
    """SC-4. The supported case the fix must not take away.

    An operator on a fresh public clone with no data overlay behind it keeps a
    gate artifact as a file in a folder. That case has no repository to bind to,
    so the working copy governs and the lock can be held. What it can never
    reach is APPROVED, because there is nothing to attribute the approval to.
    """
    manifest = _bound_manifest(tree, anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n", encoding="utf-8")

    assert freeze_gate(tree) == 0
    out = capsys.readouterr().out
    assert "LOCK HELD" in out
    assert "APPROVAL UNVERIFIED" in out


def test_a_bound_freeze_verifies_as_held_and_still_notices_a_moved_contract(
    tree, anchor
):
    """SC-5. Everything the stored root hashes, the recomputed root must hash too.

    The deepest failure available to this slice, and the plan's own review found
    it: if the binding enters root_hash but not the payload recompute rebuilds,
    the stored root covers the real binding while the recomputed root covers the
    unbound default. `held` is then FALSE on a tree where nothing moved, every
    bound freeze reports LOSS OF LOCK for ever, and the tests above cannot see it
    because three of them assert a red and would pass on the wrong cause.

    The second half is the negative control, and it is not decoration: without it
    a verify_manifest that simply set recomputed_root to manifest["root"] and
    held to True would certify a no-op and satisfy the first half exactly.
    """
    _git_init(anchor.parent)
    _git_commit(anchor.parent, "the gate artifact")
    manifest = _bound_manifest(tree, anchor)

    held = verify_manifest(manifest, tree)
    assert held["recomputed_root"] == manifest["root"]
    assert held["held"] is True

    (tree / "tests" / "test_alpha.py").write_text(
        "def test_a():\n    assert False\n")
    moved = verify_manifest(manifest, tree)
    assert moved["held"] is False
    assert "tests/test_alpha.py" in moved["changed"]
