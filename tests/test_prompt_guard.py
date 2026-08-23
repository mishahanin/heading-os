"""Every name exempted from injection scanning must be a file that exists.

`.claude/hooks/prompt-guard.py` skips injection scanning for a small set of
basenames: files that legitimately quote injection patterns, so scanning them
would fire on their own documentation. The exemption matches by BASENAME, at any
depth, under any monitored ingest path.

`prevent-secrets.py` sat in that set until 2026-08-23. The shim it named was
deleted on 2026-08-11, when `_dispatch.py` absorbed it, and `_dispatch.py` wrote
down the reason to remove the allowance in the same breath:

    The fourth entry, .claude/hooks/prevent-secrets.py, went with the shim
    itself on 2026-08-11 — an allowance for a file that cannot exist is a name
    waiting for someone to recreate it and inherit the exemption.

One wall learned the lesson; the other kept the ghost. Found by the 2026-08-23
audit. A file created at that name anywhere under an ingest path would have
skipped scanning, and nothing would have said so.

This test is the mechanical half: an exempted basename must resolve to a real
file somewhere in the engine or the data overlay. Deleting a file now fails the
suite until its exemption goes too.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "prompt-guard.py"

_SEARCH_ROOTS = [ROOT, ROOT.parent / ".heading-os-data"]
_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".memory-index"}


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("prompt_guard_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["prompt_guard_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _exists_anywhere(basename: str) -> bool:
    for root in _SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob(basename):
            if not any(part in _SKIP_DIRS for part in path.parts):
                return True
    return False


def test_every_exempted_basename_names_a_real_file(hook):
    ghosts = sorted(b for b in hook.ALLOW_BASENAMES if not _exists_anywhere(b))
    assert ghosts == [], (
        f"these basenames skip injection scanning and no such file exists: "
        f"{ghosts}. Anyone who creates a file with one of these names inherits "
        "the exemption silently. Delete the entry with the file."
    )


def test_the_deleted_shim_is_not_exempted_again(hook):
    """Named specifically, because a generic existence check would go quiet the
    moment someone recreated the file for an unrelated reason."""
    assert "prevent-secrets.py" not in hook.ALLOW_BASENAMES, (
        "prevent-secrets.py is exempted from injection scanning again; the shim "
        "was deleted on 2026-08-11"
    )


def test_the_exemption_set_is_not_empty(hook):
    """An empty set makes the test above vacuous."""
    assert len(hook.ALLOW_BASENAMES) >= 3, (
        f"only {hook.ALLOW_BASENAMES} left; if the exemptions were genuinely "
        "removed, delete this test rather than letting it pass on nothing"
    )


def test_the_guard_still_exempts_itself(hook):
    """prompt-guard.py carries the injection vocabulary it scans for. Losing its
    own exemption makes every edit to it fire the guard."""
    assert "prompt-guard.py" in hook.ALLOW_BASENAMES
