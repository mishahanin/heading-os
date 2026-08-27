#!/usr/bin/env python3
"""A lock sidecar must not reach a repository.

`CP.file_lock` writes an empty file whose only meaning is the `flock` held on it
at runtime. It carries no data, it restores itself on first use, and every
commit of it is noise in a history that two machines share.

The engine's locks were already covered: `.claude/state/` is ignored whole, and
`locked_state` puts its sidecar beside the state file. The DATA overlay's was
not. `checkpoint-save.py` serialises the shared `.latest/{summary,prompt}.md`
pair behind `latest_dir / ".pointers.lock"`, which lands in the overlay's
tracked `outputs/` tree, and it showed up untracked in `git status` on
2026-08-20 - one `git add -A` from being committed.

The guard asks git, not the text of `.gitignore`: a rule that exists but does
not match the path it was written for is exactly the failure being prevented.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import checkpoint_paths as CP  # noqa: E402
from scripts.utils.workspace import get_data_root  # noqa: E402


def _ignored(repo: Path, path: Path) -> bool:
    """True when git would refuse to track `path`. `check-ignore` exits 0 on a
    match, 1 on none, and >1 on a real error - which must not read as `False`."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(path)],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode > 1:
        pytest.fail(f"git check-ignore failed in {repo}: {result.stderr.strip()}")
    return result.returncode == 0


def _repo(path: Path) -> Path:
    if not (path / ".git").exists():
        pytest.skip(f"{path} is not a git checkout here")
    return path


def test_the_shared_pointer_lock_is_ignored_by_the_data_overlay():
    """The path is built the way `checkpoint-save.py` builds it, so renaming the
    directory in production breaks this test instead of silently escaping it."""
    data = _repo(Path(get_data_root()))
    lock = CP.handoff_dir(CP.project_root(), CP.engine_root()) / ".latest" / ".pointers.lock"
    assert str(lock).startswith(str(data)), (
        f"the handoff tree resolved outside the data overlay: {lock}"
    )
    assert _ignored(data, lock), (
        f"{lock} would be committed. A lock sidecar carries no data; the "
        f"guarantee lives in flock at runtime, never in history."
    )


# A dependency lockfile shares the suffix and nothing else: it is content, it is
# the source of truth for the resolved dependency set, and CLAUDE.md requires it
# tracked. Named rather than pattern-matched, so a NEW tracked `.lock` has to be
# argued for here instead of slipping through a loosened glob.
TRACKED_BY_DESIGN = {"uv.lock"}


def test_no_lock_sidecar_is_tracked_in_either_repository():
    """The broad half. A second lock added anywhere, under any name, fails here
    the moment it is committed - the specific test above cannot see it."""
    inspected = 0
    for repo in (ROOT, Path(get_data_root())):
        if not (repo / ".git").exists():
            continue
        inspected += 1
        listed = subprocess.run(
            ["git", "ls-files", "-z", "*.lock"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout
        tracked = [name for name in listed.split("\0")
                   if name and name not in TRACKED_BY_DESIGN]
        assert not tracked, (
            f"{repo.name} tracks {len(tracked)} lock file(s): {tracked[:5]}. "
            f"Add the path to that repo's .gitignore with the reason."
        )
    # Measured 2 repositories inspected on 2026-08-26 (engine plus the data
    # overlay); floored at 1 because a CI runner has no overlay and legitimately
    # sees only the engine. Names the drift it catches: if
    # `not (repo / ".git").exists()` becomes true for every repo (a relocation,
    # a worktree whose `.git` is a file, a data root that resolves nowhere),
    # both are skipped, `tracked` is never built, and the assertion above passes
    # having read no repository at all.
    assert inspected >= 1, f"no repository was inspected (measured {inspected})"


def test_the_state_lock_is_ignored_by_the_engine():
    """`locked_state` names its sidecar `<state file>.lock`; the engine ignores
    `.claude/state/` whole, so the sidecar is covered by the directory rule
    rather than by one of its own. Held here so a narrowing of that rule to,
    say, `.claude/state/*.json` fails loudly."""
    engine = _repo(ROOT)
    state = CP.state_path(CP.project_root(), "guard-slug")
    assert _ignored(engine, state.with_name(state.name + ".lock"))


def test_a_lock_sidecar_at_an_unseen_path_is_ignored_by_the_data_overlay():
    """The two tests above cover the sidecars that already exist. This covers the
    next one.

    The overlay's rules were written one path at a time, each after a specific
    sidecar appeared, and on 2026-08-27 that enumeration fell behind: a new
    `outputs/operations/ops-radar/autoheal.json.lock` matched no rule, `push-all`
    committed it with `git add -A`, and the broad test above then refused the
    ENGINE push over a file in the OTHER repository. The fix was one
    `outputs/**/*.lock` rule; this asks whether that rule is still there, using
    paths that exist nowhere on disk so no real sidecar can make it pass.

    `git check-ignore` answers about a path, not about a file, so an invented
    directory is a legitimate question to put to it.
    """
    data = _repo(Path(get_data_root()))
    for invented in (
        "outputs/operations/a-tool-not-yet-written/state.json.lock",
        "outputs/never-created/deep/er/still/deeper/x.lock",
        "outputs/one-level.lock",
    ):
        assert _ignored(data, data / invented), (
            f"{invented} would be committed. The overlay is back to naming lock "
            f"sidecars one path at a time, which is the arrangement that failed."
        )
