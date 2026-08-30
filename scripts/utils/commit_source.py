#!/usr/bin/env python3
"""Commit messages as index rows: `git log` in, `notes`-shaped dicts out.

Commit messages are the largest body of written reasoning in this workspace, and
until 2026-08-21 nothing retrieved them -- no FTS index, no vector, no CodeGraph
edge. `git log --grep` is exact-substring only, so "почему мы отказались от
Postgres" finds nothing. This module is the source half of the fix; the store
half already exists (`memory-index.py` `upsert_note`), which is why no schema
changes.

Design spec: `docs/superpowers/specs/2026-08-21-semantic-index-commits-and-symbols-design.md`
(private overlay). Contract: `tests/test_commit_source.py`.

Three behaviours carry the risk:

**The air gap is whole-commit, not per-path.** A commit touching ANY denied path
is skipped entirely -- message, paths, everything. Indexing the message of a
personal change leaks the change even when the path is dropped, so a partial row
is not a safer row. Denial uses `scripts/utils/air_gap.is_denied`, whose
hard-coded `personal` segment and `_secure/` prefix apply whatever the config
says.

**Backup commits are excluded.** Measured 2026-08-21: 153 of 1,257 commits are
`chore: workspace backup <date>` -- 26 engine, 127 data. On the data side that is
a fifth of all history. They answer no question, and their near-identical vectors
cluster and crowd out real hits. The pattern is anchored, so `fix: undo the
workspace backup regression` is kept.

**`since` fails toward a full walk.** An unknown sha (history rewritten, shallow
clone, wrong repo) rebuilds everything rather than silently indexing nothing.
A stale index that reads as fresh is the worse failure.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterator

from scripts.utils.air_gap import is_denied

# Anchored on purpose: only a subject that STARTS this way is noise.
BACKUP_SUBJECT_RE = re.compile(r"^chore: workspace backup\b")

# Field and record separation.
#
# The comment here used to claim these two bytes "are the only delimiters git
# will not find inside the fields it is emitting". That is false: a commit
# object may hold any byte except NUL, so `%s` and `%b` can carry `\x1f` and
# `\x1e` verbatim, and git emits them verbatim. MEASURED 2026-08-30 on a repo
# with `git commit -m "$(printf 'subject\n\nbefore \x1f after')"` - the indexed
# body came back `'before'`, with everything past the separator silently
# dropped. A `\x1e` in a body did the same and additionally split one commit's
# record in two, so the tail fragment failed the field-count guard and was
# skipped: silent loss of an adjacent commit.
#
# NUL is the byte git genuinely cannot emit inside a field, so `-z` is the real
# record separator and `_RS` is now only a marker inside a NUL-delimited entry
# (`_changed_paths` uses it to tell a commit header from a path). `_FS` stays,
# with `maxsplit=3` at the one parse so a separator inside the body cannot shift
# a field or shorten the record.
_FS = "\x1f"
_RS = "\x1e"
_FORMAT = f"%H{_FS}%at{_FS}%s{_FS}%b"


def _run(repo: Path, args: list[str]) -> str:
    # `core.quotePath` defaults ON, so git wraps any path holding a non-ASCII
    # byte in double quotes and C-escapes it: `"_secure/x/\321\204.md"`. That
    # leading quote defeats `air_gap.is_denied`, which matches deny PREFIXES
    # with `startswith` -- so the hard-coded vault prefix, and every prefix a
    # caller passes from config, silently stopped applying to exactly those
    # paths, and the commit was indexed message and all. Turning the quoting off
    # is one flag and removes the class, rather than teaching one call site to
    # un-escape.
    # Bytes, decoded here, rather than `text=True`. That is a SECOND defect and
    # the quoting fix above does not reach it: any subprocess text mode turns on
    # universal newlines, which rewrites every CR byte to LF, and `subprocess`
    # has no `newline=` knob to switch it off. MEASURED 2026-08-30 on a scratch
    # repo holding two files whose names differ only by that byte: bytes mode
    # returned two records, `text=True` returned one. `-z` closes the quoting
    # half only, so a reader can carry `-z` and still be wrong.
    #
    # The consequence here is the smaller one in that sweep, and it is named
    # rather than implied: the air-gap decision does not flip, because the deny
    # prefixes and segments survive a same-length CR to LF substitution. What
    # breaks is the indexed record, whose `Files:` line then carries a filename
    # that names nothing on disk.
    proc = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=repo, capture_output=True
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "surrogateescape").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr}")
    return proc.stdout.decode("utf-8", "surrogateescape")


def _is_repo(repo: Path) -> bool:
    if not repo.is_dir():
        return False
    proc = subprocess.run(
        # No `text=`: this reads the exit code and never looks at stdout, so a
        # text mode here would only be a decoration that the CR-translation
        # sweep has to stop and reason about again next time.
        ["git", "rev-parse", "--git-dir"], cwd=repo, capture_output=True
    )
    return proc.returncode == 0


def _known(repo: Path, sha: str) -> bool:
    proc = subprocess.run(
        # No `text=`, same reason as `_is_repo`: only the exit code is read.
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=repo, capture_output=True,
    )
    return proc.returncode == 0


def _changed_paths(repo: Path, revs: list[str]) -> dict[str, list[str]]:
    """{sha: [changed path, ...]} for the whole walk, in ONE git call.

    Per-commit `--name-only` calls would be 1,100 subprocess spawns. This is one.
    `-m` expands merges so a merge's paths are seen; without it a merge reports
    none and would slip past the air gap.

    `-z`, because `core.quotePath=false` does NOT do what `_run`'s comment
    assumed. It suppresses quoting for NON-ASCII bytes only; a path holding a
    CONTROL character is quoted and C-escaped whatever that setting says. This
    parsed newline-separated output, so such a path arrived as the literal
    `"_secure/leak\\na.md"` - quotes included - and MEASURED 2026-08-30
    `is_denied('"_secure/leak\\na.md"')` returned **False** while
    `is_denied("_secure/leak\\na.md")` returned True. The leading quote defeats
    the `startswith` prefix match, which is precisely the air-gap bypass
    `_run`'s comment says was closed: the vault path went into `changed` and
    into `embed_text`, and the gate reported nothing withheld. `-z` emits every
    path verbatim and NUL-separated, so there is nothing to quote and nothing
    for `splitlines()` to cut in half.

    Entries are NUL-delimited. A commit header is the entry the format marks
    with a leading `_RS`; every other entry is one path.
    """
    out = _run(repo, ["log", "-m", "-z", "--name-only", f"--format={_RS}%H", *revs])
    paths: dict[str, list[str]] = {}
    sha = None
    for entry in out.split("\0"):
        entry = entry.lstrip("\n")
        if not entry:
            continue
        if entry.startswith(_RS):
            sha = entry[1:].strip()
            # `-m` emits one block per parent; seed once and union below so
            # nothing is missed.
            if sha:
                paths.setdefault(sha, [])
            continue
        # A path is taken whole. No `.strip()`: a filename may legally begin or
        # end with whitespace, and trimming it produces a path that matches
        # neither the deny prefixes nor anything on disk.
        if sha and entry not in paths.get(sha, []):
            paths[sha].append(entry)
    return paths


def iter_commits(
    repo: Path,
    *,
    repo_label: str,
    since: str | None = None,
    include_paths: bool = True,
    deny_prefixes: tuple = (),
    deny_segments: tuple = (),
    stats: dict | None = None,
) -> Iterator[dict]:
    """Yield one dict per indexable commit, newest first.

    `repo_label` names the side of the engine/data seam ("engine" / "data") and
    appears in `id` and `path`, so rows from the two repositories can never
    collide on a sha that exists in both.

    `include_paths` is the body variant the spec calls for measuring: with the
    changed-path list, "what touched the action queue" is answerable by meaning;
    without it, the vector is not diluted by filenames. Build both, measure both.

    `stats` is how the air gap reports back. The refusal happens in here, so a
    caller that counts its OWN denials counts zero for this walk and prints it
    beside the ones it did see. `memory-index.py` printed "0 denied" for a pass
    that refused commits, which reads as "nothing was withheld".
    """
    repo = Path(repo)
    if not _is_repo(repo):
        raise ValueError(f"not a git repository: {repo}")

    if not _known(repo, "HEAD"):
        return  # a repo with no commits yet: nothing to walk, not an error

    revs = ["HEAD"]
    if since and _known(repo, since):
        revs = [f"{since}..HEAD"]

    raw = _run(repo, ["log", "-z", f"--format={_FORMAT}", *revs])
    records = [r for r in raw.split("\0") if r.strip()]
    if not records:
        return

    path_map = _changed_paths(repo, revs)

    for rec in records:
        # `maxsplit=3`, so a `\x1f` inside the BODY -- the last field, and the
        # only one long enough to hold arbitrary prose -- stays in the body
        # instead of becoming a fifth part that truncates it.
        parts = rec.lstrip("\n").split(_FS, 3)
        if len(parts) < 4:
            continue
        sha, at, subject, body = parts[0].strip(), parts[1], parts[2], parts[3]
        if BACKUP_SUBJECT_RE.match(subject):
            continue

        changed = path_map.get(sha, [])
        # Whole-commit denial. A commit with NO paths (empty or a plain merge)
        # cannot be denied by path, and is kept -- `any()` on [] is False.
        if any(is_denied(p, deny_prefixes, deny_segments) for p in changed):
            if stats is not None:
                stats["denied"] = stats.get("denied", 0) + 1
            continue

        text = body.strip()
        if include_paths and changed:
            text = (text + "\n\nFiles: " + " ".join(changed)).strip()

        yield {
            "sha": sha,
            "id": f"commit:{repo_label}:{sha}",
            "path": f"{repo_label}@{sha}",
            "title": subject,
            "ntype": "commit",
            "mtime": float(at),
            "body": text,
            "embed_text": f"{subject}\n{text}".strip(),
            "changed": changed,
        }
