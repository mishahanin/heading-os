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

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

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


# ============================================================
# The CALLER, which is where the defect actually was
# ============================================================
#
# Everything above exercises `git_commit` in isolation and one of its comments
# says "Exactly what the caller in main() now passes". Nothing checked that.
# MEASURED 2026-09-01 by editing `main()` to pass `[backup_path]` on the
# source-repo commit - the literal 2026-08-23 defect, restored - and again by
# dropping `source_path` from the single-repo branch: this file, its frontmatter
# sibling, and `test_a_merge_that_rewrote_what_it_was_not_asked_to.py` all
# stayed green. The regression wall was built one function away from the
# function that broke.
#
# `transfer-contact.py` carries the same two call sites, arrived at by the same
# fix on the same day, so it is measured here too rather than left to become
# the copy that stops being checked.
TOOLS = ["merge-contacts.py", "transfer-contact.py"]


def _main_source(script: str) -> str:
    return (ROOT / "scripts" / script).read_text(encoding="utf-8")


@pytest.mark.parametrize("script", TOOLS)
def test_the_source_repo_commit_names_both_the_backup_and_the_source(script):
    """Asked of the parse tree, not the text: the list handed to the source-repo
    `try_commit` must carry `source_path` as well as `backup_path`.

    The rename left the ORIGINAL path tracked. `git add <backup>` alone stages
    an addition and no deletion, so the commit ships two live copies of one
    contact - the drift the merge exists to kill.
    """
    tree = ast.parse(_main_source(script))
    lists = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "try_commit"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.List):
                lists.append({ast.unparse(e) for e in arg.elts})

    assert lists, f"{script}: no try_commit call passes a literal path list"
    assert any({"backup_path", "source_path"} <= names for names in lists), (
        f"{script}: no commit names both backup_path and source_path, so the "
        f"deletion of the merged-away original is never staged. Passed: {lists}")


@pytest.mark.parametrize("script", TOOLS)
def test_the_single_repo_branch_carries_the_removal_too(script):
    """When both contacts live in ONE repo the second commit is skipped whole by
    `if into_repo != from_repo`, so the backup and the deletion have to ride
    along on the first one or they are never committed at all."""
    tree = ast.parse(_main_source(script))
    carried = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.AugAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "first_paths"
                and isinstance(node.value, ast.List)):
            continue
        carried.append({ast.unparse(e) for e in node.value.elts})

    assert carried, f"{script}: first_paths is never extended for the one-repo case"
    assert any({"backup_path", "source_path"} <= names for names in carried), (
        f"{script}: the single-repo commit does not carry the removal. "
        f"Extended with: {carried}")


@pytest.mark.parametrize("script", TOOLS)
def test_the_merged_target_is_written_atomically(script):
    """A torn write leaves the authoritative merged record truncated, at the one
    moment the source has been read and not yet renamed.

    `test_four_changes_that_tore_and_said_they_had_not` pins this for
    `transfer-contact.py` and says in its own docstring that "its twin in
    merge-contacts.py has always used atomic_write_text" - which is a claim
    about a file it does not check. MEASURED 2026-09-01: swapping
    `atomic_write_text(target_path, merged_text)` for a plain `write_text` left
    every merge test green.
    """
    src = _main_source(script)

    assert "atomic_write_text(target_path," in src, (
        f"{script}: the merged target is no longer written atomically")
    assert "target_path.write_text(" not in src, (
        f"{script}: a plain write_text on the target is back")


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
