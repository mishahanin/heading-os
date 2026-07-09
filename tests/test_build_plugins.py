"""Tests for the F-10.1 plugin generator (scripts/dev/build-plugins.py).

Validates the built structure, the ${CLAUDE_PLUGIN_ROOT} rewrite, the generated
hooks.json, the completeness gate, and plugin-cache root resolution, all in pure
Python (no `claude` binary needed).
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "dev" / "build-plugins.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_plugins_mod", BUILDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("mkt")
    mod = _load_builder()
    rc = mod.main(["--bundle", "heading-core", "--out", str(out)])
    assert rc == 0
    return out / "plugins" / "heading-core", out


def test_plugin_json(built):
    bundle, _ = built
    pj = json.loads((bundle / ".claude-plugin" / "plugin.json").read_text())
    assert pj["name"] == "heading-core"
    assert "version" not in pj  # Decision 7: omit version (auto-update per commit)
    assert pj["author"]["email"] == "misha.hanin@odinix.com"


def test_marketplace_json(built):
    _, out = built
    mj = json.loads((out / ".claude-plugin" / "marketplace.json").read_text())
    assert mj["name"] == "heading-os-marketplace"
    assert mj["owner"]["name"] == "Misha Hanin"
    assert mj["plugins"][0]["name"] == "heading-core"
    assert mj["plugins"][0]["source"] == "./plugins/heading-core"


def test_no_bytecode_cruft(built):
    """A built bundle ships source only: no __pycache__ dirs, no compiled bytecode."""
    bundle, _ = built
    pycache = list(bundle.rglob("__pycache__"))
    compiled = list(bundle.rglob("*.pyc")) + list(bundle.rglob("*.pyo"))
    assert not pycache, f"__pycache__ shipped in bundle: {pycache[:3]}"
    assert not compiled, f"compiled bytecode shipped in bundle: {compiled[:3]}"


def test_hooks_json_registers_guards(built):
    bundle, _ = built
    hj = json.loads((bundle / "hooks" / "hooks.json").read_text())
    post = hj["hooks"]["PostToolUse"][0]["hooks"]
    cmds = " ".join(h["command"] for h in post)
    assert "${CLAUDE_PLUGIN_ROOT}/hooks/prompt-guard.py" in cmds
    assert "${CLAUDE_PLUGIN_ROOT}/hooks/post-write-sanitize.py" in cmds
    session = hj["hooks"]["SessionStart"][0]["hooks"]
    assert any("session-env.py" in h["command"] for h in session)


def test_skill_script_paths_rewritten(built):
    bundle, _ = built
    skill_md = (bundle / "skills" / "prime" / "SKILL.md").read_text()
    assert "${CLAUDE_PLUGIN_ROOT}" in skill_md
    # No bare `python scripts/` invocation should survive the rewrite.
    import re

    assert not re.search(r"\b(python3?|bash)\s+scripts/", skill_md)


def test_in_repo_skill_unchanged():
    """A build must not mutate the monorepo source SKILL.md."""
    src = ROOT / ".claude" / "skills" / "prime" / "SKILL.md"
    before = src.read_bytes()
    mod = _load_builder()
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        mod.main(["--bundle", "heading-core", "--out", td])
    assert src.read_bytes() == before


def test_completeness_gate_flags_unbundled_reference():
    mod = _load_builder()
    # A skill that references scripts it does not bundle must be flagged.
    missing = mod.completeness_gate({"skills": ["prime"], "hooks": [], "scripts": []}, ROOT)
    assert missing, "gate should flag prime's unbundled script references"
    assert any("prime-health-parallel.py" in m for m in missing)


def test_cache_simulation_root_resolution(built, tmp_path):
    """A bundled script resolves its root from a cache-like tree, not the repo."""
    bundle, _ = built
    cache = tmp_path / "cache" / "heading-core"
    shutil.copytree(bundle, cache)
    paths_py = cache / "scripts" / "utils" / "paths.py"

    # (a) via WORKSPACE_ROOT override (the primary mechanism).
    env = dict(os.environ, WORKSPACE_ROOT=str(cache))
    env.pop("HEADING_OS_DATA", None)
    out = subprocess.run(
        [sys.executable, str(paths_py)], capture_output=True, text=True, env=env
    ).stdout.strip()
    assert out == str(cache.resolve())

    # (b) with no override, the structural fallback still lands on the bundle root.
    env2 = {k: v for k, v in os.environ.items() if k != "WORKSPACE_ROOT"}
    out2 = subprocess.run(
        [sys.executable, str(paths_py)], capture_output=True, text=True, env=env2
    ).stdout.strip()
    assert out2 == str(cache.resolve())
