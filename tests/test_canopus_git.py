"""Canopus wire 2.1: the git layer behind the approval axis."""
import subprocess
from pathlib import Path


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    for argv in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "builder@example.invalid"],
        ["config", "user.name", "Builder"],
    ):
        subprocess.run(["git", "-C", str(root), *argv], check=True,
                       capture_output=True, text=True)
    return root


def _commit(root: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", message],
                   check=True, capture_output=True, text=True)


def _bound(artifact: Path, root: str) -> dict:
    """A hand-built manifest carrying the binding `freeze` would have recorded.

    Since wire 2.2, resolve_anchor judges the anchor's repository binding BEFORE
    it reads the committed anchor. A manifest that omits `anchor_repo` while its
    artifact sits inside a repository therefore resolves ANCHOR_UNBOUND, and
    every precedence test below would then be answering a question about the
    binding rather than the one it names. Three of them would still have PASSED
    on that wrong cause, which is the worse half.
    """
    from scripts.utils.canopus_freeze import REPO_PRESENT
    from scripts.utils.canopus_git import repo_identity

    status, identity = repo_identity(artifact.parent)
    return {"anchor": str(artifact), "root": root,
            "anchor_repo": {"in_repo": status == REPO_PRESENT,
                            "identity": identity}}


def test_a_committed_anchor_line_is_read_from_head(tmp_path):
    from scripts.utils.canopus_git import COMMITTED, read_committed_anchor

    root = _repo(tmp_path)
    artifact = root / "gate.md"
    artifact.write_text(f"# gate\n\ncanopus-anchor: {'a' * 64}\n", encoding="utf-8")
    _commit(root, "approve")

    assert read_committed_anchor(artifact) == (COMMITTED, "a" * 64)


def test_the_working_file_cannot_override_the_committed_one(tmp_path):
    """The whole point: an appended line is not an approval.

    Reading the working file is what let a builder append a hash locally and
    reach green. HEAD is what a human actually approved.
    """
    from scripts.utils.canopus_git import COMMITTED, read_committed_anchor

    root = _repo(tmp_path)
    artifact = root / "gate.md"
    artifact.write_text(f"# gate\n\ncanopus-anchor: {'a' * 64}\n", encoding="utf-8")
    _commit(root, "approve")
    with artifact.open("a", encoding="utf-8") as handle:
        handle.write(f"\ncanopus-anchor: {'b' * 64}\n")

    assert read_committed_anchor(artifact) == (COMMITTED, "a" * 64)


def test_the_last_committed_line_wins(tmp_path):
    from scripts.utils.canopus_git import COMMITTED, read_committed_anchor

    root = _repo(tmp_path)
    artifact = root / "gate.md"
    artifact.write_text(
        f"# gate\n\ncanopus-anchor: {'a' * 64}\n\ncanopus-anchor: {'b' * 64}\n",
        encoding="utf-8",
    )
    _commit(root, "approve twice")

    assert read_committed_anchor(artifact) == (COMMITTED, "b" * 64)


def test_an_untracked_artifact_reads_uncommitted(tmp_path):
    from scripts.utils.canopus_git import UNCOMMITTED, read_committed_anchor

    root = _repo(tmp_path)
    (root / "seed.md").write_text("seed\n", encoding="utf-8")
    _commit(root, "seed")
    artifact = root / "gate.md"
    artifact.write_text(f"canopus-anchor: {'a' * 64}\n", encoding="utf-8")

    assert read_committed_anchor(artifact) == (UNCOMMITTED, None)


def test_a_tracked_artifact_with_no_committed_line_reads_uncommitted(tmp_path):
    from scripts.utils.canopus_git import UNCOMMITTED, read_committed_anchor

    root = _repo(tmp_path)
    artifact = root / "gate.md"
    artifact.write_text("# gate\n", encoding="utf-8")
    _commit(root, "gate without an approval")
    with artifact.open("a", encoding="utf-8") as handle:
        handle.write(f"\ncanopus-anchor: {'a' * 64}\n")

    assert read_committed_anchor(artifact) == (UNCOMMITTED, None)


def test_an_artifact_outside_any_repository_reads_no_repo(tmp_path):
    from scripts.utils.canopus_git import NO_REPO, read_committed_anchor

    artifact = tmp_path / "loose" / "gate.md"
    artifact.parent.mkdir()
    artifact.write_text(f"canopus-anchor: {'a' * 64}\n", encoding="utf-8")

    assert read_committed_anchor(artifact) == (NO_REPO, None)


def test_git_output_answers_none_outside_a_repository(tmp_path):
    from scripts.utils.canopus_git import git_output

    assert git_output(tmp_path, "rev-parse", "HEAD") is None


def test_an_unavailable_git_is_told_apart_from_a_missing_repository(tmp_path, monkeypatch):
    """A generic failure would report "not a repository" on a machine with no git."""
    import scripts.utils.canopus_git as canopus_git

    monkeypatch.setattr(canopus_git, "git_output", lambda *args, **kwargs: None)
    artifact = tmp_path / "gate.md"
    artifact.write_text("# gate\n", encoding="utf-8")

    assert canopus_git.read_committed_anchor(artifact) == (canopus_git.NO_GIT, None)


# ============================================================
# Precedence: which copy of the anchor governs the lock
# ============================================================

def test_the_committed_hash_governs_the_lock_when_it_exists(tmp_path):
    """An appended working-file line must not reach LOCK HELD."""
    from scripts.utils.canopus_freeze import APPROVAL_UNVERIFIED
    from scripts.utils.canopus_git import resolve_anchor

    root = _repo(tmp_path)
    artifact = root / "gate.md"
    artifact.write_text(f"canopus-anchor: {'a' * 64}\n", encoding="utf-8")
    _commit(root, "approve")
    with artifact.open("a", encoding="utf-8") as handle:
        handle.write(f"\ncanopus-anchor: {'b' * 64}\n")

    resolution = resolve_anchor(_bound(artifact, "b" * 64))

    assert resolution.value == "a" * 64
    assert resolution.approval == APPROVAL_UNVERIFIED


def test_the_working_file_governs_when_there_is_no_repository(tmp_path):
    """The fallback is what keeps the tool usable outside a repository."""
    from scripts.utils.canopus_freeze import ANCHOR_RECORDED, APPROVAL_UNVERIFIED
    from scripts.utils.canopus_git import resolve_anchor

    artifact = tmp_path / "loose" / "gate.md"
    artifact.parent.mkdir()
    artifact.write_text(f"canopus-anchor: {'c' * 64}\n", encoding="utf-8")

    resolution = resolve_anchor({"anchor": str(artifact), "root": "c" * 64})

    assert (resolution.status, resolution.value) == (ANCHOR_RECORDED, "c" * 64)
    assert resolution.approval == APPROVAL_UNVERIFIED
    assert "not in a repository" in resolution.approval_reason


def test_a_matching_committed_hash_reports_approved(tmp_path):
    from scripts.utils.canopus_freeze import APPROVED
    from scripts.utils.canopus_git import resolve_anchor

    root = _repo(tmp_path)
    artifact = root / "gate.md"
    artifact.write_text(f"canopus-anchor: {'d' * 64}\n", encoding="utf-8")
    _commit(root, "approve")

    assert resolve_anchor(_bound(artifact, "d" * 64)).approval == APPROVED


def test_a_disagreeing_committed_hash_is_a_loss_of_lock(tmp_path):
    """Success criterion 2, first half: HEAD governs, so the appended line loses."""
    from scripts.utils.canopus_freeze import LOSS_OF_LOCK, lock_state
    from scripts.utils.canopus_git import resolve_anchor

    root = _repo(tmp_path)
    artifact = root / "gate.md"
    artifact.write_text(f"canopus-anchor: {'a' * 64}\n", encoding="utf-8")
    _commit(root, "approve the old set")
    with artifact.open("a", encoding="utf-8") as handle:
        handle.write(f"\ncanopus-anchor: {'b' * 64}\n")

    resolution = resolve_anchor(_bound(artifact, "b" * 64))
    report = {"recomputed_root": "b" * 64, "changed": [], "added": [],
              "removed": [], "held": True}

    assert lock_state(report, resolution.status, resolution.value) == LOSS_OF_LOCK


def test_an_uncommitted_approval_never_reaches_lock_held(tmp_path):
    """Success criterion 2, second half.

    The cost is named rather than hidden: a freeze whose approval was never
    committed reports amber. A hash the tool wrote and nobody committed is not an
    approval, and reporting it as one is the defect this wire removes.
    """
    from scripts.utils.canopus_freeze import LOCK_UNCONFIRMED, lock_state
    from scripts.utils.canopus_git import resolve_anchor

    root = _repo(tmp_path)
    artifact = root / "gate.md"
    artifact.write_text("# gate\n", encoding="utf-8")
    _commit(root, "gate without an approval")
    with artifact.open("a", encoding="utf-8") as handle:
        handle.write(f"\ncanopus-anchor: {'b' * 64}\n")

    resolution = resolve_anchor(_bound(artifact, "b" * 64))
    report = {"recomputed_root": "b" * 64, "changed": [], "added": [],
              "removed": [], "held": True}

    assert lock_state(report, resolution.status, resolution.value) == LOCK_UNCONFIRMED


def test_a_deleted_artifact_still_reddens_the_lock(tmp_path):
    """`git show HEAD:<rel>` is existence-blind, so the committed value alone
    would report a held lock over an anchor that is GONE.

    The committed hash survives the file being deleted, so without the
    ANCHOR_MISSING branch in resolve_anchor the committed value governs, the lock
    reads held over nothing, and `cmd_verify`'s "anchor is gone" line becomes
    unreachable inside a repository. The sibling CLI test that deletes an anchor
    sits OUTSIDE any repository, where the committed reader never answers, so it
    cannot reach this branch.
    """
    from scripts.utils.canopus_freeze import LOSS_OF_LOCK, lock_state
    from scripts.utils.canopus_git import resolve_anchor

    root = _repo(tmp_path)
    artifact = root / "gate.md"
    artifact.write_text(f"canopus-anchor: {'d' * 64}\n", encoding="utf-8")
    _commit(root, "approve")
    artifact.unlink()

    resolution = resolve_anchor(_bound(artifact, "d" * 64))
    report = {"recomputed_root": "d" * 64, "changed": [], "added": [],
              "removed": [], "held": True}

    assert lock_state(report, resolution.status, resolution.value) == LOSS_OF_LOCK


def test_a_committed_hash_differing_only_in_its_last_character_is_not_approved(tmp_path):
    """No prefix comparison anywhere, pinned where a prefix compare would pass.

    Two digests that differ at character 0 cannot tell a full comparison from a
    prefix one. These share 63 of 64 characters, so an implementation that
    compared any prefix would report APPROVED over a hash nobody approved.
    """
    from scripts.utils.canopus_freeze import APPROVAL_UNVERIFIED
    from scripts.utils.canopus_git import resolve_anchor

    root = _repo(tmp_path)
    artifact = root / "gate.md"
    approved = "a" * 63 + "1"
    computed = "a" * 63 + "2"
    artifact.write_text(f"canopus-anchor: {approved}\n", encoding="utf-8")
    _commit(root, "approve")

    resolution = resolve_anchor(_bound(artifact, computed))

    assert resolution.approval == APPROVAL_UNVERIFIED
    assert resolution.value == approved


def test_a_truncated_committed_hash_is_not_approved(tmp_path):
    """A strict prefix of the approved digest is not the approved digest.

    A builder with a shell can brute-force a short prefix by appending
    whitespace to a frozen file, so a truncated digest that looks rigorous is
    worse than a full one.
    """
    from scripts.utils.canopus_freeze import APPROVAL_UNVERIFIED
    from scripts.utils.canopus_git import resolve_anchor

    root = _repo(tmp_path)
    artifact = root / "gate.md"
    artifact.write_text(f"canopus-anchor: {'a' * 32}\n", encoding="utf-8")
    _commit(root, "approve")

    resolution = resolve_anchor(_bound(artifact, "a" * 64))

    assert resolution.approval == APPROVAL_UNVERIFIED


def test_resolve_anchor_answers_when_the_git_call_times_out(tmp_path, monkeypatch):
    """This module's stated contract is that every function ANSWERS.

    Worth one test rather than one sentence, because the caller that matters is
    freeze_gate, which wraps this call in `except OSError`, and
    subprocess.TimeoutExpired is not an OSError. A timeout escaping git_output
    would surface at pytest session start as a traceback from the layer billed as
    the guarantee.
    """
    import subprocess as sp

    import scripts.utils.canopus_git as canopus_git
    from scripts.utils.canopus_freeze import ANCHOR_RECORDED, APPROVAL_UNVERIFIED

    def timeout(*_args, **_kwargs):
        raise sp.TimeoutExpired(cmd=["git"], timeout=30)

    monkeypatch.setattr(canopus_git.subprocess, "run", timeout)
    artifact = tmp_path / "gate.md"
    artifact.write_text(f"canopus-anchor: {'e' * 64}\n", encoding="utf-8")

    resolution = canopus_git.resolve_anchor({"anchor": str(artifact), "root": "e" * 64})

    assert resolution.approval == APPROVAL_UNVERIFIED
    assert (resolution.status, resolution.value) == (ANCHOR_RECORDED, "e" * 64)


def test_a_poisoned_git_dir_does_not_hide_the_repository(tmp_path, monkeypatch):
    """The wire 2.1 Critical, at its root.

    GIT_DIR redirects git's repository discovery, and the answer decided whether
    resolve_anchor fell back to the working copy. Measured before the fix: with
    GIT_DIR set to an unrelated path, rev-parse --show-toplevel failed and the
    anchor's repository became invisible at zero cost to the builder.
    """
    from scripts.utils.canopus_freeze import REPO_PRESENT
    from scripts.utils.canopus_git import repo_identity

    root = _repo(tmp_path)
    (root / "gate.md").write_text("# gate\n", encoding="utf-8")
    _commit(root, "seed")
    clean_status, clean_identity = repo_identity(root)

    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nowhere.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "nowhere"))

    assert repo_identity(root) == (clean_status, clean_identity)
    assert clean_status == REPO_PRESENT
    assert len(clean_identity) == 64


def test_identity_survives_relocation_and_distinguishes_repositories(tmp_path):
    """Identity is the root commit set, not the path.

    A toplevel path would have been cheaper and wrong: this workspace has been
    relocated once already, and a relocated repository is the same repository.
    """
    import shutil

    from scripts.utils.canopus_git import repo_identity

    root = _repo(tmp_path)
    (root / "gate.md").write_text("# gate\n", encoding="utf-8")
    _commit(root, "seed")
    _status, identity = repo_identity(root)

    moved = tmp_path / "moved"
    shutil.copytree(root, moved)
    assert repo_identity(moved)[1] == identity

    # Distinct CONTENT, not merely a distinct directory. A commit sha is a
    # digest over tree, message, author and timestamp, so two repositories
    # seeded with the same bytes by the same author inside the same second
    # carry the same root commit and therefore the same identity. Measured
    # here: with "# gate\n" on both sides this assertion failed on equal
    # digests. That is a real property of a content-derived identity rather
    # than a defect, and seeding the same bytes would make the test flaky on
    # the clock instead of testing the claim.
    other = _repo(tmp_path / "other-parent")
    (other / "gate.md").write_text("# other gate\n", encoding="utf-8")
    _commit(other, "seed")
    assert repo_identity(other)[1] != identity


def test_a_repository_with_no_commits_has_no_identity(tmp_path):
    """An empty repository is REPO_PRESENT with an empty identity, never a digest.

    A digest over the empty set would change the moment the first commit lands,
    and that first commit is the approval act itself, so the freeze would turn
    red at the exact moment a human approved it. Callers refuse instead.
    """
    from scripts.utils.canopus_freeze import REPO_PRESENT
    from scripts.utils.canopus_git import repo_identity

    root = _repo(tmp_path)

    assert repo_identity(root) == (REPO_PRESENT, "")


def test_a_plain_directory_is_reported_absent_not_unknown(tmp_path):
    from scripts.utils.canopus_freeze import REPO_ABSENT
    from scripts.utils.canopus_git import repo_identity

    plain = tmp_path / "plain"
    plain.mkdir()

    assert repo_identity(plain) == (REPO_ABSENT, "")


def test_an_empty_toplevel_never_walks_the_ambient_repository(tmp_path, monkeypatch):
    """An empty `--show-toplevel` on exit 0 is answered absent, not passed on.

    `Path("")` is `Path(".")`, so the rev-list below would run against whatever
    repository the PROCESS happens to sit in and hand an unrelated identity back
    as REPO_PRESENT. No git on this machine was found to produce that output,
    which is exactly why the seam is stubbed rather than provoked: a test that
    can only run on a git that misbehaves is a test that never runs, and the
    guard is there to remove the class rather than one reproducible case.
    """
    import scripts.utils.canopus_git as canopus_git
    from scripts.utils.canopus_freeze import REPO_ABSENT

    seen = []

    def fake_git_output(root, *arguments):
        seen.append(arguments)
        if arguments[:1] == ("rev-parse",):
            return "\n"
        if arguments[:1] == ("--version",):
            return "git version 0.0.0\n"
        return None

    monkeypatch.setattr(canopus_git, "git_output", fake_git_output)

    assert canopus_git.repo_identity(tmp_path) == (REPO_ABSENT, "")
    assert ("rev-list", "--max-parents=0", "HEAD") not in seen


def test_head_sha_answers_rather_than_raising_outside_a_repository(tmp_path):
    from scripts.utils.canopus_git import head_sha

    root = _repo(tmp_path)
    (root / "gate.md").write_text("# gate\n", encoding="utf-8")
    _commit(root, "seed")
    plain = tmp_path / "plain"
    plain.mkdir()

    assert len(head_sha(root)) == 40
    assert head_sha(plain) == ""


def test_identity_answers_when_the_working_directory_is_gone(tmp_path, monkeypatch):
    """Path.cwd() raises FileNotFoundError when the process cwd has been deleted.

    The real condition, not a stub: chdir into a directory and remove it. This
    is the second of wire 2.1's four recorded defects, and it is reachable from
    repo_identity as well as from read_committed_anchor, which is why the probe
    is shared. monkeypatch.chdir restores the original directory afterwards.

    If this environment refuses to run a subprocess from a deleted working
    directory, report that rather than weakening the test: the guard exists
    because the raise it prevents crashes the pytest session start.
    """
    from scripts.utils.canopus_git import REPO_ABSENT, REPO_UNKNOWN, repo_identity

    doomed = tmp_path / "doomed"
    doomed.mkdir()
    monkeypatch.chdir(doomed)
    doomed.rmdir()

    plain = tmp_path / "plain"
    plain.mkdir()
    status, identity = repo_identity(plain)

    assert identity == ""
    assert status in (REPO_ABSENT, REPO_UNKNOWN)
