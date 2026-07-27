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

from datetime import datetime, timezone
from pathlib import Path
from typing import Collection, Optional, Sequence

from scripts.utils.canopus_git import git_output
from scripts.utils.colors import BOLD, RED, RESET, YELLOW


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


def _names(raw) -> list[str]:
    """Names out of one process field, tolerating a damaged record.

    A dict is read for its keys (the plugin map, whose values are origins this
    page deliberately never renders) and sorted; a sequence keeps the order the
    recorder chose. Anything else answers "nothing" rather than raising: the
    record is a JSON file an operator can hand-edit, this page is read at the
    one moment they decide to keep the work, and a TypeError there is worse
    than a missing row.
    """
    if isinstance(raw, dict):
        return sorted(str(name) for name in raw)
    if isinstance(raw, (list, tuple)):
        return [str(name) for name in raw]
    return []


def render_process(process: Optional[dict], frozen_paths: Collection[str]) -> str:
    """The interpreter the attestation speaks for, rendered for the sign-off page.

    The record answers "which plugins configured this run" and nothing prints
    that answer: the status lines report the VERDICT, and a verdict an operator
    cannot trace back to a plugin name is one they can only believe or ignore.

    Plugin ORIGINS are deliberately not rendered. The record maps each compared
    identity to one representative origin, and for a distribution plugin that
    origin is an absolute path inside the operator's virtualenv. This page is
    read at sign-off and pasted into artifacts that get committed, so the
    identity goes on the page and the origin stays in the record.

    The in-tree entries are the ones the comparison leaves alone, and the mark
    on each is the only place the residual gap is visible: an in-tree plugin
    that is neither frozen nor compared is exactly the thing this wire cannot
    catch, and a page that omits it reads as a clean bill of health. The
    `anon:`/`name:` entries and the per-worker sets are here for the same
    reason and were measured missing: the record keeps both, `process_facts`
    justifies keeping `other_plugins` on the ground that a reader of THIS page
    would otherwise not know they were there, and until this row existed that
    rationale described something the page never printed.

    Pure string work over a dict, so it neither reads disk nor raises on a
    damaged record; every field goes through `_names`, which answers "nothing"
    for a value of the wrong type rather than iterating it. A record with no
    process block reads as damage rather than as innocence, matching
    `build_attestation`.
    """
    lines = [f"\n{BOLD}interpreter{RESET}"]
    if not isinstance(process, dict):
        lines.append(f"  {YELLOW}nothing recorded what configured this run{RESET}")
        return "\n".join(lines)

    lines.append(f"  launcher   {process.get('launcher') or 'bare'}")
    compared = _names(process.get("plugins"))
    lines.append(f"  compared   {', '.join(compared) if compared else 'none'}")
    env = _names(process.get("env_configured"))
    if env:
        lines.append(f"  env        {', '.join(env)}")
    option = _names(process.get("option_plugins"))
    if option:
        # NOT "someone typed -p". This is the PARSED option, which is the same
        # value whether the name arrived on argv, through PYTEST_ADDOPTS, or
        # from an ini `addopts` (`canopus_gate.process_facts`), and a row
        # labelled `-p` alone sends an operator hunting a command line that may
        # never have carried it.
        lines.append(f"  plugin opt {', '.join(option)}  "
                     f"(parsed: argv, PYTEST_ADDOPTS or an ini addopts alike)")
    frozen = set(frozen_paths)
    for path in _names(process.get("intree_plugins")):
        mark = "frozen" if str(path) in frozen else f"{RED}NOT FROZEN{RESET}"
        lines.append(f"  in-tree    {path}  {mark}")
    other = _names(process.get("other_plugins"))
    if other:
        # `canopus_gate.process_facts` records these on the stated ground that a
        # reader of THIS page would otherwise not know they were there, so the
        # page has to carry them or that rationale is false.
        lines.append(f"  other      {', '.join(other)}  (recorded, never compared)")
    workers = process.get("workers")
    if isinstance(workers, (list, tuple)) and workers:
        # A count and a spread, not sixteen plugin lists. RECORDED, not
        # "compared": `build_attestation` runs its worker loop only where the
        # freeze captured a plugin baseline, so on a baseline-less freeze under
        # xdist a row promising a comparison would assert one that never ran.
        # A worker that does disagree prints its own symmetric difference as a
        # reason; what this row adds is that a parallel run was described at all.
        # A worker whose record is not a sequence or a mapping is UNREADABLE, and
        # the spread is computed over the readable ones only. Folding damage
        # through `_names` (which answers "nothing" for the wrong type) produced
        # two false readings, both measured: three unreadable workers printed
        # "1 distinct plugin set recorded", the string two AGREEING workers
        # print; and one unreadable worker beside a sound one printed
        # "2 distinct plugin sets", the string a real plugin MISMATCH prints.
        # The second is the expensive one. It sends an operator hunting a
        # divergence between interpreters that never happened.
        readable = [worker for worker in workers
                    if isinstance(worker, (list, tuple, dict))]
        distinct = {tuple(_names(worker)) for worker in readable}
        plural = "" if len(distinct) == 1 else "s"
        row = (f"  workers    {len(workers)}, {len(distinct)} distinct plugin "
               f"set{plural} recorded")
        unreadable = len(workers) - len(readable)
        if unreadable:
            row += f", {RED}{unreadable} unreadable{RESET}"
        lines.append(row)
    lines.append(
        "  The comparison covers distribution plugins and the in-tree ones pytest "
        "did not\n  import by collection; a collected in-tree conftest is listed "
        "here as provenance\n  and never compared. That exemption reads a private "
        "pytest attribute and fails\n  closed, comparing, if the attribute ever "
        "disappears."
    )
    return "\n".join(lines)
