"""`list_entries` stat-ed inside a sort key, so a deleted entry crashed the read.

`scripts/utils/dead_letter.py` says its artifacts are the store recovery works
from with the daemon down, and `dead_letter_dir` argues at length that the read
paths must survive a directory that does not even exist. The sort key did the
opposite: `key=lambda p: p.stat().st_mtime` over a live `glob`, so an entry
removed between the two raised `FileNotFoundError` out of `list_entries`.

The window is not theoretical. `purge` calls `list_entries` BEFORE its own
`try/except OSError`, so the race takes `purge` down as well, and
`scripts/dead-letter.py` retries an entry and then deletes it.

Measured before the fix, with one of two entries removed after the glob:
`FileNotFoundError: [Errno 2] No such file or directory: .../a.json`.

Every path here is under tmp_path; the real outputs tree is never touched.

Tests: this file.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import dead_letter  # noqa: E402


@pytest.fixture()
def dlq(tmp_path):
    """Three dead-letter entries with distinct mtimes, under tmp_path."""
    directory = dead_letter.dead_letter_dir(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    names = ["oldest.json", "middle.json", "newest.json"]
    for i, name in enumerate(names):
        path = directory / name
        path.write_text('{"kind": "email_send"}', encoding="utf-8")
        stamp = time.time() - (len(names) - i) * 3600
        os.utime(path, (stamp, stamp))
    assert len(list(directory.glob("*.json"))) == len(names), "empty corpus proves nothing"
    return directory


def _vanish_after_glob(monkeypatch, victim: str):
    """Make `victim` disappear in the window between the glob and the stat."""
    real_glob = Path.glob

    def racing_glob(self, pattern, *a, **kw):
        found = list(real_glob(self, pattern, *a, **kw))
        for path in found:
            if path.name == victim:
                path.unlink()
        return iter(found)

    monkeypatch.setattr(Path, "glob", racing_glob)


def test_an_entry_deleted_under_the_reader_is_dropped_not_raised(dlq, monkeypatch):
    _vanish_after_glob(monkeypatch, "middle.json")

    entries = dead_letter.list_entries(workspace_root=dlq.parent.parent.parent)

    names = [p.name for p in entries]
    assert names == ["newest.json", "oldest.json"]


def test_purge_survives_the_same_race(dlq, monkeypatch):
    """`purge` guards its own stat and unlink but calls the reader first, so the
    crash landed inside the recovery tool itself."""
    _vanish_after_glob(monkeypatch, "middle.json")

    removed = dead_letter.purge(older_than_days=0,
                                workspace_root=dlq.parent.parent.parent)

    assert removed == 2
    assert list(dlq.glob("*.json")) == []


def test_a_full_directory_still_sorts_newest_first(dlq):
    """The other direction: the ordering contract in the docstring is unchanged."""
    entries = dead_letter.list_entries(workspace_root=dlq.parent.parent.parent)

    assert [p.name for p in entries] == ["newest.json", "middle.json", "oldest.json"]
