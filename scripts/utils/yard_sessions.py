#!/usr/bin/env python3
"""Which YARDs have no Claude session standing in them right now.

A YARD is provisioned by `scripts/herdr/heading-os-yard/yard-bootstrap.sh`, and
its last step asks Herdr to start an agent in the worktree's pane. That is a
BEST-EFFORT start, and it is the whole reason this module exists:

  * `herdr worktree create` opens a SHELL. It has no flag that starts an agent,
    so the agent only ever arrives through the bootstrap plugin.
  * The bootstrap's start is skipped in silence when no `PANE_ID` resolves,
    and skipped by design under `HEADING_OS_AUTOSTART=0`.
  * The `worktree.opened` subscription short-circuits on `status: ok`, so
    re-opening a YARD whose agent died never starts another one.
  * `herdr pane run` exits 0 when the command was DISPATCHED to a pane, not
    when an agent booted in it. MEASURED twice before 2026-09-06: a prompt
    addressed to a pane with no agent landed somewhere else entirely, and the
    exit code said nothing.

The result on 2026-09-06 was a YARD that looks alive in the sidebar, has nothing
to show, and gets a NEIGHBOURING yard's session rendered into its slot.

THE SIGNAL, AND THE ONE IT REPLACED

Until 2026-09-06 this asked whether `~/.claude/projects/<slug>/` held any
`*.jsonl`. A transcript is a RECORD, and a record answers a question about the
past, not about now. Both directions were measured that day and both were
wrong:

  * A freshly created yard, bootstrap at `ok/11`, agent up, `/proc/<pid>/comm`
    == `claude`, cwd == the checkout, `HEADING_OS_YARD=1` in its environment --
    and its project directory held only `memory/`, ZERO transcripts. A session
    writes nothing until it is spoken to. So every correctly provisioned yard
    was named the moment it was created, which is a false alarm delivered at
    exactly the moment everything is right, and an alarm like that teaches its
    reader to skip the line.
  * Worse in the other direction: both yards alive that day EXITED cleanly
    (code 0, seven to fourteen seconds after the focus moved), and each left
    its transcript behind. A dead yard therefore read as alive, which is the
    failure this whole module exists to report.

So the signal is a LIVE PROCESS: `comm == "claude"` with `/proc/<pid>/cwd`
EQUAL to the checkout. Equality rather than containment, because an agent runs
at the checkout root while a build, a test or a shell in a subdirectory is not
an agent. The question is owned by `scripts/utils/proc_cwd.processes_in`, which
three callers share.

The transcript remains as a FALLBACK, and only where the strong signal is
unavailable: an unreadable `/proc`. When that happens the answer is still
returned, and `unknown` carries the sentence saying the weaker, indirect signal
was used. Naming it is the point; a caller must never be able to mistake "I
looked with the blunt instrument" for "I looked".

The slug rule has ONE owner, `scripts/utils/checkpoint_paths.transcript_dir()`,
and this module calls it rather than reproducing it. A second copy of that rule
has already been caught in the push gate once
(`tests/test_transcript_dir_has_one_owner.py`). The projects ROOT is taken as
`transcript_dir(...).parent` for the same reason, so this file contains no path
literal for it either.
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from scripts.utils.checkpoint_paths import transcript_dir
from scripts.utils.clone_guard import CloneGuardError, worktree_roots
from scripts.utils.proc_cwd import processes_in

#: The kernel task name a Claude Code session runs under. `/proc/<pid>/comm` is
#: capped at 15 characters, which this is well inside.
AGENT_COMM = "claude"


class YardSessions(NamedTuple):
    """What the sweep established, and what it could not.

    `unknown` is not an error channel. It is the honest third answer, and it is
    a separate field rather than an empty `silent` list because a caller that
    reads "no silent yards" as "every yard is fine" would report a clean sweep
    over a question nobody managed to ask.

    It is NOT exclusive with the other two. When `/proc` cannot be read, the
    fallback still produces `checked` and `silent`, and `unknown` says the
    answer rests on the weaker signal. A caller that prints only one of them
    should print `unknown` too, never instead.
    """
    checked: tuple[Path, ...]
    silent: tuple[Path, ...]
    unknown: str | None


def _by_transcript(roots: list[Path]) -> tuple[list[Path], str | None]:
    """The fallback: yards with no transcript on disk. (silent, note-or-None).

    Weaker in both directions, which is why it is reached only when `/proc` is
    not. It calls a yard silent that was created a minute ago and correctly
    holds an agent that has not been spoken to, and it calls a yard alive whose
    agent exited an hour ago and left its `.jsonl` behind.
    """
    probe = transcript_dir(roots[0])
    if probe is None:
        return [], ("the transcript slug rule does not answer off POSIX, so "
                    "the fallback cannot run either")
    projects = probe.parent
    if not projects.is_dir():
        # Every yard would look silent, which is a wall of false alarms rather
        # than a finding. One honest sentence instead.
        return [], (f"the transcript store {projects} does not exist, so the "
                    f"fallback cannot run either")

    silent = []
    for root in roots:
        directory = transcript_dir(root)
        if directory is None or not directory.is_dir():
            silent.append(root)
            continue
        if not any(directory.glob("*.jsonl")):
            silent.append(root)
    return silent, None


def yards_without_a_session(path: Path | str | None = None,
                            *, exclude: tuple[Path, ...] = (),
                            proc_root: Path | str = Path("/proc")) -> YardSessions:
    """Sweep every YARD of this repository for one with no agent standing in it.

    `exclude` drops checkouts the caller can already vouch for. It matters far
    less than it did: the strong signal sees the caller's own agent as a running
    process, so a session no longer names the yard it is running in for want of
    a flushed transcript. It still applies to the fallback.

    `proc_root` is a parameter so a test can build a bench, and is deliberately
    not read from the environment: a variable naming an alternative `/proc`
    would be a way to blind this from outside the process.

    Cost: one `/proc` walk per yard, MEASURED 2026-09-06 at 0.8 ms over 76
    processes, so a machine with five yards pays about 4 ms.
    """
    excluded = set()
    for candidate in exclude:
        try:
            excluded.add(Path(candidate).resolve())
        except OSError:
            excluded.add(Path(candidate))

    try:
        roots = [r for r in worktree_roots(path) if r not in excluded]
    except CloneGuardError as exc:
        return YardSessions((), (), f"could not resolve the main clone ({exc})")

    if not roots:
        return YardSessions((), (), None)

    silent = []
    for root in roots:
        agents = processes_in(root, proc_root=proc_root, comm=AGENT_COMM,
                              nested=False)
        if agents is None:
            fallback, note = _by_transcript(roots)
            weak = (f"`{proc_root}` could not be enumerated, so no live agent "
                    f"could be seen; the yards named here were judged on the "
                    f"WEAKER, indirect signal of a transcript on disk, which a "
                    f"session that has already exited leaves behind and a "
                    f"session that has not been spoken to has not written yet")
            if note:
                return YardSessions((), (), f"{weak}, and {note}")
            return YardSessions(tuple(roots), tuple(fallback), weak)
        if not agents:
            silent.append(root)
    return YardSessions(tuple(roots), tuple(silent), None)
