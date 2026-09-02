"""`setup-platform.sh` said "idempotent" and ended in an unconditional `cp`.

MEASURED 2026-09-02 by comparing the two files on the live workspace, not by
reading the code. One run of `bash scripts/setup-platform.sh` would have
discarded:

- 29 permission entries the operator had granted and no template carries;
- `autoMemoryDirectory`, the pointer at the private data overlay's auto-memory,
  whose loss does not raise and silently redirects every memory write;
- `outputStyle`, the operator's chosen way of being spoken to;
- `enabledPlugins`, which plugins are on.

None of those can live in a template, because every one of them is per-instance
by definition. And `scripts/vps-sync.sh` invokes the script from a cron whenever
the template changes, so the loss was scheduled rather than accidental.

The header above the copy read "Safe to run multiple times (idempotent)". A
comment asserting the exact property the code lacks is worse than no comment: it
is read as a guarantee, so nobody looks at line 55.

## Why merge rather than refuse

Refusing when the file exists would have been a smaller change, and wrong. The
caller has a real need: a template that gains a hook or a permission must reach
the live file. Refusing trades silent data loss for a silent update gap, which
is the same disease facing the other way.

So: the template proposes, the live file disposes. A local value is kept, a
template addition is added, permission lists are unioned because a permission is
a grant and dropping one breaks a workflow that used to run.

## The second finding in the same file

`.claude/settings.local.macos.json` existed, was maintained, was covered by
tests, and was installed by NO code path. The Darwin branch used the Linux
template under a comment claiming "same Python3 paths". Fixed here too, with a
fallback so an older clone that lacks the macOS template still sets up.

## The third, which the fix itself created

A backup of the live settings carries the same per-instance content as the file
it copies: the data-overlay path, the permission grants, the plugin choices.
`.claude/settings.local.json` is gitignored; `.claude/settings.local.json.bak-*`
was NOT, so the repair would have left an untracked candidate holding private
paths in a public repository. Both patterns are now ignored and a test below
holds that.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MERGER = ROOT / "scripts" / "merge-platform-settings.py"
SETUP = ROOT / "scripts" / "setup-platform.sh"
sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("merge_platform_settings", MERGER)
mps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mps)


TEMPLATE = {
    "permissions": {"allow": ["Bash(ls:*)", "Read"], "deny": ["Bash(rm:*)"]},
    "hooks": {"SessionStart": [{"hooks": [{"command": "a"}]}]},
}

# Shaped after the real live file: three top-level keys no template carries,
# and grants the template has never heard of.
LIVE = {
    "autoMemoryDirectory": "/invented/overlay/auto-memory",
    "outputStyle": "SomeStyle",
    "enabledPlugins": {"invented@plugins": True},
    "permissions": {"allow": ["Bash(ls:*)", "Bash(git status:*)", "Write"],
                    "deny": []},
    "hooks": {"SessionStart": [{"hooks": [{"command": "local-one"}]}],
              "Stop": [{"hooks": [{"command": "b"}]}]},
}


# ------------------------------------------------------------------
# The merge rule, as a pure function
# ------------------------------------------------------------------

def test_a_per_instance_key_survives_the_merge():
    """The three keys the copy destroyed. Named individually on purpose.

    A test asserting only "some keys survived" passes while the one that
    matters is dropped, and `autoMemoryDirectory` is the one that matters:
    losing it does not raise, it redirects.
    """
    out = mps.merge_settings(TEMPLATE, LIVE)
    assert out["autoMemoryDirectory"] == "/invented/overlay/auto-memory"
    assert out["outputStyle"] == "SomeStyle"
    assert out["enabledPlugins"] == {"invented@plugins": True}


def test_a_local_permission_the_template_never_had_survives():
    """The 29-entry loss, in miniature."""
    out = mps.merge_settings(TEMPLATE, LIVE)
    allow = out["permissions"]["allow"]
    assert "Bash(git status:*)" in allow
    assert "Write" in allow


def test_a_template_permission_reaches_the_live_file():
    """The other half. Without it, "keep everything local" degenerates into
    "never update", which is the update gap this design refused."""
    out = mps.merge_settings(TEMPLATE, LIVE)
    assert "Read" in out["permissions"]["allow"]
    assert "Bash(rm:*)" in out["permissions"]["deny"]


def test_a_union_does_not_duplicate_a_shared_entry():
    out = mps.merge_settings(TEMPLATE, LIVE)
    assert out["permissions"]["allow"].count("Bash(ls:*)") == 1


def test_local_order_is_preserved():
    """These files are read by humans. A reshuffled list is a diff nobody can
    review, so template additions append rather than interleave."""
    out = mps.merge_settings(TEMPLATE, LIVE)
    allow = out["permissions"]["allow"]
    assert allow[:3] == ["Bash(ls:*)", "Bash(git status:*)", "Write"]


def test_an_existing_hook_group_is_not_merged_into():
    """Merging two lists of matchers registers the hook twice, and a duplicated
    hook RUNS twice. So a group the live file already defines is left alone."""
    out = mps.merge_settings(TEMPLATE, LIVE)
    assert out["hooks"]["SessionStart"] == LIVE["hooks"]["SessionStart"]
    assert out["hooks"]["Stop"] == LIVE["hooks"]["Stop"]


def test_a_new_hook_group_does_reach_the_live_file():
    """The control for the test above: leaving groups alone must not mean
    leaving new capability out."""
    template = dict(TEMPLATE)
    template["hooks"] = dict(TEMPLATE["hooks"])
    template["hooks"]["PreToolUse"] = [{"hooks": [{"command": "new"}]}]
    out = mps.merge_settings(template, LIVE)
    assert "PreToolUse" in out["hooks"]


def test_the_merge_mutates_neither_input():
    """It is called before a backup exists in at least one path."""
    t = json.loads(json.dumps(TEMPLATE))
    live = json.loads(json.dumps(LIVE))
    mps.merge_settings(t, live)
    assert t == TEMPLATE
    assert live == LIVE


# ------------------------------------------------------------------
# The CLI
# ------------------------------------------------------------------

def _run(*args, cwd=None):
    return subprocess.run([sys.executable, str(MERGER), *[str(a) for a in args]],
                          capture_output=True, text=True, cwd=cwd)


@pytest.fixture
def pair(tmp_path):
    t = tmp_path / "template.json"
    v = tmp_path / "settings.local.json"
    t.write_text(json.dumps(TEMPLATE), encoding="utf-8")
    v.write_text(json.dumps(LIVE), encoding="utf-8")
    return t, v


def test_a_backup_is_written_before_the_live_file_changes(pair):
    t, v = pair
    proc = _run(t, v)
    assert proc.returncode == 0, proc.stderr
    backups = list(v.parent.glob("settings.local.json.bak-*"))
    assert len(backups) == 1, f"expected one backup, found {backups}"
    assert json.loads(backups[0].read_text(encoding="utf-8")) == LIVE, (
        "the backup does not hold the file as it was before the write"
    )


def test_no_temp_file_is_left_behind(pair):
    t, v = pair
    _run(t, v)
    assert not list(v.parent.glob("*.tmp")), (
        "the atomic write left its temp file on disk"
    )


def test_a_second_run_reports_nothing_to_do(pair):
    """This is the "idempotent" the old header claimed and the code lacked."""
    t, v = pair
    assert _run(t, v).returncode == 0
    before = v.read_text(encoding="utf-8")
    second = _run(t, v)
    assert second.returncode == 0
    assert "nothing to do" in second.stdout
    assert v.read_text(encoding="utf-8") == before
    assert len(list(v.parent.glob("*.bak-*"))) == 1, (
        "a no-op run wrote a second backup"
    )


def test_dry_run_writes_nothing(pair):
    t, v = pair
    before = v.read_text(encoding="utf-8")
    proc = _run(t, v, "--dry-run")
    assert proc.returncode == 0
    assert "would update" in proc.stdout
    assert v.read_text(encoding="utf-8") == before
    assert not list(v.parent.glob("*.bak-*"))


def test_force_replaces_and_says_what_it_discards(pair):
    """The destructive path still exists, on purpose, and announces itself."""
    t, v = pair
    proc = _run(t, v, "--force")
    assert proc.returncode == 0
    assert "DISCARDS" in proc.stdout
    assert "autoMemoryDirectory" in proc.stdout
    assert json.loads(v.read_text(encoding="utf-8")) == TEMPLATE


def test_a_missing_target_is_a_plain_copy(tmp_path):
    """First install. It must need no JSON parsing at all, because this script
    is step 1 of setup on a clone whose .venv does not exist yet."""
    t = tmp_path / "template.json"
    t.write_text(json.dumps(TEMPLATE), encoding="utf-8")
    v = tmp_path / "nested" / "settings.local.json"
    proc = _run(t, v)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(v.read_text(encoding="utf-8")) == TEMPLATE


@pytest.mark.parametrize("broken", ["template", "target"])
def test_unparseable_json_refuses_and_names_the_file(tmp_path, broken):
    """A guess about what the file meant is how the live file gets replaced by
    a guess. Both sides, because only one of them was ever going to be tested."""
    t = tmp_path / "template.json"
    v = tmp_path / "settings.local.json"
    t.write_text("{" if broken == "template" else json.dumps(TEMPLATE),
                 encoding="utf-8")
    v.write_text("{" if broken == "target" else json.dumps(LIVE),
                 encoding="utf-8")
    before = v.read_text(encoding="utf-8")
    proc = _run(t, v)
    assert proc.returncode != 0
    assert "not valid JSON" in proc.stderr
    assert v.read_text(encoding="utf-8") == before, "it wrote anyway"


def test_a_missing_template_refuses(tmp_path):
    v = tmp_path / "settings.local.json"
    v.write_text(json.dumps(LIVE), encoding="utf-8")
    proc = _run(tmp_path / "absent.json", v)
    assert proc.returncode == 1
    assert "template not found" in proc.stderr


# ------------------------------------------------------------------
# The shell wrapper, and the two findings around it
# ------------------------------------------------------------------

def test_the_shell_script_no_longer_carries_a_bare_copy_onto_the_live_file():
    """The defect, asserted at its source.

    Scoped honestly: this reads the text, so it catches the line coming back
    verbatim and nothing cleverer. The CLI tests above are what establish the
    behaviour; this one stops the one-line regression that started it all.

    COMMENTS ARE EXCLUDED, and that is not a convenience. The script's own
    header quotes the offending line while explaining what it replaced, so a
    naive substring search flags the documentation of the fix as the defect.
    The first version of this test did exactly that.
    """
    lines = SETUP.read_text(encoding="utf-8").splitlines()
    code = [ln for ln in lines if not ln.lstrip().startswith("#")]

    copies = [i for i, ln in enumerate(code)
              if 'cp "$TEMPLATE" "$TARGET"' in ln]
    assert len(copies) == 1, (
        f"expected exactly one copy onto the live settings, found "
        f"{len(copies)}. The surviving one is the FIRST-INSTALL path, which is "
        f"correct and must need no interpreter; a second is the defect back."
    )

    # And that one must sit INSIDE the existence check, or it is the same
    # unconditional copy with an extra line above it. Positional rather than a
    # substring search, because "the guard exists somewhere in this file" is
    # satisfied by a guard around something else entirely.
    guards = [i for i, ln in enumerate(code) if 'if [ ! -e "$TARGET" ]' in ln]
    assert guards, "the first-install copy is not guarded by an existence check"
    assert guards[0] < copies[0] < guards[0] + 6, (
        f"the copy at code line {copies[0]} is not inside the existence check "
        f"at code line {guards[0]}"
    )

    header = "\n".join(ln for ln in lines if ln.lstrip().startswith("#"))
    assert "idempotent" not in header or "It did not used to be" in header, (
        "the header claims idempotence without saying what changed; that "
        "sentence is what stopped anyone reading the copy below it"
    )


def test_the_macos_template_is_reachable():
    """It existed, was maintained, was tested, and was installed by nothing."""
    assert (ROOT / ".claude" / "settings.local.macos.json").is_file()
    assert "settings.local.macos.json" in SETUP.read_text(encoding="utf-8"), (
        "the Darwin branch still does not name the macOS template"
    )


def test_the_backup_pattern_is_gitignored():
    """A backup carries the same private content as the file it copies."""
    for name in ("settings.local.json.bak-20300101-000000",
                 "settings.local.json.tmp"):
        proc = subprocess.run(
            ["git", "check-ignore", "-q", f".claude/{name}"],
            cwd=str(ROOT), capture_output=True)
        assert proc.returncode == 0, (
            f".claude/{name} is not gitignored, so a backup of the operator's "
            f"per-instance settings would sit untracked in a public repository"
        )


def test_the_shell_script_parses():
    proc = subprocess.run(["bash", "-n", str(SETUP)], capture_output=True,
                          text=True)
    assert proc.returncode == 0, proc.stderr


def test_the_shell_script_merges_rather_than_clobbers(tmp_path):
    """End to end, through the shell, against a scratch workspace.

    The unit tests above exercise the Python. This one proves the shell reaches
    it, because the defect was in the shell and a merger nobody calls fixes
    nothing.
    """
    ws = tmp_path / "ws"
    (ws / "scripts").mkdir(parents=True)
    (ws / ".claude").mkdir()
    (ws / ".claude" / "settings.local.linux.json").write_text(
        json.dumps(TEMPLATE), encoding="utf-8")
    (ws / ".claude" / "settings.local.json").write_text(
        json.dumps(LIVE), encoding="utf-8")
    for name in ("setup-platform.sh", "merge-platform-settings.py"):
        (ws / "scripts" / name).write_bytes((ROOT / "scripts" / name).read_bytes())

    proc = subprocess.run(["bash", str(ws / "scripts" / "setup-platform.sh")],
                          capture_output=True, text=True,
                          env=dict(os.environ, PATH=os.environ["PATH"]))
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    after = json.loads((ws / ".claude" / "settings.local.json").read_text(
        encoding="utf-8"))
    assert after["autoMemoryDirectory"] == "/invented/overlay/auto-memory", (
        "the shell path still destroys per-instance keys"
    )
    assert "Read" in after["permissions"]["allow"], (
        "the shell path did not carry the template addition through"
    )
