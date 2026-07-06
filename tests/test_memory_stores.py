import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import memory_stores


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

    removed = memory_stores.retire_memory("feedback_foo.md",
                                          stores=[canonical, native1, native2])
    assert len(removed) == 3
    assert not (canonical / "feedback_foo.md").exists()
    assert not (native1 / "feedback_foo.md").exists()
    assert not (native2 / "feedback_foo.md").exists()
    assert (native2 / "keep.md").exists()


def test_retire_is_idempotent_and_missing_safe(tmp_path):
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    assert memory_stores.retire_memory("nope.md", stores=[canonical]) == []
    _seed(canonical, "a.md")
    assert memory_stores.retire_memory("a.md", stores=[canonical]) == [str(canonical / "a.md")]
    assert memory_stores.retire_memory("a.md", stores=[canonical]) == []
