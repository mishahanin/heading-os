"""The "YARD NOT PROVISIONED" warning was installed by provisioning.

`.claude/hooks/session-start.py` carries the one sentence that would have caught
the defect fixed earlier today -- a YARD whose bootstrap never ran, and which
therefore has the eleven PreToolUse walls unregistered and a live push url into
a public repository:

    YARD NOT PROVISIONED: this is a worktree and the bootstrap never ran here.

MEASURED 2026-09-03: that hook was registered ONLY in
`.claude/settings.local.json`, which is gitignored and which the bootstrap
itself writes at step 5. So in the one situation the sentence exists for -- the
bootstrap did not run -- the file that registers the sentence does not exist,
and the warning cannot fire. The whole time those YARDs were unprovisioned, the
only thing that said so was a plugin log nobody reads.

That is the shape this repository keeps finding: a control whose absent state is
indistinguishable from a healthy one. Here it is sharper, because the control's
presence is CAUSED BY the condition it is meant to detect being false.

THE REPAIR: register it in the TRACKED `.claude/settings.json`, which is in git
and therefore present in every checkout from the moment git creates it, before
any bootstrap runs.

AND REMOVE IT FROM THE PLATFORM TEMPLATES, or it is registered twice and runs
twice. `.claude/settings.local.{linux,macos,windows}.json` each carried it, and
`scripts/merge-platform-settings.py` merges a template into the live local file
without consulting the tracked one, so nothing would have deduplicated them.
`setup-platform.sh --check` compares the template against the live file, so
dropping the entry from the templates also stops `--check` demanding it there;
no separate change is needed for the check to stay honest.

MEASURED the same day, for the third obligation on this change: the hook now
runs in EVERY clone, including a public one with no data overlay. Driven exactly
as SessionStart drives it, in a fresh `git clone` with no sibling overlay and no
`.env`, it exited 0, wrote nothing to stderr, and named no private path -- the
data seam falls through to the engine's own `examples/` tree. It also stayed
silent about provisioning, correctly, because a clone is not a worktree.

Run: python3 -m pytest tests/test_a_warning_installed_by_the_thing_it_warns_about.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TRACKED = ROOT / ".claude" / "settings.json"
TEMPLATES = tuple(
    ROOT / ".claude" / f"settings.local.{platform}.json"
    for platform in ("linux", "macos", "windows")
)
HOOK = "session-start.py"


def _commands(settings: Path, event: str = "SessionStart") -> list[str]:
    if not settings.exists():
        return []
    document = json.loads(settings.read_text(encoding="utf-8"))
    return [hook.get("command", "")
            for group in (document.get("hooks", {}).get(event) or [])
            for hook in group.get("hooks", [])]


# ============================================================
# Registered where git puts it, not where the bootstrap puts it
# ============================================================

def test_the_warning_is_registered_in_the_tracked_settings():
    """The failing half, and it failed against the version before this change.

    Verified by running, not by argument: with the entry only in the platform
    templates this assertion went red and its sibling below went green.
    """
    registered = [c for c in _commands(TRACKED) if HOOK in c]

    assert registered, (
        f"{HOOK} is not registered in the tracked .claude/settings.json. It "
        f"carries the YARD-NOT-PROVISIONED warning, so registering it anywhere "
        f"gitignored means it is installed by the very provisioning whose "
        f"absence it exists to report.")
    assert len(registered) == 1, (
        f"{HOOK} is registered {len(registered)} times in the tracked file")


def test_the_templates_no_longer_register_it_as_well():
    """The other direction. Two registrations run the hook twice.

    `merge-platform-settings.py` merges a template into the live local file and
    never reads the tracked one, so nothing deduplicates across the two.
    """
    # The floor, outside the loop. Three platform templates on 2026-09-03;
    # with none of them found this test would assert nothing at all.
    assert len(TEMPLATES) == 3, f"{len(TEMPLATES)} platform template(s)"
    for template in TEMPLATES:
        assert template.exists(), f"{template.name} vanished"
        duplicated = [c for c in _commands(template) if HOOK in c]
        assert not duplicated, (
            f"{template.name} still registers {HOOK}, which the tracked "
            f"settings.json now registers too, so it would run twice per "
            f"session")


def test_no_session_hook_is_registered_in_both_files_at_once():
    """The general form of the rule, so the next hook cannot repeat it.

    Compares by the hook FILENAME the command loads, not by the command string:
    the two files could spell the same invocation differently and still be the
    same hook running twice.
    """
    import re

    def names(commands):
        # Two spellings live in these files: `n='session-start.py'` and the
        # inlined `'.claude'/'hooks'/'data-path-redirect.py'`. Matching only
        # the first parsed nothing out of the tracked file and the floor below
        # caught it, which is what the floor is for.
        found = set()
        for command in commands:
            found |= set(re.findall(r"n='([^']+\.py)'", command))
            found |= set(re.findall(r"'hooks'/'([^']+\.py)'", command))
        return found

    tracked = names(_commands(TRACKED)) | names(_commands(TRACKED, "PreToolUse"))
    # A floor: with nothing parsed out of the tracked file this test asserts
    # nothing at all. Two hooks there on 2026-09-03.
    assert len(tracked) >= 2, f"parsed {tracked} out of the tracked settings"

    for template in TEMPLATES:
        overlap = tracked & (names(_commands(template))
                             | names(_commands(template, "PreToolUse")))
        assert not overlap, (
            f"{template.name} and the tracked settings.json both register "
            f"{sorted(overlap)}; each would run twice per session")


# ============================================================
# What the hook does once it is registered everywhere
# ============================================================

@pytest.fixture
def clean_clone(tmp_path) -> Path:
    """A checkout with no data overlay beside it and no `.env`.

    The public-clone case. `--shared` so the object database is not copied.
    """
    target = tmp_path / "engine"
    created = subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(ROOT), str(target)],
        capture_output=True, text=True)
    if created.returncode != 0:
        pytest.skip(f"could not clone: {created.stderr.strip()}")
    return target


def _drive(cwd: Path) -> subprocess.CompletedProcess:
    payload = json.dumps({"session_id": "probe", "cwd": str(cwd),
                          "hook_event_name": "SessionStart",
                          "source": "startup"})
    env = {k: v for k, v in os.environ.items()
           if k not in ("HEADING_OS_DATA", "WORKSPACE_ROOT")}
    return subprocess.run(
        [sys.executable, str(cwd / ".claude" / "hooks" / HOOK)],
        input=payload, cwd=str(cwd), capture_output=True, text=True,
        timeout=300, env=env)


def test_it_is_harmless_on_a_clone_with_no_overlay(clean_clone):
    """The third obligation on moving a hook into every clone.

    It now runs for people who have no data overlay at all, so it must not
    crash and must not name anything private.
    """
    result = _drive(clean_clone)

    assert result.returncode == 0, (
        f"the hook failed on a clone with no overlay\n{result.stderr[-2000:]}")
    assert result.stderr.strip() == "", (
        f"it wrote to stderr on a clean clone: {result.stderr[-1000:]}")
    for private in ("heading-os-data", "auto-memory", "crm/contacts"):
        assert private not in result.stdout, (
            f"the hook named {private!r} on a clone that has no overlay")


def test_it_says_nothing_about_provisioning_on_a_main_clone(clean_clone):
    """A clone is not a worktree, so the warning must stay silent here.

    Without this, a warning that fired everywhere would be indistinguishable
    from one that fires correctly, and would be turned off within a week.
    """
    assert "YARD NOT PROVISIONED" not in _drive(clean_clone).stdout


def test_it_warns_in_a_worktree_whose_bootstrap_never_ran(armed_worktree):
    """The whole point, driven end to end.

    `armed_worktree` carries this working tree but no `.claude/.yard-bootstrap-
    status`, which is exactly the state every real YARD was in.
    """
    status = armed_worktree / ".claude" / ".yard-bootstrap-status"
    if status.exists():
        status.unlink()

    result = _drive(armed_worktree)

    assert result.returncode == 0, result.stderr[-2000:]
    assert "YARD NOT PROVISIONED" in result.stdout, (
        "an unprovisioned worktree produced no warning, which is the defect "
        f"this file exists for\n{result.stdout[-2000:]}")


def test_it_falls_silent_once_the_bootstrap_has_succeeded(armed_worktree):
    """The other direction, and the one that keeps the warning worth reading."""
    status = armed_worktree / ".claude" / ".yard-bootstrap-status"
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(
        '{"status":"ok","step":11,"timestamp":"2026-09-03T00:00:00Z",'
        '"version":"5.0"}', encoding="utf-8")

    assert "YARD NOT PROVISIONED" not in _drive(armed_worktree).stdout
