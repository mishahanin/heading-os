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

# ASCII unit/record separators. Commit messages contain newlines, tabs and every
# punctuation mark a human types; these two bytes are the only delimiters git
# will not find inside the fields it is emitting.
_FS = "\x1f"
_RS = "\x1e"
_FORMAT = f"%H{_FS}%at{_FS}%s{_FS}%b{_RS}"


def _run(repo: Path, args: list[str]) -> str:
    # `core.quotePath` defaults ON, so git wraps any path holding a non-ASCII
    # byte in double quotes and C-escapes it: `"_secure/x/\321\204.md"`. That
    # leading quote defeats `air_gap.is_denied`, which matches deny PREFIXES
    # with `startswith` -- so the hard-coded vault prefix, and every prefix a
    # caller passes from config, silently stopped applying to exactly those
    # paths, and the commit was indexed message and all. Turning the quoting off
    # is one flag and removes the class, rather than teaching one call site to
    # un-escape.
    proc = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=repo, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _is_repo(repo: Path) -> bool:
    if not repo.is_dir():
        return False
    proc = subprocess.run(
        ["git", "rev-parse", "--git-dir"], cwd=repo, capture_output=True, text=True
    )
    return proc.returncode == 0


def _known(repo: Path, sha: str) -> bool:
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=repo, capture_output=True, text=True,
    )
    return proc.returncode == 0


def _changed_paths(repo: Path, revs: list[str]) -> dict[str, list[str]]:
    """{sha: [changed path, ...]} for the whole walk, in ONE git call.

    Per-commit `--name-only` calls would be 1,100 subprocess spawns. This is one.
    `-m` expands merges so a merge's paths are seen; without it a merge reports
    none and would slip past the air gap.
    """
    out = _run(repo, ["log", "-m", "--name-only", f"--format={_RS}%H", *revs])
    paths: dict[str, list[str]] = {}
    for block in out.split(_RS):
        block = block.strip("\n")
        if not block:
            continue
        head, _, rest = block.partition("\n")
        sha = head.strip()
        if not sha:
            continue
        found = [ln.strip() for ln in rest.splitlines() if ln.strip()]
        # `-m` emits one block per parent; union them so nothing is missed.
        paths.setdefault(sha, [])
        for p in found:
            if p not in paths[sha]:
                paths[sha].append(p)
    return paths


def iter_commits(
    repo: Path,
    *,
    repo_label: str,
    since: str | None = None,
    include_paths: bool = True,
    deny_prefixes: tuple = (),
    deny_segments: tuple = (),
) -> Iterator[dict]:
    """Yield one dict per indexable commit, newest first.

    `repo_label` names the side of the engine/data seam ("engine" / "data") and
    appears in `id` and `path`, so rows from the two repositories can never
    collide on a sha that exists in both.

    `include_paths` is the body variant the spec calls for measuring: with the
    changed-path list, "what touched the action queue" is answerable by meaning;
    without it, the vector is not diluted by filenames. Build both, measure both.
    """
    repo = Path(repo)
    if not _is_repo(repo):
        raise ValueError(f"not a git repository: {repo}")

    if not _known(repo, "HEAD"):
        return  # a repo with no commits yet: nothing to walk, not an error

    revs = ["HEAD"]
    if since and _known(repo, since):
        revs = [f"{since}..HEAD"]

    raw = _run(repo, ["log", f"--format={_FORMAT}", *revs])
    records = [r for r in raw.split(_RS) if r.strip()]
    if not records:
        return

    path_map = _changed_paths(repo, revs)

    for rec in records:
        parts = rec.lstrip("\n").split(_FS)
        if len(parts) < 4:
            continue
        sha, at, subject, body = parts[0].strip(), parts[1], parts[2], parts[3]
        if BACKUP_SUBJECT_RE.match(subject):
            continue

        changed = path_map.get(sha, [])
        # Whole-commit denial. A commit with NO paths (empty or a plain merge)
        # cannot be denied by path, and is kept -- `any()` on [] is False.
        if any(is_denied(p, deny_prefixes, deny_segments) for p in changed):
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
