"""A health report that dropped the key naming its own blind spot, and a scan that died on one unreadable file.

Two defects in `scripts/utils/memory_health.py`.

**`compute_memory_defects` returned two different shapes.** `index_readable` and
`index_problem` were added to the `"ok"` branch precisely because an absent or
unreadable MEMORY.md used to read as "0 orphans". The `"missing"` branch was
never given them, so a caller reading `result["index_readable"]` - the key that
exists to answer "did anyone actually read the index?" - got a `KeyError` on the
one path where the index is most certainly unreadable. The documented "Shape:"
block listed seven keys while the code returned nine, so nothing named the
divergence either.

**`scan_redundancy` read its files unguarded.** Every other reader in the module
guards `OSError`; this one did not, over a directory that `memory-auto-retire`
mutates while the scan runs. One file at mode 000, or one retired between the
glob and the read, raised straight out of an advisory check whose contract is
"Degrades to ok=False (never raises)". The unreadable file is now skipped, the
surviving files stay index-aligned with their vectors, and the note says how
many went - a drop that is not reported is the same silence one layer down.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.memory_health import (  # noqa: E402
    compute_memory_defects,
    scan_redundancy,
)

_NINE_KEYS = {
    "status", "memory_dir", "file_count", "memory_md_lines", "over_budget",
    "stale", "orphans", "index_readable", "index_problem",
}


# ============================================================
# compute_memory_defects: one shape, both branches
# ============================================================

def test_the_missing_branch_carries_the_index_keys(tmp_path):
    absent = tmp_path / "nowhere"
    result = compute_memory_defects(absent)
    assert result["status"] == "missing"
    assert set(result) == _NINE_KEYS
    assert result["index_readable"] is False
    assert str(absent) in result["index_problem"]


def test_the_ok_branch_carries_exactly_the_same_keys(tmp_path):
    (tmp_path / "alpha.md").write_text("a", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("- [alpha](alpha.md)\n", encoding="utf-8")
    result = compute_memory_defects(tmp_path)
    assert result["status"] == "ok"
    assert set(result) == _NINE_KEYS


def test_the_two_branches_agree_on_their_key_set(tmp_path):
    """Derived from each other, not from a list written twice in this file."""
    (tmp_path / "MEMORY.md").write_text("", encoding="utf-8")
    present = compute_memory_defects(tmp_path)
    absent = compute_memory_defects(tmp_path / "nowhere")
    assert set(present) == set(absent)


def test_an_index_that_cannot_be_read_is_still_reported_as_unreadable(tmp_path):
    (tmp_path / "alpha.md").write_text("a", encoding="utf-8")
    result = compute_memory_defects(tmp_path)  # no MEMORY.md at all
    assert result["index_readable"] is False
    assert result["orphans"] == ["alpha.md"]


def test_an_index_that_is_not_utf8_is_counted_rather_than_fatal(tmp_path):
    """The line count read this file strictly while the orphan read forty lines
    down read it with `errors="ignore"`, so one byte answered the same question
    two ways. MEASURED 2026-09-01 on a `MEMORY.md` carrying a lone 0xe9:
    `compute_memory_defects` raised `UnicodeDecodeError` - a `ValueError`, which
    the `except OSError` beside it cannot catch - and took the whole health
    computation with it, defeating `index_readable`, the key this function grew
    to answer.
    """
    (tmp_path / "alpha.md").write_text("a", encoding="utf-8")
    (tmp_path / "bravo.md").write_text("b", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_bytes(b"- [alpha](alpha.md)\n- caf\xe9 note\n")

    result = compute_memory_defects(tmp_path)

    assert result["status"] == "ok"
    # The count is EXACT, not zeroed: dropping an invalid byte cannot remove a
    # newline, and a zeroed count is how an over-budget index reads as fine.
    assert result["memory_md_lines"] == 2, result["memory_md_lines"]
    # And the pointers in it were still honoured, so the undecodable byte did
    # not turn a linked file into an orphan.
    assert result["orphans"] == ["bravo.md"], result["orphans"]
    assert result["index_readable"] is True


def test_a_utf8_index_still_counts_the_same_lines(tmp_path):
    """The passing twin: the lenient handler must not change the answer for an
    ordinary index."""
    (tmp_path / "alpha.md").write_text("a", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("- [alpha](alpha.md)\n- plain note\n",
                                        encoding="utf-8")
    result = compute_memory_defects(tmp_path)
    assert result["memory_md_lines"] == 2
    assert result["orphans"] == []
    assert result["index_readable"] is True


# ============================================================
# scan_redundancy: an unreadable file is skipped, not fatal
# ============================================================

def _identical_vectors(texts):
    return [[1.0, 0.0] for _ in texts]


def _distinct_vectors(texts):
    return [[1.0, 0.0] if i % 2 == 0 else [0.0, 1.0]
            for i, _ in enumerate(texts)]


@pytest.mark.skipif(os.geteuid() == 0,
                    reason="mode 000 does not block reads for uid 0")
def test_one_unreadable_file_does_not_kill_the_scan(tmp_path):
    for name in ("alpha.md", "bravo.md", "charlie.md"):
        (tmp_path / name).write_text(f"contents of {name}", encoding="utf-8")
    os.chmod(tmp_path / "bravo.md", 0)
    try:
        result = scan_redundancy(tmp_path, embedder=_identical_vectors)
    finally:
        os.chmod(tmp_path / "bravo.md", 0o644)
    assert result["ok"] is True
    assert "1 unreadable" in result["note"]


@pytest.mark.skipif(os.geteuid() == 0,
                    reason="mode 000 does not block reads for uid 0")
def test_the_surviving_pairs_name_the_files_that_were_actually_read(tmp_path):
    """The vectors must stay aligned with the files a skip left behind."""
    for name in ("alpha.md", "bravo.md", "charlie.md"):
        (tmp_path / name).write_text(f"contents of {name}", encoding="utf-8")
    os.chmod(tmp_path / "alpha.md", 0)
    try:
        result = scan_redundancy(tmp_path, embedder=_identical_vectors)
    finally:
        os.chmod(tmp_path / "alpha.md", 0o644)
    assert result["pairs"], "the two readable files should pair"
    named = {p["a"] for p in result["pairs"]} | {p["b"] for p in result["pairs"]}
    assert named == {"bravo.md", "charlie.md"}


def test_a_fully_readable_directory_reports_no_skips(tmp_path):
    for name in ("alpha.md", "bravo.md"):
        (tmp_path / name).write_text(f"contents of {name}", encoding="utf-8")
    result = scan_redundancy(tmp_path, embedder=_distinct_vectors)
    assert result["ok"] is True
    assert result["pairs"] == []
    assert "unreadable" not in result["note"]


@pytest.mark.skipif(os.geteuid() == 0,
                    reason="mode 000 does not block reads for uid 0")
def test_too_few_readable_files_degrades_rather_than_raising(tmp_path):
    for name in ("alpha.md", "bravo.md"):
        (tmp_path / name).write_text(f"contents of {name}", encoding="utf-8")
    os.chmod(tmp_path / "bravo.md", 0)
    try:
        result = scan_redundancy(tmp_path, embedder=_identical_vectors)
    finally:
        os.chmod(tmp_path / "bravo.md", 0o644)
    assert result["ok"] is False
    assert result["pairs"] == []
    assert "unreadable" in result["note"]
