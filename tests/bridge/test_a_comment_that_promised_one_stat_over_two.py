"""The inflight scanner's per-file comment said "ONE stat" over code taking two.

`mtime = p.stat().st_mtime` followed by `p.is_file()` is two stats, because
`is_file()` stats again. The same comment goes on to describe "the two SEPARATE
stats this used to take" as a fixed defect, so the file both claimed the race was
gone and described removing it, while the second syscall was still there.

Only the enclosing `except (OSError, UnicodeDecodeError): continue` kept that
harmless, and that is the danger: a later reader trusting the comment has no way
to tell the handler is load-bearing, and deleting it as redundant is a natural
next edit. The comment and the code were made to agree by removing the stat, not
by weakening the sentence.

`is_file()` is defined as "stat, following symlinks, then S_ISREG", so reading
the mode off the stat already taken is the identical test with one syscall fewer.
The anchors below pin that equivalence, so the saving cannot be bought by
changing which files the scan accepts.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon.refreshers import inflight  # noqa: E402

ARTICLE = "---\nsession_id: sid-42\n---\n\nbody\n"


@pytest.fixture
def linkedin(tmp_path):
    d = tmp_path / inflight.SCAN_DIRS["linkedin"]
    d.mkdir(parents=True)
    return d


@pytest.fixture
def stat_calls(monkeypatch):
    """Count `Path.stat` calls per resolved path during a scan."""
    counts: dict[str, int] = {}
    real_stat = Path.stat

    def counting_stat(self, *args, **kwargs):
        counts[str(self)] = counts.get(str(self), 0) + 1
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", counting_stat)
    return counts


def test_a_scanned_file_is_stat_ed_exactly_once(tmp_path, linkedin, stat_calls):
    """THE FINDING. Two stats is a window the comment says is not there."""
    target = linkedin / "post-a.md"
    target.write_text(ARTICLE, encoding="utf-8")

    rows = inflight.scan_inflight(tmp_path)

    assert [r["id"] for r in rows] == ["post-a"], "the scan did not reach the file"
    assert stat_calls[str(target)] == 1, (
        f"{stat_calls[str(target)]} stats on one file; the comment above this "
        f"code promises ONE, and the second is a TOCTOU window"
    )


def test_a_recent_file_is_still_returned_with_its_session_id(tmp_path, linkedin):
    """ANCHOR. A scan that returned nothing would satisfy the count above."""
    (linkedin / "post-b.md").write_text(ARTICLE, encoding="utf-8")

    rows = inflight.scan_inflight(tmp_path)

    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "post-b"
    assert row["category"] == "linkedin"
    assert row["session_id"] == "sid-42"
    assert row["path"] == str(Path(inflight.SCAN_DIRS["linkedin"]) / "post-b.md")
    assert row["modified_at"] > 0


def test_a_file_older_than_the_window_is_still_excluded(tmp_path, linkedin):
    """ANCHOR. The mtime test must still come off the single stat."""
    stale = linkedin / "ancient.md"
    stale.write_text(ARTICLE, encoding="utf-8")
    long_ago = time.time() - 72 * 3600
    os.utime(stale, (long_ago, long_ago))

    assert inflight.scan_inflight(tmp_path, retention_hours=24) == []
    assert [r["id"] for r in inflight.scan_inflight(tmp_path, retention_hours=96)] \
        == ["ancient"]


def test_a_directory_named_like_an_article_is_still_excluded(tmp_path, linkedin):
    """ANCHOR. `S_ISREG` must reject exactly what `is_file()` rejected.

    A directory called `draft.md` is the case that separates "is a regular file"
    from "exists"; dropping the mode test entirely would let it through here.
    """
    (linkedin / "draft.md").mkdir()
    (linkedin / "real.md").write_text(ARTICLE, encoding="utf-8")

    assert [r["id"] for r in inflight.scan_inflight(tmp_path)] == ["real"]


def test_a_symlink_to_a_recent_article_is_still_followed(tmp_path, linkedin):
    """ANCHOR. `is_file()` follows symlinks; `p.stat()` does too, `lstat` does not.

    Swapping in `lstat` would also cut the syscall count to one and would
    silently stop reporting any artifact a producer wrote through a link.
    """
    outside = tmp_path / "elsewhere.md"
    outside.write_text(ARTICLE, encoding="utf-8")
    (linkedin / "linked.md").symlink_to(outside)

    assert [r["id"] for r in inflight.scan_inflight(tmp_path)] == ["linked"]


def test_a_file_that_vanishes_mid_scan_costs_only_its_own_row(tmp_path, linkedin,
                                                              monkeypatch):
    """The reason the stat count matters: the survivors must survive.

    Every removed syscall is one fewer chance to raise between `iterdir` and the
    row being built. Here the single remaining stat is made to fail for one file
    and the rest of the scan must still be returned.
    """
    (linkedin / "gone.md").write_text(ARTICLE, encoding="utf-8")
    (linkedin / "kept.md").write_text(ARTICLE, encoding="utf-8")

    real_stat = Path.stat

    def vanishing_stat(self, *args, **kwargs):
        if self.name == "gone.md":
            raise FileNotFoundError(2, "No such file or directory")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", vanishing_stat)

    assert [r["id"] for r in inflight.scan_inflight(tmp_path)] == ["kept"]
