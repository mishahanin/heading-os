"""A slice width is counted in characters, and so is the filter that feeds it.

Found by the 2026-08-23 audit. `census-submodel-bench.py` exists to measure how a
small model degrades as the slice in front of it grows, and its own header names
the property it protects: "A slice must actually be a slice ... every case
records the length it ACTUALLY got beside the width it asked for."

Two things broke that property, both silently, and both only on this workspace's
corpus rather than on an English one:

1. `_candidate_docs` selected documents by `Path.stat().st_size` - BYTES - and
   `build_cases` then cut them with `text[:width]` - CHARACTERS. Cyrillic costs
   two bytes per character in UTF-8, so a 50,000-byte Russian thread yields about
   25,000 characters. It passed the filter and under-filled the slice, and the
   `--dry-run` degenerate-width guard reported ВЫРОЖДЕН for documents that were
   genuinely long enough by the unit the script actually uses.

2. `actual_len` was measured AFTER `_plant` appended the probe marker, so every
   planted case counted ~33 characters it had not read from the document. A case
   one character short of the width reported itself as filled.

Both are measured against a synthetic corpus here, so the test says the same
thing on any machine.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load():
    spec = importlib.util.spec_from_file_location(
        "census_submodel_bench_width", ROOT / "scripts" / "census-submodel-bench.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["census_submodel_bench_width"] = module
    spec.loader.exec_module(module)
    return module


bench = _load()

WIDTH = 4_000


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    """Threads only; knowledge and outputs point at empty directories."""
    threads = tmp_path / "threads"
    (threads / "business").mkdir(parents=True)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(bench, "get_threads_dir", lambda: threads)
    monkeypatch.setattr(bench, "get_knowledge_dir", lambda: empty)
    monkeypatch.setattr(bench, "get_outputs_dir", lambda: empty)
    return threads / "business"


def _write(directory: Path, name: str, char: str, chars: int) -> Path:
    path = directory / name
    path.write_text(char * chars, encoding="utf-8")
    return path


def test_a_cyrillic_document_is_measured_in_characters_not_bytes(corpus):
    """Half the width in characters, the full width in bytes. It must not pass."""
    short = _write(corpus, "russian-short.md", "я", WIDTH // 2)
    assert short.stat().st_size >= WIDTH, "fixture is not byte-long enough to be a trap"
    assert len(short.read_text(encoding="utf-8")) < WIDTH

    assert bench._candidate_docs(min_len=WIDTH, want=5) == [], (
        "a document with half the requested characters was selected as long "
        "enough, because the filter counted bytes"
    )


def test_a_document_that_really_is_long_enough_still_passes(corpus):
    """The mutation guard: the fix must not simply reject everything Cyrillic."""
    long_ru = _write(corpus, "russian-long.md", "я", WIDTH + 10)
    assert bench._candidate_docs(min_len=WIDTH, want=5) == [long_ru]


def test_an_ascii_document_is_unaffected(corpus):
    """One byte per character, so this path behaved correctly before and after."""
    ascii_long = _write(corpus, "english.md", "a", WIDTH + 10)
    _write(corpus, "english-short.md", "a", WIDTH // 2)
    assert bench._candidate_docs(min_len=WIDTH, want=5) == [ascii_long]


def test_min_len_zero_still_returns_everything(corpus):
    """The fallback pass asks for `min_len=0` and must keep getting short files."""
    _write(corpus, "tiny.md", "я", 10)
    assert len(bench._candidate_docs(min_len=0, want=5)) == 1


def test_actual_len_excludes_the_planted_marker(corpus):
    """`filled` must describe the document, not the probe token appended to it."""
    _write(corpus, "russian-long.md", "я", WIDTH + 500)
    cases = bench.build_cases(width=WIDTH, marker=bench.DEFAULT_MARKER, doc_count=1)
    assert len(cases) == 1
    case = cases[0]
    assert bench.DEFAULT_MARKER in case.text, "fixture did not exercise the planted path"
    assert case.actual_len == WIDTH, (
        "actual_len counted the marker as document text"
    )
    assert case.filled


def test_a_short_document_reports_itself_short_even_when_planted(corpus):
    """The under-fill signal `--dry-run` reads. The marker must not mask it."""
    _write(corpus, "russian-short.md", "я", WIDTH // 2)
    cases = bench.build_cases(width=WIDTH, marker=bench.DEFAULT_MARKER, doc_count=1)
    assert len(cases) == 1
    assert cases[0].actual_len == WIDTH // 2
    assert not cases[0].filled, "an under-filled slice reported itself as filled"
