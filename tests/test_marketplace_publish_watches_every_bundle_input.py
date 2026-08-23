"""The publish workflow must fire for every kind of file a bundle can carry.

`.github/workflows/publish-marketplace.yml` is the ONLY path from this repo to
`heading-os-marketplace`. A push whose paths match nothing in its `push.paths`
filter does not start the workflow, and nothing anywhere reports that. The
marketplace then keeps serving old code indefinitely.

Until 2026-08-23 the filter watched `config/plugin-bundles.yaml`, two build
scripts, `.claude/skills/**` and `.claude/hooks/**`. But the manifest also
declares:

  - `scripts:` entries on four bundles, plus the standing rule in its own header
    that "scripts/utils/ is implied and always copied";
  - `commands:` entries, added 2026-08-21 precisely because a bundle shipped
    instructions naming commands the plugin did not carry.

So a behaviour change to `scripts/docparse.py`, anything under `scripts/utils/`,
or `.claude/commands/**` shipped nothing. The workflow header claimed it runs
"on any push to main that touches a bundle input", which was false for every
bundled script and every bundled command.

This test derives what must be watched FROM the manifest rather than restating
it, because a second hand-maintained list is how the first one drifted.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "publish-marketplace.yml"
MANIFEST = ROOT / "config" / "plugin-bundles.yaml"


@pytest.fixture(scope="module")
def watched() -> list[str]:
    # `on:` is parsed by PyYAML 1.1 rules as the boolean True, not the string.
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    trigger = data.get("on", data.get(True))
    assert trigger, f"no trigger block in {WORKFLOW}; keys were {sorted(map(str, data))}"
    return trigger["push"]["paths"]


@pytest.fixture(scope="module")
def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _covered(path: str, patterns: list[str]) -> bool:
    """Does any glob in the filter match this repo-relative path?"""
    from fnmatch import fnmatch
    for pattern in patterns:
        if fnmatch(path, pattern):
            return True
        # GitHub's `**` spans separators; fnmatch's `*` does not.
        if pattern.endswith("/**") and path.startswith(pattern[:-2]):
            return True
    return False


def _bundles(manifest: dict) -> list[dict]:
    for key in ("bundles", "plugins"):
        value = manifest.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return list(value.values())
    pytest.skip(f"cannot find the bundle list in {MANIFEST}")


# --- every declared file must be watched --------------------------------------

def test_every_script_a_bundle_declares_is_watched(watched, manifest):
    # The manifest lists bare filenames; the builder resolves them under scripts/.
    declared = [f"scripts/{s}" for b in _bundles(manifest)
                for s in (b.get("scripts") or [])]
    assert declared, "no bundle declares any script; has the manifest shape changed?"
    missed = sorted({s for s in declared if not _covered(s, watched)})
    assert not missed, (
        f"these bundled scripts do not trigger a publish: {missed}. Changing one "
        "ships nothing and says nothing."
    )


def test_the_declared_scripts_actually_exist(manifest):
    """A watched path that names nothing is the mirror defect: the filter looks
    complete while a bundle references a file the build cannot copy."""
    missing = sorted({s for b in _bundles(manifest) for s in (b.get("scripts") or [])
                      if not (ROOT / "scripts" / s).exists()})
    assert not missing, f"plugin-bundles.yaml declares scripts that do not exist: {missing}"


def test_the_always_copied_utils_tree_is_watched(watched):
    """The manifest header says scripts/utils/ is implied and always copied, so
    it is a bundle input even though no bundle lists it."""
    assert _covered("scripts/utils/workspace.py", watched), (
        "scripts/utils/ is copied into every bundle but does not trigger a "
        "publish, so a shared-module fix never reaches the marketplace."
    )


def test_every_command_a_bundle_declares_is_watched(watched, manifest):
    declared = [f".claude/commands/{c}" for b in _bundles(manifest)
                for c in (b.get("commands") or [])]
    if not declared:
        pytest.skip("no bundle declares a command")
    missed = sorted({c for c in declared if not _covered(c, watched)})
    assert not missed, f"these bundled commands do not trigger a publish: {missed}"


def test_the_declared_commands_actually_exist(manifest):
    missing = sorted({c for b in _bundles(manifest) for c in (b.get("commands") or [])
                      if not (ROOT / ".claude" / "commands" / c).exists()})
    assert not missing, (
        f"plugin-bundles.yaml declares commands that do not exist: {missing}. "
        "The commands: key was added because a bundle once shipped instructions "
        "naming a command it did not carry."
    )


def test_the_build_scripts_are_watched(watched):
    """A change to the builder itself changes every bundle's output."""
    for path in ("scripts/dev/build-plugins.py", "scripts/dev/publish-marketplace.py"):
        assert _covered(path, watched), f"{path} does not trigger a publish"


def test_the_manifest_and_the_workflow_are_watched(watched):
    assert _covered("config/plugin-bundles.yaml", watched)
    assert _covered(".github/workflows/publish-marketplace.yml", watched)


def test_skills_and_hooks_are_still_watched(watched):
    """Regression cover for the paths that were already correct."""
    assert _covered(".claude/skills/zk/SKILL.md", watched)
    assert _covered(".claude/hooks/checkpoint-save.py", watched)


# --- a manual escape hatch must remain ----------------------------------------

def test_the_workflow_can_still_be_run_by_hand(watched):
    """Whatever the filter misses, someone must be able to force a publish."""
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    trigger = data.get("on", data.get(True))
    assert "workflow_dispatch" in trigger, (
        "no manual trigger. With the path filter as the only entry point, a gap "
        "in it has no workaround."
    )
