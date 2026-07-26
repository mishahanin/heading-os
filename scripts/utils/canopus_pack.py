#!/usr/bin/env python3
"""Assemble the Canopus Fix 2 evidence page.

The standard says the second human decision is taken on evidence, not on a
summary: a green summary alone is a rubber stamp, and rubber stamps are how a
two-fix standard becomes a one-fix standard.

Two facts here exist nowhere else.

  * CONTINUITY. The lock says the contract is intact NOW. It says nothing about
    whether the lock was held while the work happened. Commits whose timestamps
    fall outside every freeze window in the ledger are listed, so "released it,
    finished the work, froze it again" is visible rather than invisible.
  * STALENESS. An attestation binds to the frozen CONTRACT, not to the
    implementation. Green, then an edit to the implementation, leaves ATTESTED
    standing. Comparing the attestation's timestamp against the working tree and
    the commit log answers the question the record cannot.

Staleness is computed from git rather than from file modification times, because
`git checkout` rewrites mtimes and a false alarm in a discipline tool costs more
than the gap it closes.

Reads git through canopus_git, and is therefore never imported by the PreToolUse
dispatcher.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from scripts.utils.canopus_freeze import history_state_path
from scripts.utils.canopus_git import git_output


def parse_ts(raw) -> Optional[datetime]:
    """An ISO-8601 timestamp as an AWARE datetime, or None.

    A damaged line is skipped, never fatal. The tz normalisation is not cosmetic:
    every comparison downstream mixes ledger timestamps with git's `%cI`, which
    is always offset-aware, and comparing an aware datetime against a naive one
    raises TypeError. append_history only ever writes aware timestamps, so the
    naive case arrives from a hand-edited or third-party ledger, exactly the
    damaged input this reader is built to tolerate, and the one place where the
    pack's promise to answer rather than raise would otherwise break with a
    traceback main() does not catch.
    """
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def read_ledger(root: Path) -> list[dict]:
    """Every readable line of the append-only ledger, oldest first.

    Damaged lines are skipped rather than raising: the ledger is evidence, and a
    reader that refuses to show the other nine entries because one is corrupt is
    less useful than one that shows nine.
    """
    path = history_state_path(root)
    if not path.is_file():
        return []
    entries: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def freeze_windows(entries: Sequence[dict]) -> list[tuple[datetime, Optional[datetime]]]:
    """Pair each `freeze` with the `release` that ended it.

    An open window (frozen, never released) ends in None and extends to now,
    which is the ordinary state at Fix 2.
    """
    windows: list[tuple[datetime, Optional[datetime]]] = []
    start: Optional[datetime] = None
    for entry in entries:
        when = parse_ts(entry.get("ts"))
        if when is None:
            continue
        event = entry.get("event")
        if event == "freeze":
            start = when
        elif event in ("release", "force_release") and start is not None:
            windows.append((start, when))
            start = None
    if start is not None:
        windows.append((start, None))
    return windows


def commits_outside(
    commits: Sequence[tuple[str, datetime, str]],
    windows: Sequence[tuple[datetime, Optional[datetime]]],
) -> list[tuple[str, datetime, str]]:
    """Commits made while no freeze was held."""
    outside = []
    for sha, when, subject in commits:
        held = any(
            start <= when and (end is None or when < end) for start, end in windows
        )
        if not held:
            outside.append((sha, when, subject))
    return outside


def git_commits(root: Path, base: str) -> list[tuple[str, datetime, str]]:
    """(sha, committed-at, subject) for every commit after *base*, oldest first."""
    out = git_output(root, "log", "--reverse", "--format=%h%x1f%cI%x1f%s", f"{base}..HEAD")
    if not out:
        return []
    commits = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3:
            continue
        when = parse_ts(parts[1])
        if when is not None:
            commits.append((parts[0], when, parts[2]))
    return commits


def merge_base(root: Path, ref: str) -> Optional[str]:
    out = git_output(root, "merge-base", ref, "HEAD")
    return out.strip() if out else None


def is_dirty(root: Path) -> bool:
    """True when `git status --porcelain` reports anything at all.

    That includes UNTRACKED files, not only modified tracked ones, and the
    staleness section reads it as "the working tree has uncommitted changes". The
    wider reading is the right one here: a new untracked implementation file is
    exactly the thing that can make an attestation stale, and the pack reports
    rather than blocks, so the cost of the wider net is a sentence an operator
    reads and dismisses.
    """
    out = git_output(root, "status", "--porcelain")
    return bool(out and out.strip())


def diff_stat(root: Path, base: str) -> str:
    return (git_output(root, "diff", "--stat", f"{base}..HEAD") or "").rstrip()
