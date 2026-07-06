import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import memory_expiry


def _mem(store: Path, name: str, *, expires=None, body="a fact"):
    store.mkdir(parents=True, exist_ok=True)
    fm = ["---", f"name: {name[:-3]}", "description: test", "metadata:", "  type: project"]
    if expires is not None:
        fm.append(f"  expires: {expires}")
    fm.append("---")
    (store / name).write_text("\n".join(fm) + "\n\n" + body + "\n", encoding="utf-8")


TODAY = datetime.date(2026, 7, 6)


def test_parse_expires_from_metadata(tmp_path):
    _mem(tmp_path, "a.md", expires="2026-08-25")
    assert memory_expiry.parse_expires((tmp_path / "a.md").read_text()) == datetime.date(2026, 8, 25)


def test_parse_expires_top_level(tmp_path):
    # top-level expires: also honored
    (tmp_path / "b.md").write_text(
        "---\nname: b\nexpires: 2026-01-01\nmetadata:\n  type: project\n---\nbody\n",
        encoding="utf-8",
    )
    assert memory_expiry.parse_expires((tmp_path / "b.md").read_text()) == datetime.date(2026, 1, 1)


def test_parse_expires_absent_returns_none(tmp_path):
    _mem(tmp_path, "c.md")
    assert memory_expiry.parse_expires((tmp_path / "c.md").read_text()) is None


def test_parse_expires_malformed_returns_none(tmp_path):
    (tmp_path / "d.md").write_text(
        "---\nname: d\nmetadata:\n  expires: not-a-date\n---\nbody\n", encoding="utf-8"
    )
    assert memory_expiry.parse_expires((tmp_path / "d.md").read_text()) is None


def test_find_expired_selects_strictly_past(tmp_path):
    _mem(tmp_path, "past.md", expires="2026-07-05")      # yesterday -> expired
    _mem(tmp_path, "boundary.md", expires="2026-07-06")  # today -> NOT yet (survives its last day)
    _mem(tmp_path, "future.md", expires="2026-09-01")    # future -> live
    _mem(tmp_path, "noexp.md")                            # no expires -> never touched
    (tmp_path / "MEMORY.md").write_text("# index\n", encoding="utf-8")

    expired = memory_expiry.find_expired(tmp_path, TODAY)
    names = {n for n, _ in expired}
    assert names == {"past.md"}


def test_find_expired_skips_memory_md(tmp_path):
    # MEMORY.md must never be a retire candidate even if it somehow parses
    (tmp_path / "MEMORY.md").write_text(
        "---\nexpires: 2020-01-01\n---\n# index\n", encoding="utf-8"
    )
    assert memory_expiry.find_expired(tmp_path, TODAY) == []


def test_strip_index_pointers_removes_only_named(tmp_path):
    index = (
        "# Memory index\n"
        "- [Keep me](keep.md) — stays.\n"
        "- [Drop me](drop.md) — goes.\n"
        "## Active Threads\n"
        "- [A thread](threads/business/drop.md) — managed, must stay.\n"
    )
    out = memory_expiry.strip_index_pointers(index, ["drop.md"])
    assert "(keep.md)" in out
    assert "- [Drop me](drop.md)" not in out
    # the thread pointer references a path, not the bare top-level file: untouched
    assert "threads/business/drop.md" in out


def test_strip_index_pointers_noop_when_absent(tmp_path):
    index = "# Memory index\n- [Keep](keep.md) — stays.\n"
    assert memory_expiry.strip_index_pointers(index, ["gone.md"]) == index
