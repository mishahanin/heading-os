"""A provisioned exec must get the whole guard set, not one seventh of it.

Until 2026-08-09 `provision-exec.py` wrote a single PreToolUse hook into every
new exec's settings: `protect-corporate.py`, on `Write|Edit`. That is one of the
seven checks `_dispatch.py` runs. The secret-detection guard, the personal-thread
guard, the docs guard, the cwd anchor, the rate limit and the tool budget never
ran in a provisioned workspace at all, and nothing said so.

It also kept recreating the four backward-compat shims that
`.claude/rules/documentation.md` records as waiting to be deleted: the file said
"remove once the fleet re-syncs", while the provisioner wrote a fresh reference
into every new workspace. The shims can only be deleted once no provisioner
emits them, which is what this test holds still.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SHIMS = (
    "protect-corporate.py",
    "protect-docs.py",
    "prevent-secrets.py",
    "protect-personal-threads.py",
)


def _load():
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(
        "provision_exec_mod", ROOT / "scripts" / "provision-exec.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["provision_exec_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    mod = _load()

    class Args:
        platform = "linux"

    workspace = tmp_path_factory.mktemp("exec-workspace")
    mod.create_settings_local_json({"completed_steps": []}, Args(), workspace)
    return json.loads(
        (workspace / ".claude" / "settings.local.json").read_text(encoding="utf-8")
    )


def test_no_backward_compat_shim_is_written_into_a_new_workspace(settings):
    blob = json.dumps(settings)
    present = [s for s in SHIMS if s in blob]
    assert present == [], (
        f"the provisioner still emits {present}; the shims can never be deleted "
        f"while a new workspace recreates a reference to them"
    )


def test_pretooluse_points_at_the_dispatcher_on_all_three_matchers(settings):
    pre = settings["hooks"]["PreToolUse"]
    matchers = [entry["matcher"] for entry in pre]
    assert matchers == ["Write|Edit|MultiEdit|NotebookEdit", "Bash", "Read"], matchers
    for entry in pre:
        assert "_dispatch.py" in entry["hooks"][0]["command"]


def test_the_hook_command_resolves_itself_from_any_directory(settings):
    """`python3 .claude/hooks/x.py` only works from the workspace root.

    Stated positively, over every command, with a floor. The old shape was
    `if <defect condition>: assert <two grandfathered prefixes>`, and it had two
    problems at once. The condition matched NOTHING - measured 2026-08-27, zero
    of the provisioned commands entered the branch - so the test evaluated no
    assertion at all. And had one entered, the assertion would have PASSED it,
    because `session-start.py` and `post-write-sanitize.py` are exactly the
    cwd-dependent form the test's own name forbids. A guard that whitelists the
    defect it names is worse than no guard.
    """
    commands = [hook["command"]
                for group in settings["hooks"].values()
                for entry in group
                for hook in entry["hooks"]]
    assert len(commands) >= 5, (
        f"only {len(commands)} hook command(s) provisioned; the guard measured "
        "almost nothing"
    )
    unresolved = [c for c in commands
                  if ".claude/hooks/" in c and "Path.cwd()" not in c]
    assert unresolved == [], (
        "these hook commands name a relative path and only run from the "
        f"workspace root: {unresolved}"
    )


def test_a_provisioned_workspace_starts_with_deny_rules(settings):
    deny = settings["permissions"].get("deny")
    assert deny, "no deny rules; the allow list alone leaves zero refusals"
    assert "Read(.env)" in deny
    assert any(r.startswith("Bash(git push --force") for r in deny)


def test_the_end_of_turn_check_is_wired(settings):
    stop = settings["hooks"].get("Stop")
    assert stop, "no Stop hook; a turn can end on a broken tree unnoticed"
    assert "turn-check.py" in json.dumps(stop)
