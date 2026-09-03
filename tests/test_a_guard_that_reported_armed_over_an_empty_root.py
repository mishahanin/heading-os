"""The overlay write guard armed over nothing in every YARD, and said "armed".

`_structural_overlay_root()` derives the operator's private overlay from this
file's location alone, deliberately: asking `get_data_root()` would let
`HEADING_OS_DATA` move the protection off the real data, which is the defect it
was written for. It then assumed the overlay is the checkout's SIBLING. That
holds for the main clone and for nothing else. A git worktree lives wherever it
was created, and this repository's YARDs sit at
`<workspaces>/.yard/.heading-os/<task>`, three levels away.

MEASURED 2026-09-03 in the YARD at `.yard/.heading-os/test-123`, before the fix:

    _structural_overlay_root()  -> None
    _watched_roots()            -> {}
    arm_process_wide()          -> installs the wrappers over an empty root set

So every YARD on this machine ran with the overlay unguarded in every process,
and the guard's own report could not tell that apart from working. That is the
worst failure shape this repository names: a control whose healthy output is
identical to its broken output.

Two things hid it, and both are fixed in the same change.

* The five tests that would have caught it were SKIPPED, not passing. They need
  `zz_heading_os_overlay_guard.pth` in site-packages, `uv sync` deletes it, and
  the bootstrap never put it back. Measured the same day: with the `.pth`
  absent the file reported `1 failed, 17 passed, 8 skipped`; with it restored,
  `5 failed, 20 passed, 1 skipped`. Installing it looked like a regression and
  was in fact the first honest measurement.
* Nothing proved the guard, it only reported. Step 5 of the bootstrap now
  performs a real write into the real overlay and REQUIRES a refusal, the way
  step 10 requires the tree-clean wall to fail on a decoy.

Run: python3 -m pytest tests/test_a_guard_that_reported_armed_over_an_empty_root.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.overlay_write_guard import (  # noqa: E402
    _main_clone_root, _structural_overlay_root, _watched_roots,
)
from scripts.utils.repo_files import read_sources  # noqa: E402

BOOTSTRAP = ROOT / "scripts" / "herdr" / "heading-os-yard" / "yard-bootstrap.sh"


def _bootstrap_source() -> str:
    vanished: list[Path] = []
    texts = dict(read_sources([BOOTSTRAP], vanished))
    assert not vanished, f"the bootstrap vanished mid-read: {vanished}"
    return texts[BOOTSTRAP]


def _fake_checkout(tmp_path: Path, name: str, dotgit: str | None) -> Path:
    """A directory shaped like a checkout. `dotgit=None` gives a main clone."""
    root = tmp_path / name
    root.mkdir(parents=True)
    if dotgit is None:
        (root / ".git").mkdir()
    else:
        (root / ".git").write_text(dotgit, encoding="utf-8")
    return root


# ============================================================
# The link is followed: a worktree resolves to its main clone
# ============================================================

def test_a_worktree_resolves_to_the_main_clone(tmp_path):
    """The reported defect. `<main>/.git/worktrees/<name>` names `<main>`."""
    main = _fake_checkout(tmp_path, "ws/.heading-os", None)
    wt = _fake_checkout(
        tmp_path, "ws/.yard/.heading-os/task-1",
        f"gitdir: {main}/.git/worktrees/task-1\n")
    assert _main_clone_root(wt) == main


def test_a_relative_gitdir_is_resolved_against_the_worktree(tmp_path):
    """git usually writes an absolute path and is not required to."""
    main = _fake_checkout(tmp_path, "ws/.heading-os", None)
    wt = tmp_path / "ws" / ".yard" / ".heading-os" / "task-2"
    wt.mkdir(parents=True)
    rel = os.path.relpath(main / ".git" / "worktrees" / "task-2", wt)
    (wt / ".git").write_text(f"gitdir: {rel}\n", encoding="utf-8")
    assert _main_clone_root(wt) == main


def test_the_overlay_is_then_found_beside_the_main_clone(tmp_path):
    """The whole point: the sibling rule is applied to the RIGHT directory."""
    main = _fake_checkout(tmp_path, "ws/.heading-os", None)
    (tmp_path / "ws" / ".heading-os-data").mkdir()
    wt = _fake_checkout(tmp_path, "ws/.yard/.heading-os/task-3",
                        f"gitdir: {main}/.git/worktrees/task-3\n")
    resolved = _main_clone_root(wt)
    assert (resolved.parent / ".heading-os-data").is_dir()


# ============================================================
# The direction that must still pass: a main clone is unchanged
# ============================================================

def test_a_main_clone_is_returned_unchanged(tmp_path):
    main = _fake_checkout(tmp_path, "ws/.heading-os", None)
    assert _main_clone_root(main) == main


@pytest.mark.parametrize("content", [
    "",                                   # empty .git file
    "not a gitdir line\n",                # unparseable
    "gitdir: /nowhere/special\n",         # right key, wrong shape
    "gitdir: /a/.git/notworktrees/x\n",   # near-miss on the directory name
])
def test_anything_unexpected_falls_back_to_the_checkout(tmp_path, content):
    """Fails toward the previous behaviour, never toward a stranger's directory.

    Returning some OTHER path here would point the guard at a tree nobody
    chose, which is worse than the None it used to return.
    """
    wt = _fake_checkout(tmp_path, "ws/odd", content)
    assert _main_clone_root(wt) == wt


def test_a_missing_git_entry_is_not_an_error(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    assert _main_clone_root(root) == root


# ============================================================
# This checkout, whichever kind it is
# ============================================================

def test_the_overlay_resolves_in_this_checkout():
    """The regression, on the real tree rather than on a fixture.

    Before the fix this returned None from any YARD. It is asserted as
    "resolves or there is genuinely no overlay here", so a public clone with no
    data repo beside it still passes -- and in that case `_watched_roots()`
    must be empty rather than half-populated.
    """
    root = _structural_overlay_root()
    if root is None:
        assert not _watched_roots(), (
            "no structural overlay, yet something is being watched")
        pytest.skip("no .heading-os-data beside this clone's main checkout; "
                    "nothing for the guard to watch here")
    assert root.is_dir()
    assert root.name == ".heading-os-data"


def test_the_guard_actually_refuses_a_write_into_the_overlay():
    """Driven end to end in a child interpreter, through the installed `.pth`.

    A report of "armed" is what failed here, so nothing in this test asks the
    guard about itself. It attempts a real write and requires the exception.
    The child is a fresh process because arming is process-wide state.
    """
    if _structural_overlay_root() is None:
        pytest.skip("no overlay beside this clone's main checkout")

    probe = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from scripts.utils.overlay_write_guard import _structural_overlay_root\n"
        "root = _structural_overlay_root()\n"
        "target = os.path.join(str(root), '.pytest-overlay-guard-canary')\n"
        "try:\n"
        "    open(target, 'w').write('x')\n"
        "except Exception as exc:\n"
        "    print(type(exc).__name__)\n"
        "    sys.exit(0)\n"
        "os.unlink(target)\n"
        "sys.exit(1)\n"
    )
    env = dict(os.environ, HEADING_OS_OVERLAY_GUARD="refuse")
    proc = subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=300, env=env)
    assert proc.returncode == 0, (
        "a write into the operator's overlay was NOT refused; the guard reports "
        f"armed and intercepts nothing. stdout={proc.stdout!r} "
        f"stderr={proc.stderr[-2000:]!r}")
    assert "Refused" in proc.stdout, proc.stdout


# ============================================================
# The bootstrap arms it and proves it
# ============================================================

def test_the_bootstrap_reinstalls_the_pth_after_the_sync():
    """`uv sync` deletes it. Something has to put it back, in the script."""
    src = _bootstrap_source()
    install = src.find("overlay-guard-install.py --install")
    sync = src.find("uv sync --all-extras")
    assert install != -1, "the bootstrap never re-installs the overlay guard"
    assert sync != -1, "the bootstrap no longer syncs; re-check this rule"
    assert install > sync, (
        "the guard is installed BEFORE `uv sync`, which then deletes it again")


def test_the_bootstrap_proves_the_guard_with_a_write_that_must_be_refused():
    """Obligation: a report is not proof. Step 10 sets the precedent."""
    src = _bootstrap_source()
    assert "HEADING_OS_OVERLAY_GUARD=refuse" in src
    assert "_structural_overlay_root" in src
    assert "did not refuse a write into the operator's overlay" in src, (
        "the failure message must name what was not proven")


def test_the_proof_removes_the_file_it_should_never_have_written():
    """The probe writes into the operator's REAL overlay when the guard is
    broken. It must not leave that file behind on the way to failing."""
    src = _bootstrap_source()
    start = src.index("HEADING_OS_OVERLAY_GUARD=refuse")
    block = src[start:src.index("PROBE\n", start)]
    assert "os.unlink(probe)" in block
    assert block.index("os.unlink(probe)") < block.rindex("sys.exit(1)"), (
        "the unlink must happen before the failure exit, or the canary stays")
