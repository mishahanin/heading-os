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

from tests.repo_files import tracked_paths  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = ROOT / ".claude" / "hooks"

#: The settings files as git sees them. All four below are TRACKED, so the
#: "not present in this clone" skip each test opened with was a claim that could
#: not become true without someone deleting a tracked file, and a skipped guard
#: is a guard that left the tree with nobody reading a failure. Asked of git
#: rather than of the filesystem, so an agent worktree under
#: `.claude/worktrees/` cannot answer for the repository.
TRACKED = {p.relative_to(ROOT).as_posix()
           for p in tracked_paths([".claude/settings*.json"])}


def _tracked(rel: str) -> Path:
    """The settings file at `rel`, or a failure naming what git no longer has."""
    assert rel in TRACKED, (
        f"{rel} is not tracked. It is one of the files a new workspace is built "
        "from (scripts/setup-platform.sh); if it was deliberately retired, "
        "remove it from SETTINGS_FILES in the same change rather than letting "
        "this file quietly stop checking it.")
    path = ROOT / rel
    assert path.is_file(), f"{rel} is tracked but missing from the working tree"
    return path


SETTINGS_FILES = [
    ".claude/settings.json",
    ".claude/settings.local.linux.json",
    ".claude/settings.local.macos.json",
    ".claude/settings.local.windows.json",
]

#: The smallest hook count each settings file may yield before the extractor
#: below is presumed blind rather than the file presumed empty.
FLOORS = {
    ".claude/settings.json": 1,
    ".claude/settings.local.linux.json": 12,
    ".claude/settings.local.macos.json": 12,
    ".claude/settings.local.windows.json": 12,
}

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
    path = _tracked(rel)
    referenced = _referenced_hooks(path)
    # Floored, because the whole check is `not missing` over a set this file
    # extracts itself. `_commands` walks four levels of a schema Claude Code
    # owns and `HOOK_NAME_RE` matches a shape the launcher happens to use today;
    # either going quiet leaves an empty set, an empty `missing`, and a green
    # test over a settings file naming any absent hook it likes. The floor is a
    # per-file one for the same reason a per-source floor beats a union: four
    # files sharing one total is satisfied while three contribute nothing.
    # MEASURED 2026-09-01: settings.json names 1 hook, each per-OS template
    # names 15. The floors sit just under those, so a hook being retired does
    # not fail this, and the extractor going blind does.
    assert len(referenced) >= FLOORS[rel], (
        f"{rel} yielded only {sorted(referenced)}, under its floor of "
        f"{FLOORS[rel]}. Either the settings schema or the launcher shape "
        "changed and the extractor above stopped seeing hooks, in which case "
        "this guard was about to pass over any absent hook at all.")
    missing = sorted(n for n in referenced if not (HOOKS_DIR / n).is_file())
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
    path = _tracked(rel)
    retired = {
        "prevent-secrets.py",
        "protect-corporate.py",
        "protect-docs.py",
        "protect-personal-threads.py",
        "protect-secure.py",
    }
    named = _referenced_hooks(path) & retired
    assert not named, f"{rel} references retired shim(s): {sorted(named)}"


PER_OS_TEMPLATES = [
    ".claude/settings.local.linux.json",
    ".claude/settings.local.macos.json",
    ".claude/settings.local.windows.json",
]

# The compaction-control settings, decided over 2026-08-18/19 and measured
# against Claude Code 2.1.235. The window is NOT the point compaction fires at:
# the harness computes `effective = window - 20000` and then
# `min(effective - 0.20*effective, effective - 13000)`, so 750000 puts the real
# trigger at 584000 - above the 45% hard threshold, which is the whole design.
# Setting the window to 584000 instead would move the trigger to 451200 and
# undo the tuning. Read `plans/2026-08-19-compaction-control.md` in the data
# overlay before changing any value here.
COMPACTION_ENV = {
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "750000",
    "CLAUDE_HANDOFF_SOFT_THRESHOLD": "40",
    "CLAUDE_HANDOFF_HARD_THRESHOLD": "45",
}


@pytest.mark.parametrize("rel", PER_OS_TEMPLATES)
def test_per_os_template_carries_the_compaction_env(rel):
    """A fresh install must land on the tuned compaction point, not the default.

    `settings.local.json` is gitignored, so the live values on this machine are
    invisible to a new clone. A workspace is built by copying one of these three
    templates (scripts/setup-platform.sh). All three carried an EMPTY env block
    until 2026-08-20, so every machine except this one ran the stock window and
    compacted at a different point than the one that was measured and chosen.
    """
    path = _tracked(rel)
    env = json.loads(path.read_text(encoding="utf-8")).get("env") or {}
    wrong = {k: (env.get(k), v) for k, v in COMPACTION_ENV.items() if env.get(k) != v}
    assert not wrong, (
        f"{rel} env drifted from the tuned compaction settings "
        f"(key: got, want): {wrong}. See COMPACTION_ENV above for why the "
        "window value is not the trigger value."
    )


def test_all_templates_ship_one_identical_hooks_block():
    """The three per-OS templates differ only in `permissions`, never in hooks.

    They drifted, and the drift had teeth. Until 2026-08-20 all three registered
    PostToolUse on `Write|Edit` while the live file used
    `Write|Edit|MultiEdit|NotebookEdit`, so every workspace built from a template
    ran no hidden-character scan (post-write-sanitize.py, the mechanical arm of
    hidden-chars.md) and no prompt-injection scan (prompt-guard.py, defence layer
    4 in security.md) on a MultiEdit or a NotebookEdit. The templates also
    omitted memory-reconcile.py entirely.

    Nothing compared them, so nothing noticed. This does.
    """
    blocks = {}
    for rel in PER_OS_TEMPLATES:
        path = _tracked(rel)
        hooks = json.loads(path.read_text(encoding="utf-8")).get("hooks", {})
        # Floored per file. "They all agree" is satisfied by three templates
        # that all ship NO hooks at all, which is the strongest possible
        # disagreement with the live file and the weakest possible pass. The
        # sibling below floors PostToolUse only, so the other seven events had
        # nothing holding them. The event names are the ones the workspace's
        # own hook inventory turns on; a deliberate removal edits this list and
        # is therefore visible in the diff.
        for event in ("SessionStart", "UserPromptSubmit", "PreToolUse",
                      "PostToolUse", "Stop", "PreCompact"):
            assert hooks.get(event), f"{rel} registers no {event} hook"
        blocks[rel] = json.dumps(hooks, sort_keys=True)
    distinct = set(blocks.values())
    assert len(distinct) == 1, (
        "per-OS templates carry different hooks blocks: "
        + ", ".join(f"{k} -> {hash(v)}" for k, v in blocks.items())
    )


def test_post_tool_use_covers_every_write_shape():
    """A write hook registered on `Write|Edit` silently skips two write shapes.

    MultiEdit puts its text in edits[i].new_string and NotebookEdit in
    new_source; both hook bodies already destructure them, so only the matcher
    was ever wrong.
    """
    for rel in PER_OS_TEMPLATES:
        path = _tracked(rel)
        entries = json.loads(path.read_text(encoding="utf-8"))["hooks"].get("PostToolUse", [])
        # Floored. This is the workspace's only guard that the write-side hooks
        # are registered at all, and its assertion sat two loops deep over a list
        # that `.get(..., [])` makes empty whenever the key is absent, emptied or
        # renamed. Measured: with `"PostToolUse": []` in all three shipped
        # templates - every write hook gone - this file reported 15 passed. The
        # sibling test only asserts the three templates AGREE, which an equally
        # empty trio satisfies.
        assert entries, f"{rel} registers no PostToolUse hook at all"
        for entry in entries:
            matcher = entry.get("matcher", "")
            missing = [t for t in ("Write", "Edit", "MultiEdit", "NotebookEdit") if t not in matcher]
            assert not missing, f"{rel} PostToolUse matcher {matcher!r} misses {missing}"


def test_the_compaction_window_still_derives_the_intended_trigger():
    """Pin the arithmetic, so a future window edit cannot silently move the point.

    This encodes the formula decoded from Claude Code 2.1.235 on 2026-08-19. If
    a later harness changes it, this test fails and the number gets re-measured
    rather than assumed.
    """
    window = int(COMPACTION_ENV["CLAUDE_CODE_AUTO_COMPACT_WINDOW"])
    effective = max(100000, min(1000000, window)) - 20000
    fires_at = min(effective - int(0.20 * effective), effective - 13000)
    assert fires_at == 584000, (
        f"the configured window {window} now derives a trigger of {fires_at}, "
        "not the measured 584000"
    )
    hard_threshold_pct = int(COMPACTION_ENV["CLAUDE_HANDOFF_HARD_THRESHOLD"])
    assert fires_at < window * (100 - hard_threshold_pct) / 100 + window, (
        "sanity: the trigger must sit inside the configured window"
    )


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
