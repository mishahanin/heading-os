"""The engine tree-clean wall must inspect the checkout it is running in.

A guard pointed at the wrong tree reports clean, and a clean report is exactly
what a healthy guard produces. The two states are indistinguishable by their
result, which is the 2026-06-22 failure shape, and a git worktree reintroduces
it more easily than anything else in this repository: the worktree shares one
git directory with the main clone, so a root resolved from a constant, from the
common git directory, or from an environment variable lands on the untouched
main clone while the violation sits in the worktree.

MEASURED 2026-09-03, before any code in the HELM/YARD change existed:

    scan_engine_repo(<worktree with docs/security/.yard-canary-probe.md>)
        -> ['docs/security/.yard-canary-probe.md']
    scan_engine_repo(<main clone>)
        -> []

So the wall is already correct in a worktree, and this file pins that rather
than repairing it. Both directions are asserted, because a guard that flags
everything satisfies every firing test and breaks every honest caller.

The second half of the file pins the reason the canary uses `docs/security/`
and not one of the other seven private directories the original specification
listed. `repo_carried_paths()` asks git via `ls-files --others
--exclude-standard`, so a file git ignores is invisible to the wall. MEASURED
2026-09-03: of eight candidate probe paths, SIX are gitignored, and a decoy in
any of them passes the scan while reading as "the guard is pointed at the wrong
tree". Only `docs/security/` and `auto-memory/` route non-engine AND are
visible to git.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.engine_guard import scan_engine_repo  # noqa: E402
from scripts.utils.workspace import get_routing_destination  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# The probe path the YARD bootstrap plants. Private-routed and NOT gitignored.
CANARY_REL = "docs/security/.yard-canary-probe.md"

# The eight paths the original specification told the bootstrap to try, in its
# order. The count is asserted below so this corpus cannot quietly empty.
SPEC_CANDIDATE_DIRS = (
    "docs/security/",
    "knowledge/",
    "crm/contacts/",
    "context/",
    "plans/",
    "threads/",
    "outputs/",
    "auto-memory/",
)


def _plant(tree: Path, rel: str) -> Path:
    target = tree / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("canary probe\n", encoding="utf-8")
    return target


def test_the_wall_flags_a_decoy_planted_in_the_worktree(temporary_worktree):
    """Firing case: the violation is in the worktree, and the wall sees it."""
    _plant(temporary_worktree, CANARY_REL)
    assert CANARY_REL in scan_engine_repo(temporary_worktree)


def test_the_same_decoy_leaves_the_main_clone_reported_clean(temporary_worktree):
    """Quiet case, and the one that makes the firing case mean something.

    If this asserted only that the worktree scan flags, a wall hardcoded to
    flag everything would pass. The pair proves the wall answers about the tree
    it was handed.
    """
    _plant(temporary_worktree, CANARY_REL)
    assert scan_engine_repo(ROOT) == []


def test_removing_the_decoy_restores_a_clean_worktree(temporary_worktree):
    """The bootstrap deletes its probe. Prove the deletion is observable."""
    probe = _plant(temporary_worktree, CANARY_REL)
    assert scan_engine_repo(temporary_worktree) != []
    probe.unlink()
    assert scan_engine_repo(temporary_worktree) == []


def test_the_canary_path_routes_private_and_is_visible_to_git():
    """The two properties the probe path must hold, asserted separately.

    Private-routed but gitignored is the trap: the wall never sees the file, so
    the bootstrap concludes the guard is misdirected and refuses to start every
    single YARD.
    """
    assert get_routing_destination(CANARY_REL) != "engine"
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", CANARY_REL],
        cwd=str(ROOT), capture_output=True,
    ).returncode == 0
    assert not ignored, f"{CANARY_REL} is gitignored, so the wall cannot see it"


def test_most_of_the_specifications_probe_paths_are_invisible_to_the_wall():
    """Pin the measurement that forced the probe-path filter to exist.

    The floor is asserted outside the loop: a corpus that shrank to nothing
    would otherwise satisfy every assertion inside it.
    """
    assert len(SPEC_CANDIDATE_DIRS) == 8

    usable, ignored = [], []
    for directory in SPEC_CANDIDATE_DIRS:
        rel = directory + ".yard-canary-probe.md"
        if get_routing_destination(rel) == "engine":
            continue
        is_ignored = subprocess.run(
            ["git", "check-ignore", "-q", rel],
            cwd=str(ROOT), capture_output=True,
        ).returncode == 0
        (ignored if is_ignored else usable).append(directory)

    # MEASURED 2026-09-03: usable == ['docs/security/', 'auto-memory/'].
    # Asserted as a property rather than as the literal pair, so a future
    # routing-map change that makes a third path usable does not fail this.
    assert CANARY_REL.rsplit("/", 1)[0] + "/" in usable
    assert len(ignored) >= 5, (
        "The gitignore trap this test pins has gone away; re-measure before "
        f"relaxing the bootstrap probe filter. ignored={ignored}"
    )


def _resolve_root_in(tree: Path, env_extra: dict | None = None) -> str:
    """Ask the checkout at `tree` for its own workspace root, in a subprocess.

    In-process would answer for the main clone, because `scripts.utils.paths`
    is already imported from there and resolves from its own `__file__`.
    """
    import os

    env = {k: v for k, v in os.environ.items() if k != "WORKSPACE_ROOT"}
    env.update(env_extra or {})
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]);"
         "from scripts.utils.paths import get_workspace_root;"
         "print(get_workspace_root())",
         str(tree)],
        cwd=str(tree), capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_a_worktree_resolves_its_own_root(temporary_worktree):
    """`get_workspace_root()` marker-walks from its own file, so a worktree's
    copy answers with the worktree. This is what keeps the wall pointed right."""
    assert Path(_resolve_root_in(temporary_worktree)) == temporary_worktree.resolve()


def test_workspace_root_in_the_environment_repoints_the_whole_seam(
    temporary_worktree,
):
    """The hazard, pinned as a characterisation test rather than a wish.

    `get_workspace_root()` reads `WORKSPACE_ROOT` FIRST. Every guard downstream
    derives its inspected tree from that call, so one line carried into a
    worktree's `.env` from the main clone silently repoints all of them at the
    main clone. Nothing errors, and each of them then reports clean.

    This test does NOT assert the behaviour is wrong; the override is
    deliberate and other callers need it. It asserts the override REACHES the
    seam, which is why `yard-bootstrap.sh` strips the variable and then
    verifies the resolved root instead of trusting the strip.
    """
    hijacked = _resolve_root_in(
        temporary_worktree, {"WORKSPACE_ROOT": str(ROOT)},
    )
    assert Path(hijacked) == ROOT
    assert Path(hijacked) != temporary_worktree.resolve()


@pytest.mark.parametrize("relative", [True, False])
def test_the_wall_reads_the_worktree_index_not_the_shared_one(
    temporary_worktree, relative,
):
    """`repo_carried_paths` runs git with `cwd=root`.

    A worktree has its own index and its own working tree while sharing the
    object store, so this is the property that makes a per-checkout answer
    possible at all. Passing the root as a str and as a Path both work; the
    parametrisation pins that `scan_engine_repo` coerces.
    """
    _plant(temporary_worktree, CANARY_REL)
    root_arg = str(temporary_worktree) if relative else temporary_worktree
    assert CANARY_REL in scan_engine_repo(root_arg)
