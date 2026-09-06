#!/usr/bin/env python3
"""A YARD with no agent in it looked alive, and borrowed a neighbour's session.

MEASURED 2026-09-06, twice in one day. `herdr worktree create` opens a SHELL; it
has no flag that starts an agent. The only thing that ever starts one is the last
step of `scripts/herdr/heading-os-yard/yard-bootstrap.sh`, and that step is
best-effort in four separate ways:

  * it is inside `if [ "$AUTOSTART" = "1" ] && [ -n "$PANE_ID" ]`, and an empty
    `PANE_ID` skips it with no log line at all;
  * `HEADING_OS_AUTOSTART=0` skips it by design;
  * the idempotency check at the top of the script exits on `status: ok`, so the
    `worktree.opened` subscription never reaches step 11 on a healthy yard whose
    agent has since died;
  * it ends `"$HERDR" pane run ... || log`, and `pane run` exits 0 when the
    command was DISPATCHED to a pane, not when an agent booted in it. A prompt
    addressed to a pane with no agent has already been observed landing
    somewhere else entirely, with the exit code saying nothing.

The visible result: a yard in the sidebar with nothing of its own to show, into
whose slot FleetView renders a NEIGHBOURING yard's session. Nothing on this
machine asked the one question that settles it, which is not about panes or
status files but about transcripts on disk: Claude Code writes one
`<session-id>.jsonl` under `~/.claude/projects/<slug>/` per session, so zero of
them under a checkout's own slug means no agent has ever run there.

WHERE THE CHECK LIVES, AND WHY

`.claude/hooks/session-start.py` already carries `check_yard_bootstrap`, which
asks whether THIS checkout was provisioned. The new `check_yards_without_a_session`
is beside it because the operator already reads yard health there, but it is a
separate function because it is a separate question with a separate subject: the
OTHER checkouts. It runs in HELM only. A session inside a yard, by existing at
all, writes the transcript that would clear that yard, and a fleet-wide list
printed once per yard is the same list N times.

THE SLUG RULE IS NOT REPRODUCED

`scripts/utils/checkpoint_paths.transcript_dir()` owns it, and
`tests/test_transcript_dir_has_one_owner.py` is the gate that has already caught
a second copy in the push path. `scripts/utils/yard_sessions.py` calls the owner,
and takes even the projects ROOT as `transcript_dir(...).parent` so no path
literal for it exists there either. `test_the_slug_rule_is_not_reproduced_here`
below is the jaw for that.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from scripts.utils.checkpoint_paths import transcript_dir  # noqa: E402
from scripts.utils.yard_sessions import yards_without_a_session  # noqa: E402

_HOOK = _ROOT / ".claude" / "hooks" / "session-start.py"
_MODULE = _ROOT / "scripts" / "utils" / "yard_sessions.py"


def _home(tmp_path, monkeypatch) -> Path:
    """A scratch HOME, so the sweep reads a transcript store this test owns.

    `Path.home()` goes through `expanduser`, which reads `$HOME` first on POSIX,
    so this reroutes the resolver without patching it. Patching the resolver
    would prove the test's own stub works and nothing about the code.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def _write_transcript(checkout: Path) -> Path:
    """One transcript under the checkout's own slug, named by the OWNER."""
    directory = transcript_dir(checkout)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "0193f0c2-4d6a-7c31-9e88-000000000001.jsonl"
    path.write_text('{"type":"user"}\n', encoding="utf-8")
    return path


# ============================================================
# The sweep itself
# ============================================================

def test_a_worktree_with_no_transcript_is_named(tmp_path, monkeypatch,
                                                temporary_worktree, worktree_origin):
    """The defect, reproduced: a real worktree, no agent ever in it."""
    home = _home(tmp_path, monkeypatch)
    (home / ".claude" / "projects").mkdir(parents=True)

    report = yards_without_a_session(worktree_origin)
    assert report.unknown is None, report.unknown
    assert temporary_worktree.resolve() in report.silent, (
        f"the silent yard was not named; checked={report.checked}"
    )


def test_an_emptied_transcript_directory_counts_as_no_session(
        tmp_path, monkeypatch, temporary_worktree, worktree_origin):
    """The second way a yard has no transcript, and it is not hypothetical.

    `scripts/archive-transcripts.py` MOVES transcripts out of
    `~/.claude/projects/<slug>/` on a timer, so the directory outliving its
    contents is the ordinary end state of an old yard. A check that asks only
    whether the directory exists reads that as a healthy yard.

    Found by mutation: replacing the `.jsonl` test with `if False:` left this
    file green, because every other case here reaches the answer through the
    directory being ABSENT.
    """
    home = _home(tmp_path, monkeypatch)
    emptied = transcript_dir(temporary_worktree)
    emptied.mkdir(parents=True)
    (emptied / "notes.md").write_text("not a transcript\n", encoding="utf-8")

    report = yards_without_a_session(worktree_origin)
    assert report.unknown is None, report.unknown
    assert temporary_worktree.resolve() in report.silent, (
        "a directory holding no .jsonl was read as a session"
    )


def test_a_worktree_with_a_transcript_is_not_named(tmp_path, monkeypatch,
                                                   temporary_worktree, worktree_origin):
    """The other side, and the one that decides whether this is usable: a yard
    an agent HAS run in must stay off the list, or the alert is noise the
    operator learns to ignore."""
    _home(tmp_path, monkeypatch)
    _write_transcript(temporary_worktree)

    report = yards_without_a_session(worktree_origin)
    assert report.unknown is None, report.unknown
    assert report.silent == (), f"a yard with a transcript was named: {report.silent}"
    assert temporary_worktree.resolve() in report.checked, (
        "the yard was not examined at all, so the clean answer means nothing"
    )


def test_an_absent_projects_store_is_unknown_and_not_a_clean_sweep(
        tmp_path, monkeypatch, temporary_worktree, worktree_origin):
    """No `~/.claude/projects` at all: the check must not crash, must not name
    every yard, and must not report clean either.

    Naming every yard would be a wall of false alarms on a machine where the
    store simply is not there yet. Reporting clean would be the worse half of
    the same mistake: a caller reading "no silent yards" as "the fleet is fine"
    over a question nobody managed to ask.
    """
    home = _home(tmp_path, monkeypatch)
    assert not (home / ".claude" / "projects").exists()

    report = yards_without_a_session(worktree_origin)
    assert report.unknown, "an absent transcript store was not reported as unknown"
    assert str(home / ".claude" / "projects") in report.unknown
    assert report.silent == (), "yards were named over a store that does not exist"


def test_the_callers_own_checkout_is_never_named(tmp_path, monkeypatch,
                                                 temporary_worktree, worktree_origin):
    """At SessionStart the running session's transcript may not be on disk yet,
    so without the exclusion the sweep names the very tree it runs in."""
    home = _home(tmp_path, monkeypatch)
    (home / ".claude" / "projects").mkdir(parents=True)

    report = yards_without_a_session(
        worktree_origin, exclude=(temporary_worktree,))
    assert temporary_worktree.resolve() not in report.silent
    assert temporary_worktree.resolve() not in report.checked


def test_a_registration_whose_checkout_is_gone_is_not_a_yard(
        tmp_path, monkeypatch, temporary_worktree, worktree_origin):
    """`git worktree remove` can half-fail and leave the registration behind.
    A pointer to a directory that no longer exists is not a yard without a
    session; it is not a yard. Reporting it would train the operator to ignore
    the whole alert."""
    home = _home(tmp_path, monkeypatch)
    (home / ".claude" / "projects").mkdir(parents=True)
    _write_transcript(temporary_worktree)

    ghost = worktree_origin / ".git" / "worktrees" / "ghost"
    ghost.mkdir(parents=True)
    (ghost / "gitdir").write_text(
        f"{tmp_path}/deleted-yard/.git\n", encoding="utf-8")

    report = yards_without_a_session(worktree_origin)
    assert report.silent == (), f"a deleted checkout was named: {report.silent}"
    assert all(p.is_dir() for p in report.checked)


def test_the_slug_rule_is_not_reproduced_here():
    """The single-owner jaw.

    `tests/test_transcript_dir_has_one_owner.py` walks `scripts/**/*.py` for the
    literal two-replacement form and would already fail on a copy in this
    module. This is the narrower statement that belongs beside the feature: the
    sweep must reach the transcript directory through the owner's NAME, so a
    change to the owner's rule reaches this code without anyone remembering it
    exists.
    """
    source = _MODULE.read_text(encoding="utf-8")
    assert "from scripts.utils.checkpoint_paths import transcript_dir" in source
    body = source.split('"""', 2)[-1]          # past the module docstring
    assert '.replace("/", "-")' not in body, (
        "the slug rule has a second home in scripts/utils/yard_sessions.py"
    )
    assert '".claude" / "projects"' not in body, (
        "the transcript store path is spelled out here as well as in the owner"
    )


# ============================================================
# The hook, driven as the harness drives it
# ============================================================

def _drive(cwd: Path, home: Path) -> subprocess.CompletedProcess:
    """Run the real hook the way SessionStart does: a JSON payload on stdin."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["HEADING_OS_WIZARD_QUIET"] = "1"
    for name in ("HEADING_OS_DATA", "WORKSPACE_ROOT"):
        env.pop(name, None)
    return subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps({"session_id": "t", "cwd": str(cwd),
                          "hook_event_name": "SessionStart", "source": "startup"}),
        capture_output=True, text=True, cwd=str(cwd), timeout=300, env=env,
    )


@pytest.mark.slow
def test_the_hook_names_a_silent_yard_from_the_main_clone(
        tmp_path, monkeypatch, temporary_worktree, worktree_origin):
    """End to end, at the real entry point, asserting the observable output."""
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)

    proc = _drive(worktree_origin, home)
    assert proc.returncode == 0, proc.stderr[-600:]
    assert "hold no transcript" in proc.stdout, proc.stdout[-900:]
    assert temporary_worktree.name in proc.stdout


@pytest.mark.slow
def test_the_hook_is_silent_when_every_yard_has_a_transcript(
        tmp_path, monkeypatch, temporary_worktree, worktree_origin):
    """The half that fails if the alert fires unconditionally."""
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    _write_transcript(temporary_worktree)

    proc = _drive(worktree_origin, home)
    assert proc.returncode == 0, proc.stderr[-600:]
    assert "hold no transcript" not in proc.stdout, proc.stdout[-900:]


@pytest.mark.slow
def test_the_hook_survives_a_machine_with_no_projects_directory(
        tmp_path, temporary_worktree, worktree_origin):
    """The brief's third case. The hook is an alert surface: it exits 0 and
    still delivers, and it says the state is unknown rather than clean."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    assert not (home / ".claude" / "projects").exists()

    proc = _drive(worktree_origin, home)
    assert proc.returncode == 0, proc.stderr[-600:]
    assert "Traceback" not in proc.stderr, proc.stderr[-900:]
    assert "NOT CHECKED FOR A TRANSCRIPT" in proc.stdout, proc.stdout[-900:]


@pytest.mark.slow
def test_a_yard_does_not_report_on_its_neighbours(
        tmp_path, temporary_worktree, worktree_origin):
    """HELM only. A session inside a yard prints nothing about the fleet, so the
    same list is not repeated once per open yard."""
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)

    proc = _drive(temporary_worktree, home)
    assert proc.returncode == 0, proc.stderr[-600:]
    assert "hold no transcript" not in proc.stdout, proc.stdout[-900:]
    assert "NOT CHECKED FOR A TRANSCRIPT" not in proc.stdout
