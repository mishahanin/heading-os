"""Version-sync guard (scripts/check-version-sync.py).

The guard asserts that pyproject.toml, README § Status, the newest CHANGELOG
release heading and the ROADMAP preamble name one version. It had no test at
all, which is how "HEADING OS is `v0.3.0`" survived six releases in ROADMAP.md:
the guard read three surfaces and the roadmap was the fourth.

Two properties are held here. The resolvers must read what they claim to read,
and the pre-commit hook must FIRE when one of those files is edited, because a
guard whose `files:` pattern omits a surface is a guard that never sees the
drift it was widened for.

Run: python3 -m pytest tests/test_version_sync_guard.py
"""
import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "check-version-sync.py"
PRE_COMMIT = ROOT / ".pre-commit-config.yaml"


def _guarded_surfaces() -> list[str]:
    """The files the guard reads, asked of the guard rather than typed here.

    Every resolver opens exactly one file as `root / "<name>"`, so the AST
    answers the question directly. A hand-written list is the same defect this
    module exists to catch, one level up: ROADMAP.md drifted for six releases
    because the pre-commit pattern named three files, and a hand-written
    SURFACES here would let a FIFTH resolver land with the pattern naming four
    and the test still green.
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                and isinstance(node.left, ast.Name) and node.left.id == "root"
                and isinstance(node.right, ast.Constant)
                and isinstance(node.right.value, str)
                and node.right.value not in found):
            found.append(node.right.value)
    return found


SURFACES = _guarded_surfaces()


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("check_version_sync_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hook():
    config = yaml.safe_load(PRE_COMMIT.read_text(encoding="utf-8"))
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            if hook.get("id") == "version-sync":
                return hook
    raise AssertionError("the version-sync hook is not in .pre-commit-config.yaml")


# ============================================================
# The four surfaces agree
# ============================================================

def test_every_surface_names_the_pyproject_version(guard):
    truth = guard._pyproject_version(ROOT)
    assert guard._readme_status_version(ROOT) == truth
    assert guard._changelog_latest_version(ROOT) == truth
    assert guard._roadmap_version(ROOT) == truth
    # Added 2026-09-03 with the lock surface. Enumerating four resolvers while
    # the guard runs five would leave the newest one asserted nowhere on this
    # tree, which is the shape that let ROADMAP.md drift for six releases.
    assert guard._uv_lock_version(ROOT, guard._pyproject_name(ROOT)) == truth


def test_the_guard_passes_on_this_tree(guard):
    assert guard.main.__module__  # the module loaded
    assert guard._pyproject_version(ROOT) == guard._roadmap_version(ROOT)


# ============================================================
# The resolvers read what they claim to read
# ============================================================

def test_the_roadmap_resolver_reads_the_preamble(guard, tmp_path, monkeypatch):
    (tmp_path / "ROADMAP.md").write_text(
        "# Roadmap\n\nHEADING OS is `v1.2.3`. Direction, not dates.\n", encoding="utf-8"
    )
    assert guard._roadmap_version(tmp_path) == "1.2.3"


def test_the_roadmap_resolver_returns_none_when_the_token_is_absent(guard, tmp_path):
    (tmp_path / "ROADMAP.md").write_text("# Roadmap\n\nNo version here.\n", encoding="utf-8")
    assert guard._roadmap_version(tmp_path) is None


def test_the_changelog_resolver_ignores_the_unreleased_heading(guard, tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [0.9.0] - 2026-08-17\n\n## [0.8.0] - 2026-08-09\n",
        encoding="utf-8",
    )
    assert guard._changelog_latest_version(tmp_path) == "0.9.0"


# ============================================================
# The hook fires on every surface it guards
# ============================================================

def test_the_hook_pattern_covers_every_guarded_surface():
    """A surface missing from `files:` is a surface the gate never runs for.

    The hook passes no filenames and re-reads all five files itself, so the
    pattern decides only WHEN the guard runs. ROADMAP.md drifted for six
    releases while the pattern named three files, and uv.lock drifted across
    the v0.14.0 release while the pattern named four.
    """
    assert len(SURFACES) >= 5, (
        f"the AST walk found only {SURFACES} in {SCRIPT.name}; a guard that "
        f"reads nothing is covered by a pattern that names nothing"
    )
    pattern = re.compile(_hook()["files"])
    uncovered = [s for s in SURFACES if not pattern.search(s)]
    assert not uncovered, f"these surfaces do not trigger the version-sync gate: {uncovered}"


def test_the_surface_list_is_read_off_the_guard_not_typed_here():
    """Anchor for the derivation above.

    An extractor that returned nothing would satisfy `uncovered == []` for any
    pattern at all, and the floor alone does not prove the names came from the
    script rather than from a literal in this file.
    """
    # uv.lock joined 2026-09-03. It is pinned here rather than appended
    # loosely, because the point of this assertion is that a NEW resolver
    # cannot land unnoticed: the set is exact on purpose, and widening it is
    # the deliberate act that accompanies widening the guard.
    assert set(SURFACES) == {"README.md", "CHANGELOG.md", "ROADMAP.md",
                             "pyproject.toml", "uv.lock"}, SURFACES
    assert _guarded_surfaces.__doc__  # the reasoning travels with the helper
    src = SCRIPT.read_text(encoding="utf-8")
    for name in SURFACES:
        assert f'"{name}"' in src, f"{name} is not a literal in {SCRIPT.name}"
