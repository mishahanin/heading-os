"""Canopus wire 2.1: the git layer behind the approval axis."""
import subprocess
from pathlib import Path


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
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
