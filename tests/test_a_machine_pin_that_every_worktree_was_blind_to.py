#!/usr/bin/env python3
"""The embedder pin is a fact about the machine, and no worktree could see it.

`config/ollama-hosts.yaml` is gitignored on purpose: every address in it names
one laptop, so committing it would point every other clone at nothing. A LINKED
WORKTREE is not another clone. It is the same machine, and it is where all
engine work happens.

MEASURED 2026-09-06 in `yard-memory-recalibration`, before the fix, running the
canonical query that works in HELM:

    {"hits": [], "gap": true, "embed_unavailable": {"reason": "embedding failed
     after 3 attempts -- cannot reach embedder at http://localhost:11434/api/embed:
     [Errno 111] Connection refused ..."}}

The chain: the file is absent in the worktree, so `machine_hosts` returned `[]`,
so `index_embed_preference` returned `""`, so `index_embed_target` returned
`LOCAL_HOST` UNPROBED, and the local daemon had been removed on 2026-08-23.
Memory search was dead in every yard and said nothing about it. Three runs, all
identical, 4.8 s each.

The subtle part, and why a naive fix would be wrong: an absent file is a
DESIGNED and TESTED state meaning "this machine pins nothing, use the local
daemon" (`test_machine_host_pin.py::test_no_file_means_no_pin`,
`::test_no_pin_anywhere_is_the_local_daemon_unprobed`,
`test_embed_host_pinned.py::test_an_unpinned_workspace_still_uses_the_local_daemon`).
Those three must keep passing untouched. Only the worktree case changes, where
absence means "not visible from here" rather than "not configured".

Both directions, in the sense that matters: the worktree case must now RESOLVE
(it returned `[]` against the previous version), and the ordinary-clone case
must still return `[]` (a fix that resolved something there would break the
documented zero-setup default).

Mutation-verified 2026-09-06 against `scripts/utils/ollama_host.py`: 4 of 5
caught. The one survivor, `if not dotgit.is_file(): return None` deleted, is
EQUIVALENT and is recorded rather than chased: every case that guard rejects
(a `.git` directory, an absent `.git`) raises `OSError` from the `read_text`
two lines below it, and that is caught by the same handler and returns None
too. The guard is a fast path, not a behaviour boundary. The other survivor
found on the first run was a real gap and is closed by
`test_a_worktree_of_a_bare_repository_resolves_to_nothing`.

Run: .venv/bin/python -m pytest \\
     tests/test_a_machine_pin_that_every_worktree_was_blind_to.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.ollama_host import (  # noqa: E402
    MACHINE_HOSTS_FILE,
    machine_hosts,
    main_worktree_root,
)

PINNED = "embed:\n  - 'auto:11434'\ngenerate:\n  - 'auto:11434'\n"


def _clone(tmp_path: Path, name: str, *, pinned: bool) -> Path:
    """An ordinary checkout: `.git` is a DIRECTORY."""
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    if pinned:
        (root / "config").mkdir(parents=True, exist_ok=True)
        (root / MACHINE_HOSTS_FILE).write_text(PINNED, encoding="utf-8")
    return root


def _worktree(tmp_path: Path, name: str, main: Path, *, gitdir: str | None = None,
              pinned: bool = False) -> Path:
    """A linked worktree: `.git` is a FILE naming the common git directory."""
    root = tmp_path / name
    root.mkdir(parents=True)
    target = gitdir if gitdir is not None else f"{main}/.git/worktrees/{name}"
    (root / ".git").write_text(f"gitdir: {target}\n", encoding="utf-8")
    if pinned:
        (root / "config").mkdir(parents=True, exist_ok=True)
        (root / MACHINE_HOSTS_FILE).write_text(PINNED, encoding="utf-8")
    return root


# ============================================================
# The resolver: which checkout is the main one
# ============================================================

def test_a_linked_worktree_resolves_to_its_main_checkout(tmp_path):
    main = _clone(tmp_path, "main", pinned=False)
    wt = _worktree(tmp_path, "yard-x", main)
    assert main_worktree_root(wt) == main


def test_an_ordinary_clone_resolves_to_nothing(tmp_path):
    """`.git` is a directory. This is the case that must NOT change."""
    assert main_worktree_root(_clone(tmp_path, "solo", pinned=True)) is None


def test_a_directory_with_no_git_at_all_resolves_to_nothing(tmp_path):
    plain = tmp_path / "not-a-checkout"
    plain.mkdir()
    assert main_worktree_root(plain) is None


def test_a_gitdir_outside_a_worktrees_directory_is_refused(tmp_path):
    """A `.git` file pointing somewhere else entirely must not be followed.

    Submodules use the same `gitdir:` form, and their target is NOT under
    `worktrees/`. Following one would read a pin out of an unrelated tree.
    """
    main = _clone(tmp_path, "main", pinned=True)
    wt = _worktree(tmp_path, "sub", main, gitdir=f"{main}/.git/modules/sub")
    assert main_worktree_root(wt) is None


def test_a_worktree_of_a_bare_repository_resolves_to_nothing(tmp_path):
    """A bare repo has no main checkout, and its worktrees sit one level higher.

    `git worktree` on a bare repository puts the entry at
    `<repo>.git/worktrees/<name>`, not `<main>/.git/worktrees/<name>`. Without
    the check that the common directory is literally named `.git`, this resolves
    to the bare repo's PARENT - an arbitrary directory that has nothing to do
    with this checkout - and a `config/ollama-hosts.yaml` sitting there would be
    read as though it were the machine's pin.

    Found by mutation on 2026-09-06: deleting that check left every other test
    in this file green.
    """
    bare = tmp_path / "repo.git"
    (bare / "worktrees" / "yard-bare").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / MACHINE_HOSTS_FILE).write_text("embed:\n  - 'http://stray:9999'\n",
                                               encoding="utf-8")
    wt = tmp_path / "yard-bare"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {bare}/worktrees/yard-bare\n", encoding="utf-8")

    assert main_worktree_root(wt) is None
    assert machine_hosts("embed", root=wt) == [], (
        "a stray hosts file above a bare repository was read as this machine's "
        "pin")


def test_a_relative_gitdir_resolves_against_the_worktree(tmp_path):
    main = _clone(tmp_path, "main", pinned=False)
    wt = _worktree(tmp_path, "yard-rel", main, gitdir="../main/.git/worktrees/yard-rel")
    assert main_worktree_root(wt) == main.resolve()


def test_an_unreadable_git_file_resolves_to_nothing(tmp_path):
    main = _clone(tmp_path, "main", pinned=False)
    wt = _worktree(tmp_path, "yard-bad", main)
    (wt / ".git").write_bytes(b"gitdir: \xff\xfe not utf-8\n")
    assert main_worktree_root(wt) is None


# ============================================================
# The consequence: what a worktree now reads
# ============================================================

def test_a_worktree_reads_the_machines_pin(tmp_path):
    """The defect. Against the previous version this returns [] and fails."""
    main = _clone(tmp_path, "main", pinned=True)
    wt = _worktree(tmp_path, "yard-x", main)

    assert machine_hosts("embed", root=wt) == ["auto:11434"], (
        "a linked worktree still cannot see the machine's embedder pin, so "
        "memory search in every yard resolves to a daemon removed 2026-08-23")
    assert machine_hosts("generate", root=wt) == ["auto:11434"]


def test_the_worktrees_own_file_wins_over_the_main_checkouts(tmp_path):
    """The main checkout is a FALLBACK, never an override."""
    main = _clone(tmp_path, "main", pinned=True)
    wt = _worktree(tmp_path, "yard-own", main)
    (wt / "config").mkdir(parents=True)
    (wt / MACHINE_HOSTS_FILE).write_text("embed:\n  - 'http://mine:1234'\n",
                                         encoding="utf-8")
    assert machine_hosts("embed", root=wt) == ["http://mine:1234"]


def test_an_ordinary_clone_with_no_file_still_pins_nothing(tmp_path):
    """The documented zero-setup default, and the other direction of the fix."""
    assert machine_hosts("embed", root=_clone(tmp_path, "solo", pinned=False)) == []


def test_a_worktree_whose_machine_pins_nothing_pins_nothing(tmp_path):
    """Neither tree holds the file. Absence still means absence."""
    main = _clone(tmp_path, "main", pinned=False)
    wt = _worktree(tmp_path, "yard-x", main)
    assert machine_hosts("embed", root=wt) == []


def test_a_worktree_whose_main_checkout_is_gone_pins_nothing(tmp_path):
    """A stale `.git` file must degrade, not raise."""
    main = _clone(tmp_path, "main", pinned=True)
    wt = _worktree(tmp_path, "yard-x", main)
    import shutil
    shutil.rmtree(main)
    assert machine_hosts("embed", root=wt) == []


# ============================================================
# Against the real tree, where the defect was measured
# ============================================================

def test_this_checkout_resolves_the_same_pin_as_the_main_one():
    """The floor: whatever this checkout is, it agrees with the machine."""
    main = main_worktree_root(ROOT)
    if main is None:
        pytest.skip("this test run is not inside a linked worktree")
    if not (main / MACHINE_HOSTS_FILE).is_file():
        pytest.skip("this machine pins no ollama host")
    assert machine_hosts("embed", root=ROOT) == machine_hosts("embed", root=main), (
        "the worktree and its main checkout disagree about this machine's "
        "embedder, which is the state that made recall silently empty in yards")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
