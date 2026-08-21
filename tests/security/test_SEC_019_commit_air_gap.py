#!/usr/bin/env python3
"""SEC-019 — a private commit never reaches any memory-index store.

The commit layers turn `git log` into searchable rows. On the DATA side that log
contains the subjects of every private change, and 14 of the 486 indexed data
commits touch a `chronicle/personal/` path. A subject line is prose: "closed the
villa purchase" describes the change as fully as the diff does. So the air gap
refuses the WHOLE commit, never merely the denied file inside it — indexing the
message of a personal change leaks the change.

The design spec (`docs/superpowers/specs/2026-08-21-semantic-index-commits-and-
symbols-design.md` § Testing) states this is a security test and belongs here, not
beside the bookkeeping tests. Its sibling in `tests/test_commit_layer_build.py`
covers the same invariant at the build loop; this file covers it at the source,
and states the security claim in the place a security reader looks.

Two properties, and the second is the one that fails quietly:

1. A commit touching a denied path yields no row.
2. A commit touching a denied path AND a public path also yields no row. A
   per-file filter would pass this one, keep the subject, and look correct.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.commit_source import iter_commits  # noqa: E402

DENY_SEGMENTS = ("personal",)
DENY_PREFIXES = ("_secure/",)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    return root


def _commit(repo: Path, rel: str, subject: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", subject)


def _subjects(repo: Path) -> set[str]:
    return {
        c["title"] for c in iter_commits(
            repo, repo_label="data",
            deny_prefixes=DENY_PREFIXES, deny_segments=DENY_SEGMENTS,
        )
    }


def test_a_commit_touching_a_personal_path_is_never_indexed(tmp_path):
    repo = _repo(tmp_path / "data")
    _commit(repo, "docs/a.md", "public change")
    _commit(repo, "chronicle/personal/note.md", "closed the villa purchase")

    assert _subjects(repo) == {"public change"}


def test_a_mixed_commit_is_refused_whole_not_filtered_per_file(tmp_path):
    """The quiet failure: a per-file filter keeps the subject and looks right.

    The subject describes the private change whether or not a public file rode
    along in the same commit, so the unit of refusal has to be the commit.
    """
    repo = _repo(tmp_path / "data")
    _commit(repo, "docs/a.md", "public change")
    repo_file = repo / "chronicle" / "personal" / "note.md"
    repo_file.parent.mkdir(parents=True, exist_ok=True)
    repo_file.write_text("x\n", encoding="utf-8")
    (repo / "docs" / "b.md").write_text("y\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "mixed: villa paperwork and a doc tweak")

    assert _subjects(repo) == {"public change"}


def test_the_vault_prefix_is_refused_for_commits_too(tmp_path):
    repo = _repo(tmp_path / "data")
    _commit(repo, "docs/a.md", "public change")
    _commit(repo, "_secure/creds.md", "rotated the account password")

    assert _subjects(repo) == {"public change"}


def test_a_caller_cannot_switch_the_air_gap_off(tmp_path):
    """The deny list is NOT the caller's to pass. Written after this test failed.

    The test first asserted "with no deny list the commit is indexed, with one it
    is not", to prove the fixture was not vacuously passing. It failed: the commit
    stayed refused with EMPTY deny arguments. The cause is the design, not a bug --
    `scripts/utils/air_gap.is_denied()` carries `HARDCODED_DENY_PREFIXES` and
    `HARDCODED_DENY_SEGMENTS` that a caller's arguments ADD to and can never
    subtract from.

    That is a stronger guarantee than the one being tested, so it is what is
    tested now: a future caller who forgets the deny arguments, or passes empty
    ones, still cannot index a private commit.
    """
    repo = _repo(tmp_path / "data")
    _commit(repo, "docs/a.md", "public change")
    _commit(repo, "chronicle/personal/note.md", "private")

    bare = {c["title"] for c in iter_commits(repo, repo_label="data")}
    assert bare == {"public change"}, (
        "a caller passing no deny list reached a personal commit -- the hardcoded "
        "floor in air_gap.is_denied() has been weakened"
    )
    # And the fixture is not vacuous: the public commit does come through.
    assert "public change" in bare
