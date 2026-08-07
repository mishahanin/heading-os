"""Tests for scripts/utils/memory_touch.py -- the shared auto-memory bump.

Covers the same-day debounce that keeps a single prompt burst from reading as
many independent uses. The lifetime counter itself is covered by
tests/test_memory_touch.py against the CLI wrapper.

Run: .venv/bin/python -m pytest tests/test_memory_touch_util.py
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.utils.markdown import parse_frontmatter
from scripts.utils.memory_touch import TouchError, touch_if_stale


FACT = (
    "---\n"
    "name: example-fact\n"
    "description: fixture fact\n"
    "metadata:\n"
    "  node_type: memory\n"
    "  type: feedback\n"
    "---\n\n"
    "Some fact body.\n"
)


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    mdir = tmp_path / "auto-memory"
    mdir.mkdir(parents=True)
    (mdir / "example-fact.md").write_text(FACT, encoding="utf-8")
    return mdir


def _access_count(path: Path) -> int:
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    nested = meta.get("metadata")
    nested = nested if isinstance(nested, dict) else {}
    return int(nested.get("access_count") or meta.get("access_count") or 0)


def test_first_call_bumps_and_reports_written(memory_dir):
    assert touch_if_stale("example-fact.md", memory_dir, "2026-07-16") is True
    assert _access_count(memory_dir / "example-fact.md") == 1


def test_second_call_same_day_is_a_no_op(memory_dir):
    touch_if_stale("example-fact.md", memory_dir, "2026-07-16")
    before = (memory_dir / "example-fact.md").read_text(encoding="utf-8")

    assert touch_if_stale("example-fact.md", memory_dir, "2026-07-16") is False

    assert (memory_dir / "example-fact.md").read_text(encoding="utf-8") == before
    assert _access_count(memory_dir / "example-fact.md") == 1


def test_next_day_bumps_again(memory_dir):
    touch_if_stale("example-fact.md", memory_dir, "2026-07-16")
    assert touch_if_stale("example-fact.md", memory_dir, "2026-07-17") is True
    assert _access_count(memory_dir / "example-fact.md") == 2


def test_accepts_the_data_root_relative_prefixed_form(memory_dir):
    assert touch_if_stale("auto-memory/example-fact.md", memory_dir, "2026-07-16") is True
    assert _access_count(memory_dir / "example-fact.md") == 1


def test_refuses_a_path_outside_the_auto_memory_dir(memory_dir, tmp_path):
    outside = tmp_path / "elsewhere.md"
    outside.write_text(FACT, encoding="utf-8")
    with pytest.raises(TouchError):
        touch_if_stale(str(outside), memory_dir, "2026-07-16")
