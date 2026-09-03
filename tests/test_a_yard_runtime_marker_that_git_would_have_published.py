"""A per-worktree runtime receipt that no .gitignore rule matched.

`scripts/herdr/heading-os-yard/yard-bootstrap.sh` writes
`.claude/.yard-bootstrap-status` at step 11, and `.claude/hooks/session-start.py`
reads it to decide whether a YARD is provisioned. It records the status, the
step reached and a timestamp for ONE checkout, so it is meaningless in any
other and must never be committed.

MEASURED 2026-09-03 on the YARD at `.yard/.heading-os/test-123`, before the fix:

    $ git check-ignore -v .claude/.yard-bootstrap-status
    $ echo $?
    1                          # no rule matched: the file was committable

    $ git status --porcelain
     M uv.lock
    ?? .claude/.yard-bootstrap-status

So the receipt sat untracked in every YARD's `git status` permanently, and a
`git add -A` inside any task would have carried it into a PUBLIC repository.
The engine-tree-clean wall does not close this: it looks for artifacts that
route `private`, and `.claude/` routes `engine`, so a runtime marker underneath
it is precisely the shape that wall was never built to see.

After the fix the same command exits 0 and names `.gitignore` as the source.

Both directions matter here and the second is the one with teeth. A rule wide
enough to hide the receipt is trivially easy to write (`.claude/`, `.claude/.*`,
`.claude/.yard-*`) and would hide the engine's own skills, rules and hooks from
git. So the corpus test below asserts that all 471 tracked files under
`.claude/` remain visible, with a floor so it cannot pass over an empty walk.

Run: python3 -m pytest tests/test_a_yard_runtime_marker_that_git_would_have_published.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.repo_files import git_index_paths, ignored_paths  # noqa: E402

MARKER = ".claude/.yard-bootstrap-status"

# MEASURED 2026-09-03: `git ls-files .claude/ | wc -l` -> 471. The floor is set
# well below it so ordinary growth or pruning of the skills tree does not fail
# the suite, while a walk that collapses to nothing still cannot pass.
TRACKED_CLAUDE_FLOOR = 300


def _check_ignore(path: str) -> subprocess.CompletedProcess:
    """Ask git about ONE named path, with `-v` so the matching rule is named.

    Deliberately not the batch form. `scripts/utils/repo_files.ignored_paths`
    owns `check-ignore --stdin` and a second copy of it here would be the
    duplicate that `tests/test_a_walker_that_never_asked_git.py` refuses. The
    single-path question is a different one with a different answer shape, and
    that guard exempts it by name; `-v` is why it is asked this way at all,
    since only the verbose form reports WHICH file supplied the rule.
    """
    return subprocess.run(
        ["git", "check-ignore", "-v", path],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60, check=False)


def _tracked_claude_files() -> list[str]:
    """The `.claude/` slice of git's index, through the one shared reader.

    `git_index_paths` passes `-z` and decodes with `surrogateescape`, so a path
    holding a newline or a non-UTF-8 byte is not silently dropped. A local
    `git ls-files` here was exactly the defect
    `tests/test_a_publisher_that_could_not_see_a_non_ascii_path.py` sweeps for,
    and it caught this file on 2026-09-03.
    """
    return [p for p in git_index_paths(ROOT) if p.startswith(".claude/")]


# ============================================================
# The direction that must now be refused
# ============================================================

def test_the_bootstrap_receipt_is_ignored():
    """The reported defect, asserted against the real repository."""
    proc = _check_ignore(MARKER)
    assert proc.returncode == 0, (
        f"{MARKER} matches no .gitignore rule; a `git add -A` in any YARD "
        f"would publish it. stdout={proc.stdout!r}")


def test_the_rule_lives_in_gitignore_and_says_so():
    """`-v` names the source file and line, so the match is not accidental.

    A match produced by `.git/info/exclude` or a global core.excludesFile would
    be local to one machine and would not travel with the repository, which is
    the whole point of fixing this in a tracked file.
    """
    proc = _check_ignore(MARKER)
    assert proc.returncode == 0
    assert proc.stdout.startswith(".gitignore:"), proc.stdout


def test_the_receipt_is_absent_from_status():
    """The observable consequence: it no longer appears as untracked."""
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60, check=True)
    assert MARKER not in proc.stdout, proc.stdout


# ============================================================
# The direction that must still pass
# ============================================================

def test_the_engine_tree_under_claude_stays_visible():
    """The negative case: the rule must not be a wide glob over `.claude/`.

    Asserted over the whole tracked corpus, with the floor OUTSIDE the walk so
    an empty `git ls-files` cannot render this vacuously green.
    """
    tracked = _tracked_claude_files()
    assert len(tracked) >= TRACKED_CLAUDE_FLOOR, (
        f"only {len(tracked)} tracked files under .claude/; the corpus "
        f"collapsed and this test would otherwise pass over nothing")

    ignored = ignored_paths([ROOT / p for p in tracked], ROOT)
    assert not ignored, (
        f"these tracked engine files are now ignored: {sorted(ignored)[:10]}")


@pytest.mark.parametrize("path", [
    ".claude/settings.json",
    ".claude/rules/classification.md",
    ".claude/hooks/session-start.py",
    ".claude/.yard-bootstrap-status.md",
])
def test_named_neighbours_are_not_swept_up(path):
    """Spot checks either side of the rule, including a lookalike.

    The last entry shares the receipt's whole name as a prefix. It is not the
    receipt, and a rule written as a prefix glob would take it too.
    """
    proc = _check_ignore(path)
    assert proc.returncode == 1, (
        f"{path} should not be ignored, but: {proc.stdout!r}")


def test_the_marker_path_is_the_one_the_bootstrap_writes():
    """Pin the rule to the real writer, not to a name this test invented.

    If the bootstrap ever renames its receipt, the .gitignore line silently
    stops covering anything and the defect returns. Asserting the literal
    against the shell script is what makes that a red test rather than a quiet
    regression.
    """
    src = (ROOT / "scripts" / "herdr" / "heading-os-yard"
           / "yard-bootstrap.sh").read_text(encoding="utf-8")
    assert '.claude/.yard-bootstrap-status' in src

    hook = (ROOT / ".claude" / "hooks" / "session-start.py").read_text(
        encoding="utf-8")
    assert '".yard-bootstrap-status"' in hook
