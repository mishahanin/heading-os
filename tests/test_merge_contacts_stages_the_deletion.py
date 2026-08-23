"""A merge must leave ONE copy of the contact, in git as well as on disk.

The 2026-08-23 defect: `main` renames the source contact to `.md.merged`, then
calls `git_commit(repo, [backup_path], ...)`. `git add <path>` on a path that no
longer exists stages nothing, so the commit carried the backup while the
ORIGINAL file stayed tracked. Two divergent copies of one contact survived in
the exec's repo — the drift the merge exists to kill.

A second gap in the same place: when both contacts live in one repo, the whole
second commit is skipped by `if into_repo != from_repo`, so neither the backup
nor the deletion was committed at all.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "merge_contacts", ROOT / "scripts" / "merge-contacts.py")
mc = importlib.util.module_from_spec(_spec)
sys.modules["merge_contacts"] = mc
_spec.loader.exec_module(mc)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "crm-repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True)
    return repo


def _tracked(repo: Path) -> set[str]:
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"],
                         capture_output=True, text=True, check=True).stdout
    return {p for p in out.split("\0") if p}


def test_a_renamed_away_source_is_committed_as_a_deletion(tmp_path):
    repo = _repo(tmp_path)
    contacts = repo / "contacts"
    contacts.mkdir()
    source = contacts / "zenon-makarios.md"
    source.write_text("---\nname: Zenon Makarios\n---\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True,
                   capture_output=True)
    assert "contacts/zenon-makarios.md" in _tracked(repo)

    backup = source.with_suffix(".md.merged")
    source.rename(backup)

    # Exactly what the caller in main() now passes. The old caller passed
    # [backup] only, which is the defect; this asserts the pair is what works.
    mc.git_commit(repo, [backup, source], "merge")

    tracked = _tracked(repo)
    assert "contacts/zenon-makarios.md.merged" in tracked, "the backup was not committed"
    assert "contacts/zenon-makarios.md" not in tracked, (
        "the merged-away source is still tracked; two copies of one contact "
        "survive the merge")


def test_the_commit_does_not_sweep_unrelated_edits(tmp_path):
    """Staging the parent directory would have fixed the deletion and swallowed
    whatever else the operator had in flight. The pathspec stays exact."""
    repo = _repo(tmp_path)
    contacts = repo / "contacts"
    contacts.mkdir()
    source = contacts / "zenon-makarios.md"
    source.write_text("a\n", encoding="utf-8")
    bystander = contacts / "hale-quorix.md"
    bystander.write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True,
                   capture_output=True)

    bystander.write_text("an unrelated edit in flight\n", encoding="utf-8")
    backup = source.with_suffix(".md.merged")
    source.rename(backup)

    mc.git_commit(repo, [backup, source], "merge")

    show = subprocess.run(
        ["git", "-C", str(repo), "show", "--name-only", "--format=", "HEAD"],
        capture_output=True, text=True, check=True).stdout
    assert "hale-quorix.md" not in show, "the merge commit swept an unrelated edit"
    assert bystander.read_text(encoding="utf-8") == "an unrelated edit in flight\n"


def test_passing_the_backup_alone_is_what_used_to_leave_two_copies(tmp_path):
    """Pins the mechanism, so a future caller cannot quietly drop source_path.

    Measured on git 2.43: `git add <backup>` leaves ` D contacts/<name>.md`
    unstaged, so the commit records an addition and no deletion.
    """
    repo = _repo(tmp_path)
    contacts = repo / "contacts"
    contacts.mkdir()
    source = contacts / "zenon-makarios.md"
    source.write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True,
                   capture_output=True)

    backup = source.with_suffix(".md.merged")
    source.rename(backup)
    mc.git_commit(repo, [backup], "backup only -- the old, broken call")

    assert "contacts/zenon-makarios.md" in _tracked(repo), (
        "if this ever stops holding, git changed and the guard above needs "
        "rewriting rather than deleting")
