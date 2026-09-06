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
whose slot FleetView renders a NEIGHBOURING yard's session.

THE SIGNAL CHANGED ON 2026-09-06, AND THIS FILE IS MOSTLY ABOUT WHY

The first version of this check asked whether `~/.claude/projects/<slug>/` held
any `*.jsonl`. A transcript is a RECORD. It answers a question about the past,
and the question is about now. Both directions were measured the day it landed
and both were wrong:

  * A freshly created yard: bootstrap at `ok/11`, agent up, `/proc/<pid>/comm`
    == `claude`, cwd == the checkout, `HEADING_OS_YARD=1` in its environment --
    and its project directory held only `memory/`, ZERO transcripts. A session
    writes nothing until it is spoken to. So the check named every correctly
    provisioned yard at the moment of its creation, which is a false alarm
    delivered exactly when everything is right, and an alarm like that teaches
    its reader to skip the line.
  * Worse the other way: both yards alive that day EXITED cleanly (code 0, seven
    to fourteen seconds after the focus moved) and each left its transcript
    behind, so a dead yard read as alive -- the failure the check exists to
    report.

The signal is now a LIVE PROCESS: `comm == "claude"` whose `/proc/<pid>/cwd` IS
the checkout. Equality, not containment: an agent runs at the checkout root, and
a build or a shell in a subdirectory is not an agent. The transcript survives as
a FALLBACK for one case only, an unreadable `/proc`, and `unknown` then says the
weaker signal was used.

HOW THE BENCH IS BUILT, and it is not incidental. A live agent is staged by
copying `/bin/sleep` to a file named `claude` and running it with its cwd in the
worktree: `/proc/<pid>/comm` is the kernel's task name, so that copy reads as
`claude` and the real `/proc` answers the real question. NOT by pointing the
sweep at a fabricated `/proc` through an environment variable -- a variable
naming an alternative `/proc` is a way into the guard from outside the process,
and the one case that does need a bench (an unreadable `/proc`) uses the
`proc_root` PARAMETER, which nothing outside a caller can set.

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
import shutil
import subprocess
import sys
import time
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


class _Agent:
    """A real process whose `/proc/<pid>/comm` is `claude`, standing in a cwd.

    A copy of `/bin/sleep` under the name `claude`: the kernel takes `comm` from
    the executable's basename, so this is indistinguishable from an agent by the
    only property the sweep reads. It is a REAL process against the REAL
    `/proc`, which is what makes the pass here evidence about the machine rather
    than about a fixture.
    """

    def __init__(self, tmp_path: Path, cwd: Path, name: str = "claude"):
        source = Path("/bin/sleep")
        if not source.exists():
            pytest.skip("no /bin/sleep to stage an agent from")
        binary = tmp_path / name
        if not binary.exists():
            shutil.copy(str(source), str(binary))
            binary.chmod(0o755)
        self.process = subprocess.Popen([str(binary), "120"], cwd=str(cwd))
        # comm is set by exec, which has not necessarily happened when Popen
        # returns. Wait for the kernel to agree before asserting anything about
        # it, so a pass is about the sweep rather than about scheduling.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                comm = Path(f"/proc/{self.process.pid}/comm").read_text().strip()
            except OSError:
                comm = ""
            if comm == name:
                return
            time.sleep(0.02)
        self.stop()
        pytest.skip(f"the staged process never reported comm=={name!r}")

    @property
    def pid(self) -> int:
        return self.process.pid

    def stop(self) -> None:
        self.process.kill()
        self.process.wait(timeout=30)


@pytest.fixture
def agent_factory(tmp_path):
    started: list[_Agent] = []

    def make(cwd: Path, name: str = "claude") -> _Agent:
        agent = _Agent(tmp_path, cwd, name)
        started.append(agent)
        return agent

    yield make
    for agent in started:
        agent.stop()


# ============================================================
# The live-process signal
# ============================================================

def test_a_yard_with_no_agent_in_it_is_named(tmp_path, monkeypatch,
                                             temporary_worktree, worktree_origin):
    """The defect, reproduced: a real worktree with nothing running in it."""
    _home(tmp_path, monkeypatch)
    report = yards_without_a_session(worktree_origin)
    assert report.unknown is None, report.unknown
    assert temporary_worktree.resolve() in report.silent, (
        f"the silent yard was not named; checked={report.checked}")


def test_a_live_agent_with_no_transcript_yet_is_not_named(
        tmp_path, monkeypatch, temporary_worktree, worktree_origin,
        agent_factory):
    """The false alarm the old signal produced, and the reason for this change.

    MEASURED 2026-09-06: a yard created minutes earlier, provisioned to `ok/11`,
    with a live agent in it, held ZERO transcripts, because a session writes
    nothing until it is spoken to. The old check named it. Naming a yard at the
    exact moment everything about it is correct is how an alert stops being
    read.
    """
    home = _home(tmp_path, monkeypatch)
    (home / ".claude" / "projects").mkdir(parents=True)
    assert not any(transcript_dir(temporary_worktree).glob("*.jsonl")) \
        if transcript_dir(temporary_worktree).is_dir() else True

    agent_factory(temporary_worktree)
    report = yards_without_a_session(worktree_origin)
    assert report.unknown is None, report.unknown
    assert report.silent == (), (
        f"a yard holding a live agent was named: {report.silent}")
    assert temporary_worktree.resolve() in report.checked, (
        "the yard was not examined at all, so the clean answer means nothing")


def test_an_exited_agent_that_left_its_transcript_is_named(
        tmp_path, monkeypatch, temporary_worktree, worktree_origin):
    """The worse half, and the one the old signal got backwards.

    Both yards alive on 2026-09-06 exited cleanly and left their transcripts on
    disk. A check reading the transcript calls that yard alive, which is the
    exact state this module exists to report.
    """
    _home(tmp_path, monkeypatch)
    _write_transcript(temporary_worktree)

    report = yards_without_a_session(worktree_origin)
    assert report.unknown is None, report.unknown
    assert temporary_worktree.resolve() in report.silent, (
        "a yard whose agent exited was read as alive because the transcript "
        "it left behind is still on disk")


def test_a_process_in_a_subdirectory_is_not_an_agent(
        tmp_path, monkeypatch, temporary_worktree, worktree_origin,
        agent_factory):
    """Equality, not containment. An agent runs AT the checkout root.

    Containment would clear a yard because a test runner, a build or a stray
    shell happens to sit in one of its subdirectories.
    """
    _home(tmp_path, monkeypatch)
    inside = temporary_worktree / "scripts"
    inside.mkdir(exist_ok=True)
    agent_factory(inside)

    report = yards_without_a_session(worktree_origin)
    assert temporary_worktree.resolve() in report.silent, (
        "a process in a subdirectory was counted as the yard's agent")


def test_a_process_that_is_not_an_agent_does_not_clear_the_yard(
        tmp_path, monkeypatch, temporary_worktree, worktree_origin,
        agent_factory):
    """Same cwd, different `comm`. A shell standing in a yard is not a session."""
    _home(tmp_path, monkeypatch)
    agent_factory(temporary_worktree, name="not-an-agent")

    report = yards_without_a_session(worktree_origin)
    assert temporary_worktree.resolve() in report.silent, (
        "any process at all was counted as an agent")


# ============================================================
# The fallback, and that it announces itself
# ============================================================

def test_an_unreadable_proc_falls_back_and_says_the_signal_was_weaker(
        tmp_path, monkeypatch, temporary_worktree, worktree_origin):
    """`/proc` gone: the answer still comes, and it is labelled.

    Silence here would be the defect one level up -- an answer resting on the
    signal that was just measured wrong, presented as if it rested on the one
    that replaced it.
    """
    home = _home(tmp_path, monkeypatch)
    (home / ".claude" / "projects").mkdir(parents=True)

    report = yards_without_a_session(worktree_origin,
                                     proc_root=tmp_path / "no-proc-here")
    assert report.unknown, "the fallback did not announce itself"
    assert "WEAKER" in report.unknown
    assert temporary_worktree.resolve() in report.silent, (
        "the fallback produced no answer at all")


def test_the_fallback_clears_a_yard_that_has_a_transcript(
        tmp_path, monkeypatch, temporary_worktree, worktree_origin):
    """The paired direction: the weak signal is still a signal, not a refusal."""
    _home(tmp_path, monkeypatch)
    _write_transcript(temporary_worktree)

    report = yards_without_a_session(worktree_origin,
                                     proc_root=tmp_path / "no-proc-here")
    assert report.unknown, "the caveat was dropped"
    assert report.silent == (), f"a yard with a transcript was named: {report.silent}"


def test_an_absent_projects_store_under_the_fallback_is_unknown_not_clean(
        tmp_path, monkeypatch, temporary_worktree, worktree_origin):
    """Neither signal available: not a clean sweep, and not a wall of alarms.

    Naming every yard would be a false alarm on a machine where the store is
    simply not there yet. Reporting clean would be the worse half of the same
    mistake: a caller reading "no silent yards" as "the fleet is fine" over a
    question nobody managed to ask.
    """
    home = _home(tmp_path, monkeypatch)
    assert not (home / ".claude" / "projects").exists()

    report = yards_without_a_session(worktree_origin,
                                     proc_root=tmp_path / "no-proc-here")
    assert report.unknown, "both signals were unavailable and nothing said so"
    assert str(home / ".claude" / "projects") in report.unknown
    assert report.silent == (), "yards were named over a store that does not exist"


# ============================================================
# Properties that survived the change
# ============================================================

def test_the_callers_own_checkout_is_never_named(tmp_path, monkeypatch,
                                                 temporary_worktree, worktree_origin):
    """`exclude` still drops a checkout the caller can vouch for itself."""
    home = _home(tmp_path, monkeypatch)
    (home / ".claude" / "projects").mkdir(parents=True)

    report = yards_without_a_session(
        worktree_origin, exclude=(temporary_worktree,))
    assert temporary_worktree.resolve() not in report.silent
    assert temporary_worktree.resolve() not in report.checked


def test_a_registration_whose_checkout_is_gone_is_not_a_yard(
        tmp_path, monkeypatch, temporary_worktree, worktree_origin,
        agent_factory):
    """`git worktree remove` can half-fail and leave the registration behind.
    A pointer to a directory that no longer exists is not a yard without a
    session; it is not a yard. Reporting it would train the operator to ignore
    the whole alert."""
    _home(tmp_path, monkeypatch)
    agent_factory(temporary_worktree)

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


def test_the_proc_question_has_one_owner():
    """Three callers wanted it in one day; one module answers it.

    A second copy is the one that stops being fixed, and this repository's
    dominant defect shape is a fix that landed in one of N copies.

    Asked of the AST rather than of the text. A substring scan for the path goes
    red the moment a comment quotes it to explain what the module reads, which
    teaches people to stop explaining; the question is whether this module
    CALLS the primitives, and only the tree can answer that.
    """
    import ast

    source = _MODULE.read_text(encoding="utf-8")
    assert "from scripts.utils.proc_cwd import processes_in" in source
    called = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    walking = called & {"scandir", "readlink", "listdir"}
    assert not walking, (
        f"yard_sessions walks the process table itself ({sorted(walking)}) "
        f"instead of asking the owner")


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
def test_the_hook_names_a_yard_with_no_session_from_the_main_clone(
        tmp_path, monkeypatch, temporary_worktree, worktree_origin):
    """End to end, at the real entry point, asserting the observable output."""
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)

    proc = _drive(worktree_origin, home)
    assert proc.returncode == 0, proc.stderr[-600:]
    assert "no Claude session standing in them" in proc.stdout, proc.stdout[-900:]
    assert temporary_worktree.name in proc.stdout


@pytest.mark.slow
def test_the_hook_is_silent_when_a_live_agent_stands_in_every_yard(
        tmp_path, monkeypatch, temporary_worktree, worktree_origin,
        agent_factory):
    """The half that fails if the alert fires unconditionally.

    No transcript is written here on purpose: this is the freshly-created yard
    the old signal named, driven through the real hook against the real
    `/proc`.
    """
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    agent_factory(temporary_worktree)

    proc = _drive(worktree_origin, home)
    assert proc.returncode == 0, proc.stderr[-600:]
    assert "no Claude session standing in them" not in proc.stdout, \
        proc.stdout[-900:]


@pytest.mark.slow
def test_the_hook_survives_a_machine_with_no_projects_directory(
        tmp_path, temporary_worktree, worktree_origin):
    """The transcript store is gone and the live signal does not need it.

    Before 2026-09-06 an absent store meant the sweep could answer nothing at
    all. It is now only the fallback's input, so the hook still reports the
    yard, and it still exits 0 with no traceback.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    assert not (home / ".claude" / "projects").exists()

    proc = _drive(worktree_origin, home)
    assert proc.returncode == 0, proc.stderr[-600:]
    assert "Traceback" not in proc.stderr, proc.stderr[-900:]
    assert temporary_worktree.name in proc.stdout, proc.stdout[-900:]


@pytest.mark.slow
def test_a_yard_does_not_report_on_its_neighbours(
        tmp_path, temporary_worktree, worktree_origin):
    """HELM only. A session inside a yard prints nothing about the fleet, so the
    same list is not repeated once per open yard."""
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)

    proc = _drive(temporary_worktree, home)
    assert proc.returncode == 0, proc.stderr[-600:]
    assert "no Claude session standing in them" not in proc.stdout, \
        proc.stdout[-900:]
    assert "NOT CHECKED FOR A LIVE AGENT" not in proc.stdout
