import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import memory_stores

import subprocess


def _seed(store: Path, name: str, body="x"):
    store.mkdir(parents=True, exist_ok=True)
    (store / name).write_text(body, encoding="utf-8")


def test_retire_removes_from_all_stores(tmp_path):
    canonical = tmp_path / "canonical"
    native1 = tmp_path / "native1"
    native2 = tmp_path / "native2"
    for s in (canonical, native1, native2):
        _seed(s, "feedback_foo.md")
    _seed(native2, "keep.md")

    # `retire_memory` returns (removed, failed) since 2026-08-25: a failed
    # unlink was swallowed, so a memory still on disk was reported as retired
    # and its index pointer stripped anyway.
    removed, failed = memory_stores.retire_memory(
        "feedback_foo.md", stores=[canonical, native1, native2])
    assert failed == []
    assert len(removed) == 3
    assert not (canonical / "feedback_foo.md").exists()
    assert not (native1 / "feedback_foo.md").exists()
    assert not (native2 / "feedback_foo.md").exists()
    assert (native2 / "keep.md").exists()


def test_retire_is_idempotent_and_missing_safe(tmp_path):
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    assert memory_stores.retire_memory("nope.md", stores=[canonical]) == ([], [])
    _seed(canonical, "a.md")
    assert memory_stores.retire_memory("a.md", stores=[canonical]) == (
        [str(canonical / "a.md")], [])
    assert memory_stores.retire_memory("a.md", stores=[canonical]) == ([], [])


def test_retire_cli_runs_on_missing_name(tmp_path):
    """The CLI resolves its own stores, so the child must be pointed at scratch.

    `retire_memory(name)` with no `stores=` calls `all_memory_stores()`, which
    is `get_auto_memory_dir()` (HEADING_OS_DATA) plus every
    `~/.claude/projects/*/memory`. The child inherits the environment and the
    in-process overlay guard cannot see a child at all, so an unpinned run of
    this test had the operator's live auto-memory and every native harness store
    on the unlink path, one argument away from deleting a real memory. Nothing
    but the absent name kept it read-only. Both roots are redirected here; a
    delete is never the point of this test, only the exit code and the wording.
    """
    root = Path(__file__).resolve().parent.parent
    data_root = tmp_path / "data"
    (data_root / "auto-memory").mkdir(parents=True)
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    out = subprocess.run(
        [sys.executable, str(root / "scripts" / "retire-memory.py"), "definitely_absent_zzz.md"],
        capture_output=True, text=True,
        env=dict(os.environ, HEADING_OS_DATA=str(data_root), HOME=str(home)))
    assert out.returncode == 0, out.stderr
    assert "not found" in out.stdout
