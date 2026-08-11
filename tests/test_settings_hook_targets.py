#!/usr/bin/env python3
"""Every hook a tracked settings file names must exist in .claude/hooks/.

The property this holds is the one the four compatibility shims were really
waiting on. Their removal condition was written as "every exec has re-synced a
settings.local.json that points at _dispatch.py", which is remote machine state
nobody here can read, so the row sat open indefinitely. The checkable version of
the same claim is local: a workspace built from this repository gets its hooks by
copying one of the tracked per-OS templates (scripts/setup-platform.sh), so if no
template ever names a file that is absent, no such workspace can reference one.

The test therefore fails in both directions that matter: deleting a hook whose
template entry survives, and adding a template entry for a hook that was never
written.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = ROOT / ".claude" / "hooks"

SETTINGS_FILES = [
    ".claude/settings.json",
    ".claude/settings.local.linux.json",
    ".claude/settings.local.macos.json",
    ".claude/settings.local.windows.json",
]

# The hook filename inside a self-locating launcher, in either of the two forms
# the templates use: a literal in the path expression, or bound to `n` first.
HOOK_NAME_RE = re.compile(r"'([A-Za-z0-9_][A-Za-z0-9_.-]*\.py)'")


def _commands(settings: dict) -> list[str]:
    """Every command string in a settings file, hooks and status line alike."""
    found = []
    for entries in (settings.get("hooks") or {}).values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                if hook.get("command"):
                    found.append(hook["command"])
    status_line = settings.get("statusLine") or {}
    if status_line.get("command"):
        found.append(status_line["command"])
    return found


def _referenced_hooks(path: Path) -> set[str]:
    names = set()
    for command in _commands(json.loads(path.read_text(encoding="utf-8"))):
        if ".claude" not in command or "hooks" not in command:
            continue
        names.update(HOOK_NAME_RE.findall(command))
    return names


@pytest.mark.parametrize("rel", SETTINGS_FILES)
def test_every_referenced_hook_file_exists(rel):
    path = ROOT / rel
    if not path.is_file():
        pytest.skip(f"{rel} not present in this clone")
    missing = sorted(n for n in _referenced_hooks(path) if not (HOOKS_DIR / n).is_file())
    assert not missing, (
        f"{rel} wires hooks that do not exist in .claude/hooks/: {missing}. "
        "A settings entry naming an absent file leaves the event unguarded on "
        "every workspace that copied this template."
    )


@pytest.mark.parametrize("rel", SETTINGS_FILES)
def test_no_settings_file_references_a_retired_shim(rel):
    """The four delegators removed on 2026-08-11 must not come back by name.

    They were 28-line runpy shims for _dispatch.py. Naming one again would
    re-create the ambiguity about which file a fleet workspace actually runs.
    """
    path = ROOT / rel
    if not path.is_file():
        pytest.skip(f"{rel} not present in this clone")
    retired = {
        "prevent-secrets.py",
        "protect-corporate.py",
        "protect-docs.py",
        "protect-personal-threads.py",
        "protect-secure.py",
    }
    named = _referenced_hooks(path) & retired
    assert not named, f"{rel} references retired shim(s): {sorted(named)}"


def test_the_guard_can_see_a_missing_hook():
    """Pin the detector against the defect shape, so it cannot decay to a no-op.

    A parser that silently extracts nothing would pass both tests above on any
    input. This one proves the extraction works on a template-shaped command.
    """
    command = (
        "python3 -c \"import sys,runpy;from pathlib import Path;"
        "n='definitely-not-a-hook.py';"
        "p=next((str(d/'.claude'/'hooks'/n) for d in [Path.cwd(),*Path.cwd().parents] "
        "if (d/'.claude'/'hooks'/n).is_file()),None);p and runpy.run_path(p)\""
    )
    names = HOOK_NAME_RE.findall(command)
    assert "definitely-not-a-hook.py" in names
    assert not (HOOKS_DIR / "definitely-not-a-hook.py").is_file()
