#!/usr/bin/env python3
"""What a push will actually send from HISTORY, as opposed to what is on disk now.

THE DEFECT THIS MODULE EXISTS FOR. Every wall in `scripts/push-all.py` decided
what to inspect from the PRESENT state of the clone -- the working tree, the
index, and a two-endpoint `origin/main..HEAD` diff -- and then read the bytes
back off the disk. A push does not send the present state. It sends every object
the commits carry, including the version of a file that an intermediate commit
introduced and a later edit removed. MEASURED on a real repository with a real
bare remote, 2026-08-29 (`.tmp/audit/measure61.py`):

  a secret committed with `--no-verify`, then wiped from the working tree
    -> the wall listed the file, read the CLEANED bytes off the disk,
       "No secrets detected.", exit 0, and the push shipped the commit.
  commit A adds the secret, commit B removes it, both unpushed
    -> the two-endpoint diff nets to nothing, the file was not even listed,
       exit 0, and the push shipped commit A.
  control: the same secret sitting in the working tree
    -> refused, so the harness was measuring something.

The first case is the exact scenario `content_scan`'s own docstring claims to
cover: "a bypassed commit is still caught before anything leaves the machine".
It was not.

`git rev-list <base>..HEAD | git diff-tree --stdin -r -z -m --root --no-renames`
is the primitive: one process, every commit in the range, and for each changed
entry both the path and the destination blob.

`git rev-list --objects` was the first choice and it is WRONG here, measured
2026-08-29. It prints each object ONCE, with ONE of its paths, because it walks
reachability rather than change. Two byte-identical files at
`docs/note.md` and `outputs/operations/note.md`, both added and then removed in
the range, produced exactly one line naming only `docs/note.md` -- so the
routing wall, which judges by PATH, would never have seen the private one. The
blob SETS the two commands produce are identical (verified on this repository:
28 and 28, no difference either way); only the path coverage differs, and the
path is half the question.

`-m` makes a merge diff against each parent, `--root` makes the first commit of
a fresh repository diff against the empty tree, and `--no-renames` is explicit
so a rename can never emit the two-path record this parser does not read.

Nothing here reads the working tree. A caller that needs the present state keeps
using `engine_guard.repo_carried_paths`; the two answers are different questions
and a push wall needs BOTH.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE = "origin/main"


class HistoryUnavailable(RuntimeError):
    """git could not report the unpushed objects. NOT the same as "there are none".

    The distinction is the whole reason this is an exception rather than an empty
    list. A wall that reads a broken git environment as "nothing to inspect"
    fails OPEN precisely when it cannot see, which is the failure mode every
    other gate in this workspace has already been fixed for once.
    """


@dataclass(frozen=True)
class HistoryBlob:
    """One (path, blob) pair the push will send. `sha` is the BLOB, not a commit."""

    rel: str
    sha: str


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                          check=False)


def has_base(root: Path, base: str = DEFAULT_BASE) -> bool:
    """True when *base* resolves. A clone with no upstream ref has no history delta."""
    return _git(root, "rev-parse", "--verify", "-q", base).returncode == 0


def unpushed_blobs(root: Path, base: str = DEFAULT_BASE) -> list[HistoryBlob]:
    """Every (path, blob) reachable from HEAD and not from *base*, deduplicated.

    Returns `[]` when *base* or HEAD does not resolve: with no upstream ref there
    is no history delta to speak of, and the caller's working-tree coverage is
    the whole answer. That is a real emptiness, not a failure to look, which is
    why it is a return and not a raise.

    Paths are POSIX and repo-relative. `-z` is load-bearing for the same reason
    `engine_guard.repo_carried_paths` needs it: without it git C-quotes any path
    holding a non-ASCII byte, and this is a bilingual workspace where
    `docs/докум.md` is an ordinary filename. Decoding is surrogate-escape rather
    than the locale, so the bytes survive the round trip back into git.

    Only the DESTINATION blob of each change is taken. A blob that is new to the
    range appears as the destination of some change in the range by definition;
    a blob that only ever appears as a SOURCE came from *base*, which means it is
    already pushed and taking it would refuse a backup over published history
    forever.
    """
    if not has_base(root, base) or not has_base(root, "HEAD"):
        return []

    commits = _git(root, "rev-list", f"{base}..HEAD")
    if commits.returncode != 0:
        raise HistoryUnavailable(
            f"git rev-list failed: "
            f"{commits.stderr.decode('utf-8', 'replace').strip() or 'no message'}")
    if not commits.stdout.strip():
        return []

    walk = subprocess.run(
        ["git", "diff-tree", "-r", "-z", "-m", "--root", "--no-renames",
         "--no-commit-id", "--stdin"],
        cwd=str(root), input=commits.stdout, capture_output=True, check=False)
    if walk.returncode != 0:
        raise HistoryUnavailable(
            f"git diff-tree failed: "
            f"{walk.stderr.decode('utf-8', 'replace').strip() or 'no message'}")

    # The `-z` raw stream alternates: a metadata field beginning with ':', then
    # the NUL-terminated path it describes.
    #   :<srcmode> <dstmode> <srcsha> <dstsha> <status>\0<path>\0
    fields = [f for f in walk.stdout.split(b"\0") if f]
    seen: set[tuple[str, str]] = set()
    out: list[HistoryBlob] = []
    index = 0
    while index + 1 < len(fields):
        meta, raw_path = fields[index], fields[index + 1]
        index += 2
        if not meta.startswith(b":"):
            # Resynchronise rather than mis-pair. A field that is not metadata
            # means the stream is not the shape this parser was written for, and
            # silently pairing the next two would attach a path to the wrong blob.
            index -= 1
            continue
        parts = meta.split(b" ")
        if len(parts) < 5:
            continue
        sha = parts[3].decode("ascii", "replace")
        if set(sha) == {"0"}:          # a deletion has no destination blob
            continue
        rel = raw_path.decode("utf-8", "surrogateescape").replace("\\", "/").lstrip("/")
        if not rel:
            continue
        key = (rel, sha)
        if key in seen:
            continue
        seen.add(key)
        out.append(HistoryBlob(rel=key[0], sha=key[1]))
    return sorted(out, key=lambda b: (b.rel, b.sha))


def unpushed_paths(root: Path, base: str = DEFAULT_BASE) -> list[str]:
    """The distinct paths of `unpushed_blobs`, for a wall that judges by path."""
    return sorted({b.rel for b in unpushed_blobs(root, base)})


def read_blob(root: Path, sha: str) -> bytes:
    """The bytes of one blob. Raises HistoryUnavailable rather than returning b"".

    An empty file and an unreadable object are different states, and a content
    gate that cannot tell them apart reports the second as clean.
    """
    result = _git(root, "cat-file", "blob", sha)
    if result.returncode != 0:
        raise HistoryUnavailable(
            f"{sha}: cannot read the blob: "
            f"{result.stderr.decode('utf-8', 'replace').strip() or 'git failed'}")
    return result.stdout


def generations(blobs) -> list[list[HistoryBlob]]:
    """Split *blobs* into groups within which no path appears twice.

    A scanner that takes file PATHS cannot be handed two versions of one path at
    once, so the history has to be laid out on disk in passes. Grouping by
    repeated path rather than by commit makes the number of passes equal to the
    largest number of versions any single file has in the range -- typically one,
    and two when a file was touched twice. Grouping by commit would have made it
    the commit count, which is unbounded for no gain.
    """
    groups: list[list[HistoryBlob]] = []
    taken: list[set[str]] = []
    for blob in blobs:
        for group, rels in zip(groups, taken, strict=True):
            if blob.rel not in rels:
                group.append(blob)
                rels.add(blob.rel)
                break
        else:
            groups.append([blob])
            taken.append({blob.rel})
    return groups
