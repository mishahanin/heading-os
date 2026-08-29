#!/usr/bin/env python3
"""One destination for every wreck file: a `.quarantine/` sibling directory.

A reader that finds a state file unreadable moves it aside rather than letting
the next write erase it. That is right, and the way it was done was not: each
site built the wreck's name from the live file's name, so the wreck landed in
the live directory wearing a name nobody had ignored.

MEASURED 2026-08-29 with `git check-ignore` in the data overlay:

    outputs/operations/action-queue/queue.json                      IGNORED
    outputs/operations/action-queue/queue.json.corrupt-<stamp>      NOT-IGNORED
    outputs/operations/email-intelligence/state.json                IGNORED
    outputs/operations/email-intelligence/state.json.corrupt-<stamp> NOT-IGNORED

`queue.json` is ignored because it carries pending gated cards: recipient
addresses, subjects and whole drafted email bodies. `push-all.py` commits with
`git add -A`, so the first corrupt-queue event would have put un-sent draft
bodies into the private repo's permanent history. The quarantine is reached from
the READ path (`list_action_queue`, i.e. one `GET /action-queue`), not only from
a write, so a single authenticated read was enough to create one.

The fix is a mechanism rather than a list of names. A wreck goes into a
`.quarantine/` directory beside its original, and `.quarantine/` is ignored whole
in both repositories, so the rule holds for a state file nobody has thought of
yet. Two writers already had it right and are the precedent: `checkpoint-save.py`
puts an unredacted handoff in `outputs/operations/handoff-archive/.quarantine/`,
and `fireside-bot.py` writes its schedule backup inside a directory that is
ignored whole. Both are safe today for the same reason.

A `.gitignore` suffix rule (`outputs/**/*.corrupt-*`) was the other candidate and
is weaker on its own: it covers one spelling of "wreck", it lives in the data
overlay only, and the next writer that reaches for `.bak` or `.broken` escapes
it. The directory rule is on the writing side, where the name is decided. The
overlay and the engine each carry `**/.quarantine/` as the belt.

`tests/test_a_wreck_file_that_no_gitignore_rule_matched.py` holds all of it,
including a sweep that fails on a new writer which builds a wreck name itself.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# The one directory name. Both `.gitignore` files spell it, so it is not a
# caller's choice: a site that picks its own name is the defect coming back.
QUARANTINE_DIRNAME = ".quarantine"


def quarantine_dir(path) -> Path:
    """The `.quarantine/` directory that holds wrecks of `path`."""
    return Path(path).parent / QUARANTINE_DIRNAME


def quarantine_target(path, kind: str = "corrupt", *, now=None) -> Path:
    """An unused path inside `quarantine_dir(path)` for this wreck.

    The stamp is UTC and seconds-resolution, so two failures inside one second
    would collide; the `-2`, `-3` tail is what keeps the second wreck from
    silently replacing the first, which is the failure the quarantine exists to
    prevent, one level down.
    """
    path = Path(path)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    directory = quarantine_dir(path)
    target = directory / f"{path.name}.{kind}-{stamp}"
    n = 2
    while target.exists():
        target = directory / f"{path.name}.{kind}-{stamp}-{n}"
        n += 1
    return target


def quarantine_file(path, kind: str = "corrupt", *, now=None) -> Path:
    """Move `path` into its `.quarantine/` sibling and return where it landed.

    Raises `OSError` if the move fails, exactly as the `rename`/`os.replace` it
    replaces did. A caller that has something better to do than crash catches it
    and says so; none of them may swallow it silently.
    """
    path = Path(path)
    target = quarantine_target(path, kind, now=now)
    target.parent.mkdir(parents=True, exist_ok=True)
    # A wreck inherits the sensitivity of the file it came from -- queue.json is
    # written 0600 because it carries draft bodies and recipients -- and
    # `os.replace` preserves the file's own mode. The directory is narrowed too,
    # best-effort: on a filesystem that refuses chmod the move still happens,
    # because losing the bytes is the worse outcome.
    try:
        os.chmod(target.parent, 0o700)
    except OSError as exc:
        logger.warning("could not narrow %s to 0700 (%s); the wreck about to be "
                       "written there may be readable by other local users",
                       target.parent, exc)
    os.replace(path, target)
    return target


def quarantine_ref(target) -> str:
    """`.quarantine/<name>`, for a log line that must not print a full path."""
    target = Path(target)
    return f"{target.parent.name}/{target.name}"
