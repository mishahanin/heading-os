"""Regression tests for scripts/memory-touch.py -- the atomic access_count/
last_accessed bump used by /recall (Gap #2 reinforcement signal).

Encodes the plan's Success Signal: first touch sets access_count=1 and
today's date; a second touch increments to 2; a path outside
get_auto_memory_dir() is refused (exit 1) and left untouched; all other
frontmatter/body content is byte-identical before/after except the two
touched fields.

Run: python3 -m pytest tests/test_memory_touch.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "memory-touch.py"

REAL_MEMORY_FIXTURE = """---
name: automate-what-is-safe
description: Standing instruction example fixture.
metadata:
  node_type: memory
  type: feedback
  originSessionId: 61bbddb0-d3b9-40ed-b28f-d332ecd8a919
---

Some fact body that must survive byte-identical.
"""


def load_module():
    spec = importlib.util.spec_from_file_location("memory_touch_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def memory_dir(tmp_path, monkeypatch):
    mod = load_module()
    mdir = tmp_path / "auto-memory"
    mdir.mkdir()
    monkeypatch.setattr(mod, "get_auto_memory_dir", lambda: mdir)
    monkeypatch.setattr(mod, "get_default_tz", lambda: __import__("zoneinfo").ZoneInfo("UTC"))
    return mod, mdir


def test_first_touch_sets_access_count_one_and_today(memory_dir):
    mod, mdir = memory_dir
    f = mdir / "example-fact.md"
    f.write_text(REAL_MEMORY_FIXTURE, encoding="utf-8")

    access_count, resolved = mod.touch_file(
        "example-fact.md", mdir, "2026-07-16"
    )
    assert access_count == 1
    text = Path(resolved).read_text(encoding="utf-8")
    assert "access_count: 1" in text
    assert "last_accessed: 2026-07-16" in text
    # Everything else preserved.
    assert "name: automate-what-is-safe" in text
    assert "node_type: memory" in text
    assert "Some fact body that must survive byte-identical." in text


def test_second_touch_increments_to_two(memory_dir):
    mod, mdir = memory_dir
    f = mdir / "example-fact.md"
    f.write_text(REAL_MEMORY_FIXTURE, encoding="utf-8")

    mod.touch_file("example-fact.md", mdir, "2026-07-16")
    access_count, resolved = mod.touch_file("example-fact.md", mdir, "2026-07-17")
    assert access_count == 2
    text = Path(resolved).read_text(encoding="utf-8")
    assert "access_count: 2" in text
    assert "last_accessed: 2026-07-17" in text
    # Only one access_count / last_accessed line each -- not duplicated.
    assert text.count("access_count:") == 1
    assert text.count("last_accessed:") == 1


def test_accepts_data_root_relative_prefixed_form(memory_dir):
    """/recall passes memory-index.py's JSON `path` field verbatim, which is
    data-root-relative and already carries the "auto-memory/" prefix
    (e.g. "auto-memory/example-fact.md"). This must resolve to the same file,
    not a doubled auto-memory/auto-memory/ path that raises TouchError."""
    mod, mdir = memory_dir
    f = mdir / "example-fact.md"
    f.write_text(REAL_MEMORY_FIXTURE, encoding="utf-8")

    access_count, resolved = mod.touch_file(
        "auto-memory/example-fact.md", mdir, "2026-07-17"
    )
    assert access_count == 1
    assert Path(resolved) == f.resolve()
    text = f.read_text(encoding="utf-8")
    assert "access_count: 1" in text
    assert "last_accessed: 2026-07-17" in text


def test_bare_and_prefixed_forms_hit_the_same_file(memory_dir):
    """A bare filename and the auto-memory/-prefixed form are the same file:
    touching one then the other increments to 2, never creating two files."""
    mod, mdir = memory_dir
    f = mdir / "example-fact.md"
    f.write_text(REAL_MEMORY_FIXTURE, encoding="utf-8")

    mod.touch_file("example-fact.md", mdir, "2026-07-16")
    access_count, _ = mod.touch_file("auto-memory/example-fact.md", mdir, "2026-07-17")
    assert access_count == 2
    text = f.read_text(encoding="utf-8")
    assert text.count("access_count:") == 1


def test_refuses_path_outside_auto_memory_dir(memory_dir, tmp_path):
    mod, mdir = memory_dir
    outside = tmp_path / "outside" / "not-memory.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    original = "---\nname: not-memory\nmetadata:\n  type: feedback\n---\nbody\n"
    outside.write_text(original, encoding="utf-8")

    with pytest.raises(mod.TouchError):
        mod.touch_file(str(outside), mdir, "2026-07-16")
    # Left untouched.
    assert outside.read_text(encoding="utf-8") == original


def test_content_byte_identical_except_touched_fields(memory_dir):
    mod, mdir = memory_dir
    f = mdir / "example-fact.md"
    f.write_text(REAL_MEMORY_FIXTURE, encoding="utf-8")

    mod.touch_file("example-fact.md", mdir, "2026-07-16")
    text = f.read_text(encoding="utf-8")
    expected = REAL_MEMORY_FIXTURE.replace(
        "  originSessionId: 61bbddb0-d3b9-40ed-b28f-d332ecd8a919\n---",
        "  originSessionId: 61bbddb0-d3b9-40ed-b28f-d332ecd8a919\n"
        "  access_count: 1\n"
        "  last_accessed: 2026-07-16\n"
        "---",
    )
    assert text == expected


def test_cli_main_refuses_and_reports_exit_1(memory_dir, capsys, monkeypatch):
    mod, mdir = memory_dir
    outside_dir = mdir.parent / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "x.md"
    outside_file.write_text("---\nmetadata:\n  type: feedback\n---\nbody\n", encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["memory-touch.py", str(outside_file)])
    rc = mod.main()
    assert rc == 1
