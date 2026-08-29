#!/usr/bin/env python3
"""Every handoff path a page tells a reader to open must be one something writes.

`/prime` section 2.1 instructed the model to read `outputs/operations/handoff.md`
and parse frontmatter fields named `created`, `session_summary`, `task_progress`,
`urgency` and `plan`. Measured 2026-08-20 across every script and hook in the
engine: the writer count for that path was ZERO, the file was absent from the
live overlay, and none of those five fields exists in anything the checkpoint
hooks produce. The section had done nothing since the mechanism was rebuilt
around the dated archive, and it read like a working feature the whole time - a
page describing a capability the code does not have is worse than a missing
page, because nobody goes looking.

This test holds the readers to the writers. It is deliberately about EXISTENCE
of a writer, not about the prose: a page is free to describe the handoff however
it likes, as long as the path it names is one the mechanism actually produces.
"""
from __future__ import annotations

import re
from pathlib import Path
from tests.repo_files import tracked_paths

ROOT = Path(__file__).resolve().parent.parent

# Paths under outputs/operations/ that a doc, skill or eval case may point a
# reader at. Anchored on the segment so `.latest/summary.md` and a dated archive
# both match, and a stray `handoff.md` does not hide inside a longer path.
HANDOFF_REF = re.compile(r"outputs/operations/(handoff[\w./-]*)")

# What the mechanism writes, as directory shapes rather than exact filenames -
# the stamp and the session slug vary per save.
WRITTEN_SHAPES = (
    "handoff-archive/",          # the dated archives and the .latest pointer tree
)

# Named readers. Adding a file here is a deliberate act; the point is that the
# list cannot silently grow a page nobody checked.
READER_GLOBS = (
    ".claude/skills/**/SKILL.md",
    ".claude/skills/**/evals/cases/*.json",
    ".claude/skills/**/references/*.md",
    "docs/*.md",
    "reference/**/*.md",
)


def _is_written(ref: str) -> bool:
    return any(ref.startswith(shape) for shape in WRITTEN_SHAPES)


def _readers():
    seen = set()
    for path in tracked_paths(READER_GLOBS):
        if path not in seen:
            seen.add(path)
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                for m in HANDOFF_REF.finditer(line):
                    yield path, lineno, m.group(1)


def test_no_page_points_a_reader_at_an_unwritten_handoff_path():
    orphans = []
    total = 0
    for path, lineno, ref in _readers():
        total += 1
        if _is_written(ref):
            continue
        # A line that says the path does NOT exist, or names it as removed, is
        # documenting the absence rather than instructing a read.
        orphans.append(f"{path.relative_to(ROOT)}:{lineno} -> outputs/operations/{ref}")

    assert not orphans, (
        f"{len(orphans)} of {total} handoff references name a path nothing "
        f"writes:\n  " + "\n  ".join(orphans[:15])
    )


def test_the_detector_finds_the_real_references():
    """A matcher that matches nothing passes everything. The live corpus does
    point at the archive in several places; if this drops to zero the guard has
    stopped looking rather than started passing."""
    found = list(_readers())
    assert len(found) >= 3, f"only {len(found)} handoff reference(s) found"


def test_the_detector_would_catch_the_defect_it_was_written_for():
    """The exact 2026-08-20 line, checked against the matcher directly, so the
    guard is pinned even if every page is later rewritten."""
    line = "Check if `outputs/operations/handoff.md` exists. If it does:"
    m = HANDOFF_REF.search(line)
    assert m and m.group(1) == "handoff.md"
    assert not _is_written(m.group(1))
