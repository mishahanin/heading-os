"""F-M3: threads_lib.py write_thread_file and MEMORY.md mutations must be atomic."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.utils.threads_lib import write_thread_file, ThreadFile


def _make_thread(title: str = "test") -> ThreadFile:
    return ThreadFile(
        id="test-id",
        title=title,
        status="active",
        type="business",
        classification="private",
        opened="2026-06-15",
        last_touched="2026-06-15",
        counterparties=[],
        links={},
        tags=[],
        body="body text",
    )


def test_write_thread_file_is_atomic(tmp_path):
    """write_thread_file must not leave a partial file on os.replace failure."""
    target = tmp_path / "thread.md"
    target.write_text("old content", encoding="utf-8")

    def _fail(src, dst):
        raise OSError("disk full")

    import scripts.utils.atomic as atomic_mod
    with patch.object(atomic_mod.os, "replace", side_effect=_fail):
        with pytest.raises(OSError):
            write_thread_file(target, _make_thread())

    assert target.read_text(encoding="utf-8") == "old content"


def test_write_thread_file_no_orphans(tmp_path):
    """write_thread_file must not leave tmp files on success."""
    target = tmp_path / "thread.md"
    write_thread_file(target, _make_thread())
    files = list(tmp_path.iterdir())
    assert files == [target], f"orphan files: {[f.name for f in files if f != target]}"
