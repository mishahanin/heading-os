"""Two frontmatter readers in one module that disagreed, and a comment that reset the count.

Three defects in `scripts/utils/memory_touch.py`, the single writer of
auto-memory `access_count`.

**The closing fence had to be followed by a newline.** `FRONTMATTER_RE` was
`^(---\\s*\\n)(.*?\\n)(---\\s*\\n)`, so a file whose frontmatter closes at EOF
without a trailing newline raised `TouchError("no frontmatter block found")` -
and so did a frontmatter-only file `---\\n---\\n`, because the middle group
demanded at least one line. `touch_if_stale`, two functions down in the same
module, reads those same files fine through `markdown.parse_frontmatter`. Two
parsers, one module, opposite answers about the same bytes, and `TouchError` is
documented as meaning the file has no frontmatter AT ALL.

**An inline comment reset the count to zero.** `access_count: 7  # bumped by
cron` is valid YAML worth 7. The text parser handed `"7  # bumped by cron"` to
`int()`, caught the `ValueError`, reset to 0 and wrote 1 - a file with real
access history silently demoted to the bottom of recall order, and the comment
deleted on the way out, in a module whose docstring promises comments are
"preserved byte-for-byte". A quoted number hit the same reset.

**atime was captured after the read that moved it.** `touch_file`'s docstring
says atime and mtime are RESTORED. `before = resolved.stat()` ran AFTER
`read_text`, and on a relatime mount the read itself advances atime when atime
is behind mtime, so the value put back was the post-read one. The atime test
below probes the filesystem first and skips on a mount that never updates atime,
rather than passing over a measurement it did not make.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.markdown import parse_frontmatter  # noqa: E402
from scripts.utils.memory_touch import (  # noqa: E402
    TouchError,
    _bump_frontmatter,
    touch_file,
)

TODAY = "2026-08-28"


# ============================================================
# The two frontmatter readers agree
# ============================================================

EOF_CLOSED = "---\nmetadata:\n  access_count: 3\n---"
EMPTY_BLOCK = "---\n---\n"


def test_a_frontmatter_closed_at_eof_is_bumped_not_refused():
    new_text, count = _bump_frontmatter(EOF_CLOSED, TODAY)
    assert count == 4
    assert "access_count: 4" in new_text
    assert f"last_accessed: {TODAY}" in new_text


def test_a_frontmatter_closed_at_eof_keeps_its_missing_trailing_newline():
    new_text, _ = _bump_frontmatter(EOF_CLOSED, TODAY)
    assert new_text.endswith("---")


def test_an_empty_frontmatter_block_gains_a_metadata_block():
    new_text, count = _bump_frontmatter(EMPTY_BLOCK, TODAY)
    assert count == 1
    assert "metadata:" in new_text
    assert "access_count: 1" in new_text


@pytest.mark.parametrize("text", [EOF_CLOSED, EMPTY_BLOCK])
def test_the_module_s_two_frontmatter_readers_agree_on_the_same_bytes(text):
    """Derived from the other reader, not from a second hand-written list."""
    meta, _body = parse_frontmatter(text)
    assert isinstance(meta, dict), "parse_frontmatter reads this as frontmatter"
    _bump_frontmatter(text, TODAY)  # so must this one


def test_a_file_with_genuinely_no_frontmatter_still_raises():
    with pytest.raises(TouchError):
        _bump_frontmatter("no fences here\njust prose\n", TODAY)


def test_an_unclosed_fence_still_raises():
    with pytest.raises(TouchError):
        _bump_frontmatter("---\nmetadata:\n  access_count: 3\n", TODAY)


# ============================================================
# The count survives a comment and a quote
# ============================================================

def test_an_inline_comment_does_not_reset_the_count():
    text = "---\nmetadata:\n  access_count: 7  # bumped by cron\n---\nbody\n"
    new_text, count = _bump_frontmatter(text, TODAY)
    assert count == 8
    assert "access_count: 8" in new_text


def test_the_inline_comment_survives_the_bump():
    text = "---\nmetadata:\n  access_count: 7  # bumped by cron\n---\nbody\n"
    new_text, _ = _bump_frontmatter(text, TODAY)
    assert "# bumped by cron" in new_text


def test_a_quoted_count_is_read_as_a_number():
    text = '---\nmetadata:\n  access_count: "9"\n---\n'
    _new_text, count = _bump_frontmatter(text, TODAY)
    assert count == 10


def test_a_plain_count_still_increments():
    text = "---\ntitle: x\nmetadata:\n  access_count: 2\n---\nbody\n"
    new_text, count = _bump_frontmatter(text, TODAY)
    assert count == 3
    assert "title: x" in new_text
    assert new_text.endswith("body\n")


def test_a_genuinely_unreadable_count_still_degrades_to_one():
    text = "---\nmetadata:\n  access_count: many\n---\n"
    _new_text, count = _bump_frontmatter(text, TODAY)
    assert count == 1


# ============================================================
# atime is restored, and mtime with it
# ============================================================

def _fs_updates_atime_on_read(tmp_path: Path) -> bool:
    probe = tmp_path / "atime-probe.txt"
    probe.write_text("x", encoding="utf-8")
    now = time.time()
    os.utime(probe, (now - 3 * 86400, now))
    before = probe.stat().st_atime
    probe.read_text(encoding="utf-8")
    return probe.stat().st_atime != before


def test_touch_file_restores_both_timestamps(tmp_path):
    if not _fs_updates_atime_on_read(tmp_path):
        pytest.skip("this mount does not update atime on read (measured above), "
                    "so it cannot distinguish the two stat orderings")
    memory_dir = tmp_path / "auto-memory"
    memory_dir.mkdir()
    target = memory_dir / "harbour-ledger.md"
    target.write_text("---\nmetadata:\n  access_count: 4\n---\nbody\n",
                      encoding="utf-8")
    now = time.time()
    os.utime(target, (now - 3 * 86400, now - 86400))
    before = target.stat()

    count, _path = touch_file("harbour-ledger.md", memory_dir, TODAY)

    after = target.stat()
    assert count == 5
    assert after.st_mtime == pytest.approx(before.st_mtime, abs=1e-3)
    assert after.st_atime == pytest.approx(before.st_atime, abs=1e-3)
