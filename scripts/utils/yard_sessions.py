#!/usr/bin/env python3
"""Which YARDs have never had a Claude session in them.

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
to show, and gets a NEIGHBOURING yard's session rendered into its slot. Nothing
on the machine asked the one question that settles it.

THE QUESTION, AND THE ONLY EVIDENCE THAT ANSWERS IT

Not a status file, not a pane, not an exit code: a transcript. Claude Code writes
one `<session-id>.jsonl` per session under `~/.claude/projects/<slug>/`, so zero
`.jsonl` files under a checkout's own slug means no agent has ever run there.

The slug rule has ONE owner, `scripts/utils/checkpoint_paths.transcript_dir()`,
and this module calls it rather than reproducing it. A second copy of that rule
has already been caught in the push gate once
(`tests/test_transcript_dir_has_one_owner.py`), and two more copies that disagree
with the owner are live in the tree today; see that test's own report. The
projects ROOT is taken as `transcript_dir(...).parent` for the same reason, so
this file contains no path literal for it either.
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from scripts.utils.checkpoint_paths import transcript_dir
from scripts.utils.clone_guard import CloneGuardError, worktree_roots


class YardSessions(NamedTuple):
    """What the sweep established, and what it could not.

    `unknown` is not an error channel. It is the honest third answer, and it is
    a separate field rather than an empty `silent` list because a caller that
    reads "no silent yards" as "every yard is fine" would report a clean sweep
    over a question nobody managed to ask.
    """
    checked: tuple[Path, ...]
    silent: tuple[Path, ...]
    unknown: str | None


def yards_without_a_session(path: Path | str | None = None,
                            *, exclude: tuple[Path, ...] = ()) -> YardSessions:
    """Sweep every YARD of this repository for one with no transcript.

    `exclude` drops checkouts the caller can already vouch for. The caller's OWN
    checkout belongs there whenever this runs inside a session: that session's
    transcript may not have been flushed yet at SessionStart, so the sweep would
    otherwise name the very yard it is running in.
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

    # The projects ROOT, asked of the owner rather than spelled out here: the
    # argument only decides the leaf name, and the parent is the same directory
    # for every checkout.
    probe = transcript_dir(roots[0])
    if probe is None:
        return YardSessions((), (), "the transcript slug rule does not answer "
                                    "off POSIX, so no yard can be checked here")
    projects = probe.parent
    if not projects.is_dir():
        # Every yard would look silent, which is a wall of false alarms rather
        # than a finding. One honest sentence instead.
        return YardSessions((), (), f"the transcript store {projects} does not "
                                    f"exist, so no yard can be checked")

    silent = []
    for root in roots:
        directory = transcript_dir(root)
        if directory is None or not directory.is_dir():
            silent.append(root)
            continue
        if not any(directory.glob("*.jsonl")):
            silent.append(root)
    return YardSessions(tuple(roots), tuple(silent), None)
