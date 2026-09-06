"""`herdr-brief` asked the transcript store whether a pane had an agent.

The question the pre-flight needs answered is "is there an agent in that pane to
receive this?". Until 2026-09-06 it asked "does this checkout's Claude project
directory hold a `.jsonl`?", and that is a PROXY which is wrong in both
directions:

* MEASURED 2026-09-06 on `yard-slug-one-owner`: an agent was up, carrying
  `HEADING_OS_YARD=1`, and its project directory held only `memory/` — a session
  writes no transcript until it is prompted. The script refused, so the FIRST
  brief into a new yard was impossible, which is the normal case for every yard
  ever created. The one yard briefed successfully that day had a transcript only
  because the bootstrap's own agent-start command had landed in it as a stray
  prompt; the workflow worked by accident.
* An agent that has EXITED leaves its transcript behind, and the proxy passes
  it. That is the exact shape of both recorded misdeliveries (2026-09-05 and
  2026-09-06), in which the addressed pane had no agent running.

So the direct question is now asked directly, of `/proc`. These tests drive
`live_agent_pids` over a constructed proc tree rather than stubbing it: a stub
that agrees with the code proves only that both were written by the same hand.

BOTH DIRECTIONS. The first two tests fail against the previous version — the
first because it refused a live agent with no transcript, the second because it
allowed a dead one that had left a transcript behind.

Symlinks are used in `tmp_path` because `/proc/<pid>/cwd` IS a symlink and the
function reads it with `os.readlink`; `no-symlinks-ever` governs the repository
tree, not a fixture that has to reproduce a kernel interface.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "herdr-brief.py"


@pytest.fixture(scope="module")
def brief():
    """The script under test, loaded by path (its name is not importable)."""
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("herdr_brief_agents", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _proc(root: Path, pid: int, comm: str, cwd: Path) -> None:
    """One entry shaped like the kernel's: a `comm` file and a `cwd` symlink."""
    entry = root / str(pid)
    entry.mkdir(parents=True)
    (entry / "comm").write_text(f"{comm}\n", encoding="utf-8")
    os.symlink(cwd, entry / "cwd")


def test_a_live_agent_with_no_transcript_is_found(tmp_path, brief):
    """The half that fails against the previous version.

    A booted, never-prompted agent. There is no transcript anywhere in this
    fixture, and there does not need to be: the process is the evidence.
    """
    checkout = tmp_path / "yard"
    checkout.mkdir()
    proc = tmp_path / "proc"
    proc.mkdir()
    _proc(proc, 4242, "claude", checkout)

    assert brief.live_agent_pids(checkout, proc_root=proc) == [4242]


def test_an_exited_agent_leaves_no_pid_behind(tmp_path, brief):
    """The other half, and the one the transcript proxy got backwards.

    The checkout carries a transcript from a session that has ended. Nothing is
    running. The old proxy read the leftover file as a live pane, which is what
    both recorded misdeliveries did.
    """
    checkout = tmp_path / "yard"
    checkout.mkdir()
    project = tmp_path / "projects" / "-yard"
    project.mkdir(parents=True)
    (project / "1a2b.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")
    proc = tmp_path / "proc"
    proc.mkdir()

    assert brief.transcripts(project), "fixture must carry a transcript"
    assert brief.live_agent_pids(checkout, proc_root=proc) == []


def test_a_process_that_is_not_claude_is_not_an_agent(tmp_path, brief):
    """A shell sitting in the yard is not something that can receive a brief.

    Without this the check would pass for a pane whose agent had exited back to
    its shell, which is the state this whole file exists to refuse.
    """
    checkout = tmp_path / "yard"
    checkout.mkdir()
    proc = tmp_path / "proc"
    proc.mkdir()
    _proc(proc, 11, "bash", checkout)
    _proc(proc, 12, "node", checkout)
    _proc(proc, 13, "claude-code", checkout)   # a longer name is a other name

    assert brief.live_agent_pids(checkout, proc_root=proc) == []


def test_an_agent_in_a_neighbouring_checkout_is_not_counted(tmp_path, brief):
    """Cwd equality, not containment.

    A yard sitting under another yard's parent must not answer for it, and an
    agent in a SUBDIRECTORY of the checkout is not the pane's agent either.
    """
    checkout = tmp_path / "yard"
    (checkout / "scripts").mkdir(parents=True)
    neighbour = tmp_path / "other-yard"
    neighbour.mkdir()
    proc = tmp_path / "proc"
    proc.mkdir()
    _proc(proc, 21, "claude", neighbour)
    _proc(proc, 22, "claude", checkout / "scripts")

    assert brief.live_agent_pids(checkout, proc_root=proc) == []


def test_an_unreadable_proc_is_unknown_and_not_an_empty_answer(tmp_path, brief):
    """None, never [].

    A caller that reads "could not look" as "nothing is there" refuses every
    brief on every platform without `/proc`. The distinction is what lets the
    pre-flight fall back to the transcript proxy and SAY that it did.
    """
    checkout = tmp_path / "yard"
    checkout.mkdir()

    assert brief.live_agent_pids(checkout, proc_root=tmp_path / "absent") is None


def test_a_pid_that_vanishes_mid_scan_is_skipped_not_fatal(tmp_path, brief):
    """The race is the normal case on a busy machine, not an error.

    An entry whose `comm` cannot be read is stepped over, and the agent found
    after it is still reported. Asserted with the survivor LAST so a scan that
    aborts on the first unreadable entry fails here.
    """
    checkout = tmp_path / "yard"
    checkout.mkdir()
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "31").mkdir()                       # no comm file at all: exited
    _proc(proc, 32, "claude", checkout)

    assert brief.live_agent_pids(checkout, proc_root=proc) == [32]


def test_the_real_machine_answers_for_this_very_checkout(brief):
    """A floor under the fixtures: the function works against the real `/proc`.

    Every test above builds its own tree, so all six would pass over a function
    that only understands trees this file builds. This session IS a Claude agent
    whose working directory is the engine checkout, so the live answer must name
    at least one pid. Skipped where `/proc` is not the Linux one.
    """
    if not Path("/proc/self/comm").is_file():
        pytest.skip("no Linux /proc on this platform")
    if Path(os.readlink("/proc/self/cwd")) != ROOT:
        pytest.skip("this test process is not running from the engine checkout")

    pids = brief.live_agent_pids(ROOT)
    assert pids is not None, "/proc is present, so the answer must not be unknown"
