"""The recovery the script advised could not run on the day it was advised.

`git_commit_and_push` staged, committed, then pushed under supervision. When the
push failed, `main` printed "the note did NOT reach the corporate repo" and told
the operator to re-run with --overwrite. Measured: run 1 commits and the push
fails, leaving the note committed but unpushed; the same-day re-run rebuilds a
byte-identical target (`promoted_date` is the only time-varying field), so `git
add` stages nothing, `git commit` exits non-zero on "nothing to commit", and
`supervised_push` is never reached. The advice loops forever and the tool never
pushes the commit it made.

Every test here runs against a git repo created inside tmp_path and replaces
`supervised_push` with a recorder. Nothing reaches a remote, and nothing reaches
the corporate repo.

Run: python3 -m pytest tests/test_a_push_retry_that_died_at_an_empty_commit.py
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "promote-knowledge.py"


@pytest.fixture()
def pk():
    spec = importlib.util.spec_from_file_location("promote_knowledge_retry_mod", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo), check=True,
                          capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    """A throwaway corporate-repo stand-in with no remote configured."""
    r = tmp_path / "corporate"
    (r / "knowledge" / "shared" / "signals").mkdir(parents=True)
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "james.bond@example.com")
    _git(r, "config", "user.name", "James Bond")
    (r / "README.md").write_text("Acme Telecom shared knowledge\n", encoding="utf-8")
    _git(r, "add", "README.md")
    _git(r, "commit", "-qm", "seed")
    return r


def _record_push(pk, verdict):
    calls = []

    def fake(repo_path, branch="main", stall_window=120, label=""):
        calls.append((repo_path, branch))
        return verdict

    pk.supervised_push = fake
    return calls


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_a_re_run_with_nothing_to_stage_still_reaches_the_push(pk, repo):
    note = repo / "knowledge" / "shared" / "signals" / "note.md"
    note.write_text("---\npromoted_date: 2026-08-29\n---\nbody\n", encoding="utf-8")
    # Run 1 committed and the push failed, so the note is committed-not-pushed.
    _git(repo, "add", str(note))
    _git(repo, "commit", "-qm", "Promote knowledge note note.md to shared/signals")
    committed = _head(repo)

    calls = _record_push(pk, {"state": "ok", "reason": "recorded"})
    pk.git_commit_and_push(repo, [note], "Promote knowledge note note.md to shared/signals")

    assert calls, "the push was never retried, which is the whole defect"
    assert _head(repo) == committed, "an empty re-run must not manufacture a commit"


def test_a_real_change_is_still_committed_before_the_push(pk, repo):
    note = repo / "knowledge" / "shared" / "signals" / "note.md"
    note.write_text("---\npromoted_date: 2026-08-29\n---\nbody\n", encoding="utf-8")
    before = _head(repo)

    calls = _record_push(pk, {"state": "ok", "reason": "recorded"})
    pk.git_commit_and_push(repo, [note], "Promote knowledge note note.md to shared/signals")

    assert _head(repo) != before, "the new note was never committed"
    assert calls
    tracked = _git(repo, "ls-files").stdout.split()
    assert "knowledge/shared/signals/note.md" in tracked


def test_a_failing_push_still_raises(pk, repo):
    """Skipping the empty commit must not soften the push verdict."""
    note = repo / "knowledge" / "shared" / "signals" / "note.md"
    note.write_text("---\npromoted_date: 2026-08-29\n---\nbody\n", encoding="utf-8")
    _record_push(pk, {"state": "stalled", "reason": "no remote"})

    with pytest.raises(subprocess.CalledProcessError) as exc:
        pk.git_commit_and_push(repo, [note], "Promote knowledge note note.md to shared/signals")
    assert b"stalled" in exc.value.stderr
