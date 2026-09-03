"""`assert_data_root_external()` — the precondition a YARD must clear.

The failure it exists to catch is silent by construction. The leak guard and the
data-path redirect arm themselves on `get_data_root() != get_workspace_root()`,
and that predicate is TRUE in demo mode too, so a worktree with no `.env`
resolves its data root to the bundled `examples` tree, keeps both guards armed,
reports healthy, and classifies every path against example data.

MEASURED 2026-09-03 in a fresh worktree of this repository:

    get_workspace_root()   -> <worktree>
    get_data_root()        -> <worktree>/examples      DEMO
    data_overlay_present() -> False

Both directions are asserted: each refusal has a matching case where the same
shape is healthy and the function returns. A guard that raised unconditionally
would satisfy every refusal test here and break every real caller.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import paths as paths_mod  # noqa: E402
from scripts.utils.paths import (  # noqa: E402
    DataRootError,
    assert_data_root_external,
)


def _overlay(tmp_path: Path, name: str = "overlay") -> Path:
    """A directory shaped like a real data overlay: exists, has a .git."""
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    return root


def _engine(tmp_path: Path) -> Path:
    """A directory shaped like an engine clone, for get_workspace_root()."""
    root = tmp_path / "engine"
    (root / ".claude").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("# engine\n", encoding="utf-8")
    return root


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Point both roots at throwaway trees, and hand back the pair."""
    engine = _engine(tmp_path)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))
    return engine, tmp_path


# ============================================================
# The healthy case — asserted first, so every refusal below means something
# ============================================================

def test_a_real_sibling_overlay_is_accepted(wired, monkeypatch):
    engine, tmp_path = wired
    overlay = _overlay(tmp_path)
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))
    assert assert_data_root_external() == overlay.resolve()


def test_the_accepted_case_returns_the_path_not_merely_none(wired, monkeypatch):
    """The return value is the contract the bootstrap prints back."""
    engine, tmp_path = wired
    overlay = _overlay(tmp_path)
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))
    result = assert_data_root_external()
    assert isinstance(result, Path) and result.is_absolute()


# ============================================================
# Refusal 1 — a relative HEADING_OS_DATA
# ============================================================

def test_a_relative_data_root_is_refused(wired, monkeypatch, tmp_path):
    engine, _ = wired
    _overlay(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HEADING_OS_DATA", "overlay")
    with pytest.raises(DataRootError, match="relative path"):
        assert_data_root_external()


def test_the_same_overlay_by_absolute_path_is_accepted(
    wired, monkeypatch, tmp_path,
):
    """The pair to the test above: only the SPELLING was wrong, not the tree."""
    engine, _ = wired
    overlay = _overlay(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))
    assert assert_data_root_external() == overlay.resolve()


# ============================================================
# Refusals 2 and 3 — the data root is, or is inside, the engine clone
# ============================================================

def test_a_data_root_equal_to_the_workspace_root_is_refused(wired, monkeypatch):
    engine, _ = wired
    (engine / ".git").mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(engine))
    with pytest.raises(DataRootError, match="inside the engine clone"):
        assert_data_root_external()


def test_a_data_root_nested_inside_the_engine_is_refused(wired, monkeypatch):
    engine, _ = wired
    inner = engine / "private"
    (inner / ".git").mkdir(parents=True)
    monkeypatch.setenv("HEADING_OS_DATA", str(inner))
    with pytest.raises(DataRootError, match="inside the engine clone"):
        assert_data_root_external()


# ============================================================
# Refusal 4 — demo mode, the one that looks healthy
# ============================================================

def test_demo_mode_is_refused(wired, monkeypatch):
    """The measured worktree state: no HEADING_OS_DATA, no sibling overlay.

    `get_data_root()` falls through to `<root>/examples`, `data_root_is_demo()`
    is True, and every guard gated on `get_data_root() != get_workspace_root()`
    stays armed while pointing at example data.
    """
    engine, _ = wired
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    (engine / "examples").mkdir()
    with pytest.raises(DataRootError, match="read-only examples"):
        assert_data_root_external()


def test_demo_mode_still_satisfies_the_predicate_both_guards_arm_on(
    wired, monkeypatch,
):
    """Why the demo refusal has to exist at all, pinned as a fact.

    If demo mode made the arming predicate False, the guards would go inert
    loudly and something else would have caught this. It does not. They stay
    armed, and that is precisely why the state is indistinguishable from a
    healthy one without this assertion.
    """
    engine, _ = wired
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    (engine / "examples").mkdir()
    assert paths_mod.get_data_root() != paths_mod.get_workspace_root()
    assert paths_mod.data_root_is_demo() is True


# ============================================================
# Refusal 5 — the overlay is not a git repository
# ============================================================

def test_an_overlay_without_a_git_directory_is_refused(wired, monkeypatch,
                                                       tmp_path):
    engine, _ = wired
    bare = tmp_path / "no-git"
    bare.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(bare))
    with pytest.raises(DataRootError, match="not a git repository"):
        assert_data_root_external()


def test_adding_the_git_directory_makes_the_same_overlay_acceptable(
    wired, monkeypatch, tmp_path,
):
    engine, _ = wired
    overlay = tmp_path / "becomes-a-repo"
    overlay.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))
    with pytest.raises(DataRootError):
        assert_data_root_external()
    (overlay / ".git").mkdir()
    assert assert_data_root_external() == overlay.resolve()


# ============================================================
# The pre-existing refusal this function deliberately does not duplicate
# ============================================================

def test_a_missing_directory_still_raises_from_the_env_resolver(
    wired, monkeypatch, tmp_path,
):
    """`env_data_root()` already refuses a set-but-missing value.

    Asserted here so the division of labour is visible: this function adds the
    ambiguous-but-present cases, and does not reimplement the absent one.
    """
    engine, _ = wired
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "does-not-exist"))
    with pytest.raises(DataRootError, match="not an existing directory"):
        assert_data_root_external()
