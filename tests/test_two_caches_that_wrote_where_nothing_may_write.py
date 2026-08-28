"""Two caches that wrote where nothing may write.

Shard 51 of the engine audit. Two tools cache derived PRIVATE content, and
neither asked where it was allowed to write.

`docparse` cached the full extracted TEXT of every document it parsed into
`<workspace_root>/.cache/docparse`, which is inside the ENGINE clone, on a
workspace whose whole premise is that the engine carries code and nothing else.
It is gitignored, so it could never be committed and this was never a leak. It
was on the wrong side of the seam, in a tree no content wall looks at:
`repo_carried_paths` passes `--exclude-standard` and so cannot see it, which is
correct for that function's stated job and is exactly why nothing caught this.
MEASURED 2026-08-28 on the operator's own machine: five parsed documents.

`firecrawl` cached scraped pages under `get_outputs_dir()`, which is the right
side when a data overlay exists. With NO overlay `get_data_root()` answers
`<workspace_root>/examples`, the bundled demo tree, and
`scripts/utils/engine_guard.py` treats that tree as a CLOSED MANIFEST: anything
untracked under it is a data artifact. MEASURED on a clone with no overlay, one
cached scrape wrote `examples/outputs/browser/firecrawl-cache/<key>.json`, no
gitignore rule covered it (`outputs/browser/firecrawl-cache/` is root-anchored
and does not match a path under `examples/`), and `scan_engine_repo` flagged it.
The pre-commit wall runs on every commit and the push wall on every push, so
from that moment both refuse, over a directory nothing told the operator about.

The two are the same defect pointing opposite ways, so they share one answer:
`paths.private_cache_dir`. Cache beside the data when there is a separate data
overlay; otherwise cache under the workspace root, which is where a standalone
clone's own material belongs. Never under the data root, because with no overlay
the data root IS the demo tree.

Nothing here reaches the network, spends a Firecrawl credit, parses a real
document, or touches the operator's live caches.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import docparse as dp  # noqa: E402
from scripts import firecrawl as fc  # noqa: E402
from scripts.utils.engine_guard import (  # noqa: E402
    repo_carried_paths,
    scan_engine_repo,
)
from scripts.utils.paths import private_cache_dir  # noqa: E402


@pytest.fixture
def clone(tmp_path, monkeypatch):
    """A workspace root that looks like an engine clone, plus a data overlay.

    Returns (workspace, overlay). Neither is selected yet: each test says which
    world it is in by setting or clearing HEADING_OS_DATA.
    """
    ws = tmp_path / "clone" / ".heading-os"
    (ws / ".claude").mkdir(parents=True)
    (ws / "CLAUDE.md").write_text("marker\n", encoding="utf-8")
    (ws / "examples").mkdir()
    overlay = tmp_path / "clone" / "overlay"
    overlay.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    monkeypatch.delenv("WORKSPACE_CACHE_DIR", raising=False)
    return ws, overlay


# ============================================================
# private_cache_dir - the rule itself
# ============================================================

def test_with_an_overlay_the_cache_goes_under_the_overlay(clone, monkeypatch):
    ws, overlay = clone
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))

    assert private_cache_dir("docparse") == overlay.resolve() / ".cache" / "docparse"


def test_with_no_overlay_the_cache_goes_under_the_workspace_root(clone):
    """NOT under the data root. With no overlay the data root IS the demo tree,
    and the demo tree is a closed manifest nothing may write into."""
    ws, _ = clone

    resolved = private_cache_dir("docparse")

    assert resolved == ws / ".cache" / "docparse"
    assert "examples" not in resolved.parts, resolved


def test_a_legacy_single_tree_workspace_does_not_cache_into_its_own_data(clone,
                                                                        monkeypatch):
    """`get_data_root()` returns the workspace ITSELF once `knowledge/` appears
    in it (the transitional in-tree layout), so the cache must land under
    `.cache/` rather than beside the records.

    A recorded finding sits here. `data_overlay_present()` and
    `not data_root_is_demo()` cannot be told apart at this call site: MEASURED
    2026-08-28 across all three worlds (demo, real overlay, legacy in-tree),
    both spellings return the same path every time, because the ONLY world
    where the two predicates disagree is this one, and here the data root IS
    the workspace root, so both branches answer with the same directory. The
    mutation swapping them was therefore an equivalent mutant and was REMOVED
    from `.tmp/audit/mut_caches.py` rather than left to survive.
    `data_overlay_present()` stays in the code because it says what is meant;
    the coincidence is not something a reader should have to reconstruct.
    """
    ws, _ = clone
    (ws / "knowledge").mkdir()

    assert private_cache_dir("docparse") == ws / ".cache" / "docparse"


def test_the_env_override_wins(clone, monkeypatch):
    ws, overlay = clone
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))
    elsewhere = ws.parent / "elsewhere"
    monkeypatch.setenv("WORKSPACE_CACHE_DIR", str(elsewhere))

    assert private_cache_dir("docparse") == elsewhere / "docparse"


def test_no_parts_answers_the_cache_root_itself(clone):
    ws, _ = clone
    assert private_cache_dir() == ws / ".cache"


def test_asking_where_the_cache_is_does_not_create_it(clone):
    """Unlike `data_dir`, `state_dir` and `log_dir`, which mkdir eagerly.

    Writers already mkdir before they write. A resolver that creates the
    directory leaves an empty one behind on every clone that merely asked.
    """
    ws, _ = clone

    resolved = private_cache_dir("docparse")

    assert not resolved.exists()
    assert not (ws / ".cache").exists()


# ============================================================
# The consequence, against the real wall
# ============================================================

def _fake_engine_clone(tmp_path: Path) -> Path:
    """A git repo carrying this engine's real .gitignore.

    The REAL file, not a hand-written stand-in: the point of the exercise is
    which paths that file covers, so a copy written here would be testing the
    copy.
    """
    repo = tmp_path / "engine"
    (repo / "examples" / "outputs").mkdir(parents=True)
    (repo / "examples" / "outputs" / ".gitkeep").write_text("", encoding="utf-8")
    (repo / ".gitignore").write_text(
        (ROOT / ".gitignore").read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    return repo


def test_a_cache_under_the_demo_tree_is_refused_by_the_engine_wall(tmp_path):
    """The defect, reproduced. This is what one scrape did on a clone with no
    data overlay, and from then on every commit and every push refused."""
    repo = _fake_engine_clone(tmp_path)
    old = repo / "examples" / "outputs" / "browser" / "firecrawl-cache"
    old.mkdir(parents=True)
    (old / "key.json").write_text('{"content": "a scraped page"}', encoding="utf-8")

    flagged = scan_engine_repo(repo)

    assert "examples/outputs/browser/firecrawl-cache/key.json" in flagged


def test_the_new_cache_locations_are_covered_by_this_repos_gitignore(tmp_path):
    """The fix, and it has to ask GIT, not the wall.

    The first version of this test asserted `scan_engine_repo(repo) == []` and
    passed with the `.gitignore` rules deleted, because the wall answers a
    narrower question: it flags what routes private/corporate and what sits
    untracked under `examples/`. A `.cache/` path at the engine root routes to
    the `engine` default, so the wall clears it either way. Two mutations that
    stripped the ignore rules survived that assertion.

    The rules matter for the other reason: without them git OFFERS the files.
    They show in `git status`, `git add -A` stages them, and what gets staged is
    the extracted text of the operator's documents. So this asks
    `repo_carried_paths`, which is exactly "what would git carry".
    """
    repo = _fake_engine_clone(tmp_path)
    for tool in ("firecrawl", "docparse"):
        d = repo / ".cache" / tool
        d.mkdir(parents=True)
        (d / "key.json").write_text('{"content": "private"}', encoding="utf-8")

    carried = repo_carried_paths(repo)

    assert [p for p in carried if p.startswith(".cache/")] == [], carried
    assert scan_engine_repo(repo) == []


def test_without_the_ignore_rules_git_would_carry_the_cache(tmp_path):
    """The negative case, so the test above is a guard and not a coincidence.

    A control with no ignore rules at all: the same two files ARE carried. If
    this ever stops being true, the assertion above has stopped measuring the
    `.gitignore` and starts passing for a reason nobody chose.
    """
    repo = tmp_path / "bare"
    (repo / ".cache" / "firecrawl").mkdir(parents=True)
    (repo / ".cache" / "firecrawl" / "key.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)

    assert ".cache/firecrawl/key.json" in repo_carried_paths(repo)


# ============================================================
# The two tools
# ============================================================

def test_docparse_caches_beside_the_data_not_inside_the_engine(clone, monkeypatch):
    ws, overlay = clone
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))

    resolved = dp.cache_dir()

    assert resolved == overlay.resolve() / ".cache" / "docparse"
    assert ws not in resolved.parents, resolved


def test_docparse_on_a_clone_with_no_overlay_caches_under_the_root(clone):
    ws, _ = clone

    resolved = dp.cache_dir()

    assert resolved == ws / ".cache" / "docparse"
    assert "examples" not in resolved.parts, resolved


def test_firecrawls_overlay_path_is_deliberately_unchanged(clone, monkeypatch):
    """Moving it would orphan whatever is cached there now, and a cache
    `clear-cache` can no longer see is worse than an untidy path. MEASURED
    2026-08-28 on the operator's machine: twelve live entries."""
    ws, overlay = clone
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))

    assert fc.cache_dir() == overlay.resolve() / "outputs" / "browser" / "firecrawl-cache"


def test_firecrawl_on_a_clone_with_no_overlay_leaves_the_demo_tree_alone(clone):
    ws, _ = clone

    resolved = fc.cache_dir()

    assert resolved == ws / ".cache" / "firecrawl"
    assert "examples" not in resolved.parts, resolved


def test_clear_cache_names_the_directory_it_acted_on(clone, capsys):
    """The cache moves with the data overlay, so a bare "Cache directory does
    not exist" leaves the operator unable to tell an empty cache from a cache
    somewhere they did not look."""
    ws, _ = clone

    fc.cmd_clear_cache(None)
    missing = capsys.readouterr().err
    assert str(ws / ".cache" / "firecrawl") in missing

    d = ws / ".cache" / "firecrawl"
    d.mkdir(parents=True)
    (d / "a.json").write_text("{}", encoding="utf-8")
    fc.cmd_clear_cache(None)
    cleared = capsys.readouterr().err
    assert "Cleared 1 cached entries" in cleared
    assert str(d) in cleared
    assert not (d / "a.json").exists()


# ============================================================
# The old name is gone, and its absence is the guard
# ============================================================

@pytest.mark.parametrize("mod", [dp, fc], ids=["docparse", "firecrawl"])
def test_the_module_constant_is_deleted_not_reassigned(mod):
    """Six tests monkeypatched `CACHE_DIR` to redirect the cache.

    Had the name survived as a stale constant, each of those patches would have
    bound something nothing reads, and the test would have gone on writing into
    the operator's REAL cache while reporting green. Deleting the name makes
    `monkeypatch.setattr` raise instead.
    """
    assert not hasattr(mod, "CACHE_DIR")
    assert callable(mod.cache_dir)


@pytest.mark.parametrize("module", ["scripts.docparse", "scripts.firecrawl"])
def test_importing_the_module_does_not_resolve_the_data_root(tmp_path, module):
    """A by-product of the move, pinned for these two files only.

    `firecrawl.CACHE_DIR` and `docparse.DEFAULT_OUTPUT_DIR` both called
    `get_outputs_dir()` at import, so a HEADING_OS_DATA naming a directory that
    had since moved raised DataRootError out of the import itself: no argparse,
    no usage line, a traceback from `--help`.

    Scope, stated because the pattern is common: 31 scripts under `scripts/` and
    `.claude/` still bind a module constant from `get_outputs_dir()` or
    `get_data_root()` at import (MEASURED 2026-08-28). This test covers the two
    this shard touched and no others, and it is NOT a claim that the shape is
    gone. Whether it should be is the operator's call, not a defect this audit
    can decide alone: the loud raise on a stale HEADING_OS_DATA is deliberate
    (2026-08-25), and the only thing wrong here is WHEN it fires.

    A subprocess, because the module is already imported in this process and an
    import-time defect cannot be observed after the fact.
    """
    env = dict(os.environ)
    env["HEADING_OS_DATA"] = str(tmp_path / "gone")
    env["PYTHONPATH"] = str(ROOT)

    out = subprocess.run([sys.executable, "-c", f"import {module}"],
                         cwd=str(ROOT), env=env, capture_output=True, text=True)

    assert out.returncode == 0, out.stderr
    assert "DataRootError" not in out.stderr
