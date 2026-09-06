#!/usr/bin/env python3
"""Which processes are standing in a directory, asked of the kernel.

`/proc/<pid>/cwd` is a symlink the kernel keeps current, so "is anybody in this
checkout right now?" is one readlink per process and no subprocess at all.
MEASURED 2026-09-06 on this machine: 0.8 ms over 76 processes. It scales with
the number of processes, not with the size of any tree, which is what lets a
PreToolUse hook and a SessionStart hook both call it inside their budgets.

WHY THIS IS ONE MODULE AND NOT THREE

Three callers wanted the same question within one day, each for a different
reason, and this repository's dominant defect shape is a fix that lands in one
of N copies:

  * `check_yard_deletion_guard` in `.claude/hooks/_dispatch.py` refuses to
    delete a worktree somebody is standing in;
  * `yards_without_a_session` in `scripts/utils/yard_sessions.py` asks whether a
    yard holds a LIVE agent;
  * `scripts/herdr-brief.py` asks the same before addressing a brief to one.

`None` IS NOT AN EMPTY LIST, and every caller has to handle it. An unreadable
`/proc` means "could not look", and a caller that reads that as "nobody is
there" deletes a worktree with a session in it, or reports a clean sweep over a
question nobody managed to ask. That distinction is the whole reason this
returns an Optional rather than a list.

WHAT IT DOES NOT ESTABLISH, stated here rather than left to be discovered:

  * A process the current user may not inspect answers `OSError` on the readlink
    and is SKIPPED, so a directory held open only by another user's process
    reads as empty. The kernel offers nothing else; there is no widening
    available.

    MEASURED 2026-09-06 on this machine, 73 processes: 37 answered the readlink
    and 36 raised `EACCES`, 29 of those owned by root. The skip was reviewed
    that day and DELIBERATELY LEFT AS IT IS, because the obvious improvement
    does not survive being written down. Counting the skipped ones and saying
    "36 could not be inspected" would attach a machine-wide number to a
    question about ONE directory: `EACCES` on the readlink means the cwd is
    unknown, so none of the 36 can be placed inside or outside the tree being
    asked about. A caller printing that count next to a yard's name states an
    unknown as if it were a suspicion, which is worse than the silence it
    replaces. Reporting it only when a caller already refuses would be honest
    and useless -- the caller is refusing for a reason it can name -- and there
    is nowhere else to put it, because a caller that PASSES prints nothing by
    design.

    So the honest statement is this paragraph rather than a number in a
    refusal: this sweep sees the processes of the user running it. On a
    single-user machine the set it cannot see is the system's own daemons,
    which do not stand in a checkout; on a shared one, another user's shell
    sitting in a yard is invisible here and no amount of care in this module
    changes that.
  * `comm` is the kernel's 15-character task name, so a match on it is a match
    on what the binary was called, never on who wrote it or what it is doing.
  * A process can exit between the readlink and the caller acting on the answer.
    This is a snapshot, and every caller is a warning or a refusal rather than
    a lock.

`proc_root` is a parameter and NOT an environment variable, deliberately. A test
needs a bench, and a variable naming an alternative `/proc` would be a way in
from outside the process for anything that reads it -- a guard with its own
disarm switch. A caller passes a directory it built; nothing can hand one in.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import NamedTuple


class Process(NamedTuple):
    pid: int
    comm: str
    cwd: Path
    #: The requested environment variables, or None when the environment could
    #: not be read at all. An EMPTY DICT means "read it, none of the requested
    #: names were set", which is a different fact from "could not look" and the
    #: callers act on the difference. Always empty when `env_names` was empty,
    #: because nothing was asked for.
    env: dict[str, str] | None = {}


def processes_in(root: Path | str, *, proc_root: Path | str = Path("/proc"),
                 comm: str | None = None, nested: bool = True,
                 env_names: tuple[str, ...] = ()) -> list[Process] | None:
    """Every process whose cwd is `root`, or None when `/proc` cannot be read.

    `nested=True` counts a process anywhere INSIDE `root` -- the question a
    deletion asks, since `rm -rf` takes the subdirectories too. `nested=False`
    demands equality, which is the question "is an agent standing in this
    checkout" asks: an agent runs with its cwd AT the checkout root, while a
    build or a test in a subdirectory is not one.

    `comm` filters on the kernel's task name (`/proc/<pid>/comm`), exactly.

    `env_names` reads those variables from `/proc/<pid>/environ` for each match.
    Opt-in, because it is a second read per process and most callers only need
    to know that somebody is there. It NEVER filters: a caller that wants to
    treat a value as ownership has to decide for itself what an absent one
    means, and returning the processes with `env=None` or `env={}` is what lets
    it distinguish the two.
    """
    try:
        root = Path(root).resolve()
    except OSError:
        root = Path(root)
    prefix = str(root)

    proc_root = Path(proc_root)
    try:
        entries = list(os.scandir(proc_root))
    except OSError:
        # The one case that must never look like "nobody is here".
        return None

    found: list[Process] = []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        task = proc_root / entry.name
        try:
            cwd = os.readlink(task / "cwd")
        except OSError:
            continue                   # gone, or not ours to look at
        if cwd != prefix and not (nested and cwd.startswith(prefix + os.sep)):
            continue
        name = ""
        # Suppressed, not handled: nameless is still a process standing here, and
        # the only caller that filters on the name wants an unnamed process to
        # fail that filter rather than to end the walk.
        #
        # `errors="replace"`: `comm` is bytes the kernel copied from an
        # executable's name, and a non-UTF-8 one would raise a ValueError no
        # caller here catches.
        with contextlib.suppress(OSError):
            name = (task / "comm").read_text(
                encoding="utf-8", errors="replace").strip()
        if comm is not None and name != comm:
            continue
        found.append(Process(int(entry.name), name, Path(cwd),
                             _environ(task, env_names) if env_names else {}))
    return found


def _environ(task: Path, names: tuple[str, ...]) -> dict[str, str] | None:
    """The requested variables of one process, or None if it could not be read.

    `/proc/<pid>/environ` is the environment as it was at the last `execve`, NUL
    separated, and it is readable only for a process this user owns. None is
    returned for an unreadable one rather than an empty dict, because "I could
    not look" and "the variable is not set" send a caller opposite ways and the
    first must never be delivered as the second.

    Decoded with `errors="replace"`: an environment can hold bytes that are not
    UTF-8, and a `UnicodeDecodeError` is a ValueError that the `OSError` handler
    would not catch, so it would end the caller rather than the read.
    """
    try:
        raw = (task / "environ").read_bytes()
    except OSError:
        return None
    wanted = set(names)
    found: dict[str, str] = {}
    for item in raw.split(b"\0"):
        key, sep, value = item.partition(b"=")
        if not sep:
            continue
        key_text = key.decode("utf-8", "replace")
        if key_text in wanted:
            found[key_text] = value.decode("utf-8", "replace")
    return found
