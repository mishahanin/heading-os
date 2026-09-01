"""Tests for the marketplace publisher's pure parts (scripts/dev/publish-marketplace.py).

The git/network side of publishing is not exercised here; these cover the
content the publisher writes into the marketplace repo: the README, the repo
meta, and the sync that replaces the generated tree.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = ROOT / "scripts" / "dev" / "publish-marketplace.py"


def _load():
    spec = importlib.util.spec_from_file_location("publish_marketplace_mod", PUBLISHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MKT = {
    "name": "heading-os-marketplace",
    "plugins": [{"name": "heading-core", "description": "Sovereignty and session core."}],
}


def test_readme_carries_install_commands():
    readme = _load()._readme(MKT)
    assert "/plugin marketplace add mishahanin/heading-os-marketplace" in readme
    assert "/plugin install heading-core@heading-os-marketplace" in readme
    assert "heading-core" in readme
    assert "generated distribution artifact" in readme


def test_sync_replaces_generated_tree(tmp_path):
    mod = _load()
    build_out = tmp_path / "build"
    (build_out / ".claude-plugin").mkdir(parents=True)
    (build_out / ".claude-plugin" / "marketplace.json").write_text("{}")
    (build_out / "plugins" / "heading-core").mkdir(parents=True)
    (build_out / "plugins" / "heading-core" / "plugin.json").write_text("{}")

    repo = tmp_path / "repo"
    # Stale content that must be wiped, plus repo-owned meta that must survive.
    (repo / "plugins" / "old-bundle").mkdir(parents=True)
    (repo / "plugins" / "old-bundle" / "stale.txt").write_text("old")
    (repo / "README.md").write_text("keep me")

    mod.sync_into_repo(build_out, repo)

    assert (repo / "plugins" / "heading-core" / "plugin.json").exists()
    assert not (repo / "plugins" / "old-bundle").exists()  # stale bundle gone
    assert (repo / "README.md").read_text() == "keep me"  # repo meta untouched by sync


def test_a_build_that_produced_no_bundle_is_refused_before_the_wipe(tmp_path):
    """The destructive half of the sync above, with the input nobody tested.

    `test_sync_replaces_generated_tree` proves the wipe works. This proves it
    does not work when there is nothing to put back. `build-plugins.py --all`
    picks its bundle list by asking each manifest entry for
    `skills`/`hooks`/`commands`; a renamed key yields an empty list, and every
    layer below it treats that as success.

    MEASURED 2026-09-01 before the refusal existed, on exactly this fixture: the
    repo went from one bundle to zero and `sync_into_repo` returned None. The
    public marketplace would then serve nothing, and `commit_and_push` would
    print "Pushed ... (verified in sync)" over the deletion.

    The assertion is the SIDE EFFECT, not the message: a refusal that still
    deleted the bundles would satisfy `pytest.raises` on its own.
    """
    mod = _load()
    build_out = tmp_path / "build"
    (build_out / ".claude-plugin").mkdir(parents=True)
    (build_out / ".claude-plugin" / "marketplace.json").write_text(
        '{"name": "heading-os-marketplace", "plugins": []}')
    (build_out / "plugins").mkdir(parents=True)      # built, and empty

    repo = tmp_path / "repo"
    (repo / "plugins" / "heading-core").mkdir(parents=True)
    (repo / "plugins" / "heading-core" / "plugin.json").write_text("{}")

    with pytest.raises(mod.NothingToPublish):
        mod.sync_into_repo(build_out, repo)
    assert (repo / "plugins" / "heading-core" / "plugin.json").exists(), (
        "the refusal fired after the rmtree: the live bundles are already gone")


def test_a_build_tree_with_no_plugins_directory_at_all_is_refused(tmp_path):
    """The near-miss shape. An `iterdir()` on an absent directory raises
    FileNotFoundError, which is a crash rather than a refusal, and a caller
    catching only NothingToPublish would let it out as a traceback."""
    mod = _load()
    build_out = tmp_path / "build"
    (build_out / ".claude-plugin").mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / "plugins").mkdir(parents=True)

    with pytest.raises(mod.NothingToPublish):
        mod.sync_into_repo(build_out, repo)


def test_write_repo_meta(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    repo.mkdir()
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "LICENSE").write_text("Apache License 2.0 (fixture)")

    mod.write_repo_meta(repo, engine, MKT)

    assert (repo / "README.md").exists()
    assert (repo / "LICENSE").read_text() == "Apache License 2.0 (fixture)"
    gitignore = (repo / ".gitignore").read_text()
    assert "__pycache__/" in gitignore and "*.pyc" in gitignore
