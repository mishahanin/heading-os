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
import yaml

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "dev" / "build-plugins.py"

sys.path.insert(0, str(ROOT))

from tests.repo_files import read_sources  # noqa: E402


def _built_sources(paths, what):
    """`(path, text)` for every built file, or a failure naming the one missing.

    COMPLETENESS, not a scan: both callers assert something about EVERY file the
    generator wrote, and a file dropped because it vanished between the rglob
    and the read would be a file the guard certifies without reading. The walk
    and the read are still two moments -- these bundles are built into a tmp dir
    on a checkout several agents share -- so the race is read through
    `read_sources`, retried once, and then FAILS naming the file.
    """
    lost: list[Path] = []
    out = list(read_sources(paths, lost))
    if lost:
        still_gone: list[Path] = []
        out += list(read_sources(lost, still_gone))
        if still_gone:
            raise AssertionError(
                f"{what} disappeared between the walk and the read and is still "
                "gone on retry; it cannot be certified unread: "
                + ", ".join(str(p) for p in still_gone))
    return out


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


@pytest.fixture(scope="module")
def built_ops(tmp_path_factory):
    out = tmp_path_factory.mktemp("mkt_ops")
    mod = _load_builder()
    rc = mod.main(["--bundle", "heading-ops", "--out", str(out)])
    assert rc == 0
    return out / "plugins" / "heading-ops", out


def test_curated_bundle_composition(built_ops):
    """A curated multi-skill bundle builds green with its skills and enumerated scripts."""
    bundle, _ = built_ops
    skills = {p.name for p in (bundle / "skills").iterdir() if p.is_dir()}
    assert {"create-plan", "deep-think", "editorial-review"} <= skills
    # The enumerated scripts the bundled skills reference are present (the
    # completeness gate would have failed the build otherwise).
    for sc in ("elicit.py", "humanization-check.py", "sanitize-text.py", "resolve_customization.py"):
        assert (bundle / "scripts" / sc).is_file(), f"missing enumerated script: {sc}"
    # Curated bundles ship source only, same as heading-core.
    assert not list(bundle.rglob("__pycache__"))
    assert not list(bundle.rglob("*.pyc"))


def test_all_builds_curated_marketplace(tmp_path):
    """--all builds every curated (non-empty) bundle; the fully-reserved crm is skipped."""
    mod = _load_builder()
    rc = mod.main(["--all", "--out", str(tmp_path)])
    assert rc == 0
    mj = json.loads((tmp_path / ".claude-plugin" / "marketplace.json").read_text())
    names = {p["name"] for p in mj["plugins"]}
    assert {
        "heading-core",
        "heading-intel",
        "heading-comms",
        "heading-content",
        "heading-ops",
    } <= names
    assert "heading-crm" not in names  # empty skills -> skipped by --all, never published


def test_all_builds_a_bundle_whose_only_content_is_commands(tmp_path, monkeypatch):
    """`--all` filters on `skills or hooks or commands`, and only the first two
    had a case.

    `commands` became a first-class field on 2026-08-21 and the filter was
    updated for it with a comment saying why, and with nothing standing on it:
    MEASURED 2026-09-01, dropping `s.get("commands")` back out of the filter left
    all 92 tests across the five files that build this generator green, because
    no bundle in the shipped manifest is commands-only. So the fix would have
    regressed in silence, and the symptom is the quiet one the comment
    describes - `--all` builds nothing for that bundle and says nothing about it.

    The manifest is supplied rather than edited, so the test measures the filter
    and not the current contents of `config/plugin-bundles.yaml`.
    """
    mod = _load_builder()
    manifest = {
        "probe-commands-only": {
            "description": "commands only, no skills and no hooks",
            "skills": [],
            "hooks": [],
            "hook_events": {},
            "commands": ["unattended.md"],
            "scripts": ["checkpoint-paths.py"],
        },
    }
    monkeypatch.setattr(mod, "load_manifest", lambda root: manifest)

    assert mod.main(["--all", "--out", str(tmp_path)]) == 0

    mj = json.loads((tmp_path / ".claude-plugin" / "marketplace.json").read_text())
    assert [p["name"] for p in mj["plugins"]] == ["probe-commands-only"], (
        "a commands-only bundle was skipped by --all and never published"
    )
    assert (tmp_path / "plugins" / "probe-commands-only" / "commands"
            / "unattended.md").is_file()


def test_all_still_skips_a_bundle_that_declares_nothing(tmp_path, monkeypatch):
    """The negative case. A filter that stopped filtering would satisfy the test
    above while publishing every empty placeholder in the manifest."""
    mod = _load_builder()
    manifest = {
        "probe-empty": {"description": "placeholder", "skills": [], "hooks": [],
                        "hook_events": {}, "commands": [], "scripts": []},
        "probe-commands-only": {
            "description": "commands only",
            "skills": [], "hooks": [], "hook_events": {},
            "commands": ["unattended.md"], "scripts": ["checkpoint-paths.py"],
        },
    }
    monkeypatch.setattr(mod, "load_manifest", lambda root: manifest)

    assert mod.main(["--all", "--out", str(tmp_path)]) == 0

    mj = json.loads((tmp_path / ".claude-plugin" / "marketplace.json").read_text())
    assert [p["name"] for p in mj["plugins"]] == ["probe-commands-only"]
    assert not (tmp_path / "plugins" / "probe-empty").exists()


def test_hooks_json_registers_guards(built):
    bundle, _ = built
    hj = json.loads((bundle / "hooks" / "hooks.json").read_text())
    post = hj["hooks"]["PostToolUse"][0]["hooks"]
    cmds = " ".join(h["command"] for h in post)
    assert "${CLAUDE_PLUGIN_ROOT}/hooks/prompt-guard.py" in cmds
    assert "${CLAUDE_PLUGIN_ROOT}/hooks/post-write-sanitize.py" in cmds
    # Across ALL SessionStart blocks, not block 0. The generated env hook is
    # APPENDED, so the moment a bundle declares a SessionStart of its own (the
    # checkpoint inject hook did, 2026-08-16) the env hook stops being first and
    # a positional assertion fails without anything being wrong.
    session = [h for block in hj["hooks"]["SessionStart"] for h in block["hooks"]]
    assert any("session-env.py" in h["command"] for h in session)


def test_hooks_json_registers_the_checkpoint_system(built):
    """The four hooks a bundle CAN wire. The status line is not among them:
    Claude Code exposes context usage only to a statusLine and a plugin manifest
    has no statusLine key, so it ships as a script the consumer wires once."""
    bundle, _ = built
    hj = json.loads((bundle / "hooks" / "hooks.json").read_text())
    everything = json.dumps(hj)
    assert "checkpoint-inject.py" in everything
    assert "checkpoint-save.py" in everything
    assert "checkpoint-offer.py" in everything
    assert (bundle / "hooks" / "checkpoint-statusline.py").is_file(), (
        "the status line must still SHIP even though it cannot be auto-wired"
    )
    assert "checkpoint-statusline.py" not in everything, (
        "a statusLine cannot be registered from a plugin; wiring it here is a lie"
    )


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


# --------------------------------------------------------------------------
# Slash commands (2026-08-21). A bundle shipped `/checkpoint` while both of the
# switches that skill documents - `/unattended` and `/compact-at` - lived in
# `.claude/commands/`, which the builder had no field for and never copied. A
# consumer installing heading-core got the skill and neither command, so the
# skill's own instructions named a slash command the plugin does not carry.
# --------------------------------------------------------------------------


def test_declared_commands_are_bundled(built):
    bundle, _ = built
    for name in ("unattended.md", "compact-at.md"):
        assert (bundle / "commands" / name).is_file(), f"{name} missing from the bundle"


def test_a_missing_command_fails_the_build(tmp_path):
    mod = _load_builder()
    spec = {"description": "x", "skills": [], "hooks": [], "hook_events": {},
            "commands": ["no-such-command.md"], "scripts": []}
    with pytest.raises(SystemExit):
        mod.build_bundle("probe", spec, tmp_path, ROOT)


def test_a_missing_source_is_refused_before_the_bundle_is_touched(tmp_path):
    """The comment above `completeness_gate` promises to fail "before writing
    anything", and the per-component checks ran AFTER `shutil.rmtree(bundle)`
    and after `plugin.json` was written. So a typo in the manifest destroyed the
    previous bundle and left a half-written one.

    Low severity - `dist/marketplace/` is untracked and the next successful
    build regenerates it - but the promise was not kept.
    """
    mod = _load_builder()
    bundle = tmp_path / "plugins" / "probe"
    (bundle / "skills" / "old-skill").mkdir(parents=True)
    (bundle / "skills" / "old-skill" / "SKILL.md").write_text("previous build\n",
                                                              encoding="utf-8")
    spec = {"description": "x", "skills": [], "hooks": [], "hook_events": {},
            "commands": ["no-such-command.md"], "scripts": []}

    with pytest.raises(SystemExit):
        mod.build_bundle("probe", spec, tmp_path, ROOT)

    assert (bundle / "skills" / "old-skill" / "SKILL.md").read_text(
        encoding="utf-8") == "previous build\n", "the previous bundle was destroyed"
    assert not (bundle / ".claude-plugin" / "plugin.json").exists()


def test_every_absent_source_is_named_at_once(tmp_path, capsys):
    """Raising on the first one makes a manifest with three typos take three
    runs to fix."""
    mod = _load_builder()
    spec = {"description": "x", "skills": ["no-such-skill"], "hooks": ["no-such-hook.py"],
            "hook_events": {}, "commands": ["no-such-command.md"],
            "scripts": ["no-such-script.py"]}

    absent = mod.manifest_sources("probe", spec, ROOT)

    assert len(absent) == 4
    assert any("skill not found" in m for m in absent)
    assert any("command not found" in m for m in absent)
    assert any("hook not found" in m for m in absent)
    assert any("script not found" in m for m in absent)


def test_a_manifest_whose_sources_all_exist_reports_nothing():
    """The negative case. A pre-pass that always finds something blocks every
    build."""
    mod = _load_builder()
    spec = {"description": "x", "skills": [], "hooks": [], "hook_events": {},
            "commands": ["unattended.md"], "scripts": []}

    assert mod.manifest_sources("probe", spec, ROOT) == []


def test_command_script_paths_are_rewritten(built):
    """Same rewrite the SKILL.md bodies get: a bare `python scripts/...` resolves
    against the consumer's cwd in a plugin cache, not against the bundle."""
    bundle, _ = built
    text = (bundle / "commands" / "compact-at.md").read_text(encoding="utf-8")
    assert "${CLAUDE_PLUGIN_ROOT}" in text
    assert "python scripts/checkpoint-paths.py" not in text


def test_in_repo_command_unchanged():
    """The monorepo copy keeps the plain path. The rewrite happens on the way
    into the bundle and never in the source tree."""
    text = (ROOT / ".claude" / "commands" / "compact-at.md").read_text(encoding="utf-8")
    assert "${CLAUDE_PLUGIN_ROOT}" not in text
    assert "python scripts/checkpoint-paths.py" in text


def test_every_built_frontmatter_still_parses_as_yaml(built):
    """The rewrite must not break the file it rewrites.

    `allowed-tools` is a double-quoted scalar carrying `Bash(python scripts/...)`
    patterns. The single-form substitution closed that scalar early, so every
    built SKILL.md shipped frontmatter that is not YAML - measured 2026-08-21,
    present since the generator shipped. This is the guard: parse what was
    written, rather than trust the substitution.
    """
    bundle, _ = built
    targets = list((bundle / "skills").rglob("SKILL.md")) + \
        list((bundle / "commands").glob("*.md"))
    assert targets, "nothing to check - the bundle shipped no skills or commands"
    for path, text in _built_sources(targets, "a built skill/command file"):
        assert text.startswith("---"), f"{path.name} lost its frontmatter"
        front = text.split("---", 2)[1]
        try:
            parsed = yaml.safe_load(front)
        except yaml.YAMLError as exc:  # noqa: PERF203 - one message per file is the point
            raise AssertionError(f"{path.name} frontmatter is not YAML: {exc}") from exc
        assert isinstance(parsed, dict), f"{path.name} frontmatter is not a mapping"


def test_every_bundle_parses_not_only_heading_core(tmp_path):
    """The same guard, across `--all`, because one bundle is not the corpus.

    The check above rides the `built` fixture, which builds `heading-core` alone.
    Four other bundles ship eleven more skills through the same rewrite, and a
    frontmatter shape that only they carry would pass every test in this file
    while shipping broken. Measured 2026-08-21: 5 bundles, 14 skill and command
    files, 10 of them rewritten.
    """
    mod = _load_builder()
    assert mod.main(["--all", "--out", str(tmp_path)]) == 0
    plugins = tmp_path / "plugins"
    targets = sorted(plugins.rglob("SKILL.md")) + sorted(plugins.rglob("commands/*.md"))
    assert len(targets) >= 10, f"only {len(targets)} files reached the guard"
    rewritten = 0
    # `rewritten` is a COUNT the assertion at the end depends on, so a silently
    # dropped file would understate it. `_built_sources` fails naming the file.
    for path, text in _built_sources(targets, "a built bundle file"):
        if "CLAUDE_PLUGIN_ROOT" in text:
            rewritten += 1
        try:
            parsed = yaml.safe_load(text.split("---", 2)[1])
        except yaml.YAMLError as exc:
            rel = path.relative_to(plugins)
            raise AssertionError(f"{rel} frontmatter is not YAML: {exc}") from exc
        assert isinstance(parsed, dict), f"{path.relative_to(plugins)} is not a mapping"
    assert rewritten, "no file was rewritten - the guard proved nothing about the rewrite"


def test_the_quoted_scalar_keeps_its_quotes_after_the_rewrite(built):
    """Escaped, not dropped. The quotes protect a cache path containing a space,
    so the fix must keep them in the PARSED value, not only in the file."""
    bundle, _ = built
    text = (bundle / "skills" / "checkpoint" / "SKILL.md").read_text(encoding="utf-8")
    parsed = yaml.safe_load(text.split("---", 2)[1])
    assert '"${CLAUDE_PLUGIN_ROOT}"/scripts/checkpoint-paths.py' in parsed["allowed-tools"]
