"""One corrupt line that truncated the whole recall log.

`memory_ops_log.read_recall_log` promises "Return all recall records (empty if
none/unreadable)". The `json.loads` sat inside the loop, but the `try` wrapped
the loop, so the FIRST unparseable line aborted iteration: records before it
came back, every record after it was discarded, and nothing said so. The log is
append-only and written by `log_recall` while readers read it, so a torn line is
an ordinary event, not a corruption scare - and the deferred-memory metrics
downstream were then computed over a silently shortened log.

The read is per-line now: a bad line skips itself and nothing else.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scripts.utils.memory_ops_log as mol  # noqa: E402


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    path = tmp_path / "recall.jsonl"
    monkeypatch.setattr(mol, "_recall_log_path", lambda: path)
    return path


def test_a_torn_middle_line_costs_only_itself(log_path):
    log_path.write_text(
        '{"collection": "alpha", "n_hits": 1}\n'
        '{"collection": "bravo", "n_hi\n'
        '{"collection": "charlie", "n_hits": 3}\n',
        encoding="utf-8")
    records = mol.read_recall_log()
    assert [r["collection"] for r in records] == ["alpha", "charlie"]


def test_a_torn_first_line_does_not_empty_the_log(log_path):
    log_path.write_text(
        'not json at all\n'
        '{"collection": "bravo", "n_hits": 2}\n',
        encoding="utf-8")
    assert [r["collection"] for r in mol.read_recall_log()] == ["bravo"]


def test_a_torn_final_line_leaves_the_rest_intact(log_path):
    log_path.write_text(
        '{"collection": "alpha", "n_hits": 1}\n'
        '{"collection": "bravo", "n_hits": 2}\n'
        '{"collection": "charl\n',
        encoding="utf-8")
    assert [r["collection"] for r in mol.read_recall_log()] == ["alpha", "bravo"]


def test_a_clean_log_reads_whole(log_path):
    log_path.write_text(
        '{"collection": "alpha"}\n\n{"collection": "bravo"}\n', encoding="utf-8")
    assert [r["collection"] for r in mol.read_recall_log()] == ["alpha", "bravo"]


def test_an_absent_log_is_empty_not_an_error(log_path):
    assert mol.read_recall_log() == []


def test_a_round_trip_through_the_writer_survives_a_hand_edited_line(log_path):
    mol.log_recall(query_snippet="q", collection="alpha", layer="l",
                   top_score=0.5, gap=False, n_hits=1, threshold=0.2,
                   latency_ms=3)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("{oops\n")
    mol.log_recall(query_snippet="q", collection="charlie", layer="l",
                   top_score=0.5, gap=False, n_hits=1, threshold=0.2,
                   latency_ms=3)
    assert [r["collection"] for r in mol.read_recall_log()] == ["alpha", "charlie"]
