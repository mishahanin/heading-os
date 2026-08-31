#!/usr/bin/env python3
"""No tracked file in this PUBLIC repository may carry a real session id.

MEASURED 2026-08-31: four test files carried the operator's own Claude Code
session ids as fixture constants, committed, in a repository that is public.

    tests/test_herdr_agent.py                    SESSION
    tests/test_checkpoint_unattended_contract.py SESSION, SLUG
    tests/test_unattended_continuation_brevity.py
    tests/test_unattended_resume_on_prompt.py

Two distinct real ids, oldest touch `debee5b` on 2026-08-19. A session id is a
live pointer into the operator's machine: it names a transcript directory under
``~/.claude/projects/`` holding the full text of a working session. It reads as
a random string, which is exactly why five reviews walked past it.

Nothing could have caught it. `scripts/leak-guard.py check-paths` exits 0 over
all of `tests/`, the push wall has no session-id rule, and the content guard's
denylist is harvested from the DATA overlay, where session ids do not live.
This file is that missing rule.

WHY IT DERIVES INSTEAD OF LISTING. A hand-written list of forbidden ids would
protect the two someone remembered today and nothing minted afterwards - the
shape recorded in the workspace's own notes as "a hand-maintained security list
falls behind". So the forbidden set is READ OFF THE MACHINE: every session id
that exists under ``~/.claude/projects/``. Paste a fresh one into a test
tomorrow and this fails tomorrow, with no edit here.

SCOPE, stated honestly. On a public clone or a CI runner there is no
``~/.claude/projects/``, so the real-id set is empty and this test reports that
it verified nothing. That is the same bound layer 7 (the content guard) carries
and for the same reason: only the operator's machine both AUTHORS and PUSHES
engine content, so that is the machine on which it must hold. It is not a hole
in CI; it is a check CI cannot perform.

Synthetic UUIDs are untouched. The corpus holds roughly twenty, and all but the
four above are invented (`acme.com`-grade fixtures, RFC-4122's own example).
This test says nothing about them - only about ids that really exist here.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.repo_files import ROOT, tracked_paths  # noqa: E402

# A session transcript is `~/.claude/projects/<project-slug>/<session-id>.jsonl`.
# Only filenames are read; no transcript is ever opened.
PROJECTS_DIR = Path.home() / ".claude" / "projects"

_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

# The truncated form the checkpoint machinery writes into handoff filenames:
# the id cut to 32 characters, dashes included. It is still a unique pointer.
SLUG_LEN = 32

# Text-ish tracked files worth opening. A UUID cannot hide in a PNG in any form
# this test could act on, and reading the LFS binaries would cost minutes.
TEXT_SUFFIXES = (
    ".py", ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".cfg",
    ".ini", ".sh", ".bash", ".ps1", ".html", ".htm", ".css", ".js", ".ts",
    ".xml", ".csv",
)

# Below this, the corpus did not load and a pass would be green over nothing.
# The tracked text corpus was ~2,700 files on 2026-08-31; 500 is a floor that
# catches a broken walk without failing on ordinary growth or pruning.
MIN_FILES_SCANNED = 500


def _real_session_ids() -> set[str]:
    """Every session id present on this machine, lowercased. Filenames only."""
    if not PROJECTS_DIR.is_dir():
        return set()
    ids = set()
    for project in PROJECTS_DIR.iterdir():
        if not project.is_dir():
            continue
        for transcript in project.glob("*.jsonl"):
            stem = transcript.stem.lower()
            if _UUID_RE.fullmatch(stem):
                ids.add(stem)
    return ids


def _text_files() -> list[Path]:
    """Every not-ignored text file in the tree, git deciding what counts.

    Deliberately wider than `git ls-files`: a file that is untracked but NOT
    ignored is one `git add -A` away from being published, which is how the
    2026-08-19 ids arrived in the first place.
    """
    paths = tracked_paths([f"**/*{suffix}" for suffix in TEXT_SUFFIXES], ROOT)
    return [p for p in paths if ".git" not in p.parts]


def test_no_tracked_file_carries_a_real_session_id():
    real = _real_session_ids()
    if not real:
        pytest.skip(
            "no ~/.claude/projects/ on this host, so the set of real session ids is "
            "empty and this test verified NOTHING. Expected on a public clone and on "
            "CI. On the operator's machine an empty set means the transcript "
            "directory moved, and this guard has stopped guarding."
        )

    slugs = {sid[:SLUG_LEN]: sid for sid in real}
    files = _text_files()
    assert len(files) >= MIN_FILES_SCANNED, (
        f"only {len(files)} tracked text files found; the walk is broken, not the "
        f"corpus. A pass here would be green over nothing."
    )

    hits: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(ROOT)
        for lineno, line in enumerate(text.splitlines(), 1):
            for found in _UUID_RE.findall(line):
                low = found.lower()
                if low in real:
                    hits.append(f"{rel}:{lineno} carries a real session id")
                elif low[:SLUG_LEN] in slugs:
                    hits.append(f"{rel}:{lineno} carries a real session-id prefix")

    assert not hits, (
        "a real Claude Code session id is committed in this PUBLIC repository. It "
        "names a transcript directory under ~/.claude/projects/ holding the full "
        "text of an operator session.\n  " + "\n  ".join(sorted(set(hits))) +
        "\nReplace each with an invented UUID. Keep any derived slug consistent "
        "with it (the slug is the id cut to 32 characters, dashes included)."
    )


def test_the_detector_actually_recognises_a_real_id():
    """Without this, the test above passes by failing to detect anything.

    A guard whose absence changes no result is not a guard. Here the risk is
    specific: if `_real_session_ids()` silently returned an empty set, or the
    UUID pattern stopped matching, the scan above would report clean forever.
    So feed the matcher an id taken from this machine and require a hit.
    """
    real = _real_session_ids()
    if not real:
        pytest.skip("no ~/.claude/projects/ on this host; nothing to feed the matcher")

    sample = sorted(real)[0]
    assert _UUID_RE.fullmatch(sample), (
        f"a filename accepted as a session id does not match the UUID pattern the "
        f"scan uses: {sample!r}. The two have drifted apart."
    )
    line = f'SESSION = "{sample}"'
    assert [m.lower() for m in _UUID_RE.findall(line)] == [sample], (
        "the scan's pattern does not find a session id inside an ordinary Python "
        "assignment, which is the exact form the 2026-08-31 leak took."
    )
    assert sample[:SLUG_LEN] in {s[:SLUG_LEN] for s in real}, (
        "the 32-character slug form no longer derives from the id, so the prefix "
        "half of the scan checks nothing."
    )
