"""Six ways `scripts/harness-audit.py` vouched for, or crashed over, what it
had not measured.

Covers the k3 audit shard `scripts-09-p2`, findings 2, 4, 5, 7, 8 and 9.

*A disabled plugin reported live.* The unreadable-`hooks.json` branch hardcoded
`"loaded": True` and skipped the `_is_loaded(path, active, disabled)` call the
parseable branch makes. A plugin set `false` in `enabledPlugins` whose cached
`hooks.json` was corrupt printed under "running in this session and not owned by
this repository". The settings file saying false is still perfectly readable, so
the proof `.claude/rules/scope-claims.md` obligation 1 asks for was in hand and
never consulted.

*A settings file that was not an object.* `json.loads` returns any JSON type,
and `data.get("hooks")` on a top-level list raised AttributeError, which the
handler does not catch. The whole audit died with a traceback and produced no
report, in a tool whose own rule is that an unreadable record degrades to
"treat everything as live", never to silence.

*An allowance with no path boundary.* `_is_allowed_repo_path` used
`rel.startswith(p)`, so `.claude/rules/security.md.draft.md` and a DIRECTORY
named `.claude/rules/security.md` holding a payload were both dropped from the
injection scan and counted under the reviewed rule file's allowance. Both are
reachable by the `.claude/rules/**/*.md` glob.

*A dedup that could never fire.* One unreadable plugin file is recorded twice,
by two producers using two labels for it (`a/b.py` and `plugins/a/b.py`), so the
printed "N file(s) could not be read" counted one file as two.

*A baseline of the wrong shape.* `read_manifest` checked only the top level, so
`{"entries": ["a.py"]}` parsed, passed, and then killed `compare` with
`TypeError: list indices must be integers`.

*An acceptance that named none of this.* `--update-manifest` had
`hash_unreadable` and `symlinks` in hand and printed neither, so the one moment
a human asserts "I reviewed this" minted a silently partial baseline.

Every path here is under `tmp_path`. Nothing reads the operator's real plugin
cache, real settings, or real manifest.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scripts" / "harness-audit.py"


@pytest.fixture(scope="module")
def ha():
    spec = importlib.util.spec_from_file_location(
        "harness_audit_disabled_mod", str(SOURCE))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["harness_audit_disabled_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


HOOKS_JSON = json.dumps(
    {"hooks": {"PreToolUse": [{"hooks": [{"command": "acme-guard.sh"}]}]}})


# ============================================================
# A disabled plugin whose hooks.json cannot be parsed
# ============================================================

def _disabled_plugin_tree(tmp_path: Path, hooks_body: str):
    """A cache holding one plugin, switched OFF in the repo's settings."""
    install = tmp_path / "cache" / "acme-plugin" / "1.0"
    install.mkdir(parents=True)
    (install / "hooks.json").write_text(hooks_body, encoding="utf-8")

    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"acme-plugin": False}}), encoding="utf-8")

    installed = tmp_path / "installed_plugins.json"
    installed.write_text(json.dumps(
        {"plugins": {"acme-plugin": [{"installPath": str(install)}]}}),
        encoding="utf-8")
    return tmp_path / "cache", repo, installed


def _hooks(ha, cache, repo, installed, settings):
    return ha.third_party_hooks(
        cache, settings,
        ha.active_install_paths(installed),
        ha.disabled_install_paths(installed, repo))


def test_a_disabled_plugin_with_a_corrupt_hooks_file_is_not_called_live(
        ha, tmp_path, monkeypatch):
    """The measured defect."""
    cache, repo, installed = _disabled_plugin_tree(tmp_path, "{")
    monkeypatch.setattr(ha, "user_settings_path", lambda: tmp_path / "absent.json")

    entries = _hooks(ha, cache, repo, installed, tmp_path / "absent.json")

    assert len(entries) == 1, entries
    assert "unreadable" in entries[0]["command"]
    assert entries[0]["loaded"] is False, (
        "an explicit false in enabledPlugins IS the proof, and it was readable")


def test_an_unknown_plugin_with_a_corrupt_hooks_file_is_still_called_live(
        ha, tmp_path, monkeypatch):
    """The negative direction, and the one this must never reverse.

    Nothing disables this plugin and the activation record does not mention it,
    so absence of proof still reads as live. Without this case, `loaded: False`
    on the unreadable branch would satisfy the test above while hiding a hook
    that executes.
    """
    cache = tmp_path / "cache" / "acme-plugin" / "1.0"
    cache.mkdir(parents=True)
    (cache / "hooks.json").write_text("{", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    installed = tmp_path / "installed_plugins.json"
    installed.write_text(json.dumps({"plugins": {}}), encoding="utf-8")
    monkeypatch.setattr(ha, "user_settings_path", lambda: tmp_path / "absent.json")

    entries = _hooks(ha, tmp_path / "cache", repo, installed,
                     tmp_path / "absent.json")

    assert len(entries) == 1, entries
    assert entries[0]["loaded"] is True


def test_a_readable_hooks_file_from_a_disabled_plugin_stays_dormant(
        ha, tmp_path, monkeypatch):
    """The behaviour the unreadable branch now matches: same tree, valid JSON."""
    cache, repo, installed = _disabled_plugin_tree(tmp_path, HOOKS_JSON)
    monkeypatch.setattr(ha, "user_settings_path", lambda: tmp_path / "absent.json")

    entries = _hooks(ha, cache, repo, installed, tmp_path / "absent.json")

    assert [e["loaded"] for e in entries] == [False]


# ============================================================
# A settings file that is valid JSON of the wrong shape
# ============================================================

@pytest.mark.parametrize("body", ["[]", '"x"', "17", "null"])
def test_a_non_object_settings_file_does_not_kill_the_audit(ha, tmp_path, body):
    settings = tmp_path / "settings.json"
    settings.write_text(body, encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()

    # No AttributeError, and no hook invented out of a shape that has none.
    assert ha.third_party_hooks(cache, settings) == []


def test_a_well_shaped_settings_file_still_yields_its_hooks(ha, tmp_path):
    """The negative direction: the isinstance guard must not eat real hooks."""
    settings = tmp_path / "settings.json"
    settings.write_text(HOOKS_JSON, encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()

    entries = ha.third_party_hooks(cache, settings)

    assert [(e["event"], e["command"], e["loaded"]) for e in entries] == [
        ("PreToolUse", "acme-guard.sh", True)]


# ============================================================
# The allow-list boundary
# ============================================================

def test_the_allowance_covers_the_named_file_and_nothing_beside_it(ha):
    allowed = ha.ALLOWED_REPO_PREFIXES
    assert allowed, "empty allow-list proves nothing"
    entry = allowed[0]
    assert not entry.endswith("/"), "this test is about the file-entry case"

    assert ha._is_allowed_repo_path(entry) is True
    # Both shapes are reachable by `.claude/rules/**/*.md`.
    assert ha._is_allowed_repo_path(entry + ".draft.md") is False
    assert ha._is_allowed_repo_path(entry + "/payload.md") is False
    assert ha._is_allowed_repo_path(entry + "x") is False


def test_every_allow_list_entry_still_matches_itself(ha):
    """Derived from the real list, both directions, so neither a widened nor a
    narrowed matcher passes by luck."""
    assert len(ha.ALLOWED_REPO_PREFIXES) >= 3
    for entry in ha.ALLOWED_REPO_PREFIXES:
        assert ha._is_allowed_repo_path(entry) is True, entry


def test_an_allow_listed_file_is_reported_as_skipped_not_scanned(ha, tmp_path):
    """Through the real scan, so the boundary change is measured at the seam."""
    repo = tmp_path / "repo"
    rules = repo / ".claude" / "rules"
    rules.mkdir(parents=True)
    named = ha.ALLOWED_REPO_PREFIXES[0]
    (repo / named).write_text("# allowed\n", encoding="utf-8")
    (repo / (named + ".draft.md")).write_text("# not allowed\n", encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()

    _findings, scanned, _unreadable, allowed_skipped = ha.scan_loaded_content(
        repo, cache)

    assert allowed_skipped == [named]
    assert named + ".draft.md" in scanned


# ============================================================
# The unreadable-file dedup
# ============================================================

def test_one_unreadable_plugin_file_is_counted_once(tmp_path):
    """Through the CLI, because the dedup lives in `main`.

    Root-owned checkouts read a chmod-000 file anyway, so the test says so
    rather than passing vacuously.
    """
    if os.geteuid() == 0:
        pytest.skip("root bypasses the permission bit this case depends on")

    cache = tmp_path / "cache" / "acme" / "1.0" / "hooks"
    cache.mkdir(parents=True)
    blocked = cache / "run.py"
    blocked.write_text("print('x')\n", encoding="utf-8")
    blocked.chmod(0)
    try:
        assert not os.access(blocked, os.R_OK), "the file is still readable"
        env = dict(os.environ,
                   HEADING_OS_PLUGIN_ROOT=str(tmp_path / "cache"),
                   HEADING_OS_USER_SETTINGS=str(tmp_path / "absent.json"))
        out = subprocess.run(
            [sys.executable, str(SOURCE), "--json",
             "--manifest", str(tmp_path / "manifest.json")],
            capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120)
    finally:
        blocked.chmod(stat.S_IRUSR | stat.S_IWUSR)

    result = json.loads(out.stdout)
    paths = [e["path"] for e in result["unreadable"]]
    assert len(paths) == 1, paths


# ============================================================
# A baseline of the wrong shape
# ============================================================

@pytest.mark.parametrize("entries", [["a.py"], "a.py", 3])
def test_a_manifest_whose_entries_are_not_a_map_is_no_baseline(ha, tmp_path,
                                                               entries):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": 1, "entries": entries}),
                    encoding="utf-8")

    # None routes to "No reviewed baseline", which is a finding and an exit 1.
    assert ha.read_manifest(path) is None


def test_a_well_shaped_manifest_is_still_read(ha, tmp_path):
    """The negative direction: the shape guard must not reject real baselines."""
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": 1, "entries": {"a.py": "deadbeef"}}),
                    encoding="utf-8")

    baseline = ha.read_manifest(path)

    assert baseline is not None
    assert ha.compare({"a.py": "deadbeef"}, baseline) == {
        "added": [], "changed": [], "removed": []}


def test_a_list_shaped_baseline_no_longer_reaches_compare(ha, tmp_path):
    """The crash the shape guard exists to prevent, at the seam it happened."""
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": 1, "entries": ["a.py"]}),
                    encoding="utf-8")
    baseline = ha.read_manifest(path)

    # `main` only calls compare when read_manifest returned something.
    assert baseline is None
    with pytest.raises(TypeError):
        ha.compare({"a.py": "h"}, {"entries": ["a.py"]})


# ============================================================
# What --update-manifest accepts, and what it says about it
# ============================================================

def test_accepting_a_surface_names_what_it_could_not_vouch_for(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root bypasses the permission bit this case depends on")

    plugin = tmp_path / "cache" / "acme" / "1.0"
    plugin.mkdir(parents=True)
    (plugin / "readme.md").write_text("fine\n", encoding="utf-8")
    blocked = plugin / "hook.py"
    blocked.write_text("print('x')\n", encoding="utf-8")
    blocked.chmod(0)
    (plugin / "link.md").symlink_to(tmp_path / "elsewhere.md")

    try:
        assert not os.access(blocked, os.R_OK)
        env = dict(os.environ,
                   HEADING_OS_PLUGIN_ROOT=str(tmp_path / "cache"),
                   HEADING_OS_USER_SETTINGS=str(tmp_path / "absent.json"))
        out = subprocess.run(
            [sys.executable, str(SOURCE), "--update-manifest",
             "--manifest", str(tmp_path / "manifest.json")],
            capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120)
    finally:
        blocked.chmod(stat.S_IRUSR | stat.S_IWUSR)

    body = out.stdout + out.stderr
    assert out.returncode == 0, body
    assert "Accepted 1 installed file(s) as reviewed" in body
    assert "hook.py" in body, "the unreadable file was accepted in silence"
    assert "could not be read" in body
    assert "link.md" in body, "the symlink was accepted in silence"
    assert "symlink" in body


def test_a_clean_surface_accepts_without_a_caveat(tmp_path):
    """The negative direction: the caveat must fire on evidence, not always."""
    plugin = tmp_path / "cache" / "acme" / "1.0"
    plugin.mkdir(parents=True)
    (plugin / "readme.md").write_text("fine\n", encoding="utf-8")

    env = dict(os.environ,
               HEADING_OS_PLUGIN_ROOT=str(tmp_path / "cache"),
               HEADING_OS_USER_SETTINGS=str(tmp_path / "absent.json"))
    out = subprocess.run(
        [sys.executable, str(SOURCE), "--update-manifest",
         "--manifest", str(tmp_path / "manifest.json")],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120)

    body = out.stdout + out.stderr
    assert out.returncode == 0, body
    assert "Accepted 1 installed file(s) as reviewed" in body
    assert "could not be read" not in body
    assert "symlink" not in body
